"""DENG Tool: Rejoin — License Check + Package Download API Server.

Provides HTTP endpoints so the Android/Termux client can verify licenses
and download signed release packages WITHOUT exposing the Supabase
service-role key to the client.

Architecture
────────────
• Bot server (this module) holds the service-role key.
• Android client sends only: key, install_id_hash, device_model, app_version.
• The client NEVER sees Supabase credentials or GitHub tokens.
• All sensitive operations happen server-side here.

Endpoints
─────────
  GET  /api/license/health
       Returns {"status": "ok", "version": "...", "store": "..."}.

  GET  /assets/denghub_logo.png
       Public static branding image (no auth). Used as Discord embed thumbnail
       when ``LICENSE_API_PUBLIC_URL`` is set (e.g.
       https://rejoin.deng.my.id/assets/denghub_logo.png).

  GET  /install/latest
  GET  /install/<version>   e.g. /install/v1.0.0
  GET  /install/test/latest   (fixed URL; internal test channel banner)
  GET  /install/beta/latest   (302 alias → /install/test/latest)
  GET  /install/dev/main?exp=...&sig=...   (HMAC-signed; legacy internal)
  GET  /install/launcher/bundle.tar.gz
       Small public **launcher** tarball (no secrets). The bootstrap script fetches this;
       license entry and POST /api/install/authorize happen on first ``deng-rejoin``.
  Shell bootstrap scripts are non-interactive (no license prompt). The full artifact is
  installed after authorize + tokenized GET /api/download/package/<token>.

  POST /api/install/authorize
       Body: {"license_key", "requested_version", "install_id_hash", optional "bootstrap_session"}
       Validates license **without binding HWID**, returns download_url + sha256 + resolved_version.

  POST /api/license/check
       Body: {"key": "DENG-...", "install_id_hash": "...",
               "device_model": "...", "app_version": "...",
               "device_label": ""}
       Returns: {"result": "active|wrong_device|key_not_redeemed|not_found|...", "message": "..."}

  POST /api/license/heartbeat
       Same body as /check. Updates last_seen_at for an active binding.

  POST /api/download/authorize
       Body: {"key", "install_id_hash", "device_model", "app_version", "channel"}
       Verifies license, returns a short-lived download_token and package
       metadata (version, sha256, size_bytes, download_url).

  GET  /api/download/package/<token>
       Validates the token, serves the package file as a binary stream.
       Token is single-use and expires after LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS.

Environment variables
─────────────────────
  LICENSE_API_ENABLED                  "true" to enable (default: false).
  LICENSE_API_HOST                     Bind host (default: 127.0.0.1).
  LICENSE_API_PORT                     Port (default: 8787).
  LICENSE_API_SHARED_SECRET            Optional bearer token for auth.
  LICENSE_API_PUBLIC_URL               Public base URL served by a reverse proxy
                                       (e.g. https://yourdomain.com/rejoin-api).
                                       Used to construct the download_url returned
                                       to clients.  Trailing slash optional.
                                       If unset, falls back to http://host:port.
  LICENSE_API_PATH_PREFIX              URL path prefix the reverse proxy strips
                                       before forwarding (e.g. /rejoin-api).
                                       Leave empty when running directly.
  LICENSE_DOWNLOAD_ROOT                Path to dist/releases/ directory.
  LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS   Token TTL in seconds (default: 300).

Security notes
──────────────
• Do NOT expose the port to the public internet without HTTPS +
  LICENSE_API_SHARED_SECRET.
• The service-role key is NEVER returned in any response.
• Full license keys are NEVER logged.
• Download tokens are single-use, short-lived, and stored as SHA-256 hashes.
• Package path is validated against LICENSE_DOWNLOAD_ROOT (no traversal).
• Rate limiting: max 10 authorize requests per 60 s per IP (in-memory).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("deng.rejoin.license_api")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGO_REL = Path("assets") / "denghub_logo.png"

# ── Bundle etag (SHA-256 prefix of launcher tarball for CDN cache-busting) ────

def _bundle_etag() -> str:
    """Return the first 16 hex chars of the SHA-256 of the launcher bundle.

    Cached after first computation per process lifetime so that each bootstrap
    request does not re-read the file, yet the etag changes whenever the PM2
    process is restarted with a new bundle.
    """
    if _bundle_etag._cache:                        # type: ignore[attr-defined]
        return _bundle_etag._cache[0]              # type: ignore[attr-defined]
    import hashlib
    repo_bundle = _PROJECT_ROOT / "releases" / "launcher" / "deng-rejoin-launcher.tar.gz"
    try:
        digest = hashlib.sha256(repo_bundle.read_bytes()).hexdigest()[:16]
    except OSError:
        digest = ""
    _bundle_etag._cache = [digest]                 # type: ignore[attr-defined]
    return digest

_bundle_etag._cache: list[str] = []               # type: ignore[attr-defined]



def _public_base_url() -> str:
    """Return the public base URL used in download_url responses.

    Priority:
    1. LICENSE_API_PUBLIC_URL env var (set when behind a reverse proxy).
    2. http://<LICENSE_API_HOST>:<LICENSE_API_PORT>  (fallback for direct access).

    The returned string has NO trailing slash.
    """
    pub = os.environ.get("LICENSE_API_PUBLIC_URL", "").strip().rstrip("/")
    if pub:
        return pub
    host = os.environ.get("LICENSE_API_HOST", "127.0.0.1").strip()
    port = os.environ.get("LICENSE_API_PORT", "8787").strip()
    return f"http://{host}:{port}"


def _strip_path_prefix(path: str) -> str:
    """Remove the configured LICENSE_API_PATH_PREFIX from the request path.

    Allows the WSGI app to be mounted at a sub-path by a reverse proxy
    (e.g. nginx ``location /rejoin-api/ { proxy_pass ... }`` with
    ``proxy_pass http://127.0.0.1:8787/;`` stripping the prefix, OR
    simply set LICENSE_API_PATH_PREFIX=/rejoin-api and let this handle it).
    """
    prefix = os.environ.get("LICENSE_API_PATH_PREFIX", "").strip().rstrip("/")
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    return path or "/"


# ── In-memory download token store ────────────────────────────────────────────
# Tokens are stored by their SHA-256 hash (raw token is returned to client once).
_download_tokens: dict[str, dict] = {}
_tokens_lock = threading.Lock()

# ── In-memory rate limit (per IP, for /api/download/authorize) ────────────────
_rate_limit: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
_RATE_LIMIT_WINDOW: float = 60.0   # seconds
_RATE_LIMIT_MAX: int = 10          # requests per window per IP

# Bootstrap sessions — issued when serving GET /install/dev/main after HMAC gate.
_bootstrap_sessions: dict[str, dict] = {}
_bootstrap_sessions_lock = threading.Lock()

# Token URL-safe character set: letters, digits, hyphen, underscore
_TOKEN_SAFE_RE = re.compile(r'^[A-Za-z0-9_\-]{1,100}$')

def _wrong_device_api_message() -> str:
    from agent.license import WRONG_DEVICE_USER_MESSAGE

    return WRONG_DEVICE_USER_MESSAGE


def _key_not_redeemed_api_message() -> str:
    from agent.license import KEY_NOT_REDEEMED_API_MESSAGE

    return KEY_NOT_REDEEMED_API_MESSAGE


_TEST_INSTALL_FORBIDDEN_MESSAGE = (
    "This test build is only available to owner/admin/testers."
)


_RESULT_MESSAGES: dict[str, str] = {
    "active": "License active.",
    "not_found": "Key not found. Check the key and try again.",
    "revoked": "This key has been revoked.",
    "expired": "This key has expired.",
    "inactive": "License inactive.",
    "missing_key": "No license key provided.",
    "server_unavailable": "License server temporarily unavailable.",
    "no_release": "No release found for this channel.",
    "token_expired": "Download token expired or already used.",
}

# ── Token helpers ─────────────────────────────────────────────────────────────


def _expire_old_tokens() -> None:
    """Remove expired/used tokens. Must be called under _tokens_lock."""
    now = time.time()
    expired = [
        k for k, v in _download_tokens.items()
        if now > v["expires_at"] or v.get("used")
    ]
    for k in expired:
        del _download_tokens[k]


def _issue_download_token(
    path: Path,
    sha256: str,
    filename: str,
    version: str,
    channel: str,
    size_bytes: int,
    ttl: int,
) -> str:
    """Issue a short-lived, single-use download token. Returns the raw token."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = time.time() + ttl
    with _tokens_lock:
        _expire_old_tokens()
        _download_tokens[token_hash] = {
            "path": str(path),
            "sha256": sha256,
            "filename": filename,
            "version": version,
            "channel": channel,
            "size_bytes": size_bytes,
            "expires_at": expires_at,
            "used": False,
        }
    return raw


