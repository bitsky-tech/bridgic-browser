"""Download stealth extensions and pack them into a single extensions.zip."""
import sys
import zipfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bridgic.browser.session._stealth import StealthArgsBuilder, StealthConfig

dest = Path("bridgic/browser/extensions")
dest.mkdir(parents=True, exist_ok=True)
tmp = Path(".ext_tmp_dl")

config = StealthConfig(extension_cache_dir=tmp)
builder = StealthArgsBuilder(config)
paths = builder._ensure_extensions()

zip_path = dest / "extensions.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in paths:
        ext_dir = Path(p)
        for f in sorted(ext_dir.rglob("*")):
            if f.is_file():
                zf.write(f, ext_dir.name / f.relative_to(ext_dir))
        print(f"  Packed: {ext_dir.name}")

shutil.rmtree(tmp, ignore_errors=True)
print(f"Done. ({zip_path.stat().st_size // 1024} KB total)")
