"""Bootstrap installer must NOT crash when invoked via dash.

Real user error (2026-05-18, Termux):

    $ curl -fsSL https://rejoin.deng.my.id/install/latest | sh
    /data/data/com.termux/files/usr/bin/sh: 2: set: Illegal option -o pipefail

Termux's ``/data/data/com.termux/files/usr/bin/sh`` is **dash**, which
doesn't understand ``set -o pipefail`` or ``[[ ]]``.  When the script
is piped to ``sh``, the ``#!/usr/bin/env bash`` shebang on line 1 is
ignored — dash is already executing the bytes.

The fix is a POSIX-sh preamble (``_BASH_REEXEC_PREAMBLE``) that runs
before any bash-only construct.  It re-execs into bash when needed
and bails with a clear message when bash is not installed.

These tests run the rendered scripts under a real ``/bin/dash`` (or
``/bin/sh`` falling back to whatever POSIX shell is available on the
test host).  On Windows CI we exercise the same logic by running
``sh.exe`` from MSYS / Git-for-Windows when present, else we statically
inspect the preamble structure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import unittest

from agent.bootstrap_installer import (
    _BASH_REEXEC_PREAMBLE,
    render_direct_install_bootstrap,
    render_public_bootstrap,
)


class PreambleStructureTest(unittest.TestCase):
    """Static checks on the preamble — no shell invocation needed."""

    def _strip_comments(self, script: str) -> str:
        """Return ``script`` with leading ``#`` comment lines removed.

        We intentionally allow bash-only TOKENS inside comments (the
        preamble's own docstring mentions ``set -o pipefail`` to explain
        why the re-exec exists) and only verify that no EXECUTABLE
        line uses them.
        """
        kept: list[str] = []
        for raw in script.splitlines():
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                continue
            kept.append(raw)
        return "\n".join(kept)

    def test_preamble_uses_no_bash_only_features(self) -> None:
        """The preamble itself MUST be valid in POSIX sh — that's the
        whole point.  Reject any of the bash-only tokens that bit us
        in real executable code (comments may mention them)."""
        executable = self._strip_comments(_BASH_REEXEC_PREAMBLE)
        forbidden = [
            "set -o pipefail",
            "set -euo pipefail",
            "[[",
            "]]",
            "BASH_REMATCH",
        ]
        for tok in forbidden:
            self.assertNotIn(
                tok, executable,
                f"preamble must be POSIX-sh only in executable code — "
                f"found bash-only token {tok!r}",
            )

    def test_preamble_detects_bash_via_BASH_VERSION(self) -> None:
        self.assertIn('BASH_VERSION', _BASH_REEXEC_PREAMBLE)
        self.assertIn('exec bash', _BASH_REEXEC_PREAMBLE)

    def test_preamble_prints_install_hint_on_missing_bash(self) -> None:
        self.assertIn("pkg install bash", _BASH_REEXEC_PREAMBLE)
        # And exits non-zero.
        self.assertIn("exit 1", _BASH_REEXEC_PREAMBLE)


class RenderedScriptHasPreambleTest(unittest.TestCase):

    def test_public_bootstrap_includes_preamble_before_pipefail(self) -> None:
        out = render_public_bootstrap(
            base_url="https://example.test",
            requested="latest",
        )
        # The preamble must precede `set -euo pipefail` so dash never
        # reaches that line.
        idx_preamble = out.find("BASH_VERSION")
        idx_pipefail = out.find("set -euo pipefail")
        self.assertGreater(idx_preamble, 0,
                            "rendered script must include the bash re-exec "
                            "preamble")
        self.assertGreater(idx_pipefail, idx_preamble,
                           "preamble must come BEFORE `set -euo pipefail`, "
                           "otherwise dash chokes on line 2")

    def test_direct_install_bootstrap_includes_preamble(self) -> None:
        out = render_direct_install_bootstrap(
            base_url="https://example.test",
            package_sha256="0" * 64,
        )
        idx_preamble = out.find("BASH_VERSION")
        idx_pipefail = out.find("set -euo pipefail")
        self.assertGreater(idx_preamble, 0)
        self.assertGreater(idx_pipefail, idx_preamble)


@unittest.skipUnless(
    shutil.which("dash") or shutil.which("sh"),
    "no POSIX shell available — skipping live preamble test",
)
class LivePosixShellTest(unittest.TestCase):
    """Run a synthetic script through dash to prove the preamble works.

    We can't run the full installer in a sandbox (it touches
    ``$PREFIX``, downloads bundles, etc.), but we CAN prove that the
    preamble itself doesn't trip the dash parser and successfully
    re-execs into bash when bash is available.
    """

    def _shell(self) -> str:
        # Prefer dash explicitly — that's what Termux ships.
        return shutil.which("dash") or shutil.which("sh")

    def test_preamble_does_not_error_under_posix_sh(self) -> None:
        """Run JUST the preamble under dash with bash NOT on PATH.

        Expected: clean exit-1 with the install-bash hint, NOT a
        parse error on a bash-only token.
        """
        if not shutil.which("bash"):
            self.skipTest("bash not available on host; can't simulate missing-bash branch reliably")

        # Build a small script: preamble + a marker that should NEVER
        # execute when bash is missing (because preamble must exit
        # before reaching it).
        script = (
            _BASH_REEXEC_PREAMBLE
            + 'echo MARKER_SHOULD_NOT_PRINT\n'
        )

        env = dict(os.environ)
        # Strip bash from PATH so the "no bash available" branch fires.
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and "bash" not in p.lower()
            and not (os.path.exists(os.path.join(p, "bash"))
                     or os.path.exists(os.path.join(p, "bash.exe")))
        )

        result = subprocess.run(
            [self._shell()],
            input=script,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        # If bash genuinely isn't found, we should exit with code 1 and
        # the install-bash hint should appear on stderr.  Either way,
        # we must NOT see the "Illegal option -o pipefail" parse error
        # which is the user-reported regression.
        self.assertNotIn(
            "Illegal option", (result.stdout + result.stderr),
            f"preamble must not trip dash's parser: stderr={result.stderr!r}",
        )
        self.assertNotIn("MARKER_SHOULD_NOT_PRINT", result.stdout)

    def test_preamble_reexecs_into_bash_when_available(self) -> None:
        """When bash IS on PATH, the preamble should re-exec into bash
        and let the rest of the script run."""
        if not shutil.which("bash"):
            self.skipTest("bash not available on host")

        script = (
            _BASH_REEXEC_PREAMBLE
            + 'set -euo pipefail\n'                # bash-only line
            + 'echo "running under bash: ${BASH_VERSION:-NONE}"\n'
            + 'echo MARKER_REACHED_BASH\n'
        )

        result = subprocess.run(
            [self._shell()],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(
            result.returncode, 0,
            f"preamble + bash body should succeed.  rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}",
        )
        self.assertIn("MARKER_REACHED_BASH", result.stdout)
        self.assertNotIn("Illegal option", result.stderr)


if __name__ == "__main__":
    unittest.main()
