"""Tests for GitHub tag + manifest version list and install command builders."""

from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path

from agent import rejoin_versions as rv


def _write_manifest(path: Path, data: list) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class RejoinVersionMergeTests(unittest.TestCase):
    def test_stable_before_beta_in_sort(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_sort_manifest.json"
        _write_manifest(
            manifest,
            [
                {"version": "v2.0.0", "channel": "beta", "visible": True, "install_ref": "refs/tags/v2.0.0"},
                {"version": "v1.0.0", "channel": "stable", "visible": True, "install_ref": "refs/tags/v1.0.0"},
            ],
        )
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=True)
            self.assertEqual(out[0].channel, "stable")
            self.assertEqual(out[-1].channel, "beta")
        finally:
            manifest.unlink(missing_ok=True)

    def test_recommended_stable_before_non_recommended_stable(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_rec_manifest.json"
        _write_manifest(
            manifest,
            [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "recommended": True,
                    "label": "v1.0.0 Stable",
                    "install_ref": "refs/tags/v1.0.0",
                    "visible": True,
                },
                {
                    "version": "v1.0.1",
                    "channel": "stable",
                    "recommended": False,
                    "label": "v1.0.1 Stable",
                    "install_ref": "refs/tags/v1.0.1",
                    "visible": True,
                },
            ],
        )
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=False)
            self.assertEqual(out[0].version, "v1.0.0")
            self.assertTrue(out[0].recommended)
        finally:
            manifest.unlink(missing_ok=True)

    def test_beta_hidden_for_public_by_default(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_beta_manifest.json"
        _write_manifest(
            manifest,
            [
                {"version": "v1.0.0", "channel": "stable", "visible": True, "install_ref": "refs/tags/v1.0.0"},
                {"version": "v2.0.0", "channel": "beta", "visible": True, "install_ref": "refs/tags/v2.0.0"},
            ],
        )
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=False)
            self.assertEqual([v.version for v in out], ["v1.0.0"])
        finally:
            manifest.unlink(missing_ok=True)

    def test_beta_shown_when_public_beta_env(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_beta2_manifest.json"
        _write_manifest(
            manifest,
            [
                {"version": "v1.0.0", "channel": "stable", "visible": True, "install_ref": "refs/tags/v1.0.0"},
                {"version": "v2.0.0", "channel": "beta", "visible": True, "install_ref": "refs/tags/v2.0.0"},
            ],
        )
        try:
            with unittest.mock.patch.dict(
                "os.environ",
                {"REJOIN_VERSIONS_MANIFEST": str(manifest), "REJOIN_PUBLIC_BETA": "1"},
            ):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=False)
            self.assertGreaterEqual(len(out), 2)
            self.assertTrue(any(v.channel == "beta" for v in out))
        finally:
            manifest.unlink(missing_ok=True)

    def test_main_ref_dropped(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_main_manifest.json"
        _write_manifest(
            manifest,
            [
                {
                    "version": "mainline",
                    "channel": "dev",
                    "install_ref": "refs/heads/main",
                    "visible": True,
                }
            ],
        )
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=True)
            self.assertEqual(out, [])
        finally:
            manifest.unlink(missing_ok=True)

    def test_empty_tags_and_empty_manifest(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_empty_manifest.json"
        _write_manifest(manifest, [])
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}):
                out = rv.merge_version_sources(tag_names=[], include_dev_for_admin=False)
            self.assertEqual(out, [])
        finally:
            manifest.unlink(missing_ok=True)

    def test_manifest_source_used_when_github_returns_empty(self) -> None:
        manifest = Path(__file__).resolve().parent / "_tmp_git_empty_manifest.json"
        _write_manifest(
            manifest,
            [
                {
                    "version": "v1.0.0",
                    "channel": "stable",
                    "visible": True,
                    "install_ref": "refs/tags/v1.0.0",
                    "recommended": True,
                }
            ],
        )
        try:
            with unittest.mock.patch.dict("os.environ", {"REJOIN_VERSIONS_MANIFEST": str(manifest)}), unittest.mock.patch.object(
                rv, "fetch_github_tag_names", return_value=[]
            ):
                out = rv.list_public_rejoin_versions(include_dev_for_admin=False)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0].version, "v1.0.0")
        finally:
            manifest.unlink(missing_ok=True)


class InstallCommandTests(unittest.TestCase):
    def test_v100_uses_refs_tags_in_url_and_env(self) -> None:
        info = rv.RejoinVersionInfo(
            version="v1.0.0",
            channel="stable",
            label="v1.0.0 Stable",
            install_ref="refs/tags/v1.0.0",
        )
        cmd = rv.build_full_install_command("dengjiangbin", "deng-tool-rejoin", info.install_ref)
        self.assertIn("refs/tags/v1.0.0", cmd)
        self.assertIn("raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/refs/tags/v1.0.0/install.sh", cmd)
        self.assertNotIn("/main/", cmd)
        self.assertIn("DENG_REJOIN_INSTALL_REF=refs/tags/v1.0.0", cmd)

    def test_format_instructions_has_desktop_and_mobile(self) -> None:
        info = rv.RejoinVersionInfo(
            version="v1.0.0",
            channel="stable",
            label="v1.0.0 Stable",
            install_ref="refs/tags/v1.0.0",
        )
        with unittest.mock.patch.object(rv, "github_owner", return_value="o"), unittest.mock.patch.object(
            rv, "github_repo", return_value="r"
        ):
            text = rv.format_install_instructions_plain(info)
        self.assertIn("Desktop Copy:", text)
        self.assertIn("Mobile Copy:", text)
        self.assertIn("After install:", text)
        self.assertIn("deng-rejoin", text)


class InstallShRefTests(unittest.TestCase):
    def test_install_sh_documents_env_ref(self) -> None:
        raw = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")
        self.assertIn("DENG_REJOIN_INSTALL_REF", raw)
        self.assertIn("INSTALL_REF", raw)


class PanelAndDocsConsistencyTests(unittest.TestCase):
    def test_panel_embed_title(self) -> None:
        from agent.license_panel import BUTTON_SELECT_VERSION, build_panel_buttons, build_panel_embed

        self.assertEqual(build_panel_embed()["title"], "DENG Tool: Rejoin Panel")
        ids = [b["custom_id"] for b in build_panel_buttons()[0]["components"]]
        self.assertIn("license_panel:generate", ids)
        self.assertIn(BUTTON_SELECT_VERSION, ids)

    def test_public_install_doc_avoids_update_command(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "PUBLIC_INSTALL.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("deng-rejoin-update", text)

    def test_public_beginner_docs_use_select_version_not_long_guides(self) -> None:
        root = Path(__file__).resolve().parents[1]
        nu = (root / "docs" / "NEW_USER_TERMUX_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("Rejoin Panel", nu)
        self.assertIn("Select Version", nu)
        self.assertNotIn("deng-rejoin-update", nu.lower())
        lowered = nu.lower()
        for fragment in (
            "## useful commands",
            "## troubleshooting",
            "## start table",
            "## package auto",
            "## username",
            "## private server",
        ):
            self.assertNotIn(fragment, lowered, msg=fragment)


if __name__ == "__main__":
    unittest.main()
