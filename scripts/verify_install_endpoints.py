#!/usr/bin/env python3
"""Compare public install endpoints against local release manifest."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "rejoin_versions.json"
PROOF = ROOT / "data" / "rejoin_artifact_build_proof.json"

BASES = (
    "https://rejoin.deng.my.id",
    "https://tool.deng.my.id",
)

ENDPOINTS = (
    "/install/latest",
    "/install/v1.2.0",
    "/install/v1.0.0",
    "/install/test/latest",
)


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "deng-rejoin-verify/1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def parse_install_script(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, pattern in {
        "package_sha256": r'package_sha256\s*=\s*"([a-f0-9]{64})"',
        "version_label": r'version_label\s*=\s*"([^"]+)"',
        "channel": r'channel\s*=\s*"([^"]+)"',
        "installer_endpoint": r'installer_endpoint\s*=\s*"([^"]+)"',
    }.items():
        m = re.search(pattern, body)
        if m:
            out[key] = m.group(1)
    if "use Reset HWID" in body:
        out["forbidden_reset_hwid"] = "yes"
    return out


def main() -> int:
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_endpoint: dict[str, dict] = {}
    for row in rows:
        if row.get("kind") == "channel_pointers":
            continue
        ep = str(row.get("installer_endpoint") or "").strip()
        if ep:
            by_endpoint[ep] = row
    by_endpoint["/install/latest"] = next(
        (r for r in rows if str(r.get("version") or "") == "v1.2.0"),
        {},
    )

    proof = {}
    if PROOF.is_file():
        proof = json.loads(PROOF.read_text(encoding="utf-8"))

    report = []
    ok = True
    for base in BASES:
        for ep in ENDPOINTS:
            url = base + ep
            status, body = fetch(url)
            parsed = parse_install_script(body) if status == 200 else {}
            expected = by_endpoint.get(ep if ep != "/install/latest" else "/install/v1.2.0", {})
            expected_sha = str(expected.get("artifact_sha256") or "").lower()
            live_sha = str(parsed.get("package_sha256") or "").lower()
            match = (not expected_sha) or (live_sha == expected_sha)
            if status != 200 or not match or parsed.get("forbidden_reset_hwid"):
                ok = False
            report.append(
                {
                    "url": url,
                    "http_status": status,
                    "live_sha256": live_sha or None,
                    "expected_sha256": expected_sha or None,
                    "sha_match": match,
                    "version": parsed.get("version_label"),
                    "channel": parsed.get("channel"),
                    "license_gate": "key-free" if ep.endswith("test/latest") else "license-gated",
                    "forbidden_reset_hwid": parsed.get("forbidden_reset_hwid"),
                }
            )
            print(json.dumps(report[-1], indent=2))

    out_path = ROOT / "data" / "rejoin_install_endpoint_proof.json"
    out_path.write_text(json.dumps({"endpoints": report, "artifacts": proof.get("artifacts", [])}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
