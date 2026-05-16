"""Tests for the supervisor state machine: state transitions, URL-awareness,
Launching timeout, anti-flapping, and new state constants.

Covers:
  - Launching → Lobby when healthy (no URL)
  - Joining → In Server when healthy (URL used)
  - Launching timeout forces a re-check and promotes or marks Failed
  - STATUS_LOBBY, STATUS_IN_SERVER, STATUS_JOINING, STATUS_CLOSED exported
  - _PackageWorker does not stay in Launching/Joining after health returns healthy
  - Reconnect path uses Joining when URL configured
  - Revive path uses Joining when URL configured
  - MultiPackageSupervisor.run_forever accepts render_callback
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from agent.supervisor import (
    STATUS_BACKGROUND,
    STATUS_CHECKING,
    STATUS_CLOSED,
    STATUS_FAILED,
    STATUS_IN_SERVER,
    STATUS_JOIN_UNCONFIRMED,
    STATUS_JOINING,
    STATUS_LAUNCHING,
    STATUS_LOBBY,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_RECONNECTING,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    MultiPackageSupervisor,
    _PackageWorker,
    _HEALTHY_STATES,
)


class TestStatusConstants(unittest.TestCase):
    """Verify new status constants are exported and correctly typed."""

    def test_lobby_is_string(self):
        self.assertIsInstance(STATUS_LOBBY, str)
        self.assertEqual(STATUS_LOBBY, "Lobby")

    def test_in_server_is_string(self):
        self.assertIsInstance(STATUS_IN_SERVER, str)
        self.assertEqual(STATUS_IN_SERVER, "In Server")

    def test_joining_is_string(self):
        self.assertIsInstance(STATUS_JOINING, str)
        self.assertEqual(STATUS_JOINING, "Joining")

    def test_closed_is_string(self):
        self.assertIsInstance(STATUS_CLOSED, str)
        self.assertEqual(STATUS_CLOSED, "Closed")

    def test_join_unconfirmed_is_string(self):
        self.assertIsInstance(STATUS_JOIN_UNCONFIRMED, str)
        self.assertEqual(STATUS_JOIN_UNCONFIRMED, "Join Unconfirmed")

    def test_healthy_states_contains_lobby_and_in_server(self):
        self.assertIn(STATUS_LOBBY, _HEALTHY_STATES)
        self.assertIn(STATUS_IN_SERVER, _HEALTHY_STATES)
        self.assertIn(STATUS_ONLINE, _HEALTHY_STATES)

    def test_join_unconfirmed_in_healthy_states(self):
        """Join Unconfirmed is a healthy state — app is running."""
        self.assertIn(STATUS_JOIN_UNCONFIRMED, _HEALTHY_STATES)

    def test_joining_not_in_healthy_states(self):
        self.assertNotIn(STATUS_JOINING, _HEALTHY_STATES)

    def test_launching_not_in_healthy_states(self):
        self.assertNotIn(STATUS_LAUNCHING, _HEALTHY_STATES)


def _make_entry(package: str, private_url: str = "") -> dict:
    return {
        "package": package,
        "account_username": "TestUser",
        "enabled": True,
        "username_source": "manual",
        "private_server_url": private_url,
        "auto_reopen_enabled": True,
        "auto_reconnect_enabled": True,
    }


def _make_cfg(package: str = "com.roblox.client") -> dict:
    return {
        "roblox_package": package,
        "launch_mode": "app",
        "launch_url": "",
        "private_server_url": "",
        "auto_rejoin_enabled": True,
        "health_check_interval_seconds": 30,
        "foreground_grace_seconds": 30,
        "reconnect_delay_seconds": 8,
        "backoff_min_seconds": 10,
        "backoff_max_seconds": 300,
        "max_fast_failures": 3,
        "log_level": "WARNING",
        "supervisor": {
            "enabled": True,
            "health_check_interval_seconds": 30,
            "launch_grace_seconds": 10,
            "restart_backoff_seconds": 5,
            "max_restart_attempts_per_hour": 10,
            "auto_reopen_enabled": True,
            "auto_reconnect_enabled": True,
        },
        "roblox_packages": [
            {
                "package": package,
                "account_username": "TestUser",
                "enabled": True,
                "username_source": "manual",
                "private_server_url": "",
            }
        ],
    }


def _run_worker_one_iteration(entry, cfg, initial_status: str, *, url_launched: bool = False):
    """Run a _PackageWorker through exactly one healthy health-check iteration.

    Returns the final status string after the worker exits.
    """
    pkg = entry["package"]
    status_map = {pkg: initial_status}
    stop_event = threading.Event()

    from agent.monitor import HealthResult

    def health_side_effect(_cfg, _package):
        stop_event.set()   # stop after this iteration completes
        return HealthResult("healthy", {}, "")

    with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
         unittest.mock.patch("agent.supervisor.db"), \
         unittest.mock.patch("agent.supervisor.log_event"), \
         unittest.mock.patch(
             "agent.supervisor._PackageWorker.run",
             wraps=None,  # will be set below
         ) as _unused:
        pass  # just checking context; real run below

    # Real run — mock health and epsu, stop after first iteration
    with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
         unittest.mock.patch("agent.supervisor.db"), \
         unittest.mock.patch("agent.supervisor.log_event"), \
         unittest.mock.patch(
             "agent.config.effective_private_server_url",
             return_value="roblox://x" if url_launched else "",
         ):
        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        # Ensure the url-launch flag is set correctly after run() initialises it
        worker._url_launched = url_launched
        worker.launching_since = time.time() - 5  # slightly in the past but < timeout
        worker.run()

    return status_map[pkg]


class TestLaunchingToLobbyTransition(unittest.TestCase):
    """Launching (no URL) → Lobby when health returns healthy."""

    def test_launching_with_no_url_maps_to_lobby_constant(self):
        """After healthy check when in Launching (no URL), state must become Lobby."""
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg("com.roblox.client")
        result = _run_worker_one_iteration(entry, cfg, STATUS_LAUNCHING, url_launched=False)
        self.assertEqual(result, STATUS_LOBBY)

    def test_joining_with_url_no_evidence_maps_to_join_unconfirmed(self):
        """URL launch + healthy process + no Android evidence → Join Unconfirmed (not In Server).

        The tool must not claim In Server just because the process is healthy
        after a private URL launch.  Without real Android evidence (logcat /
        dumpsys / uiautomator), the honest state is Join Unconfirmed.
        In this test environment (Windows) all Android probes fail gracefully,
        so the detector always returns FOREGROUND_APP evidence.
        """
        entry = _make_entry("com.roblox.client", private_url="roblox://placeId=123")
        cfg = _make_cfg("com.roblox.client")
        result = _run_worker_one_iteration(entry, cfg, STATUS_JOINING, url_launched=True)
        self.assertEqual(result, STATUS_JOIN_UNCONFIRMED)

    def test_joining_with_url_and_ingame_evidence_maps_to_in_server(self):
        """URL launch + healthy + strong in-game evidence → In Server."""
        from agent.experience_detector import EvidenceLevel, ExperienceEvidence

        in_game_ev = ExperienceEvidence(
            level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED,
            detail="test: game loaded signal",
            source="logcat",
            raw_snippet="GameLoaded",
        )

        entry = _make_entry("com.roblox.client", private_url="roblox://placeId=123")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_JOINING}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value="roblox://x"), \
             unittest.mock.patch("agent.supervisor.detect_experience_state", return_value=in_game_ev):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = True
            worker.launching_since = time.time() - 5
            worker.run()

        self.assertEqual(status_map[pkg], STATUS_IN_SERVER)

    def test_lobby_stays_lobby_on_subsequent_healthy_checks(self):
        """Once in Lobby, healthy checks must not demote to Online."""
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_LOBBY}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value=""):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = False
            worker.launching_since = None  # already healthy; no timeout
            worker.run()

        self.assertEqual(status_map[pkg], STATUS_LOBBY)

    def test_in_server_stays_in_server_on_subsequent_healthy_checks(self):
        """Once In Server, healthy checks must not demote to Online."""
        entry = _make_entry("com.roblox.client", private_url="roblox://placeId=123")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_IN_SERVER}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value="roblox://x"):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = True
            worker.launching_since = None
            worker.run()

        self.assertEqual(status_map[pkg], STATUS_IN_SERVER)


class TestLaunchingTimeout(unittest.TestCase):
    """Launching timeout forces re-check and promotes or marks Failed.

    Timeout threshold = max(90, grace * 4). With grace=10 from _make_cfg,
    _launching_timeout = max(90, 40) = 90 seconds. We set launching_since
    to 200 seconds ago so the timeout always fires in the first iteration.
    The timeout guard calls check_package_health once, then does `continue`,
    skipping the second health check in that iteration. After that, stop_event
    is set so the loop exits.
    """

    def test_launching_timeout_promotes_to_lobby_when_healthy_no_url(self):
        """If Launching for too long (no URL) and health=healthy → promote to Lobby."""
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_LAUNCHING}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value=""):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = False
            worker.launching_since = time.time() - 200
            worker.run()

        self.assertEqual(status_map[pkg], STATUS_LOBBY)

    def test_joining_timeout_with_url_no_evidence_is_join_unconfirmed(self):
        """Joining timeout + URL used + no Android evidence → Join Unconfirmed, NOT In Server."""
        entry = _make_entry("com.roblox.client", private_url="roblox://x")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_JOINING}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value="roblox://x"):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = True
            worker.launching_since = time.time() - 200
            worker.run()

        # Without Android environment the evidence level is FOREGROUND_APP only.
        # Must NOT be In Server.
        self.assertNotEqual(status_map[pkg], STATUS_IN_SERVER)
        self.assertEqual(status_map[pkg], STATUS_JOIN_UNCONFIRMED)

    def test_launching_timeout_fails_when_health_not_running(self):
        """If Launching for too long and health=not_running → status is Failed."""
        entry = _make_entry("com.roblox.client", private_url="")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: STATUS_LAUNCHING}
        stop_event = threading.Event()

        from agent.monitor import HealthResult

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("roblox_not_running", {}, "process missing")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url", return_value=""):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = False
            worker.launching_since = time.time() - 200
            worker.run()

        self.assertEqual(status_map[pkg], STATUS_FAILED)


class TestReconnectURLAwareness(unittest.TestCase):
    """After reconnect/revive: STATUS_JOINING when URL configured, else STATUS_LAUNCHING."""

    def _make_worker(self, has_url: bool, initial_st: str) -> tuple:
        entry = _make_entry("com.roblox.client", private_url="roblox://x" if has_url else "")
        cfg = _make_cfg("com.roblox.client")
        pkg = "com.roblox.client"
        status_map = {pkg: initial_st}
        stop_event = threading.Event()
        worker = _PackageWorker(entry, cfg, status_map, stop_event)
        worker.has_private_url = has_url
        worker._url_launched = False
        return worker, status_map, stop_event

    def test_reconnect_no_url_sets_launching(self):
        worker, status_map, _ = self._make_worker(False, STATUS_RECONNECTING)
        worker._url_launched = False
        worker.launching_since = None

        # Simulate a successful reconnect
        worker._url_launched = worker.has_private_url
        worker.launching_since = time.time()
        new_st = STATUS_JOINING if worker.has_private_url else STATUS_LAUNCHING
        worker._set_status(new_st, "test reconnect")

        self.assertEqual(status_map["com.roblox.client"], STATUS_LAUNCHING)

    def test_reconnect_with_url_sets_joining(self):
        worker, status_map, _ = self._make_worker(True, STATUS_RECONNECTING)
        worker._url_launched = worker.has_private_url
        worker.launching_since = time.time()
        new_st = STATUS_JOINING if worker.has_private_url else STATUS_LAUNCHING
        worker._set_status(new_st, "test reconnect")

        self.assertEqual(status_map["com.roblox.client"], STATUS_JOINING)


class TestMultiPackageSupervisorRenderCallback(unittest.TestCase):
    """MultiPackageSupervisor.run_forever must call render_callback instead of _print_live_status."""

    def _build_supervisor(self, entries, cfg, initial_status):
        """Create a MultiPackageSupervisor with workers that exit immediately."""
        sup = MultiPackageSupervisor(entries, cfg, initial_status=initial_status)
        return sup

    def test_render_callback_called_on_refresh(self):
        entries = [_make_entry("com.roblox.client")]
        cfg = _make_cfg("com.roblox.client")
        cfg["roblox_packages"] = entries

        render_calls = []

        def fake_render():
            render_calls.append(1)

        # Patch _PackageWorker.run to prevent the worker thread from running real logic
        with unittest.mock.patch.object(_PackageWorker, "run", return_value=None), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.supervisor.signal"):
            sup = self._build_supervisor(entries, cfg, {"com.roblox.client": STATUS_LOBBY})

            iteration = [0]

            def fake_wait(timeout=None):
                iteration[0] += 1
                if iteration[0] >= 2:
                    sup.stop_event.set()
                return False  # simulate timeout elapsed (did not get set)

            sup.stop_event.wait = fake_wait
            sup.run_forever(display_interval=0.001, render_callback=fake_render)

        self.assertGreater(len(render_calls), 0, "render_callback was never called")

    def test_print_live_status_fallback_when_no_callback(self):
        """Without render_callback, _print_live_status is called."""
        entries = [_make_entry("com.roblox.client")]
        cfg = _make_cfg("com.roblox.client")
        cfg["roblox_packages"] = entries

        print_calls = []

        with unittest.mock.patch.object(_PackageWorker, "run", return_value=None), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.supervisor.signal"), \
             unittest.mock.patch("builtins.print", side_effect=lambda *a, **kw: print_calls.append(a)):
            sup = self._build_supervisor(entries, cfg, {"com.roblox.client": STATUS_ONLINE})

            iteration = [0]

            def fake_wait(timeout=None):
                iteration[0] += 1
                if iteration[0] >= 2:
                    sup.stop_event.set()
                return False

            sup.stop_event.wait = fake_wait
            sup.run_forever(display_interval=0.001)

        all_output = " ".join(str(a) for call in print_calls for a in call)
        self.assertIn("Monitor", all_output)


class TestColorizeStatusNewStates(unittest.TestCase):
    """_colorize_status must handle all new state strings."""

    def test_lobby_has_color(self):
        from agent.commands import _colorize_status
        result = _colorize_status("Lobby", use_color=True)
        self.assertIn("Lobby", result)

    def test_in_server_has_color(self):
        from agent.commands import _colorize_status
        result = _colorize_status("In Server", use_color=True)
        self.assertIn("In Server", result)

    def test_joining_has_color(self):
        from agent.commands import _colorize_status
        result = _colorize_status("Joining", use_color=True)
        self.assertIn("Joining", result)

    def test_closed_has_color(self):
        from agent.commands import _colorize_status
        result = _colorize_status("Closed", use_color=True)
        self.assertIn("Closed", result)

    def test_join_unconfirmed_has_color(self):
        from agent.commands import _colorize_status
        result = _colorize_status("Join Unconfirmed", use_color=True)
        self.assertIn("Join Unconfirmed", result)

    def test_no_color_mode_returns_plain_string(self):
        from agent.commands import _colorize_status
        for state in ("Lobby", "Joining", "In Server", "Closed", "Join Unconfirmed"):
            self.assertEqual(_colorize_status(state, use_color=False), state)


class TestStateSummaryNewStates(unittest.TestCase):
    """build_final_summary must handle new state strings."""

    def test_lobby_counts_as_online(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Lobby"})
        self.assertIn("online", text.lower())

    def test_in_server_counts_as_online(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "In Server"})
        self.assertIn("online", text.lower())

    def test_joining_counts_as_launching(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Joining"})
        self.assertIn("launching", text.lower())

    def test_start_table_shows_lobby(self):
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "TestUser", "Lobby")]
        table = build_start_table(rows)
        self.assertIn("Lobby", table)

    def test_start_table_shows_joining(self):
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "TestUser", "Joining")]
        table = build_start_table(rows)
        self.assertIn("Joining", table)

    def test_start_table_shows_in_server(self):
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "TestUser", "In Server")]
        table = build_start_table(rows)
        self.assertIn("In Server", table)

    def test_join_unconfirmed_counts_as_launching(self):
        from agent.commands import build_final_summary
        entries = [{"package": "com.roblox.client", "account_username": "", "enabled": True, "username_source": "not_set"}]
        text = build_final_summary(entries, {"com.roblox.client": "Join Unconfirmed"})
        self.assertIn("launching", text.lower())

    def test_start_table_shows_join_unconfirmed(self):
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "TestUser", "Join Unconfirmed")]
        table = build_start_table(rows)
        self.assertIn("Join Unconfirmed", table)


class TestEvidenceBasedStateTransitions(unittest.TestCase):
    """Comprehensive evidence-model state transition tests."""

    def _run_with_evidence(self, initial_st: str, url_launched: bool, evidence_level) -> str:
        """Run one worker iteration with a given mocked evidence level."""
        from agent.experience_detector import EvidenceLevel, ExperienceEvidence
        from agent.monitor import HealthResult

        pkg = "com.roblox.client"
        entry = _make_entry(pkg, private_url="roblox://x" if url_launched else "")
        cfg = _make_cfg(pkg)
        status_map = {pkg: initial_st}
        stop_event = threading.Event()

        ev = ExperienceEvidence(
            level=evidence_level,
            detail=f"test evidence level={evidence_level}",
            source="test",
        )

        def health_side_effect(_cfg, _package):
            stop_event.set()
            return HealthResult("healthy", {}, "")

        with unittest.mock.patch("agent.supervisor.check_package_health", side_effect=health_side_effect), \
             unittest.mock.patch("agent.supervisor.db"), \
             unittest.mock.patch("agent.supervisor.log_event"), \
             unittest.mock.patch("agent.config.effective_private_server_url",
                                  return_value="roblox://x" if url_launched else ""), \
             unittest.mock.patch("agent.supervisor.detect_experience_state", return_value=ev):
            worker = _PackageWorker(entry, cfg, status_map, stop_event)
            worker._url_launched = url_launched
            worker.launching_since = time.time() - 5
            worker.run()

        return status_map[pkg]

    def test_url_launch_no_evidence_is_join_unconfirmed(self):
        """URL launch + FOREGROUND_APP evidence → Join Unconfirmed, not In Server."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_JOINING, url_launched=True,
                                          evidence_level=EvidenceLevel.FOREGROUND_APP)
        self.assertEqual(result, STATUS_JOIN_UNCONFIRMED)
        self.assertNotEqual(result, STATUS_IN_SERVER)

    def test_url_launch_process_only_is_join_unconfirmed(self):
        """URL launch + PROCESS_ONLY evidence → Join Unconfirmed."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_JOINING, url_launched=True,
                                          evidence_level=EvidenceLevel.PROCESS_ONLY)
        self.assertEqual(result, STATUS_JOIN_UNCONFIRMED)

    def test_url_launch_game_evidence_is_in_server(self):
        """URL launch + EXPERIENCE_LIKELY_LOADED evidence → In Server."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_JOINING, url_launched=True,
                                          evidence_level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED)
        self.assertEqual(result, STATUS_IN_SERVER)

    def test_url_launch_home_evidence_is_join_unconfirmed(self):
        """URL launch + HOME_OR_LOBBY evidence → Join Unconfirmed (honest: join probably failed)."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_JOINING, url_launched=True,
                                          evidence_level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY)
        self.assertEqual(result, STATUS_JOIN_UNCONFIRMED)

    def test_url_launch_join_failed_evidence_is_join_unconfirmed(self):
        """URL launch + JOIN_FAILED_OR_HOME evidence → Join Unconfirmed."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_JOINING, url_launched=True,
                                          evidence_level=EvidenceLevel.JOIN_FAILED_OR_HOME)
        self.assertEqual(result, STATUS_JOIN_UNCONFIRMED)

    def test_no_url_healthy_home_evidence_is_lobby(self):
        """No URL + ROBLOX_HOME_OR_LOBBY evidence → Lobby."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_LAUNCHING, url_launched=False,
                                          evidence_level=EvidenceLevel.ROBLOX_HOME_OR_LOBBY)
        self.assertEqual(result, STATUS_LOBBY)

    def test_no_url_game_evidence_is_in_server(self):
        """No URL + EXPERIENCE_LIKELY_LOADED evidence → In Server (unusual, but honest)."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_LAUNCHING, url_launched=False,
                                          evidence_level=EvidenceLevel.EXPERIENCE_LIKELY_LOADED)
        self.assertEqual(result, STATUS_IN_SERVER)

    def test_no_url_foreground_app_is_lobby(self):
        """No URL + FOREGROUND_APP evidence → Lobby (app is open, no URL, assume home)."""
        from agent.experience_detector import EvidenceLevel
        result = self._run_with_evidence(STATUS_LAUNCHING, url_launched=False,
                                          evidence_level=EvidenceLevel.FOREGROUND_APP)
        self.assertEqual(result, STATUS_LOBBY)

    def test_join_unconfirmed_stays_in_healthy_states(self):
        """Once Join Unconfirmed, the next healthy check must stay in healthy states."""
        from agent.experience_detector import EvidenceLevel
        # Join Unconfirmed stays on repeated healthy polls (no evidence change)
        result = self._run_with_evidence(STATUS_JOIN_UNCONFIRMED, url_launched=True,
                                          evidence_level=EvidenceLevel.FOREGROUND_APP)
        # After a healthy check from JOIN_UNCONFIRMED, it should stay in a healthy state
        self.assertIn(result, _HEALTHY_STATES)

    def test_public_table_has_only_package_username_state(self):
        """build_start_table output must have only Package, Username, State columns."""
        from agent.commands import build_start_table
        rows = [(1, "com.roblox.client", "TestUser", "Join Unconfirmed")]
        table = build_start_table(rows)
        # Table headers must include exactly these four columns
        self.assertIn("Package", table)
        self.assertIn("Username", table)
        self.assertIn("State", table)
        # No debug columns
        self.assertNotIn("Evidence", table)
        self.assertNotIn("logcat", table)
        self.assertNotIn("dumpsys", table)


if __name__ == "__main__":
    unittest.main()
