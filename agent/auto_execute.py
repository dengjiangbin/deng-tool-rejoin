"""Filesystem-only Auto Execute management for executor auto-run folders.

This module never executes Lua/script content.  It only writes/removes
DENG-managed files under known executor autoexecute directories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ANDROID_STORAGE_ROOT = Path("/storage/emulated/0")
MANAGED_PREFIX = "deng_autoexec_"
MANAGED_SUFFIX = ".lua"
MANAGED_RE = re.compile(r"^deng_autoexec_(\d{3})\.lua$")


@dataclass(frozen=True)
class ExecutorSpec:
    key: str
    label: str
    autoexecute_subpath: str

    def autoexecute_dir(self, package: str, *, storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT) -> Path:
        return storage_root / "Android" / "data" / package / self.autoexecute_subpath


EXECUTORS: dict[str, ExecutorSpec] = {
    "delta": ExecutorSpec(
        key="delta",
        label="Delta",
        autoexecute_subpath="files/gloop/external/Autoexecute",
    ),
}


def executor_choices() -> list[ExecutorSpec]:
    return list(EXECUTORS.values())


def get_executor(key: str) -> ExecutorSpec:
    try:
        return EXECUTORS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported Auto Execute executor: {key}") from exc


def delta_autoexecute_dir(package: str, *, storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT) -> Path:
    return EXECUTORS["delta"].autoexecute_dir(package, storage_root=storage_root)


def configured_package_names(config_data: dict[str, Any]) -> list[str]:
    packages: list[str] = []
    for entry in config_data.get("roblox_packages") or []:
        if isinstance(entry, dict):
            if entry.get("enabled") is False:
                continue
            package = str(entry.get("package") or "").strip()
        else:
            package = str(entry or "").strip()
        if package and package not in packages:
            packages.append(package)
    if not packages:
        package = str(config_data.get("roblox_package") or "").strip()
        if package:
            packages.append(package)
    return packages


def managed_filename(index: int) -> str:
    return f"{MANAGED_PREFIX}{max(1, int(index)):03d}{MANAGED_SUFFIX}"


def _managed_index(path_or_name: Path | str) -> int | None:
    name = path_or_name.name if isinstance(path_or_name, Path) else str(path_or_name)
    match = MANAGED_RE.match(name)
    if not match:
        return None
    return int(match.group(1))


def is_managed_filename(path_or_name: Path | str) -> bool:
    return _managed_index(path_or_name) is not None


def _existing_managed_files(directory: Path) -> list[Path]:
    try:
        if not directory.is_dir():
            return []
        return sorted(
            [p for p in directory.iterdir() if p.is_file() and is_managed_filename(p.name)],
            key=lambda p: p.name,
        )
    except OSError:
        return []


def list_managed_filenames(
    packages: Iterable[str],
    *,
    executor: str = "delta",
    storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT,
) -> list[str]:
    spec = get_executor(executor)
    names: set[str] = set()
    for package in packages:
        for path in _existing_managed_files(spec.autoexecute_dir(package, storage_root=storage_root)):
            names.add(path.name)
    return sorted(names)


def next_managed_filename(
    packages: Iterable[str],
    *,
    executor: str = "delta",
    storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT,
) -> str:
    indexes: list[int] = []
    spec = get_executor(executor)
    for package in packages:
        directory = spec.autoexecute_dir(package, storage_root=storage_root)
        for path in _existing_managed_files(directory):
            idx = _managed_index(path)
            if idx is not None:
                indexes.append(idx)
    return managed_filename((max(indexes) if indexes else 0) + 1)


def _script_bytes(script: str) -> bytes:
    text = str(script)
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def write_script_to_packages(
    packages: Iterable[str],
    script: str,
    *,
    executor: str = "delta",
    storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    """Write one DENG-managed script file to every package.

    Results intentionally exclude script content.
    """
    package_list = list(packages)
    spec = get_executor(executor)
    target_name = filename or next_managed_filename(package_list, executor=executor, storage_root=storage_root)
    if not is_managed_filename(target_name):
        raise ValueError("Auto Execute filename must be DENG-managed")
    raw = _script_bytes(script)
    results: list[dict[str, Any]] = []
    for package in package_list:
        directory = spec.autoexecute_dir(package, storage_root=storage_root)
        target = directory / target_name
        row: dict[str, Any] = {
            "package": package,
            "path": str(target),
            "filename": target_name,
            "byte_count": len(raw),
            "success": False,
            "error": "",
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if target.exists():
                row["error"] = "file exists; not overwritten"
            else:
                target.write_bytes(raw)
                row["success"] = True
        except PermissionError as exc:
            row["error"] = f"permission denied: {exc}"
        except OSError as exc:
            row["error"] = f"directory create/write failed: {exc}"
        results.append(row)
    return results


def remove_script_from_packages(
    packages: Iterable[str],
    filename: str,
    *,
    executor: str = "delta",
    storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT,
) -> list[dict[str, Any]]:
    if not is_managed_filename(filename):
        raise ValueError("Refusing to remove non-DENG Auto Execute file")
    spec = get_executor(executor)
    results: list[dict[str, Any]] = []
    for package in packages:
        target = spec.autoexecute_dir(package, storage_root=storage_root) / filename
        row: dict[str, Any] = {
            "package": package,
            "path": str(target),
            "filename": filename,
            "deleted": False,
            "success": False,
            "error": "",
        }
        try:
            if target.exists() and target.is_file():
                target.unlink()
                row["deleted"] = True
            row["success"] = True
        except PermissionError as exc:
            row["error"] = f"permission denied: {exc}"
        except OSError as exc:
            row["error"] = f"delete failed: {exc}"
        results.append(row)
    return results


def remove_all_scripts_from_packages(
    packages: Iterable[str],
    *,
    executor: str = "delta",
    storage_root: Path = DEFAULT_ANDROID_STORAGE_ROOT,
) -> list[dict[str, Any]]:
    spec = get_executor(executor)
    results: list[dict[str, Any]] = []
    for package in packages:
        directory = spec.autoexecute_dir(package, storage_root=storage_root)
        row: dict[str, Any] = {
            "package": package,
            "path": str(directory),
            "deleted_count": 0,
            "success": False,
            "error": "",
        }
        try:
            for path in _existing_managed_files(directory):
                path.unlink()
                row["deleted_count"] += 1
            row["success"] = True
        except PermissionError as exc:
            row["error"] = f"permission denied: {exc}"
        except OSError as exc:
            row["error"] = f"delete failed: {exc}"
        results.append(row)
    return results


def summarize_results(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    rows = list(results)
    return {
        "total": len(rows),
        "success": sum(1 for row in rows if row.get("success")),
        "failure": sum(1 for row in rows if not row.get("success")),
    }