def _consume_download_token(raw_token: str) -> dict | None:
    """Return and invalidate a token entry. Returns None if invalid/expired."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    now = time.time()
    with _tokens_lock:
        entry = _download_tokens.get(token_hash)
        if not entry:
            return None
        if entry.get("used") or now > entry["expires_at"]:
            _download_tokens.pop(token_hash, None)
            return None
        entry["used"] = True
        return dict(entry)


# ── Rate limiting ─────────────────────────────────────────────────────────────


def _check_rate_limit(remote_addr: str) -> bool:
    """Return True if request is within the rate limit (allowed)."""
    now = time.time()
    with _rate_limit_lock:
        history = _rate_limit.get(remote_addr, [])
        history = [t for t in history if now - t < _RATE_LIMIT_WINDOW]
        if len(history) >= _RATE_LIMIT_MAX:
            _rate_limit[remote_addr] = history
            return False
        history.append(now)
        _rate_limit[remote_addr] = history
        # Prevent unbounded growth
        if len(_rate_limit) > 2000:
            oldest_ip = min(_rate_limit, key=lambda ip: min(_rate_limit[ip], default=0))
            _rate_limit.pop(oldest_ip, None)
    return True


# ── Download root + manifest ──────────────────────────────────────────────────


def _get_download_root() -> Path | None:
    """Return resolved PATH of the download root, or None if not configured."""
    dr = os.environ.get("LICENSE_DOWNLOAD_ROOT", "").strip()
    if not dr:
        return None
    p = Path(dr).resolve()
    return p if p.is_dir() else None


def _load_manifest(download_root: Path, channel: str) -> dict | None:
    """Find and return the newest manifest for *channel*.

    Looks in ``download_root/releases/<channel>/*/manifest.json`` and picks
    the newest by semantic version.  Returns None if no manifest found.
    """
    channel_dir = download_root / "releases" / channel
    if not channel_dir.is_dir():
        # Also try without the ``releases/`` sub-level (flat layout)
        channel_dir = download_root / channel
    if not channel_dir.is_dir():
        return None

    manifests = list(channel_dir.glob("*/manifest.json"))
    if not manifests:
        return None

    def _ver_key(p: Path) -> tuple[int, ...]:
        parts = []
        for seg in p.parent.name.split("."):
            try:
                parts.append(int(seg))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    manifests.sort(key=_ver_key, reverse=True)
    newest = manifests[0]
    try:
        data: dict = json.loads(newest.read_text(encoding="utf-8"))
        pkg_path = (newest.parent / data.get("filename", "")).resolve()
        data["_pkg_path"] = str(pkg_path)
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _mask_key(key: str) -> str:
    """Return a masked version of the key for safe logging."""
    parts = (key or "").split("-")
    if len(parts) >= 5:
        return f"{parts[0]}-{parts[1]}...{parts[-1]}"
    return key[:8] + "..." if len(key) > 8 else "***"


