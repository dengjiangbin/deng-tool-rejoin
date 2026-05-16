"""resolve_install_api and first-run API defaults (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.deferred_bundle_install import (  # noqa: E402
    DEFAULT_PUBLIC_INSTALL_API,
    resolve_install_api,
)


class ResolveInstallApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.mkdtemp()
        self._home = Path(self._td) / "fake_home"
        self._app = self._home / ".deng-tool" / "rejoin"
        self._app.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._td, ignore_errors=True)

    def test_env_wins(self) -> None:
        old = os.environ.get("DENG_REJOIN_INSTALL_API")
        try:
            os.environ["DENG_REJOIN_INSTALL_API"] = "https://custom.example/api"
            u = resolve_install_api(self._app)
        finally:
            if old is None:
                os.environ.pop("DENG_REJOIN_INSTALL_API", None)
            else:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, "https://custom.example/api")

    def test_file_used_when_env_missing(self) -> None:
        (self._app / ".install_api").write_text(
            "https://rejoin.deng.my.id\n", encoding="utf-8"
        )
        old = os.environ.pop("DENG_REJOIN_INSTALL_API", None)
        try:
            u = resolve_install_api(self._app)
        finally:
            if old is not None:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, "https://rejoin.deng.my.id")

    def test_default_public_url(self) -> None:
        old = os.environ.pop("DENG_REJOIN_INSTALL_API", None)
        try:
            u = resolve_install_api(self._app)
        finally:
            if old is not None:
                os.environ["DENG_REJOIN_INSTALL_API"] = old
        self.assertEqual(u, DEFAULT_PUBLIC_INSTALL_API.rstrip("/"))


if __name__ == "__main__":
    unittest.main()
