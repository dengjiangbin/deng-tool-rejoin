"""Tests for agent.roblox_presence — ground-truth presence detection."""

from __future__ import annotations

import inspect
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


class TestAuthenticatedCookiePresence(unittest.TestCase):
    def setUp(self) -> None:
        rp.clear_presence_cache()

    def test_cookie_authenticated_id_and_ingame_presence(self) -> None:
        cookie = "cookie-value-long-enough"
        with mock.patch.object(
            rp.safe_http,
            "get_json",
            return_value={"id": 12345},
        ) as authenticated, mock.patch.object(
            rp,
            "_post_json",
            return_value={"userPresences": [{"userPresenceType": 2, "userId": 12345}]},
        ) as presence:
            user_id = rp.authenticated_user_id(cookie)
            result = rp.fetch_presence_one(user_id, cookie=cookie, refresh=True)
        self.assertEqual(user_id, 12345)
        self.assertTrue(result.is_in_game)
        self.assertEqual(authenticated.call_args.args[0], rp._AUTHENTICATED_USER_URL)
        self.assertEqual(
            authenticated.call_args.kwargs["headers"]["Cookie"],
            f".ROBLOSECURITY={cookie}",
        )
        self.assertEqual(presence.call_args.args[0], rp._PRESENCE_URL)
        self.assertEqual(presence.call_args.args[1], {"userIds": [12345]})

    def test_cookie_authenticated_presence_type_zero_is_offline(self) -> None:
        cookie = "cookie-value-long-enough"
        with mock.patch.object(
            rp.safe_http,
            "get_json",
            return_value={"id": "67890"},
        ), mock.patch.object(
            rp,
            "_post_json",
            return_value={"userPresences": [{"userPresenceType": 0, "userId": 67890}]},
        ):
            user_id = rp.authenticated_user_id(cookie)
            result = rp.fetch_presence_one(user_id, cookie=cookie, refresh=True)
        self.assertEqual(user_id, 67890)
        self.assertTrue(result.is_offline)

    def test_single_user_presence_allows_omitted_user_id(self) -> None:
        with mock.patch.object(
            rp,
            "_post_json",
            return_value={"userPresences": [{"userPresenceType": "2"}]},
        ):
            result = rp.fetch_presence_one(12345, refresh=True)
        self.assertEqual(result.user_id, 12345)
        self.assertTrue(result.is_in_game)


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
    def test_post_json_raises_api_fault_on_network_error(self) -> None:
        with mock.patch.object(
            rp,
            "_roblox_post_once",
            side_effect=rp.safe_http.SafeHttpNetworkError("no network"),
        ):
            with self.assertRaises(rp.RobloxApiFaultError):
                rp._post_json(rp._PRESENCE_URL, {"userIds": [1]})

    def test_post_json_raises_api_fault_on_server_error(self) -> None:
        with mock.patch.object(
            rp,
            "_roblox_post_once",
            return_value=(503, {}, None),
        ):
            with self.assertRaises(rp.RobloxApiFaultError) as ctx:
                rp._post_json(rp._PRESENCE_URL, {"userIds": [1]})
        self.assertEqual(ctx.exception.fault, "server_error")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_post_json_returns_none_on_bad_json(self) -> None:
        with mock.patch.object(
            rp,
            "_roblox_post_once",
            return_value=(200, {}, None),
        ):
            self.assertIsNone(rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}))

    def test_post_json_uses_safe_http_for_termux_stability(self) -> None:
        with mock.patch.object(
            rp,
            "_roblox_post_once",
            return_value=(200, {}, {"ok": True}),
        ) as post:
            self.assertEqual(rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}), {"ok": True})
        post.assert_called_once()

    def test_post_json_live_path_uses_post_with_response(self) -> None:
        src = inspect.getsource(rp._post_json)
        self.assertIn("_roblox_post_once", src)
        src_once = inspect.getsource(rp._roblox_post_once)
        self.assertIn("post_with_response", src_once)
        self.assertNotIn("urlopen", src)
        self.assertNotIn("create_default_context", src)

    def test_post_json_csrf_retry_on_403(self) -> None:
        rp.clear_presence_cache()
        calls: list[dict[str, str]] = []

        def _fake(url, body, *, headers, timeout):
            calls.append(dict(headers))
            if "X-CSRF-TOKEN" not in headers:
                return 403, {"x-csrf-token": "tok123"}, None
            return 200, {}, {"data": [{"id": 1, "name": "alice"}]}

        with mock.patch.object(rp, "_roblox_post_once", side_effect=_fake):
            out = rp._post_json(
                rp._USERNAME_LOOKUP_URL,
                {"usernames": ["alice"], "excludeBannedUsers": False},
                cookie="cookie-value-long",
            )
        self.assertEqual(out, {"data": [{"id": 1, "name": "alice"}]})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].get("X-CSRF-TOKEN"), "tok123")

    def test_post_json_reuses_cached_csrf_token(self) -> None:
        rp.clear_presence_cache()
        calls: list[dict[str, str]] = []

        def _fake(url, body, *, headers, timeout):
            calls.append(dict(headers))
            if len(calls) == 1:
                return 403, {"x-csrf-token": "tok456"}, None
            return 200, {}, {"userPresences": [{"userPresenceType": 2, "userId": 1}]}

        with mock.patch.object(rp, "_roblox_post_once", side_effect=_fake):
            first = rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}, cookie="cookie-value-long")
            second = rp._post_json(rp._PRESENCE_URL, {"userIds": [1]}, cookie="cookie-value-long")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1].get("X-CSRF-TOKEN"), "tok456")
        self.assertEqual(calls[2].get("X-CSRF-TOKEN"), "tok456")

    def test_csrf_header_extraction_is_case_insensitive(self) -> None:
        self.assertEqual(rp._extract_csrf_token({"x-csrf-token": "lower"}), "lower")
        self.assertEqual(rp._extract_csrf_token({"X-CsRf-ToKeN": "mixed"}), "mixed")