def _hash_install_id(raw_id: str) -> str:
    """SHA-256 hash of a raw install_id (if the client sends unhashed; defensive)."""
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()


def _build_response(result: str, status: int = 200) -> tuple[bytes, int]:
    if result == "wrong_device":
        message = _wrong_device_api_message()
    elif result == "key_not_redeemed":
        message = _key_not_redeemed_api_message()
    else:
        message = _RESULT_MESSAGES.get(result, result)
    payload = json.dumps({"result": result, "message": message}).encode("utf-8")
    return payload, status


def _is_authorized(environ: dict) -> bool:
    """Check Bearer token if LICENSE_API_SHARED_SECRET is set."""
    secret = os.environ.get("LICENSE_API_SHARED_SECRET", "").strip()
    if not secret:
        return True  # No secret configured — open
    auth_header = environ.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:] == secret
    return False


def _read_json_body(environ: dict) -> dict | None:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length <= 0 or length > 8192:
            return None
        body = environ["wsgi.input"].read(length)
        return json.loads(body)
    except (ValueError, KeyError, UnicodeDecodeError):
        return None


# Tail segment after ``/install/`` — pinned semver-ish labels (not ``dev/main``).
_INSTALL_TAIL_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _expire_bootstrap_sessions() -> None:
    now = time.time()
    dead = [k for k, v in _bootstrap_sessions.items() if now > v["expires_at"]]
    for k in dead:
        del _bootstrap_sessions[k]


def _register_bootstrap_session(subpath: str) -> str:
    sid = secrets.token_urlsafe(24)
    with _bootstrap_sessions_lock:
        _expire_bootstrap_sessions()
        if len(_bootstrap_sessions) > 5000:
            _bootstrap_sessions.clear()
        _bootstrap_sessions[sid] = {"subpath": subpath, "expires_at": time.time() + 7200}
    return sid


