#!/usr/bin/env python3
"""Pack minimal launcher tarball (stdlib deferred install + stub entrypoint)."""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCHER_REL = "releases/launcher/deng-rejoin-launcher.tar.gz"

_STUB_DENG = '''#!/usr/bin/env python3
"""Stub entry — full tree arrives after first successful license-gated download."""
from __future__ import annotations
import sys
from pathlib import Path

_app_home = Path(__file__).resolve().parents[1]
if str(_app_home) not in sys.path:
    sys.path.insert(0, str(_app_home))

from agent.deferred_bundle_install import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
'''


def main() -> int:
    src_def = _ROOT / "agent" / "deferred_bundle_install.py"
    if not src_def.is_file():
        print("missing agent/deferred_bundle_install.py", file=sys.stderr)
        return 1
    out = _ROOT / _LAUNCHER_REL.replace("\\", "/")
    out.parent.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tf:
        tf.add(src_def, arcname="agent/deferred_bundle_install.py")
        ti = tarfile.TarInfo(name="agent/deng_tool_rejoin.py")
        data = _STUB_DENG.encode("utf-8")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
        init_p = _ROOT / "agent" / "__init__.py"
        if init_p.is_file():
            tf.add(init_p, arcname="agent/__init__.py")

    out.write_bytes(buf.getvalue())
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