class TestDualPresenceVerification(unittest.TestCase):
    def setUp(self) -> None:
        rp.clear_presence_cache()

    def test_public_fallback_rescues_false_offline_cookie_pass(self) -> None:
        calls: list[tuple[str, object | None]] = []

        def _post(url, body, *, cookie=None, timeout=rp.HTTP_TIMEOUT):
            calls.append((url, cookie))
            if cookie:
                return {"userPresences": [{"userPresenceType": 0, "userId": 12345}]}
            return {
                "userPresences": [
                    {
                        "userPresenceType": 2,
                        "userId": 12345,
                        "placeId": 999,
                        "rootPlaceId": 888,
                        "lastLocation": "Game",
                    }
                ]
            }

        with mock.patch.object(rp, "_post_json", side_effect=_post):
            result = rp.fetch_presence_dual_verified(
                12345,
                cookie="cookie-value-long-enough",
            )

        self.assertTrue(result.is_in_game)
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[0][1])
        self.assertIsNone(calls[1][1])

    def test_cookie_in_game_skips_public_fallback(self) -> None:
        calls: list[tuple[str, object | None]] = []

        def _post(url, body, *, cookie=None, timeout=rp.HTTP_TIMEOUT):
            calls.append((url, cookie))
            return {
                "userPresences": [
                    {"userPresenceType": 2, "userId": 12345, "placeId": 1}
                ]
            }

        with mock.patch.object(rp, "_post_json", side_effect=_post):
            result = rp.fetch_presence_dual_verified(
                12345,
                cookie="cookie-value-long-enough",
            )

        self.assertTrue(result.is_in_game)
        self.assertEqual(len(calls), 1)

    def test_both_passes_offline_returns_offline(self) -> None:
        def _post(url, body, *, cookie=None, timeout=rp.HTTP_TIMEOUT):
            return {"userPresences": [{"userPresenceType": 0, "userId": 12345}]}

        with mock.patch.object(rp, "_post_json", side_effect=_post):
            result = rp.fetch_presence_dual_verified(
                12345,
                cookie="cookie-value-long-enough",
            )

        self.assertTrue(result.is_offline)
        self.assertFalse(result.is_in_game)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