def _bootstrap_session_ok(sid: str, subpath: str) -> bool:
    if not sid:
        return False
    with _bootstrap_sessions_lock:
        _expire_bootstrap_sessions()
        entry = _bootstrap_sessions.get(sid)
        return bool(entry and entry["subpath"] == subpath)


def _download_roots_allowed() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            resolved = p.expanduser().resolve()
        except OSError:
            return
        if not resolved.is_dir():
            return
        s = str(resolved)
        if s not in seen:
            seen.add(s)
            roots.append(resolved)

    for key in ("REJOIN_ARTIFACT_ROOT", "LICENSE_DOWNLOAD_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            add(Path(raw))
    add(_get_download_root())
    return roots


def _path_under_allowed_download_roots(pkg_path: Path, roots: list[Path]) -> bool:
    rp = pkg_path.resolve()
    for root in roots:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _guess_package_content_type(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "application/gzip"
    if lower.endswith(".zip"):
        return "application/zip"
    return "application/octet-stream"


def _route_public_install(
    environ: dict, path: str, method: str
) -> tuple[bytes, int, str, list[tuple[str, str]] | None] | None:
    """Handle install bootstrap + artifact authorize + package GET; no bearer auth."""
    from agent.bootstrap_installer import render_public_bootstrap
    from agent.install_registry import (
        artifact_path_for_row,
        get_artifact_root,
        get_exact_registry_row,
        is_admin_internal_row,
        public_install_base_url,
        resolve_requested_public_version,
    )
    from agent.install_signing import verify_internal_path

    # GET /install/<tail>
    if path.startswith("/install/"):
        if method != "GET":
            return (
                json.dumps({"error": "GET required"}).encode("utf-8"),
                405,
                "application/json",
                None,
            )
        tail = path[len("/install/") :].strip()
        if not tail or ".." in tail:
            return (
                json.dumps({"error": "Not found"}).encode("utf-8"),
                404,
                "application/json",
                None,
            )
        base = public_install_base_url()
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        exp = (qs.get("exp") or [""])[0]
        sig = (qs.get("sig") or [""])[0]

        if tail == "latest":
            script = render_public_bootstrap(base_url=base, requested="latest", bundle_etag=_bundle_etag())
            return (script.encode("utf-8"), 200, "text/x-shellscript", None)

        if tail == "test/latest":
            script = render_public_bootstrap(
                base_url=base,
                requested="test-latest",
                installer_title="DENG Tool: Rejoin Test Installer",
                banner_lines=("Channel: internal test", "Version: main-dev"),
                bundle_etag=_bundle_etag(),
            )
            return (script.encode("utf-8"), 200, "text/x-shellscript", None)

        if tail == "beta/latest":
            return (
                b"",
                302,
                "text/plain",
                [("Location", "/install/test/latest")],
            )

        if tail == "dev/main":
            if not verify_internal_path("dev/main", exp, sig):
                return (
                    json.dumps({"error": "Forbidden"}).encode("utf-8"),
                    403,
                    "application/json",
                    None,
                )
            sid = _register_bootstrap_session("dev/main")
            script = render_public_bootstrap(
                base_url=base, requested="main-dev", bootstrap_session=sid, bundle_etag=_bundle_etag()
            )
            return (script.encode("utf-8"), 200, "text/x-shellscript", None)

        if tail == "launcher/bundle.tar.gz":
            from agent.install_registry import get_artifact_root

            root = get_artifact_root()
            # Prefer the deployed repo copy (git checkout / PM2 cwd) so `releases/launcher`
            # always wins over a stale duplicate under the artifact download root.
            repo_bundle = _PROJECT_ROOT / "releases" / "launcher" / "deng-rejoin-launcher.tar.gz"
            launcher_path: Path | None = None
            if repo_bundle.is_file():
                launcher_path = repo_bundle
            elif root is not None:
                alt = root / "releases" / "launcher" / "deng-rejoin-launcher.tar.gz"
                if alt.is_file():
                    launcher_path = alt
                    log.warning(
                        "serving launcher bundle from artifact root (repo bundle missing): %s",
                        alt,
                    )
            if launcher_path is None:
                return (
                    json.dumps({"error": "Launcher bundle not found"}).encode("utf-8"),
                    404,
                    "application/json",
                    None,
                )
            try:
                data = launcher_path.read_bytes()
            except OSError as exc:
                log.error("launcher bundle read failed: %s", exc)
                return (
                    json.dumps({"error": "read failed"}).encode("utf-8"),
                    500,
                    "application/json",
                    None,
                )
            return (
                data,
                200,
                "application/gzip",
                [
                    ("Content-Disposition", 'attachment; filename="deng-rejoin-launcher.tar.gz"'),
                    ("Cache-Control", "no-store"),
                ],
            )

        if "/" in tail:
            return (
                json.dumps({"error": "Not found"}).encode("utf-8"),
                404,
                "application/json",
                None,
            )

        if not _INSTALL_TAIL_SAFE.match(tail):
            return (
                json.dumps({"error": "Not found"}).encode("utf-8"),
                404,
                "application/json",
                None,
            )

        script = render_public_bootstrap(base_url=base, requested=tail, bundle_etag=_bundle_etag())
        return (script.encode("utf-8"), 200, "text/x-shellscript", None)

    if path == "/api/install/authorize":
        if method != "POST":
            return (
                json.dumps({"error": "POST required"}).encode("utf-8"),
                405,
                "application/json",
                None,
            )
        remote_addr = environ.get("REMOTE_ADDR", "unknown")
        forwarded_for = (environ.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        client_ip = forwarded_for or remote_addr
        user_agent = (environ.get("HTTP_USER_AGENT") or "")[:120]
        log.info(
            "install/authorize request: method=%s ip=%s ua=%s",
            method, client_ip, user_agent or "<none>",
        )
        if not _check_rate_limit(remote_addr):
            return (
                json.dumps({"error": "Too many requests. Try again later."}).encode("utf-8"),
                429,
                "application/json",
                None,
            )
        body = _read_json_body(environ)
        if not body:
            return (
                json.dumps({"error": "Invalid JSON body"}).encode("utf-8"),
                400,
                "application/json",
                None,
            )
        raw_key = (body.get("license_key") or body.get("key") or "").strip()
        req_ver = (body.get("requested_version") or "").strip()
        install_id_hash = (body.get("install_id_hash") or "").strip()
        if install_id_hash and len(install_id_hash) != 64:
            install_id_hash = _hash_install_id(install_id_hash)
        bootstrap_session = (body.get("bootstrap_session") or "").strip()
        if not raw_key:
            payload, status = _build_response("missing_key", 400)
            return (payload, status, "application/json", None)
        if not req_ver:
            return (
                json.dumps(
                    {"result": "missing_version", "message": "requested_version is required."}
                ).encode("utf-8"),
                400,
                "application/json",
                None,
            )

        try:
            from agent.license_store import get_default_store

            store = get_default_store()
        except Exception as exc:  # noqa: BLE001
            log.error("install authorize store error: %s", exc)
            p, s = _build_response("server_unavailable", 500)
            return (p, s, "application/json", None)

        req_lower = req_ver.lower()

        if req_lower == "main-dev":
            if not _bootstrap_session_ok(bootstrap_session, "dev/main"):
                return (
                    json.dumps(
                        {
                            "result": "forbidden",
                            "message": "Internal install requires a signed bootstrap URL.",
                        }
                    ).encode("utf-8"),
                    403,
                    "application/json",
                    None,
                )
            row = get_exact_registry_row("main-dev")
            if row is None or not is_admin_internal_row(row):
                return (
                    json.dumps(
                        {"result": "not_found", "message": "Internal build is not configured."}
                    ).encode("utf-8"),
                    404,
                    "application/json",
                    None,
                )
        elif req_lower == "test-latest":
            row = get_exact_registry_row("main-dev")
            if row is None or not is_admin_internal_row(row):
                return (
                    json.dumps(
                        {"result": "not_found", "message": "Internal build is not configured."}
                    ).encode("utf-8"),
                    404,
                    "application/json",
                    None,
                )
        else:
            row, err = resolve_requested_public_version(req_ver)
            if err or row is None:
                return (
                    json.dumps({"result": "not_found", "message": err or "Version not found."}).encode(
                        "utf-8"
                    ),
                    404,
                    "application/json",
                    None,
                )

        lic = store.check_install_download_access(raw_key, install_id_hash)
        if lic != "active":
            log.info("install authorize denied: %s key=%s", lic, _mask_key(raw_key))
            p, s = _build_response(lic, 403)
            return (p, s, "application/json", None)

        if req_lower == "test-latest":
            from agent.install_internal_access import (
                internal_test_install_allowlisted_discord_ids,
                is_internal_test_install_allowed,
            )

            owner_discord_id = store.get_owner_discord_id_for_license_key(raw_key)
            _allowed = is_internal_test_install_allowed(owner_discord_id)
            log.info(
                "install test-latest access: key=%s owner_discord_id=%s allowed=%s allowlist_n=%d",
                _mask_key(raw_key),
                owner_discord_id or "<none>",
                _allowed,
                len(internal_test_install_allowlisted_discord_ids()),
            )
            if not _allowed:
                log.info(
                    "install authorize denied: test-latest not owner/tester key=%s",
                    _mask_key(raw_key),
                )
                return (
                    json.dumps(
                        {
                            "result": "forbidden",
                            "message": _TEST_INSTALL_FORBIDDEN_MESSAGE,
                        }
                    ).encode("utf-8"),
                    403,
                    "application/json",
                    None,
                )

        root = get_artifact_root()
        if root is None:
            return (
                json.dumps(
                    {
                        "result": "server_unavailable",
                        "message": "Artifact storage is not configured on this server.",
                    }
                ).encode("utf-8"),
                503,
                "application/json",
                None,
            )
        pkg_path = artifact_path_for_row(row, root)
        if pkg_path is None or not pkg_path.is_file():
            log.error("install artifact missing for %s", row.get("version"))
            return (
                json.dumps(
                    {
                        "result": "server_unavailable",
                        "message": "Release artifact is not available on this server yet.",
                    }
                ).encode("utf-8"),
                503,
                "application/json",
                None,
            )
        try:
            pkg_path.resolve().relative_to(root.resolve())
        except ValueError:
            log.error("artifact path escape: %s", pkg_path)
            return (
                json.dumps({"result": "forbidden", "message": "Forbidden."}).encode("utf-8"),
                403,
                "application/json",
                None,
            )

        sha = str(row.get("artifact_sha256") or "").strip()
        ttl = max(30, int(os.environ.get("LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS", "300")))
        token = _issue_download_token(
            pkg_path,
            sha,
            pkg_path.name,
            str(row.get("version") or ""),
            str(row.get("channel") or "stable"),
            pkg_path.stat().st_size,
            ttl,
        )
        download_url = f"{_public_base_url()}/api/download/package/{token}"
        log.info(
            "Install artifact authorized: key=%s version=%s",
            _mask_key(raw_key),
            row.get("version", "?"),
        )
        payload = json.dumps(
            {
                "result": "active",
                "message": "OK",
                "download_url": download_url,
                "sha256": sha,
                "resolved_version": str(row.get("version") or ""),
            }
        ).encode("utf-8")
        return (payload, 200, "application/json", None)

    if path.startswith("/api/download/package/"):
        raw_token = path[len("/api/download/package/") :]

        if not _TOKEN_SAFE_RE.match(raw_token):
            return (
                json.dumps({"error": "Invalid token format."}).encode("utf-8"),
                400,
                "application/json",
                None,
            )

        entry = _consume_download_token(raw_token)
        if entry is None:
            return (
                json.dumps({"error": "Token invalid or expired."}).encode("utf-8"),
                401,
                "application/json",
                None,
            )

        pkg_path = Path(entry["path"])
        roots = _download_roots_allowed()
        if roots:
            if not _path_under_allowed_download_roots(pkg_path, roots):
                log.error("Path escape via token: %s", pkg_path)
                return (
                    json.dumps({"error": "Forbidden."}).encode("utf-8"),
                    403,
                    "application/json",
                    None,
                )
        else:
            return (
                json.dumps({"error": "Download service not configured on this server."}).encode("utf-8"),
                503,
                "application/json",
                None,
            )

        if not pkg_path.is_file():
            log.error("Package file gone: %s", pkg_path)
            return (
                json.dumps({"error": "Package not found."}).encode("utf-8"),
                404,
                "application/json",
                None,
            )

        try:
            data = pkg_path.read_bytes()
        except OSError as exc:
            log.error("Cannot read package %s: %s", pkg_path, exc)
            return (
                json.dumps({"error": "Cannot serve package."}).encode("utf-8"),
                500,
                "application/json",
                None,
            )

        filename = entry.get("filename") or pkg_path.name
        ctype = _guess_package_content_type(filename)
        log.info("Package served: %s channel=%s", filename, entry.get("channel", "?"))
        return (
            data,
            200,
            ctype,
            [("Content-Disposition", f'attachment; filename="{filename}"')],
        )

    return None


def _wsgi_app(environ: dict, start_response):  # noqa: ANN001
    """Minimal WSGI application — no external framework required."""
    path = _strip_path_prefix(environ.get("PATH_INFO", ""))
    method = environ.get("REQUEST_METHOD", "GET")

    _STATUS_TEXT = {
        200: "200 OK", 302: "302 Found", 400: "400 Bad Request", 401: "401 Unauthorized",
        403: "403 Forbidden", 404: "404 Not Found", 405: "405 Method Not Allowed",
        429: "429 Too Many Requests", 500: "500 Internal Server Error",
        503: "503 Service Unavailable",
    }

    def respond(
        body: bytes,
        status: int = 200,
        content_type: str = "application/json",
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> list[bytes]:
        status_str = _STATUS_TEXT.get(status, f"{status} Error")
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status_str, headers)
        return [body]

    # ── Health check ──────────────────────────────────────────────────────────
    if path == "/api/license/health":
        from agent.license_store import get_default_store
        store_mode = os.environ.get("DENG_LICENSE_STORE", "local")
        try:
            from agent.constants import VERSION
        except ImportError:
            VERSION = "unknown"
        payload = json.dumps({"status": "ok", "version": VERSION, "store": store_mode}).encode()
        return respond(payload)

    # ── Static branding asset (no auth; Discord must fetch via public URL) ───
    if path == "/assets/denghub_logo.png":
        if method != "GET":
            return respond(json.dumps({"error": "GET required"}).encode(), 405)
        try:
            logo_path = (_PROJECT_ROOT / _LOGO_REL).resolve()
            logo_path.relative_to(_PROJECT_ROOT.resolve())
        except ValueError:
            return respond(b"Not found", 404)
        if not logo_path.is_file():
            return respond(b"Not found", 404)
        try:
            data = logo_path.read_bytes()
        except OSError:
            return respond(b"Not found", 404)
        return respond(data, content_type="image/png")

    # ── Public bootstrap + install authorize + signed package downloads ───────
    routed = _route_public_install(environ, path, method)
    if routed is not None:
        body, st, ctype, extra = routed
        return respond(body, status=st, content_type=ctype, extra_headers=extra)

    # ── Shared-secret gate (Discord/agent endpoints; not public installers) ───
    if not _is_authorized(environ):
        return respond(json.dumps({"error": "Unauthorized"}).encode(), 401)

    # ── Check / Heartbeat ─────────────────────────────────────────────────────
    if path in ("/api/license/check", "/api/license/heartbeat"):
        if method != "POST":
            return respond(json.dumps({"error": "POST required"}).encode(), 405)

        body = _read_json_body(environ)
        if not body:
            return respond(json.dumps({"error": "Invalid JSON body"}).encode(), 400)

        raw_key = (body.get("key") or "").strip()
        install_id_hash = (body.get("install_id_hash") or "").strip()
        device_model = (body.get("device_model") or "unknown")[:120]
        app_version = (body.get("app_version") or "unknown")[:40]
        device_label = (body.get("device_label") or "").strip()[:80]

        if not raw_key:
            payload, status = _build_response("missing_key", 400)
            return respond(payload, status)

        # If client sends a raw (unhashed) install_id, hash it here.
        # Clients should send the hash directly; this is a fallback.
        if len(install_id_hash) != 64:  # SHA-256 hex = 64 chars
            install_id_hash = _hash_install_id(install_id_hash)

        log.info(
            "License %s for key %s device_model=%s",
            "check" if path.endswith("check") else "heartbeat",
            _mask_key(raw_key),
            device_model,
        )

        try:
            from agent.license_store import get_default_store
            store = get_default_store()
            result = store.bind_or_check_device(
                raw_key, install_id_hash, device_model, app_version, device_label,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("License check error: %s", exc)
            payload, status = _build_response("server_unavailable", 500)
            return respond(payload, status)

        log.info("License result: %s for key %s", result, _mask_key(raw_key))
        payload, status = _build_response(result)
        return respond(payload, status)

    # ── Download authorize ────────────────────────────────────────────────────
    if path == "/api/download/authorize":
        if method != "POST":
            return respond(json.dumps({"error": "POST required"}).encode(), 405)

        remote_addr = environ.get("REMOTE_ADDR", "unknown")
        if not _check_rate_limit(remote_addr):
            return respond(
                json.dumps({"error": "Too many requests. Try again later."}).encode(), 429
            )

        body = _read_json_body(environ)
        if not body:
            return respond(json.dumps({"error": "Invalid JSON body"}).encode(), 400)

        raw_key = (body.get("key") or "").strip()
        install_id_hash = (body.get("install_id_hash") or "").strip()
        device_model = (body.get("device_model") or "unknown")[:120]
        app_version = (body.get("app_version") or "unknown")[:40]
        device_label = (body.get("device_label") or "").strip()[:80]
        channel = (body.get("channel") or "stable").strip().lower()
        if channel not in ("stable", "beta", "dev"):
            channel = "stable"

        if not raw_key:
            p, s = _build_response("missing_key", 400)
            return respond(p, s)

        if len(install_id_hash) != 64:
            install_id_hash = _hash_install_id(install_id_hash)

        download_root = _get_download_root()
        if not download_root:
            return respond(
                json.dumps({"error": "Download service not configured on this server.",
                            "result": "server_unavailable"}).encode(), 503
            )

        # Validate license via store
        try:
            from agent.license_store import get_default_store
            store = get_default_store()
            lic_result = store.bind_or_check_device(
                raw_key, install_id_hash, device_model, app_version, device_label,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("License check error in authorize: %s", exc)
            p, s = _build_response("server_unavailable", 500)
            return respond(p, s)

        if lic_result != "active":
            log.info("Download authorize denied: %s key=%s", lic_result, _mask_key(raw_key))
            p, s = _build_response(lic_result, 403)
            return respond(p, s)

        # Find manifest for requested channel
        manifest = _load_manifest(download_root, channel)
        if not manifest:
            return respond(
                json.dumps({"error": f"No release found for channel '{channel}'.",
                            "result": "no_release"}).encode(), 404
            )

        pkg_path = Path(manifest["_pkg_path"])
        if not pkg_path.is_file():
            log.error("Package file missing: %s", pkg_path)
            return respond(
                json.dumps({"error": "Package file not found on server."}).encode(), 503
            )

        # Safety: path must be within download_root
        try:
            pkg_path.resolve().relative_to(download_root.resolve())
        except ValueError:
            log.error("Package path escape attempt: %s", pkg_path)
            return respond(json.dumps({"error": "Forbidden."}).encode(), 403)

        ttl = max(30, int(os.environ.get("LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS", "300")))
        token = _issue_download_token(
            pkg_path,
            manifest.get("sha256", ""),
            manifest.get("filename", ""),
            manifest.get("version", ""),
            channel,
            manifest.get("size_bytes", 0),
            ttl,
        )

        expires_dt = datetime.fromtimestamp(time.time() + ttl, tz=timezone.utc)
        download_url = f"{_public_base_url()}/api/download/package/{token}"

        log.info(
            "Download token issued: key=%s channel=%s version=%s",
            _mask_key(raw_key), channel, manifest.get("version", "?")
        )

        payload = json.dumps({
            "result": "active",
            "download_token": token,
            "expires_at": expires_dt.isoformat(),
            "version": manifest.get("version", ""),
            "channel": channel,
            "filename": manifest.get("filename", ""),
            "sha256": manifest.get("sha256", ""),
            "size_bytes": manifest.get("size_bytes", 0),
            "download_url": download_url,
            "notes": manifest.get("notes", ""),
        }).encode()
        return respond(payload)

    # ── 404 ───────────────────────────────────────────────────────────────────
    return respond(json.dumps({"error": "Not found"}).encode(), 404)


def start_api_server(host: str, port: int) -> None:
    """Start the license API server using wsgiref (stdlib only, no extra deps)."""
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
            pass  # wsgiref logs suppressed; we use our own logger

    log.info("License API server starting on %s:%d", host, port)
    with make_server(host, port, _wsgi_app, handler_class=_QuietHandler) as httpd:
        log.info("License API server ready — http://%s:%d/api/license/health", host, port)
        httpd.serve_forever()


def maybe_start_api_thread() -> None:
    """Start the license API in a daemon thread if LICENSE_API_ENABLED=true.

    Called from bot/main.py on startup.  Does nothing if not enabled.
    """
    enabled = os.environ.get("LICENSE_API_ENABLED", "").strip().lower()
    if enabled not in ("1", "true", "yes"):
        log.debug("License API disabled (set LICENSE_API_ENABLED=true to enable).")
        return

    host = os.environ.get("LICENSE_API_HOST", "127.0.0.1").strip()
    try:
        port = int(os.environ.get("LICENSE_API_PORT", "8787"))
    except ValueError:
        log.error("LICENSE_API_PORT is not a valid integer — API not started.")
        return

    import threading
    t = threading.Thread(
        target=start_api_server,
        args=(host, port),
        daemon=True,
        name="license-api",
    )
    t.start()
    log.info("License API thread started (daemon) on %s:%d.", host, port)
