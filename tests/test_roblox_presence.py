"""Tests for agent.roblox_presence — ground-truth presence detection."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from agent import roblox_presence as rp


# ─── helpers ─────────────────────────────────────────────────────────────────

def _fake_post_json(mapping):
    """Build a stand-in for ``_post_json`` that returns canned bodies."""
    def _inner(url, body, **kwargs):
        return mapping.get(url)
    return _inner


# ─── cookie masking ──────────────────────────────────────────────────────────

class TestMaskCookie(unittest.TestCase):
    def test_short_cookie_fully_masked(self) -> None:
        self.assertEqual(rp.mask_cookie("abc"), "***")

    def test_long_cookie_masked_to_first_last_chars(self) -> None:
        masked = rp.mask_cookie("_|WARNING:-DO-NOT-SHARE-THIS|_xyzabc1234")
        # The masker must not leak the middle.
        self.assertNotIn("WARNING", masked)
        self.assertNotIn("DO-NOT-SHARE", masked)
        # Must be much shorter than the original.
        self.assertLess(len(masked), 20)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(rp.mask_cookie(None), "")
        self.assertEqual(rp.mask_cookie(""), "")


# ─── lookup_user_id ──────────────────────────────────────────────────────────

class TestLookupUserId(unittest.TestCase):
    def setUp(self) -> None:
        rp.clear_presence_cache()

    def test_resolves_valid_username(self) -> None:
        responses = {
            rp._USERNAME_LOOKUP_URL: {
                "data": [{"id": 1234567, "name": "alice", "requestedUsername": "alice"}],
            },
        }
        with mock.patch.object(rp, "_post_json", _fake_post_json(responses)):
            self.assertEqual(rp.lookup_user_id("alice"), 1234567)

    def test_returns_none_for_empty(self) -> None:
        self.assertIsNone(rp.lookup_user_id(None))
        self.assertIsNone(rp.lookup_user_id(""))
        self.assertIsNone(rp.lookup_user_id("   "))

    def test_returns_none_for_invalid_chars(self) -> None:
        # spaces, slashes, control chars — must NOT be POSTed.
        self.assertIsNone(rp.lookup_user_id("a b"))
        self.assertIsNone(rp.lookup_user_id("alice/bob"))
        self.assertIsNone(rp.lookup_user_id("ab"))    # too short
        self.assertIsNone(rp.lookup_user_id("x" * 21))  # too long

    def test_returns_none_on_api_failure(self) -> None:
        with mock.patch.object(rp, "_post_json", return_value=None):
            self.assertIsNone(rp.lookup_user_id("alice"))

    def test_caches_result_across_calls(self) -> None:
        calls = []

        def counter(*args, **kw):
            calls.append(args)
            return {"data": [{"id": 99, "name": "bob"}]}

        with mock.patch.object(rp, "_post_json", side_effect=counter):
            self.assertEqual(rp.lookup_user_id("bob"), 99)
            self.assertEqual(rp.lookup_user_id("bob"), 99)
            self.assertEqual(rp.lookup_user_id("BOB"), 99)  # case-insensitive cache
        self.assertEqual(len(calls), 1, "should hit network only once for the same username")


# ─── fetch_presence ──────────────────────────────────────────────────────────

class TestFetchPresence(unittest.TestCase):
    def setUp(self) -> None:
        rp.clear_presence_cache()

    def test_returns_in_game_for_user_in_a_place(self) -> None:
        responses = {
            rp._PRESENCE_URL: {
                "userPresences": [
                    {"userPresenceType": 2, "placeId": 9876, "rootPlaceId": 9876,
                     "userId": 100, "lastLocation": "Roblox Adopt Me",
                     "lastOnline": "2026-05-17T03:14:00Z"},
                ],
            },
        }
        with mock.patch.object(rp, "_post_json", _fake_post_json(responses)):
            out = rp.fetch_presence([100])
        self.assertIn(100, out)
        self.assertTrue(out[100].is_in_game)
        self.assertEqual(out[100].place_id, 9876)
        self.assertEqual(out[100].last_location, "Roblox Adopt Me")

    def test_offline_user_is_offline_not_unknown(self) -> None:
        responses = {
            rp._PRESENCE_URL: {
                "userPresences": [{"userPresenceType": 0, "userId": 50}],
            },
        }
        with mock.patch.object(rp, "_post_json", _fake_post_json(responses)):
            out = rp.fetch_presence([50])
        self.assertTrue(out[50].is_offline)
        self.assertFalse(out[50].is_unknown)

    def test_lobby_online_user(self) -> None:
        responses = {
            rp._PRESENCE_URL: {
                "userPresences": [{"userPresenceType": 1, "userId": 7}],
            },
        }
        with mock.patch.object(rp, "_post_json", _fake_post_json(responses)):
            out = rp.fetch_presence([7])
        self.assertTrue(out[7].is_lobby)
        self.assertFalse(out[7].is_in_game)
        self.assertFalse(out[7].is_offline)

    def test_unknown_when_api_returns_nothing(self) -> None:
        with mock.patch.object(rp, "_post_json", return_value=None):
            out = rp.fetch_presence([42])
        self.assertEqual(out[42].presence_type, rp.PresenceType.UNKNOWN)
        self.assertTrue(out[42].is_unknown)

    def test_empty_id_list_returns_empty(self) -> None:
        self.assertEqual(rp.fetch_presence([]), {})
        self.assertEqual(rp.fetch_presence([0, -1]), {})

    def test_cached_results_skip_network(self) -> None:
        responses = {
            rp._PRESENCE_URL: {
                "userPresences": [{"userPresenceType": 2, "userId": 11, "placeId": 1}],
            },
        }
        call_count = [0]

        def counter(*args, **kw):
            call_count[0] += 1
            return responses.get(args[0])

        with mock.patch.object(rp, "_post_json", side_effect=counter):
            rp.fetch_presence([11])
            rp.fetch_presence([11])
            rp.fetch_presence([11])
        self.assertEqual(call_count[0], 1)

    def test_fetch_presence_one_returns_unknown_for_zero(self) -> None:
        r = rp.fetch_presence_one(0)
        self.assertTrue(r.is_unknown)
        r = rp.fetch_presence_one(None)
        self.assertTrue(r.is_unknown)


# ─── never raises ────────────────────────────────────────────────────────────

class TestNeverRaises(unittest.TestCase):
    def test_post_json_returns_none_on_network_error(self) -> None:
        with mock.patch.object(rp.urllib.request, "urlopen",
                              side_effect=rp.urllib.error.URLError("no network")):
            self.assertIsNone(rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}))

    def test_post_json_returns_none_on_bad_json(self) -> None:
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, _n): return b"not json"
        with mock.patch.object(rp.urllib.request, "urlopen", return_value=_Resp()):
            self.assertIsNone(rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
