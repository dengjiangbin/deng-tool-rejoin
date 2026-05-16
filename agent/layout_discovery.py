"""Real App Cloner / cloud-phone layout-key discovery.

Why this exists
───────────────
Previous "force resize/position" attempts wrote the four ``app_cloner_current_window_*``
ints into ``pkg_preferences.xml`` and assumed App Cloner would honor them.  Real
cloud-phone builds (and many App Cloner versions) ignore those four ints unless
the matching "Set window position" / "Set window size" enable flags are also
true, and unless the per-orientation portrait/landscape keys exist.  A Kaeru
clue pointed at: ``Set`` in auto-DPI portrait/landscape settings.

What this module does (read-only)
─────────────────────────────────
For each package, walk every shared_prefs XML it owns (direct file read first;
root cat fallback when the file is not group-readable).  Parse every
``<int|string|boolean|long|float>`` entry and categorize the key name against
a set of regular expressions covering:

  * position bounds (left/top/right/bottom, x/y, position_x/y)
  * size (width/height)
  * orientation flags (portrait/landscape/orientation/force_landscape)
  * auto-DPI flags (auto_dpi[_portrait|_landscape])
  * the critical ``Set`` enable booleans
    (``set_window_position``, ``set_window_size``, ``set_dpi``,
    ``enable_resize``, ``enable_custom_screen_size``, ``custom_screen_size``,
    ``freeform``, ``floating``, ``custom_resolution``, ``saved_bounds``)
  * multi/clone/per-clone display markers

Discovery results are written to ``~/.deng-tool/rejoin/logs/layout-discovery.log``
(masked, one block per package) — never to public stdout.

Public users never invoke this directly.  ``cmd_start`` calls it silently when
no per-package key map is cached yet.  Hidden command ``--discover-layout-keys``
exposes one terminal line: ``Layout key discovery saved: <path>``.

All file access is wrapped in try/except.  Never raises.  Never prints.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

from . import android
from .constants import LOG_DIR

_log = logging.getLogger("deng.rejoin.layout_discovery")

# ── Discovery log path ────────────────────────────────────────────────────────

LAYOUT_DISCOVERY_LOG = LOG_DIR / "layout-discovery.log"

# Cap on how many shared_prefs files we inspect per package (defence against
# pathological clone packages with hundreds of XMLs).
_MAX_PREF_FILES_PER_PACKAGE = 32
_MAX_KEYS_PER_FILE = 2000

# ── Categorisation patterns ───────────────────────────────────────────────────
#
# Each (category, regex) — case-insensitive.  Multiple categories may match a
# single key (a key like ``app_cloner_set_window_position_landscape_left`` will
# match position, landscape, set_enable, position_left).

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Position bounds
    ("position_left",     re.compile(r"(?:^|[._-])(left|x|pos[ _-]*x|window[_ -]*x|window[ _-]*position[_ -]*x)(?:$|[._-])", re.I)),
    ("position_top",      re.compile(r"(?:^|[._-])(top|y|pos[ _-]*y|window[_ -]*y|window[ _-]*position[_ -]*y)(?:$|[._-])", re.I)),
    ("position_right",    re.compile(r"(?:^|[._-])(right|window[ _-]*right)(?:$|[._-])", re.I)),
    ("position_bottom",   re.compile(r"(?:^|[._-])(bottom|window[ _-]*bottom)(?:$|[._-])", re.I)),
    ("position_generic",  re.compile(r"(window[_ -]*position|saved[_ -]*bounds|task[_ -]*bounds|stack[_ -]*bounds|win[_ -]*bounds|bounds)", re.I)),
    # Size
    ("size_width",        re.compile(r"(?:^|[._-])(width|window[_ -]*width|screen[_ -]*width)(?:$|[._-])", re.I)),
    ("size_height",       re.compile(r"(?:^|[._-])(height|window[_ -]*height|screen[_ -]*height)(?:$|[._-])", re.I)),
    ("size_generic",      re.compile(r"(window[_ -]*size|custom[_ -]*size|screen[_ -]*size|custom[_ -]*resolution|resolution)", re.I)),
    # Orientation
    ("orient_landscape",  re.compile(r"(landscape|land[_ -]*mode|force[_ -]*landscape)", re.I)),
    ("orient_portrait",   re.compile(r"(portrait|port[_ -]*mode|force[_ -]*portrait)", re.I)),
    ("orient_generic",    re.compile(r"(orientation|orient)", re.I)),
    # Auto DPI
    ("auto_dpi",          re.compile(r"(auto[_ -]*dpi|dpi[_ -]*auto)", re.I)),
    ("dpi_value",         re.compile(r"(?:^|[._-])(dpi|density|target[_ -]*dpi|override[_ -]*dpi)(?:$|[._-])", re.I)),
    # The CRITICAL "Set" / enable flags
    ("set_position_enable", re.compile(
        r"(set[_ -]*window[_ -]*position|set[_ -]*pos|enable[_ -]*position|"
        r"use[_ -]*custom[_ -]*position|custom[_ -]*position[_ -]*enabled|"
        r"override[_ -]*position)", re.I)),
    ("set_size_enable",   re.compile(
        r"(set[_ -]*window[_ -]*size|set[_ -]*size|enable[_ -]*resize|"
        r"enable[_ -]*custom[_ -]*screen[_ -]*size|enable[_ -]*custom[_ -]*size|"
        r"custom[_ -]*screen[_ -]*size[_ -]*enabled|"
        r"custom[_ -]*size[_ -]*enabled|override[_ -]*size|"
        r"force[_ -]*resize|allow[_ -]*resize)", re.I)),
    ("set_dpi_enable",    re.compile(r"(set[_ -]*dpi|override[_ -]*dpi|enable[_ -]*dpi|custom[_ -]*dpi[_ -]*enabled)", re.I)),
    ("freeform",          re.compile(r"(freeform|floating[_ -]*window|float[_ -]*mode|pip[_ -]*mode)", re.I)),
    # Per-clone / display
    ("multi_clone",       re.compile(r"(per[_ -]*clone|multi[_ -]*window|clone[_ -]*window)", re.I)),
    ("display_info",      re.compile(r"(display[_ -]*id|target[_ -]*display)", re.I)),
)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class LayoutKey:
    """One discovered preference key.

    ``categories`` may be multiple (e.g. a key that is both landscape and
    position_left).  ``raw_value`` is the value as a string for safe logging.
    """

    file: str
    name: str
    tag: str            # "int" | "boolean" | "string" | "long" | "float" | ...
    raw_value: str
    categories: tuple[str, ...]
    writable_direct: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class PackageDiscovery:
    """All keys discovered for one package, grouped by category."""

    package: str
    files_scanned: list[str] = field(default_factory=list)
    keys: list[LayoutKey] = field(default_factory=list)
    used_root: bool = False
    error: str | None = None

    def by_category(self, category: str) -> list[LayoutKey]:
        return [k for k in self.keys if category in k.categories]

    def has_category(self, category: str) -> bool:
        return any(category in k.categories for k in self.keys)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for k in self.keys:
            for c in k.categories:
                counts[c] = counts.get(c, 0) + 1
        return counts


# ── Helpers ──────────────────────────────────────────────────────────────────

def _classify(key: str) -> tuple[str, ...]:
    """Return categories that match ``key`` (may be empty)."""
    out: list[str] = []
    for cat, pat in _CATEGORY_PATTERNS:
        if pat.search(key):
            out.append(cat)
    return tuple(out)


def _list_pref_files_direct(package: str) -> list[Path]:
    """List shared_prefs XML files via direct filesystem access."""
    root = Path("/data/data") / package / "shared_prefs"
    try:
        if not root.is_dir():
            return []
        files = []
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".xml":
                files.append(entry)
        return sorted(files)[:_MAX_PREF_FILES_PER_PACKAGE]
    except OSError:
        return []


def _list_pref_files_root(package: str, root_tool: str) -> list[str]:
    """List shared_prefs XML files via root ``ls``.  Returns absolute path strings."""
    path = f"/data/data/{package}/shared_prefs"
    try:
        res = android.run_root_command(
            ["sh", "-c", f"ls -1 '{path}'/*.xml 2>/dev/null"],
            root_tool=root_tool,
            timeout=5,
        )
        if not res.ok:
            return []
        lines = [ln.strip() for ln in (res.stdout or "").splitlines()]
        return [ln for ln in lines if ln.endswith(".xml")][:_MAX_PREF_FILES_PER_PACKAGE]
    except Exception:  # noqa: BLE001
        return []


def _read_pref_file_direct(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_pref_file_root(path_str: str, root_tool: str) -> str | None:
    try:
        res = android.run_root_command(
            ["sh", "-c", f"cat '{path_str}' 2>/dev/null"],
            root_tool=root_tool,
            timeout=5,
        )
        if not res.ok:
            return None
        return res.stdout
    except Exception:  # noqa: BLE001
        return None


def _parse_xml_keys(
    xml_text: str, file_label: str, writable_direct: bool
) -> list[LayoutKey]:
    """Parse one XML; return categorized keys.  Never raises."""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    out: list[LayoutKey] = []
    count = 0
    for child in root_el:
        if count >= _MAX_KEYS_PER_FILE:
            break
        name = child.attrib.get("name") or ""
        if not name:
            continue
        cats = _classify(name)
        if not cats:
            continue
        tag = child.tag
        if "value" in child.attrib:
            value = child.attrib.get("value") or ""
        else:
            value = (child.text or "").strip()
        out.append(
            LayoutKey(
                file=file_label,
                name=name,
                tag=tag,
                raw_value=value[:200],
                categories=cats,
                writable_direct=writable_direct,
            )
        )
        count += 1
    return out


# ── Public API ───────────────────────────────────────────────────────────────

def discover_for_package(
    package: str,
    *,
    root_tool: str | None = None,
) -> PackageDiscovery:
    """Scan all shared_prefs XML files for ``package`` and categorize keys.

    Tries direct read first; falls back to root cat when direct read fails or
    yields nothing (private data dirs are typically not group-readable).

    Returns a populated :class:`PackageDiscovery`.  Never raises.
    """
    disc = PackageDiscovery(package=package)
    try:
        direct_files = _list_pref_files_direct(package)
    except Exception as exc:  # noqa: BLE001
        direct_files = []
        disc.error = f"list_direct: {exc}"

    seen_files: set[str] = set()

    for path in direct_files:
        label = str(path)
        text = _read_pref_file_direct(path)
        if text is None:
            continue
        seen_files.add(label)
        disc.files_scanned.append(label)
        disc.keys.extend(_parse_xml_keys(text, label, writable_direct=True))

    if root_tool:
        try:
            root_files = _list_pref_files_root(package, root_tool)
        except Exception as exc:  # noqa: BLE001
            root_files = []
            disc.error = (disc.error or "") + f"; list_root: {exc}"
        for path_str in root_files:
            if path_str in seen_files:
                # Already read directly; no need to re-read via root, but mark
                # writable_direct=False on root-only files only.
                continue
            text = _read_pref_file_root(path_str, root_tool)
            if text is None:
                continue
            seen_files.add(path_str)
            disc.used_root = True
            disc.files_scanned.append(path_str)
            disc.keys.extend(_parse_xml_keys(text, path_str, writable_direct=False))

    return disc


def discover_all(
    packages: Iterable[str],
    *,
    root_tool: str | None = None,
) -> dict[str, PackageDiscovery]:
    """Run discovery for each package; return ``{package: PackageDiscovery}``."""
    out: dict[str, PackageDiscovery] = {}
    for pkg in packages:
        try:
            out[pkg] = discover_for_package(pkg, root_tool=root_tool)
        except Exception as exc:  # noqa: BLE001
            disc = PackageDiscovery(package=pkg)
            disc.error = f"discover: {exc}"
            out[pkg] = disc
    return out


# ── Discovery log writer ─────────────────────────────────────────────────────

def write_discovery_log(
    discoveries: dict[str, PackageDiscovery],
    *,
    path: Path | None = None,
) -> Path:
    """Write a human-readable discovery log.  Returns the path.

    Never raises.  Best-effort: returns the intended path even if writing
    failed (so callers can surface the path to the user; the log might just be
    incomplete).
    """
    target = path or LAYOUT_DISCOVERY_LOG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        lines = [f"# Layout key discovery — {ts}", ""]
        for pkg, disc in discoveries.items():
            lines.append(f"== Package: {pkg} ==")
            lines.append(f"  used_root: {disc.used_root}")
            lines.append(f"  files_scanned: {len(disc.files_scanned)}")
            for f in disc.files_scanned:
                lines.append(f"    - {f}")
            if disc.error:
                lines.append(f"  error: {disc.error}")
            counts = disc.summary()
            lines.append(f"  category_counts: {counts}")
            if disc.keys:
                lines.append("  keys:")
                for k in disc.keys:
                    lines.append(
                        f"    - [{','.join(k.categories)}] "
                        f"name={k.name} tag={k.tag} value={k.raw_value!r} "
                        f"writable_direct={k.writable_direct} file={Path(k.file).name}"
                    )
            else:
                lines.append("  keys: (none matched any layout category)")
            lines.append("")
        target.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log.debug("write_discovery_log error: %s", exc)
    return target


# ── Cache (process-local) ────────────────────────────────────────────────────
#
# Discovery is expensive (dumpsys+cat for many files).  Cache the result for
# the lifetime of the supervisor process and refresh when explicitly asked.

_DISCOVERY_CACHE: dict[str, PackageDiscovery] = {}
_DISCOVERY_CACHE_TS: float = 0.0
_DISCOVERY_CACHE_TTL = 300.0  # 5 minutes


def get_cached_or_discover(
    packages: Iterable[str],
    *,
    root_tool: str | None = None,
    refresh: bool = False,
) -> dict[str, PackageDiscovery]:
    """Return cached discoveries or run a fresh discovery.

    Uses an in-memory cache.  Pass ``refresh=True`` to force re-discovery.
    """
    global _DISCOVERY_CACHE_TS
    now = time.time()
    pkg_list = list(packages)
    pkg_set = set(pkg_list)
    cached_keys = set(_DISCOVERY_CACHE.keys())
    have_all = pkg_set.issubset(cached_keys)
    fresh = (now - _DISCOVERY_CACHE_TS) < _DISCOVERY_CACHE_TTL
    if not refresh and have_all and fresh:
        return {p: _DISCOVERY_CACHE[p] for p in pkg_list}

    discoveries = discover_all(pkg_list, root_tool=root_tool)
    _DISCOVERY_CACHE.update(discoveries)
    _DISCOVERY_CACHE_TS = now
    return discoveries


def clear_cache() -> None:
    global _DISCOVERY_CACHE_TS
    _DISCOVERY_CACHE.clear()
    _DISCOVERY_CACHE_TS = 0.0


# ── Convenience: render to log + return path ─────────────────────────────────

def run_discovery_and_log(
    packages: Iterable[str],
    *,
    root_tool: str | None = None,
    refresh: bool = True,
    path: Path | None = None,
) -> tuple[Path, dict[str, PackageDiscovery]]:
    """Run discovery, write the log file, and return ``(path, discoveries)``.

    Never raises.  Always returns the path (even if writing failed).
    """
    try:
        discoveries = get_cached_or_discover(
            packages, root_tool=root_tool, refresh=refresh
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("run_discovery_and_log error: %s", exc)
        discoveries = {}
    out_path = write_discovery_log(discoveries, path=path)
    return out_path, discoveries
