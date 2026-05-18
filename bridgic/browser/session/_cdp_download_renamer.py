"""GUID → real-filename renamer for CDP ``allowAndName`` downloads.

When the host CDP-borrowed code path sends
``Browser.setDownloadBehavior(behavior="allowAndName", downloadPath=...,
eventsEnabled=true)`` Chrome saves every download under a GUID name like
``08d0c134-9231-478e-aca1-08b3e0ec1798``. ``allowAndName`` is the only CDP
knob that overrides Chrome's "Ask where to save each file" user preference, so
we cannot avoid the GUID. We restore the original filename by listening to
``Browser.downloadWillBegin`` (captures ``suggestedFilename``) and renaming
the file once ``Browser.downloadProgress`` reports ``state="completed"``.

Why this lives outside ``DownloadManager``:
``DownloadManager`` is wired into Playwright's per-context download events,
which only fire when downloads route through Playwright's ``artifactsDir``
— that path is what ``allowAndName + downloadPath=<user dir>`` actively
suppresses, so Playwright never sees those downloads. CDP-level events are
the lowest stable layer available.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


_WINDOWS_FORBIDDEN_PATTERN = re.compile(r'[<>:"|?*\x00-\x1f]')
_PATH_SEPARATOR_PATTERN = re.compile(r"[/\\]")
_MAX_FILENAME_BYTES = 255  # NTFS / ext4 ceiling
_CONFLICT_SUFFIX_LIMIT = 999
_DEFAULT_NAME = "download"


def sanitize_filename(name: str) -> str:
    """Make ``name`` safe to use as a single path segment.

    - Strips path separators (no traversal).
    - Replaces Windows-forbidden chars and control bytes.
    - Trims leading/trailing dots and whitespace.
    - Falls back to ``"download"`` when the result would be empty.
    - Truncates to fit ``_MAX_FILENAME_BYTES`` while preserving the
      extension.
    """
    cleaned = _PATH_SEPARATOR_PATTERN.sub("_", name)
    cleaned = _WINDOWS_FORBIDDEN_PATTERN.sub("_", cleaned)
    cleaned = cleaned.strip(" .\t\r\n")

    if not cleaned:
        return _DEFAULT_NAME

    encoded = cleaned.encode("utf-8")
    if len(encoded) <= _MAX_FILENAME_BYTES:
        return cleaned

    stem, ext = _split_stem_ext(cleaned)
    ext_bytes = ext.encode("utf-8")
    budget = _MAX_FILENAME_BYTES - len(ext_bytes)
    if budget <= 0:
        # Pathological extension — drop the extension entirely.
        return cleaned.encode("utf-8")[:_MAX_FILENAME_BYTES].decode(
            "utf-8", errors="ignore"
        ) or _DEFAULT_NAME
    truncated_stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return f"{truncated_stem}{ext}" if truncated_stem else _DEFAULT_NAME


def _split_stem_ext(name: str) -> Tuple[str, str]:
    """Return ``(stem, ext)`` like ``Path.stem``/``Path.suffix`` but pure-string.

    Dotfiles (``.bashrc``) collapse to ``(".bashrc", "")``. Multi-dot names
    (``foo.tar.gz``) split at the last dot (``("foo.tar", ".gz")``).
    """
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot:]


def _resolve_conflict(target: Path) -> Path:
    """Pick a non-colliding sibling of ``target`` using ``"name (N).ext"``.

    Mirrors Chrome's own conflict scheme. Beyond ``_CONFLICT_SUFFIX_LIMIT``
    we fall back to a timestamped suffix so we never enter an infinite loop.
    """
    if not target.exists():
        return target
    stem, ext = _split_stem_ext(target.name)
    parent = target.parent
    for n in range(1, _CONFLICT_SUFFIX_LIMIT + 1):
        candidate = parent / f"{stem} ({n}){ext}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem} ({int(time.time() * 1000)}){ext}"


def _safe_rename(src: Path, dst: Path) -> None:
    """Atomic same-FS rename; fall back to ``shutil.move`` across volumes."""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


@dataclass(frozen=True)
class _Pending:
    """Per-download state captured at ``downloadWillBegin`` time.

    ``target_dir`` is snapshotted so that a hot ``set_default_dir`` call does
    not retarget downloads already in flight.
    """

    sanitized_name: str
    target_dir: Path


class CdpDownloadRenamer:
    """Subscribe to CDP download events and restore filenames post-completion.

    Lifecycle: ``attach`` → ``set_default_dir`` (as many as needed) → ``detach``.
    Thread-safety: events arrive on the asyncio loop thread; all state
    mutation happens in the same thread, no locking required.
    """

    def __init__(self, default_dir: Path) -> None:
        self._default_dir: Path = Path(default_dir)
        self._pending: Dict[str, _Pending] = {}
        self._session: Optional[Any] = None  # playwright CDPSession
        self._handlers: Dict[str, Callable[[dict], None]] = {}
        self._attached: bool = False

    @property
    def default_dir(self) -> Path:
        return self._default_dir

    def set_default_dir(self, new_dir: Path) -> None:
        """Future ``downloadWillBegin`` events use ``new_dir``.

        In-flight downloads keep the directory captured when their willBegin
        event fired — necessary because the daemon may swap the path
        per-command but downloads cannot retarget mid-write.
        """
        self._default_dir = Path(new_dir)

    async def attach(self, session: Any) -> None:
        """Subscribe ``Browser.downloadWillBegin`` / ``Browser.downloadProgress``.

        ``session`` is expected to be a Playwright ``CDPSession`` (or any
        object exposing ``on(event, handler)`` / ``detach()``). The two
        events do not need to be ``send``-enabled — Chrome emits them
        unconditionally once the CDP target is attached.
        """
        if self._attached:
            return
        self._session = session

        on_will_begin: Callable[[dict], None] = self._on_will_begin
        on_progress: Callable[[dict], None] = self._on_progress

        session.on("Browser.downloadWillBegin", on_will_begin)
        session.on("Browser.downloadProgress", on_progress)

        self._handlers = {
            "Browser.downloadWillBegin": on_will_begin,
            "Browser.downloadProgress": on_progress,
        }
        self._attached = True

    async def detach(self) -> None:
        """Best-effort cleanup. Failures are swallowed — we run from close()."""
        if not self._attached:
            return
        self._attached = False
        session = self._session
        self._session = None
        if session is None:
            return
        remove = getattr(session, "remove_listener", None)
        if callable(remove):
            for event, handler in self._handlers.items():
                try:
                    remove(event, handler)
                except Exception:
                    pass
        self._handlers = {}
        try:
            await session.detach()
        except Exception as exc:
            logger.debug("[CdpDownloadRenamer] session.detach() failed: %s", exc)

    # ---------- event handlers ----------

    def _on_will_begin(self, params: dict) -> None:
        guid = params.get("guid")
        if not isinstance(guid, str) or not guid:
            return
        suggested = params.get("suggestedFilename") or ""
        sanitized = sanitize_filename(str(suggested))
        self._pending[guid] = _Pending(
            sanitized_name=sanitized,
            target_dir=self._default_dir,
        )

    def _on_progress(self, params: dict) -> None:
        state = params.get("state")
        guid = params.get("guid")
        if not isinstance(guid, str) or not guid:
            return
        if state == "completed":
            pending = self._pending.pop(guid, None)
            if pending is None:
                # Ghost completion — never saw the willBegin. Possible if
                # bridgic attached after the download had already started.
                return
            self._finalize_rename(guid, pending)
        elif state == "canceled":
            self._pending.pop(guid, None)
            # Best-effort delete the GUID stub so we don't leak.
            for parent in {self._default_dir, *[p.target_dir for p in [self._pending.get(guid)] if p]}:
                try:
                    (parent / guid).unlink(missing_ok=True)  # type: ignore[call-arg]
                except (TypeError, OSError):
                    # Python <3.8 has no missing_ok; we support 3.10+.
                    try:
                        (parent / guid).unlink()
                    except (FileNotFoundError, OSError):
                        pass

    # ---------- rename pipeline ----------

    def _finalize_rename(self, guid: str, pending: _Pending) -> None:
        src = pending.target_dir / guid
        if not src.exists():
            logger.warning(
                "[CdpDownloadRenamer] completed event for %s but source file "
                "missing at %s (likely raced with user move)",
                guid, src,
            )
            return
        desired = pending.target_dir / pending.sanitized_name
        final = _resolve_conflict(desired)
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            _safe_rename(src, final)
            logger.info(
                "[CdpDownloadRenamer] %s → %s", guid[:8], final.name
            )
        except OSError as exc:
            logger.warning(
                "[CdpDownloadRenamer] could not rename %s → %s: %s "
                "(file left at its GUID path)",
                src, final, exc,
            )
