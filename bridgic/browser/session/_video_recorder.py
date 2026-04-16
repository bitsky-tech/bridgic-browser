"""CDP screencast video recorder — encodes to WebM via ffmpeg.

The architecture mirrors Playwright CLI's recording pipeline:
  Playwright CLI sources:
    screencast.ts        → manages the CDP screencast session
    videoRecorder.ts     → receives JPEG frames → pipes them to ffmpeg → WebM
  This file combines both responsibilities.

How it works:
  1. ``Page.startScreencast`` (CDP) tells Chrome to push JPEG snapshots.
  2. Each frame produced by Chrome fires a ``Page.screencastFrame`` event.
  3. We forward the frame bytes to an ffmpeg subprocess via its stdin pipe.
  4. ffmpeg encodes the JPEG stream as VP8/WebM straight to the output file.
  5. On stop(), closing the pipe lets ffmpeg flush and the file is immediately
     usable.

Compared with Playwright Python's ``record_video_dir`` option:
  record_video_dir: starts ffmpeg at context-create time, records every page,
                    and the file is streamed back via RPC (1 MB base64 chunks).
  CDP screencast:   starts on demand, the file is ready as soon as stop()
                    returns, with zero RPC overhead.

Reference paths in the Playwright monorepo:
  packages/playwright-core/src/server/screencast.ts
  packages/playwright-core/src/server/chromium/videoRecorder.ts

Usage::

    recorder = VideoRecorder(context, page, "/tmp/video.webm", (800, 600))
    await recorder.start()
    # ... drive the browser ...
    path = await recorder.stop()   # file is ready
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import os
import platform
import re
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 25 fps — matches Playwright's videoRecorder.ts (line 17: ``const fps = 25;``).
_FPS = 25

# Matches "ffmpeg-<digits>" so we can sort version directories numerically
# rather than lexicographically (otherwise "ffmpeg-999" sorts above
# "ffmpeg-1011", which would pin us to an older binary).
_FFMPEG_VERSION_RE = re.compile(r"^ffmpeg-(\d+)$")

# Upper bound on stderr retained in memory per recorder.  64 KiB matches the
# typical OS pipe buffer, which is why this number also matches the historical
# deadlock threshold — anything that would fit in the pipe is kept verbatim,
# and real error scenarios dump a few KiB at most.
_STDERR_CAP = 64 * 1024


# ---------------------------------------------------------------------------
# ffmpeg path discovery
# ---------------------------------------------------------------------------

def _find_ffmpeg() -> str:
    """Locate Playwright's bundled ffmpeg, or fall back to the one on PATH.

    When Playwright installs browsers it also downloads ffmpeg into its cache:
      macOS:   ~/Library/Caches/ms-playwright/ffmpeg-{revision}/ffmpeg-mac
      Linux:   ~/.cache/ms-playwright/ffmpeg-{revision}/ffmpeg-linux
      Windows: %LOCALAPPDATA%/ms-playwright/ffmpeg-{revision}/ffmpeg-win64.exe

    The cache root can be overridden with ``PLAYWRIGHT_BROWSERS_PATH``. If no
    Playwright copy is found we fall back to ``ffmpeg`` from ``$PATH``.
    """
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path:
        system = platform.system()
        if system == "Darwin":
            browsers_path = os.path.expanduser("~/Library/Caches/ms-playwright")
        elif system == "Linux":
            browsers_path = os.path.expanduser("~/.cache/ms-playwright")
        else:
            # Windows: %LOCALAPPDATA% is the first choice, but services,
            # sandboxed sessions, and some CI agents run without it set.
            # Fall back to the canonical ~/AppData/Local path so we still
            # find Playwright's cache instead of jumping straight to PATH.
            _local_app = os.environ.get("LOCALAPPDATA", "")
            if _local_app:
                browsers_path = os.path.join(_local_app, "ms-playwright")
            else:
                browsers_path = str(
                    Path.home() / "AppData" / "Local" / "ms-playwright"
                )

    bp = Path(browsers_path)
    if browsers_path and bp.is_dir():
        suffix_map = {"Darwin": "mac", "Linux": "linux", "Windows": "win64.exe"}
        suffix = suffix_map.get(platform.system(), "linux")
        # Pick the highest numeric revision (e.g. ffmpeg-1011 > ffmpeg-999).
        # Lexicographic sort would pick ffmpeg-999 here, which is wrong.
        candidates: List[Tuple[int, Path]] = []
        try:
            entries = list(bp.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            match = _FFMPEG_VERSION_RE.match(entry.name)
            if match:
                candidates.append((int(match.group(1)), entry))
        for _, entry in sorted(candidates, key=lambda c: c[0], reverse=True):
            candidate = entry / f"ffmpeg-{suffix}"
            # X_OK guards against musl/glibc mismatches (e.g. Alpine): the file
            # exists but the loader refuses to execute it.  Fall through to the
            # next version, and ultimately to PATH where apk/yum install places
            # a system-native binary.
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    raise FileNotFoundError(
        "ffmpeg not found. Tried Playwright's cache at "
        f"{browsers_path or '(unset — LOCALAPPDATA missing on Windows)'} "
        "and system PATH. Resolve by any of:\n"
        "  1. playwright install ffmpeg       (downloads the Playwright copy)\n"
        "  2. set PLAYWRIGHT_BROWSERS_PATH    (point at an existing install)\n"
        "  3. install system ffmpeg           (apt / brew / choco / manual)"
    )


# ---------------------------------------------------------------------------
# Empty-recording fallback frame
# ---------------------------------------------------------------------------

# A baked 1×1 white JPEG. Used in the rare case where no real frame arrived
# before stop() (e.g. start_video → immediate stop_video). ffmpeg refuses to
# produce a valid WebM when its input pipe is empty, so we feed it this single
# byte sequence; the ``scale=W:H`` filter stretches it to the target resolution.
# The resulting frame is intentionally minimal — the only goal is "produce a
# playable file", not "produce a meaningful frame".
# Playwright's videoRecorder.ts has an analogous fallback in writeFrame().
_FALLBACK_WHITE_JPEG_1X1 = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\x27 \",.+\x1c\x1c(7),01444\x1f\x27"
    b"9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08"
    b"\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdb\x9e\x97\xf0"
    b"\x07\xff\xd9"
)


def _create_white_jpeg(width: int, height: int) -> bytes:
    """Return a JPEG used as a placeholder when no real frame was captured.

    If Pillow is available we render a true ``width × height`` white JPEG;
    otherwise we return the baked 1×1 fallback above and rely on ffmpeg's
    ``scale`` filter to stretch it. The fallback path is taken in production
    because Pillow is not a project dependency — see the comment on
    ``_FALLBACK_WHITE_JPEG_1X1`` for the implications.

    Reference: Playwright's videoRecorder.ts uses an equivalent
    "ensure at least one frame" fallback inside writeFrame().
    """
    try:
        from PIL import Image  # type: ignore[import-untyped]

        img = Image.new("RGB", (width, height), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except ImportError:
        return _FALLBACK_WHITE_JPEG_1X1


# ---------------------------------------------------------------------------
# VideoRecorder
# ---------------------------------------------------------------------------

class VideoRecorder:
    """Records a page to WebM via CDP screencast + ffmpeg.

    Pipeline overview:

    start():
      ┌─────────────┐   stdin (JPEG frames)   ┌──────────┐
      │ Chrome CDP  │ ──────────────────────► │ ffmpeg   │ ──► output.webm
      │ screencast  │   Page.screencastFrame  │ subproc  │
      └─────────────┘                         └──────────┘

    stop():
      1. CDP Page.stopScreencast — Chrome stops pushing frames.
      2. Pad the tail with ≥1 second of the last frame so the video isn't
         truncated.
      3. Close ffmpeg's stdin → ffmpeg flushes → output file is ready.
      4. Detach the CDP session.

    Parameters
    ----------
    context : Playwright BrowserContext
        Used to create the CDP session (``context.new_cdp_session(page)``).
    page : Playwright Page
        The page to record.
    output_path : str
        Output file path; must end in ``.webm``.
    size : (width, height)
        Output dimensions. Both must be even (a VP8 encoder requirement).
    """

    def __init__(
        self,
        context: Any,
        page: Any,
        output_path: str,
        size: Tuple[int, int],
    ) -> None:
        if not output_path.lower().endswith(".webm"):
            raise ValueError("Output file must have .webm extension")
        self._context = context
        self._page = page
        self._output_path = output_path
        self._width = size[0]
        self._height = size[1]

        self._cdp_session: Any = None
        self._ffmpeg: Optional[asyncio.subprocess.Process] = None

        # Frame state — mirrors FfmpegVideoRecorder in Playwright's
        # videoRecorder.ts (lines 98-103).
        #
        # M2 — clock types (do NOT mix):
        #   * `_first_frame_ts`   — CDP wall-clock seconds (metadata.timestamp)
        #                           unless no CDP frame ever arrived, in which
        #                           case the stop() path seeds it with a
        #                           time.monotonic() value. Either way, all
        #                           downstream deltas in _write_frame are
        #                           consistent because the *same* clock fills
        #                           both operands (timestamp and _first_frame_ts
        #                           come from the same call site).
        #   * `_last_frame[1]`    — same clock as _first_frame_ts (see above).
        #   * `_last_write_time`  — always time.monotonic().
        #
        # The only place a cross-clock arithmetic happens is in prepare_stop()
        # (line ~508): `time.monotonic() - _last_write_time` is a *duration*
        # and is added to `_last_frame[1]`. Durations are clock-agnostic, so
        # the sum stays in _last_frame[1]'s clock. Never subtract monotonic
        # from wall-clock (or vice versa) — only add/subtract durations.
        self._first_frame_ts: float = 0.0                              # timestamp of the first frame; used to compute frame numbers (wall-clock OR monotonic — see note above)
        self._last_frame: Optional[Tuple[bytes, float, int]] = None    # (jpeg_bytes, timestamp[same clock as _first_frame_ts], frame_number)
        self._last_write_time: float = 0.0                             # monotonic seconds of the last _write_frame() call
        self._frame_queue: deque[bytes] = deque()                        # frames waiting to be written to ffmpeg's stdin
        self._is_stopped = False
        self._write_lock = asyncio.Lock()                              # serializes writes to ffmpeg's stdin
        self._flush_pending = False                                    # dedup flag: avoid scheduling a flush task per frame
        self._ffmpeg_write_warned = False                               # one-shot dedup for ffmpeg write errors

        # stderr diagnostics — populated by _stderr_reader_task while ffmpeg
        # runs so its pipe never fills (would deadlock stdin writes).  Capped
        # at _STDERR_CAP bytes; overflow is dropped with a single log note.
        self._stderr_buf: bytearray = bytearray()
        self._stderr_reader_task: Optional[asyncio.Task[None]] = None
        self._stderr_logged: bool = False

        # Strong refs to in-flight Page.screencastFrameAck tasks — prevents
        # the event-loop weak-ref GC from collecting them mid-flight (which
        # surfaces as "Task was destroyed but it is pending!" warnings) and
        # lets prepare_stop() cancel any pending acks that would otherwise
        # race against the CDP session detach.
        self._ack_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # stderr plumbing
    # ------------------------------------------------------------------

    async def _read_stderr(self) -> None:
        """Drain ffmpeg's stderr into a capped buffer for diagnostics.

        Runs for the entire ffmpeg lifetime and exits on EOF (which ffmpeg
        closes when it terminates).  The buffer is capped at _STDERR_CAP;
        overflow is drained-and-dropped so the pipe never back-pressures
        into ffmpeg's stdout reader (which would deadlock `stdin.drain()`
        in the main write path).
        """
        if not self._ffmpeg or not self._ffmpeg.stderr:
            return
        stderr = self._ffmpeg.stderr
        truncated_logged = False
        while True:
            try:
                chunk = await stderr.read(4096)
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            if not chunk:
                return
            remaining = _STDERR_CAP - len(self._stderr_buf)
            if remaining > 0:
                self._stderr_buf.extend(chunk[:remaining])
            elif not truncated_logged:
                logger.debug(
                    "[VideoRecorder] ffmpeg stderr exceeded %d bytes, "
                    "further output dropped: %s",
                    _STDERR_CAP, self._output_path,
                )
                truncated_logged = True

    async def _drain_stderr_reader(self) -> None:
        """Await the stderr reader task and log captured output once.

        Safe to call multiple times — it's a no-op after the first call.
        """
        task = self._stderr_reader_task
        self._stderr_reader_task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:
                pass
        if self._stderr_logged:
            return
        self._stderr_logged = True
        if self._stderr_buf:
            logger.debug(
                "[VideoRecorder] ffmpeg stderr (%d bytes) for %s:\n%s",
                len(self._stderr_buf),
                self._output_path,
                bytes(self._stderr_buf).decode("utf-8", errors="replace"),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start recording: spawn ffmpeg, then start the CDP screencast."""
        ffmpeg_path = _find_ffmpeg()
        os.makedirs(os.path.dirname(self._output_path) or ".", exist_ok=True)

        w, h = self._width, self._height

        # ffmpeg arguments — based on Playwright's videoRecorder.ts
        # ``_startProcess()`` but tuned for legibility instead of raw speed.
        # Playwright's defaults (``-b:v 1M -crf 8 -deadline realtime
        # -speed 8 -qmax 50``) bias hard toward "encode as fast as possible",
        # which leaves browser text smeared. Bridgic's recordings are usually
        # replayed by humans inspecting an LLM session, so sharpness matters
        # more than encode CPU.
        #
        # Input:
        #   -f image2pipe        read an image stream from stdin
        #   -c:v mjpeg           input is a JPEG stream
        # Output:
        #   -c:v vp8             VP8-encoded WebM
        #   -b:v 5M              5 Mbps target — enough headroom for crisp
        #                        text at typical 1280×800 viewports
        #   -crf 4               constant-rate factor (0 = best, 63 = worst);
        #                        4 is high quality but still bounded
        #   -qmin 0 -qmax 30     tighter quantizer cap → no muddy frames when
        #                        the page is busy (vs. Playwright's qmax 50)
        #   -deadline good       balanced encoder mode instead of "realtime";
        #                        ~2-3× slower per frame but visibly cleaner
        #   -speed 2             slower preset (valid 0-5 with deadline=good)
        #   -threads 2           extra worker to keep up with the slower preset
        # Filters:
        #   scale={w}:{h}  scale frames to exact target dimensions.
        #
        #   Why scale instead of pad: Chrome's Page.startScreencast honours
        #   maxWidth/maxHeight as an *aspect-preserving clamp* — it never
        #   upsamples.  When the viewport's aspect ratio differs from W:H
        #   (e.g. viewport 1710×856 vs target 1280×720), Chrome produces
        #   frames that fit within W×H but are shorter/narrower than the
        #   target.  ``pad`` would fill the gap with a visible gray border;
        #   ``scale`` stretches the frame to the exact target size instead.
        #   The distortion is negligible (typically < 12 %) and eliminates
        #   the gray bar entirely.
        args = [
            ffmpeg_path,
            "-loglevel", "error",
            "-f", "image2pipe",
            "-avioflags", "direct",
            "-fpsprobesize", "0",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-c:v", "mjpeg",
            "-i", "pipe:0",
            "-y", "-an",
            "-r", str(_FPS),
            "-c:v", "vp8",
            "-qmin", "0", "-qmax", "30",
            "-crf", "4",
            "-deadline", "good",
            "-speed", "2",
            "-b:v", "5M",
            "-threads", "2",
            "-vf", f"scale={w}:{h}",
            self._output_path,
        ]
        # stdout → DEVNULL (never inspected), stderr → PIPE + background
        # reader task.  Prior code pointed stderr at DEVNULL to avoid the
        # pipe-fill deadlock described below; the tradeoff was that ffmpeg
        # encode failures were completely silent, making diagnosis of
        # corrupt-JPEG / codec errors impossible.  The reader task keeps the
        # pipe drained while buffering up to _STDERR_CAP bytes for later
        # logging, giving us both safety and visibility.
        #
        # Deadlock constraint: if nothing reads stderr and ffmpeg emits >pipe
        # size (~64 KiB on Linux), its next write() blocks → its stdin
        # reader stalls → our `await stdin.drain()` deadlocks → stop() hangs
        # and ffmpeg is force-killed, corrupting the output.
        self._ffmpeg = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_buf = bytearray()
        self._stderr_logged = False
        self._stderr_reader_task = asyncio.create_task(self._read_stderr())

        # Create the CDP session and start the screencast.
        # Reference: Playwright's screencast.ts startScreencast().
        # Reference: Chrome DevTools Protocol — Page.startScreencast
        #   https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-startScreencast
        #
        # If the CDP setup fails we must kill the already-started ffmpeg
        # subprocess; otherwise it leaks.
        try:
            self._cdp_session = await self._context.new_cdp_session(self._page)
            self._cdp_session.on(
                "Page.screencastFrame", self._on_screencast_frame
            )
            await self._cdp_session.send("Page.startScreencast", {
                "format": "jpeg",
                # JPEG quality of source frames coming out of Chrome. This
                # caps the ceiling regardless of encoder tuning — Playwright
                # uses q=80 by default but that visibly smudges browser
                # text. q=95 is essentially visually lossless and the cost
                # is bandwidth we already have to spare on a local CDP
                # connection.
                "quality": 95,
                # maxWidth/maxHeight is a *clamp*, not a target: Chrome
                # downsamples (preserving aspect) to fit within these bounds
                # but never upsamples. When the viewport aspect ratio differs
                # from W:H, Chrome produces smaller frames and ffmpeg's scale
                # filter stretches them to the exact target size.
                # See bridgic/browser/session/_browser.py
                # ``start_video()`` for the dimension-resolution comment.
                "maxWidth": self._width,
                "maxHeight": self._height,
            })
        except BaseException:
            if self._ffmpeg:
                self._ffmpeg.kill()
                self._ffmpeg = None
            # Reader task will exit on EOF; await briefly so it doesn't
            # outlive the recorder.
            await self._drain_stderr_reader()
            raise
        logger.debug(
            "[VideoRecorder] started screencast %dx%d → %s",
            self._width, self._height, self._output_path,
        )

    async def prepare_stop(self) -> None:
        """Phase 1: stop screencast, pad frames, detach CDP session.

        Fast (~milliseconds).  Must be called while Chrome is still alive
        so that ``Page.stopScreencast`` can reach Chrome.  After this
        method returns, the CDP session is detached and Chrome resources
        are released — ``finalize()`` can run even after Chrome exits.

        Idempotent: calling twice is safe (second call is a no-op).
        """
        if self._is_stopped:
            return

        logger.debug("[VideoRecorder] prepare_stop step1: stopScreencast %s", self._output_path)
        # Step 1: tell Chrome to stop pushing frames.
        # Reference: CDP Page.stopScreencast.
        if self._cdp_session:
            try:
                await self._cdp_session.send("Page.stopScreencast")
            except Exception as _e:
                logger.debug("[VideoRecorder] stopScreencast err: %s(%s)", type(_e).__name__, _e)

        logger.debug("[VideoRecorder] prepare_stop step2: ensure frame %s", self._output_path)
        # Step 2: make sure at least one frame has been written. ffmpeg
        # refuses to produce a valid container with an empty input stream.
        # Reference: videoRecorder.ts lines 136-138.
        if not self._last_frame:
            white = _create_white_jpeg(self._width, self._height)
            self._write_frame(white, time.monotonic())

        logger.debug("[VideoRecorder] prepare_stop step3: pad tail %s", self._output_path)
        # Step 3: pad the tail with ≥1 second of the last frame so the
        # output never ends abruptly. Sending an empty frame (b"") is the
        # sentinel that tells _write_frame to advance the frame counter
        # without replacing the cached JPEG bytes.
        # Reference: videoRecorder.ts lines 140-144.
        #
        # Note: _last_write_time is monotonic while _last_frame[1] is
        # wall-clock (Chrome's metadata.timestamp). Mixing clocks is safe
        # here because add_time is a *duration* (not an absolute
        # timestamp) — both clocks advance at the same rate, so the
        # delta is valid.  monotonic is preferred for the duration to
        # avoid NTP jump artifacts.
        add_time = max(time.monotonic() - self._last_write_time, 1.0)
        self._write_frame(b"", self._last_frame[1] + add_time)  # type: ignore[index]

        self._is_stopped = True

        # Cancel any in-flight screencastFrameAck tasks before detaching
        # the CDP session — an ack racing against detach() just errors
        # out and floods logs.  Cancellation is idempotent on already-done
        # tasks; the done-callback handles bookkeeping either way.
        if self._ack_tasks:
            pending = list(self._ack_tasks)
            for t in pending:
                if not t.done():
                    t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        logger.debug("[VideoRecorder] prepare_stop step4: detach CDP %s", self._output_path)
        # Step 4 (moved here from old stop()): detach the CDP session
        # early so Chrome resources are released before finalize().
        if self._cdp_session:
            try:
                await self._cdp_session.detach()
            except Exception:
                pass
            self._cdp_session = None

        logger.debug("[VideoRecorder] prepare_stop done: %s", self._output_path)

    async def finalize(self) -> str:
        """Phase 2: flush frames to ffmpeg, close stdin, wait for exit.

        Returns the output file path.  Chrome can be dead — this method
        only needs the ffmpeg subprocess.  If ``prepare_stop()`` was not
        called beforehand, it is called automatically as a safety fallback.
        """
        if not self._is_stopped:
            try:
                await asyncio.wait_for(self.prepare_stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[VideoRecorder] finalize: prepare_stop fallback timed out, "
                    "force-marking stopped: %s", self._output_path,
                )
                self._is_stopped = True
                self._cdp_session = None

        # Step 4: drain any frames still queued for ffmpeg's stdin.
        await self._flush_queue()

        # Step 5: close ffmpeg's stdin so it can finalize the file.
        if self._ffmpeg and self._ffmpeg.stdin:
            try:
                self._ffmpeg.stdin.close()
                await self._ffmpeg.stdin.wait_closed()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._ffmpeg.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                self._ffmpeg.kill()
                logger.warning("[VideoRecorder] ffmpeg killed after timeout")

        # Step 6: reader task exits on stderr EOF (which ffmpeg closes when it
        # exits in step 5).  Await briefly so it doesn't outlive finalize();
        # emit whatever was captured so operators can diagnose encode failures.
        await self._drain_stderr_reader()

        logger.debug("[VideoRecorder] finalize done → %s", self._output_path)
        return self._output_path

    async def stop(self) -> str:
        """Full stop (convenience): ``prepare_stop()`` + ``finalize()``.

        On return the file is fully written. The shutdown sequence mirrors
        ``stop()`` in Playwright's videoRecorder.ts (lines 130-155).

        For the two-phase flow used by ``Browser.close()`` — where Chrome
        must exit between the two phases — call ``prepare_stop()`` and
        ``finalize()`` separately instead.
        """
        await self.prepare_stop()
        return await self.finalize()

    async def detach_screencast(self) -> None:
        """Stop the CDP screencast and detach the session without stopping ffmpeg.

        Used when the last recorded page is about to close — the CDP session
        is bound to that page and will die with it, but ffmpeg must stay alive
        for a later ``finalize()`` call.  Idempotent: safe to call when the
        session is already detached.
        """
        if self._cdp_session:
            try:
                await self._cdp_session.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await self._cdp_session.detach()
            except Exception:
                pass
            self._cdp_session = None

    async def switch_page(self, new_page: Any) -> None:
        """Switch screencast source to a different page. ffmpeg stays alive."""
        if self._is_stopped:
            return
        if new_page == self._page:
            return

        # Tear down old screencast
        if self._cdp_session:
            try:
                await self._cdp_session.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                self._cdp_session.remove_listener(
                    "Page.screencastFrame", self._on_screencast_frame,
                )
            except Exception:
                pass
            try:
                await self._cdp_session.detach()
            except Exception:
                pass

        # Set up new screencast on the new page
        self._page = new_page
        try:
            self._cdp_session = await self._context.new_cdp_session(new_page)
            self._cdp_session.on("Page.screencastFrame", self._on_screencast_frame)
            await self._cdp_session.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 95,
                "maxWidth": self._width,
                "maxHeight": self._height,
            })
            logger.debug("[VideoRecorder] switched screencast to new page")
        except Exception as e:
            logger.warning("[VideoRecorder] switch_page CDP setup failed: %s", e)
            self._cdp_session = None

    @property
    def current_page(self) -> Any:
        """The page currently being recorded."""
        return self._page

    @property
    def output_path(self) -> str:
        return self._output_path

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped

    # ------------------------------------------------------------------
    # Frame handling — mirrors _writeFrame() in Playwright's
    # videoRecorder.ts (lines 195-213).
    # ------------------------------------------------------------------

    def _on_screencast_frame(self, params: dict) -> None:
        """Handle a ``Page.screencastFrame`` CDP event from Chrome.

        Reference: Chrome DevTools Protocol — Page.screencastFrame
          https://chromedevtools.github.io/devtools-protocol/tot/Page/#event-screencastFrame

        ``params`` carries:
          - data: base64-encoded JPEG bytes
          - metadata.timestamp: frame timestamp in seconds (wall-clock)
          - sessionId: ack token; Chrome will not send the next frame until
            ``Page.screencastFrameAck`` is replied with this id
        """
        if self._is_stopped:
            return

        try:
            data = base64.b64decode(params["data"])
        except Exception as exc:
            # Should not happen — Chrome always provides valid base64 — but
            # we want to surface a hint without taking down the event loop.
            logger.warning("[VideoRecorder] dropping malformed frame: %s", exc)
            return
        metadata = params.get("metadata", {})
        timestamp: float = metadata.get("timestamp", time.time())

        # Ack the frame so Chrome will push the next one. After stop() the
        # CDP session is detached, so the ack may fail — swallow the
        # exception via add_done_callback to keep the event loop quiet.
        # Reference: CDP Page.screencastFrameAck.
        session_id = params.get("sessionId")
        if session_id and self._cdp_session:
            task = asyncio.create_task(
                self._cdp_session.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            )
            # Retain a strong ref so the task isn't GC'd mid-flight, and
            # remove ourselves from the set when done so it stays bounded
            # to "currently in-flight" rather than "ever created".
            self._ack_tasks.add(task)

            def _on_ack_done(t: asyncio.Task[Any]) -> None:
                self._ack_tasks.discard(t)
                if not t.cancelled():
                    # Consume the exception so asyncio doesn't warn on
                    # "exception was never retrieved".
                    t.exception()

            task.add_done_callback(_on_ack_done)

        self._write_frame(data, timestamp)

    def _write_frame(self, frame: bytes, timestamp: float) -> None:
        """Queue a frame for ffmpeg, padding gaps with the previous frame.

        Mirrors ``_writeFrame()`` in Playwright's videoRecorder.ts
        (lines 195-213).

        Why padding: Chrome's screencast emits frames irregularly — it does
        not push anything while the page is idle — but ffmpeg's input needs
        a constant 25 fps. So whenever the new frame's frame_number is
        ``N`` and the previous one was ``M``, we re-emit the last JPEG
        ``N - M`` times to fill the gap.

          frame_number = floor((timestamp - first_frame_timestamp) * 25)
          repeat_count = current frame_number - previous frame_number

        Sentinel: an empty ``frame`` (b"") signals the tail-padding case
        used by stop(). It advances the frame counter without replacing the
        cached JPEG bytes.
        """
        if self._is_stopped and frame:
            return

        if not self._first_frame_ts:
            self._first_frame_ts = timestamp

        # Compute the current frame number — videoRecorder.ts line 200.
        frame_number = math.floor((timestamp - self._first_frame_ts) * _FPS)

        # Repeat the last frame to cover the gap up to the current frame
        # number. Reference: videoRecorder.ts lines 203-207.
        if self._last_frame is not None:
            repeat_count = frame_number - self._last_frame[2]
            for _ in range(max(repeat_count, 0)):
                self._frame_queue.append(self._last_frame[0])
            # Schedule an async flush. The dedup flag ensures that we only
            # have a single flush task pending at any time even if many
            # frames arrive in quick succession.
            if not self._flush_pending:
                try:
                    loop = asyncio.get_running_loop()
                    self._flush_pending = True
                    def _schedule_flush() -> None:
                        t = asyncio.create_task(self._flush_and_reset())
                        t.add_done_callback(lambda _t: _t.exception() if not _t.cancelled() else None)
                    loop.call_soon(_schedule_flush)
                except RuntimeError:
                    self._flush_pending = False

        if frame:
            # Real frame: replace the cached entry.
            self._last_frame = (frame, timestamp, frame_number)
        else:
            # Empty-frame sentinel: advance the counter, keep the JPEG
            # bytes (used by stop() to extend the tail).
            if self._last_frame is not None:
                self._last_frame = (self._last_frame[0], timestamp, frame_number)
        self._last_write_time = time.monotonic()

    async def _flush_queue(self) -> None:
        """Drain the frame queue into ffmpeg's stdin under a write lock."""
        async with self._write_lock:
            while self._frame_queue:
                frame_data = self._frame_queue.popleft()
                await self._send_frame(frame_data)

    async def _flush_and_reset(self) -> None:
        """Flush the queue and clear the dedup flag (always, via finally)."""
        try:
            await self._flush_queue()
        finally:
            self._flush_pending = False

    async def _send_frame(self, frame: bytes) -> None:
        """Write a JPEG frame to ffmpeg's stdin.

        Errors are logged at WARNING level (with one-shot dedup) so a broken
        encoder is visible in the daemon log. Subsequent failures during the
        same recording are downgraded to DEBUG to avoid log spam.
        """
        if not self._ffmpeg or not self._ffmpeg.stdin or self._ffmpeg.stdin.is_closing():
            return
        try:
            self._ffmpeg.stdin.write(frame)
            await self._ffmpeg.stdin.drain()
        except Exception as e:
            if not self._ffmpeg_write_warned:
                logger.warning("[VideoRecorder] ffmpeg write error: %s", e)
                self._ffmpeg_write_warned = True
            else:
                logger.debug("[VideoRecorder] ffmpeg write error: %s", e)
