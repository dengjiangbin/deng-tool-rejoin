"""YesCaptcha API client for CAPTCHA solving.

Provides a minimal wrapper around the YesCaptcha REST API.
Configure the ``yescaptcha_key`` field in config.json to enable.

The key is never logged or transmitted to any server other than api.yescaptcha.com.
This module only handles the API call mechanics — CAPTCHA detection and result
application are handled by the caller.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

YESCAPTCHA_API_BASE = "https://api.yescaptcha.com"
_POLL_INTERVAL_SECONDS = 3
_DEFAULT_MAX_WAIT_SECONDS = 60


class CaptchaError(Exception):
    """Raised when CAPTCHA solving fails or the API returns an error."""


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise CaptchaError(f"YesCaptcha API request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CaptchaError(f"YesCaptcha returned invalid JSON: {exc}") from exc


def _create_task(api_key: str, task: dict[str, Any]) -> str:
    """Submit a task and return its task_id string."""
    if not api_key:
        raise CaptchaError("yescaptcha_key is not configured")
    result = _post_json(f"{YESCAPTCHA_API_BASE}/createTask", {"clientKey": api_key, "task": task})
    if result.get("errorId", 0) != 0:
        raise CaptchaError(f"YesCaptcha error {result.get('errorId')}: {result.get('errorDescription', 'unknown')}")
    task_id = result.get("taskId")
    if not task_id:
        raise CaptchaError("YesCaptcha did not return a taskId")
    return str(task_id)


def _get_task_result(api_key: str, task_id: str, *, max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS) -> dict[str, Any]:
    """Poll for a task result until ready or timed out."""
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        result = _post_json(f"{YESCAPTCHA_API_BASE}/getTaskResult", {"clientKey": api_key, "taskId": task_id})
        if result.get("errorId", 0) != 0:
            raise CaptchaError(f"YesCaptcha error: {result.get('errorDescription', 'unknown')}")
        if result.get("status") == "ready":
            solution = result.get("solution")
            if not solution:
                raise CaptchaError("YesCaptcha returned ready status but solution is missing")
            return solution
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise CaptchaError(f"YesCaptcha task timed out after {max_wait_seconds}s (taskId={task_id})")


def solve_funcaptcha(
    api_key: str,
    public_key: str,
    *,
    website_url: str = "https://www.roblox.com",
    max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
) -> str:
    """Solve a FunCaptcha / Arkose Labs challenge.

    Returns the arkose token string to submit with the login form.
    Raises CaptchaError on failure.
    """
    task = {
        "type": "FunCaptchaTaskProxyLess",
        "websiteURL": website_url,
        "websitePublicKey": public_key,
    }
    task_id = _create_task(api_key, task)
    solution = _get_task_result(api_key, task_id, max_wait_seconds=max_wait_seconds)
    token = solution.get("token")
    if not token:
        raise CaptchaError("FunCaptcha solution is missing the 'token' field")
    return token


def solve_recaptcha_v2(
    api_key: str,
    site_key: str,
    *,
    website_url: str = "https://www.roblox.com",
    max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
) -> str:
    """Solve a reCAPTCHA v2 challenge.

    Returns the gRecaptchaResponse token string.
    Raises CaptchaError on failure.
    """
    task = {
        "type": "NoCaptchaTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": site_key,
    }
    task_id = _create_task(api_key, task)
    solution = _get_task_result(api_key, task_id, max_wait_seconds=max_wait_seconds)
    token = solution.get("gRecaptchaResponse")
    if not token:
        raise CaptchaError("reCAPTCHA v2 solution is missing the 'gRecaptchaResponse' field")
    return token


def get_balance(api_key: str) -> float:
    """Return current YesCaptcha account balance. Raises CaptchaError on failure."""
    if not api_key:
        raise CaptchaError("yescaptcha_key is not configured")
    result = _post_json(f"{YESCAPTCHA_API_BASE}/getBalance", {"clientKey": api_key})
    if result.get("errorId", 0) != 0:
        raise CaptchaError(f"YesCaptcha error: {result.get('errorDescription', 'unknown')}")
    balance = result.get("balance")
    if balance is None:
        raise CaptchaError("YesCaptcha balance response missing 'balance' field")
    return float(balance)
