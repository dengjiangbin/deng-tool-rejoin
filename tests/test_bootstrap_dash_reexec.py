"""Bootstrap installer must run cleanly under POSIX sh (dash on Termux).

Real user errors that motivated this test file:

  2026-05-18 (first regression):
    $ curl -fsSL https://rejoin.deng.my.id/install/latest | sh
    /data/data/com.termux/files/usr/bin/sh: 2: set: Illegal option -o pipefail

  2026-05-18 (second regression, after a clever-but-broken re-exec attempt):
    $ curl -fsSL https://rejoin.deng.my.id/install/latest | sh
    /data/data/com.termux/files/usr/bin/sh: /data/data/com.termux/files/usr/bin/sh: cannot execute binary file

Termux's ``/data/data/com.termux/files/usr/bin/sh`` is **dash**.  When
the user runs ``curl ... | sh``, the ``#!`` shebang on line 1 is
ignored — dash is already executing the bytes.  Anything bash-only in
the rendered installer (``set -o pipefail``, ``[[ ]]``, ``shopt``,
arrays, ``${VAR:0:N}`` substring) makes dash abort before our code
ever does anything useful.

We previously tried to fix this by emitting a POSIX preamble that
re-execs into bash.  It blew up because dash's ``$0`` on the piped
invocation is the **dash binary itself**, so ``exec bash "$0"`` tried
to run an ELF as a shell script.

The robust answer is: emit pure POSIX-sh.  These tests enforce that.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from agent.bootstrap_installer import (
    render_direct_install_bootstrap,
    render_public_bootstrap,
)


# Bash-only tokens that MUST NOT appear in any executable installer line.
# Comments are allowed to reference them for context (we strip those).
_BASH_ONLY_FORBIDDEN: tuple[str, ...] = (
    "set -o pipefail",
    "set -euo pipefail",
    "shopt",
    "[[",
    "]]",
    "BASH_REMATCH",
    "BASH_VERSION",
)

# Bash-only parameter-expansion forms.  ``${VAR:0:N}`` substring slicing
# is bash-only and was the source of the install verification crash in
# direct-install bootstrap.
_BASH_SUBSTRING_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:[0-9]+:[0-9]+\}")


def _strip_comments_and_heredocs(script: str) -> str:
    """Return ``script`` with comment lines and ``DENG_REJOIN_WRAPPER``
    heredoc bodies removed.

    The wrapper body is a separate ``sh`` script written verbatim to
    disk; the outer installer treats it as opaque text.  Forbidden
    tokens there are not parsed by the outer dash and would only
    create false positives.
    """
    out: list[str] = []
    in_wrapper = False
    for line in script.splitlines():
        s = line.lstrip()
        if in_wrapper:
            if s == "DENG_REJOIN_WRAPPER":
                in_wrapper = False
            continue
        # Heredoc OPEN line: anything that ends with ``<< 'DENG_REJOIN_WRAPPER'``.
        if s.endswith("<< 'DENG_REJOIN_WRAPPER'"):
            in_wrapper = True
            continue
        # Inline ``echo "...# comment..."`` are fine; only drop lines
        # that are pure comments.
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class PosixShellDisciplineTest(unittest.TestCase):
    """Static checks: the rendered installers contain no bash-only syntax."""

    def _render_both(self) -> dict[str, str]:
        return {
            "public": render_public_bootstrap(
                base_url="https://example.test",
                requested="latest",
            ),
            "direct": render_direct_install_bootstrap(
                base_url="https://example.test",
                package_sha256="0" * 64,
            ),
        }

    def test_shebang_is_posix_sh(self) -> None:
        """Both installers must declare ``#!/usr/bin/env sh``.

        Even though the shebang is ignored under ``curl | sh``, a
        ``bash`` shebang is a misleading signal to maintainers and
        external auditors that the script can rely on bash features.
        """
        for name, script in self._render_both().items():
            self.assertTrue(
                script.startswith("#!/usr/bin/env sh\n"),
                f"{name} installer should start with `#!/usr/bin/env sh`, "
                f"got: {script.splitlines()[0]!r}",
            )

    def test_no_bash_only_tokens_in_executable_lines(self) -> None:
        for name, script in self._render_both().items():
            executable = _strip_comments_and_heredocs(script)
            for tok in _BASH_ONLY_FORBIDDEN:
                self.assertNotIn(
                    tok, executable,
                    f"{name} installer contains bash-only token "
                    f"{tok!r} in executable code (Termux dash will choke)",
                )

    def test_no_bash_substring_expansion(self) -> None:
        """``${VAR:0:N}`` is bash-only.  Real-device regression: the
        direct installer used ``${EXPECTED_SHA256:0:12}`` to compute
        the short SHA, which makes dash abort with a parse error
        before the install ever finishes."""
        for name, script in self._render_both().items():
            executable = _strip_comments_and_heredocs(script)
            m = _BASH_SUBSTRING_RE.search(executable)
            self.assertIsNone(
                m,
                f"{name} installer uses bash-only substring expansion "
                f"{m.group(0) if m else ''!r} — use `printf '%.Ns'` instead",
            )


@unittest.skipUnless(
    shutil.which("dash"),
    "no `dash` on PATH — skipping live POSIX shell test",
)
class LiveDashParseTest(unittest.TestCase):
    """Run the rendered installers through a real ``dash`` parser.

    This catches future regressions that the static token checks above
    can't — e.g. someone adding ``$(< file)`` or ``coproc`` or a
    ``function name() { }`` definition.
    """

    def _dash(self) -> str:
        return shutil.which("dash")

    def _parse(self, script: str) -> subprocess.CompletedProcess[bytes]:
        """Use ``dash -n`` to parse without executing.  Exit 0 means
        the script is at least syntactically valid POSIX-sh; anything
        else means dash hated something.

        NOTE: we send raw bytes (``text=False``) because Python's
        Windows text mode converts ``\\n`` → ``\\r\\n`` on stdin, which
        would corrupt every dash command line.  Termux uses LF-only,
        so this matches the user's invocation environment.
        """
        return subprocess.run(
            [self._dash(), "-n"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_public_installer_parses_under_dash(self) -> None:
        script = render_public_bootstrap(
            base_url="https://example.test",
            requested="latest",
        )
        result = self._parse(script)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(
            result.returncode, 0,
            f"public installer failed to parse under dash.\n"
            f"stderr: {stderr!r}",
        )
        self.assertNotIn("Illegal option", stderr)
        self.assertNotIn("Syntax error", stderr)

    def test_direct_installer_parses_under_dash(self) -> None:
        script = render_direct_install_bootstrap(
            base_url="https://example.test",
            package_sha256="0" * 64,
        )
        result = self._parse(script)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(
            result.returncode, 0,
            f"direct installer failed to parse under dash.\n"
            f"stderr: {stderr!r}",
        )
        self.assertNotIn("Illegal option", stderr)
        self.assertNotIn("Syntax error", stderr)

    def test_public_installer_reaches_curl_when_piped(self) -> None:
        """Pipe the script (real ``curl ... | sh`` pattern) and confirm
        dash gets PAST every bash-incompat-prone line.

        We assert two things on the captured output:

        * The installer's first banner ("DENG Tool: Rejoin Installer")
          is on stdout — that proves dash parsed and ran the very first
          line of the script body.
        * The installer's *own* error message
          ("Could not download launcher bundle.") is on stderr — that
          proves dash parsed the bash-incompat-prone region (``case``
          statement, ``[ ... ]``, glob loop, heredoc) AND ran the
          curl-error branch.

        Whatever ``curl`` is on PATH doesn't matter — it will fail
        against ``https://example.test`` and our error handler runs.
        That's exactly the signal we want.
        """
        script = render_public_bootstrap(
            base_url="https://example.test",
            requested="latest",
        )

        result = subprocess.run(
            [self._dash()],
            input=script.encode("utf-8"),  # LF only — see _parse() note
            capture_output=True,
            timeout=30,
            check=False,
        )

        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        combined = stdout + stderr

        # Banner came from the very first installer line.
        self.assertIn(
            "DENG Tool: Rejoin Installer", combined,
            f"dash did not execute the first echo — possible shebang "
            f"or parse failure.\nstdout={stdout!r}\nstderr={stderr!r}",
        )
        # Reaching this error message proves dash got through the
        # ``case``, exports, ``if [ ... ]``, mkdir, ``mktemp``, and
        # the curl-error branch.
        self.assertIn(
            "Could not download launcher bundle.", combined,
            f"dash did not reach the curl-error branch — bash-only "
            f"syntax may have caused an early parse failure.\n"
            f"stdout={stdout!r}\nstderr={stderr!r}",
        )
        # And none of the historical regressions.
        self.assertNotIn("Illegal option", combined)
        self.assertNotIn("cannot execute binary file", combined)
        self.assertNotIn("Syntax error", combined)


if __name__ == "__main__":
    unittest.main()
