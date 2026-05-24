"""Protected internal ``main-dev`` client artifact builder."""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import io
import json
import marshal
import re
import subprocess
import tarfile
import time
import zlib
from pathlib import Path

MAIN_DEV_ARCHIVE_REL_PATH = "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz"

_CLIENT_SOURCE_DIR = "agent"
_PROTECTED_BUNDLE = "agent/.deng_runtime.bin"
_MANIFEST_NAME = "RELEASE-MANIFEST.json"

_RAW_RUNTIME_FILES = {
    "agent/__init__.py": '''"""DENG Tool: Rejoin protected client package."""\nfrom . import _protected_runtime as _dpr\n_dpr.install()\n__all__ = ["__version__"]\n__version__ = "1.0.0"\n''',
    "agent/deng_tool_rejoin.py": '''#!/usr/bin/env python3\nfrom __future__ import annotations\nimport sys\nfrom pathlib import Path\nif __package__ in {None, ""}:\n    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\nimport agent._protected_runtime  # noqa: F401\nfrom agent.commands import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
    "agent/_protected_runtime.py": r'''from __future__ import annotations
import importlib.abc, importlib.machinery, marshal, sys, zlib
from pathlib import Path
_B=None
class _L(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in _m():
            return importlib.machinery.ModuleSpec(fullname, self, origin=_o(fullname))
        return None
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        code=_m()[module.__name__]
        module.__file__=_o(module.__name__)
        module.__loader__=self
        module.__package__=module.__name__.rpartition(".")[0]
        exec(code, module.__dict__)
def _o(fullname):
    return str(Path(__file__).resolve().parent.parent / (fullname.replace(".","/")+".py"))
def _m():
    global _B
    if _B is None:
        p=Path(__file__).with_name(".deng_runtime.bin")
        _B=marshal.loads(zlib.decompress(p.read_bytes()))
    return _B
def install():
    if not any(isinstance(x,_L) for x in sys.meta_path):
        sys.meta_path.insert(0,_L())
install()
''',
}

_SERVER_ONLY_MODULES = frozenset(
    {
        "agent/bootstrap_installer.py",
        "agent/deferred_bundle_install.py",
        "agent/dev_probe_store.py",
        "agent/install_internal_access.py",
        "agent/install_registry.py",
        "agent/install_signing.py",
        "agent/internal_test_artifact.py",
        "agent/key_stats_format.py",
        "agent/keygen.py",
        "agent/license_key_export.py",
        "agent/license_owner_recovery.py",
        "agent/license_panel.py",
        "agent/license_store.py",
        "agent/rejoin_versions.py",
    }
)

_CLIENT_ONLY_MODULES = frozenset(
    {
        "agent/deng_tool_rejoin.py",
        "agent/__init__.py",
        "agent/_protected_runtime.py",
    }
)

_FORBIDDEN_PATH_SEGMENTS = frozenset(
    {
        ".git",
        ".github",
        "__pycache__",
        "backups",
        "bot",
        "data",
        "docs",
        "examples",
        "logs",
        "migrations",
        "node_modules",
        "scripts",
        "server",
        "site",
        "tests",
    }
)

_FORBIDDEN_STRING_MARKERS = (
    "DISCORD_TOKEN",
    "BOT_TOKEN",
    "SUPABASE_SERVICE_ROLE",
    "SERVICE_ROLE",
    "SUPABASE_URL",
    "DATABASE_URL",
    "AD_RETURN_SIGNING_SECRET",
    "LOOTLABS_API_TOKEN",
    "LINKVERTISE_SECRET",
    "CLIENT_SECRET",
    "PRIVATE_KEY",
    "CLOUDFLARE",
    "process.env",
    "ecosystem.config",
    "generate_license_key",
    "create_key_in_db",
    "license_panel",
    "reset HWID admin",
)

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "htmlcov",
        ".idea",
        ".vscode",
        ".cursor",
        "logs",
        "run",
        "backups",
        "launcher",
        "dist",
        "build",
        ".egg-info",
    }
)

_SENSITIVE_MARKERS = ("token", "secret", "password", "credential", "cookie", "session")


def path_should_exclude(rel_posix: str) -> bool:
    """Return True if *rel_posix* must not appear in the internal test tarball."""
    lower = rel_posix.strip().lower().replace("\\", "/")
    if not lower or lower.startswith("../"):
        return True
    parts = lower.split("/")
    # Junk / secrets-by-location
    if parts[0] in {"data", "tests"}:
        return True
    if lower == ".env" or lower.endswith("/.env"):
        return True
    if lower.endswith((".db", ".sqlite", ".sqlite3")):
        return True
    for p in parts:
        pl = p.lower()
        if p in _SKIP_DIR_NAMES or (p.startswith(".") and pl != ".env.example"):
            return True
        if pl == ".env.example":
            return True
        if pl.endswith(".pyc") or pl.endswith(".pyo"):
            return True
        for m in _SENSITIVE_MARKERS:
            if m in pl:
                return True
    # Root-only junk Filenames
    if lower.endswith(
        (
            ".pid",
            ".sock",
            "pm2-out.log",
            "pm2-error.log",
            "ecosystem.config.js.map",
        )
    ):
        return True
    return False


def iter_internal_test_pack_files(repo_root: Path) -> list[tuple[str, Path]]:
    """Sorted client source files compiled into the protected runtime bundle."""
    repo_root = repo_root.resolve()
    out: list[tuple[str, Path]] = []

    base = repo_root / _CLIENT_SOURCE_DIR
    if not base.is_dir():
        return []
    for path in base.rglob("*.py"):
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if rel in _SERVER_ONLY_MODULES or rel in _CLIENT_ONLY_MODULES:
            continue
        if path_should_exclude(rel):
            continue
        out.append((rel, path))

    out.sort(key=lambda x: x[0])
    return out


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
        if isinstance(body, list) and not body and not isinstance(node, ast.Module):
            body.append(ast.Pass())
    ast.fix_missing_locations(tree)
    return tree


def _client_source_for_compile(rel: str, text: str) -> str:
    if rel == "agent/build_info.py":
        text = re.sub(
            r"\ndef _module_file_path\(modname: str\) -> str \| None:\n(?:    .*\n)+?(?=\ndef _hash_file)",
            '\ndef _module_file_path(modname: str) -> str | None:\n    import importlib.util\n    try:\n        spec = importlib.util.find_spec(modname)\n        if spec is None:\n            return None\n        origin = getattr(spec, "origin", None) or ""\n        if origin and origin != "<deng-protected>":\n            return origin\n        if modname.startswith("agent."):\n            return str(INSTALL_ROOT / (modname.replace(".", "/") + ".py"))\n        return origin or None\n    except Exception:\n        return None\n\n',
            text,
            count=1,
        )
        text = re.sub(
            r"sv_src = Path\(sv_path\)\.read_text\(encoding=\"utf-8\", errors=\"replace\"\)",
            lambda _m: 'sv_src = "" if "agent/supervisor.py" in sv_path.replace("\\\\", "/") else Path(sv_path).read_text(encoding="utf-8", errors="replace")',
            text,
        )
    if rel == "agent/license.py":
        text = re.sub(
            r"\ndef generate_license_key\(\) -> str:\n(?:    .*\n)+?\n(?=# ── Normalization)",
            "\ndef generate_license_key() -> str:\n    raise RuntimeError(\"server-only\")\n\n",
            text,
            count=1,
        )
    if rel == "agent/keystore.py":
        for name in (
            "generate_key",
            "create_key_in_db",
            "revoke_key_in_db",
            "list_keys_in_db",
            "unbind_key",
        ):
            text = re.sub(
                rf"\ndef {name}\([^)]*\)(?: -> [^:]+)?:\n(?:    .*\n)+?(?=\ndef |\n# ──|\Z)",
                f"\ndef {name}(*_args, **_kwargs):\n    raise RuntimeError(\"server-only\")\n",
                text,
                count=1,
            )
    return text


def _module_name_from_rel(rel: str) -> str:
    return rel[:-3].replace("/", ".")


def _compile_client_bundle(pairs: list[tuple[str, Path]]) -> bytes:
    modules: dict[str, object] = {}
    for rel, path in pairs:
        text = _client_source_for_compile(rel, path.read_text(encoding="utf-8"))
        tree = _strip_docstrings(ast.parse(text, filename=f"<deng-protected>/{rel}"))
        modules[_module_name_from_rel(rel)] = compile(
            tree,
            f"<deng-protected>/{rel}",
            "exec",
            optimize=2,
        )
    return zlib.compress(marshal.dumps(modules), level=9)


def _git_commit_short(repo_root: Path) -> str:
    """Best-effort: ``git rev-parse --short=12 HEAD`` from the repo.

    Returns empty string if git is unavailable or the directory is not a repo.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        sha = res.stdout.strip()
        return sha if res.returncode == 0 and sha else ""
    except Exception:  # noqa: BLE001
        return ""


def _make_build_info_bytes(
    repo_root: Path, *, channel: str = "main-dev",
) -> bytes:
    """Render the BUILD-INFO.json payload embedded into the tarball.

    The hash of the tarball itself is computed AFTER write, so it's added
    to ``.installed-build.json`` at install time, not here.  This file
    carries the parts the runtime cannot otherwise discover: git commit
    hash, build time, channel, and a unique probe_id for this build.
    """
    import hashlib

    commit = _git_commit_short(repo_root)
    built_at_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    built_at_unix = int(time.time())
    # probe_id: short stable ID that uniquely identifies this build.
    # Derived from commit + timestamp so it differs even for same-commit rebuilds.
    probe_seed = f"{commit}{built_at_unix}{channel}"
    probe_id = "p-" + hashlib.sha256(probe_seed.encode()).hexdigest()[:16]
    info = {
        "channel": channel,
        "git_commit": commit,
        "built_at_iso": built_at_iso,
        "built_at_unix": built_at_unix,
        "product": "DENG Tool: Rejoin",
        "artifact_format_version": 3,
        "protection": "protected-bytecode-bundle",
        "probe_id": probe_id,
    }
    return json.dumps(info, indent=2, sort_keys=True).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _make_release_manifest_bytes(entries: dict[str, bytes]) -> bytes:
    def raw_bytes(data: bytes | str) -> bytes:
        return data.encode("utf-8") if isinstance(data, str) else data

    payload = {
        "product": "DENG Tool: Rejoin",
        "artifact_format_version": 3,
        "protection": "protected-bytecode-bundle",
        "allowed_top_level": ["agent", "BUILD-INFO.json", _MANIFEST_NAME],
        "files": {
            name: {"sha256": _sha256_bytes(raw_bytes(data)), "size_bytes": len(raw_bytes(data))}
            for name, data in sorted(entries.items())
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def build_internal_test_tarball(repo_root: Path, output_tar_gz: Path) -> str:
    """Write gzip tarball and return lowercase SHA-256 hex digest of the file.

    Also embeds a top-level ``BUILD-INFO.json`` with git commit + build time
    so the runtime can prove what build it is even before the installer
    drops ``.installed-build.json``.
    """
    repo_root = repo_root.resolve()
    output_tar_gz = output_tar_gz.resolve()
    output_tar_gz.parent.mkdir(parents=True, exist_ok=True)
    pairs = iter_internal_test_pack_files(repo_root)
    if not pairs:
        raise RuntimeError("No client modules matched protected artifact rules.")

    build_info_bytes = _make_build_info_bytes(repo_root)
    bundle_bytes = _compile_client_bundle(pairs)
    entries = dict(_RAW_RUNTIME_FILES)
    entries[_PROTECTED_BUNDLE] = bundle_bytes
    entries["BUILD-INFO.json"] = build_info_bytes
    entries[_MANIFEST_NAME] = _make_release_manifest_bytes(entries)

    buf = io.BytesIO()
    digest = hashlib.sha256()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tf:
        for arcname, data in sorted(entries.items()):
            ti = tarfile.TarInfo(name=arcname)
            raw = data.encode("utf-8") if isinstance(data, str) else data
            ti.size = len(raw)
            ti.mtime = int(time.time())
            ti.mode = 0o755 if arcname.endswith("deng_tool_rejoin.py") else 0o644
            tf.addfile(ti, io.BytesIO(raw))
    raw = buf.getvalue()
    digest.update(raw)
    output_tar_gz.write_bytes(raw)
    return digest.hexdigest()


def verify_tarball_exclusions(tar_bytes: bytes) -> None:
    """Raise AssertionError if forbidden names appear inside tarball bytes.

    Also asserts the tarball contains a top-level ``BUILD-INFO.json`` so
    every published artifact carries its own build proof.
    """
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        names = [n for n in tf.getnames() if n.rstrip("/")]
        file_bytes = {
            n: (tf.extractfile(n).read() if tf.getmember(n).isfile() else b"")
            for n in names
        }
    lowered = [n.lower().replace("\\", "/") for n in names]
    for n in lowered:
        segs = n.split("/")
        assert _FORBIDDEN_PATH_SEGMENTS.isdisjoint(segs), n
        assert not n.endswith(".db"), n
        assert not n.endswith(".sqlite"), n
        assert not n.endswith(".sqlite3"), n
        assert not n.endswith(".pyc"), n
        if n == ".env" or n.endswith("/.env"):
            raise AssertionError(f"unexpected env file in tarball: {n}")
        if n.endswith(".py") and n not in _RAW_RUNTIME_FILES:
            raise AssertionError(f"unexpected raw python source in tarball: {n}")
    top_level = {n.split("/", 1)[0] for n in lowered}
    assert top_level <= {"agent", "build-info.json", "release-manifest.json"}, sorted(top_level)
    if "BUILD-INFO.json" not in names:
        raise AssertionError("tarball missing BUILD-INFO.json — build proof is required")
    if _MANIFEST_NAME not in names:
        raise AssertionError("tarball missing RELEASE-MANIFEST.json")
    if _PROTECTED_BUNDLE not in names:
        raise AssertionError("tarball missing protected runtime bundle")
    combined = b"\n".join(file_bytes.values()).decode("utf-8", errors="ignore")
    for marker in _FORBIDDEN_STRING_MARKERS:
        if marker in combined:
            raise AssertionError(f"forbidden string marker in tarball: {marker}")
