package my.id.deng.monitor.ui

import android.net.Uri
import android.util.Log
import android.webkit.CookieManager
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

const val APK_AUTH_LOG_TAG = "DengApkAuth"
const val APK_AUTH_HANDOFF_MARKER = "APK_DISCORD_AUTH_HANDOFF_COMPLETION_FIX_2026_06_14"
const val APK_MOBILE_AUTH_MARKER = "APK_MOBILE_AUTH_WEBVIEW_BOOTSTRAP_2026_06_15"

sealed class ApkOAuthHandoffResult {
    data class Ready(val bridgeUrl: String) : ApkOAuthHandoffResult()
    data class Failed(val reason: String) : ApkOAuthHandoffResult()
}

private fun matchesApkCallbackUri(uri: Uri, publicWebHost: String): Boolean {
    val scheme = uri.scheme?.lowercase().orEmpty()
    val host = uri.host?.lowercase().orEmpty()
    val path = uri.path.orEmpty()
    val matchesCustomScheme = scheme == BuildConfig.DENG_AIO_APP_SCHEME.lowercase()
        && host == "auth"
        && (path == "/callback" || path.startsWith("/callback/"))
    val matchesHttpsHandoff = (scheme == "http" || scheme == "https")
        && host == publicWebHost.lowercase()
        && path.startsWith("/auth/apk-open")
    return matchesCustomScheme || matchesHttpsHandoff
}

fun extractApkOAuthCode(uri: Uri?, publicWebHost: String): String? {
    if (uri == null || !matchesApkCallbackUri(uri, publicWebHost)) return null
    return uri.getQueryParameter("code")?.trim()?.takeIf { it.isNotBlank() }
}

/** The transaction state nonce that binds a consume code to its mobile-auth transaction. */
fun extractApkOAuthState(uri: Uri?, publicWebHost: String): String? {
    if (uri == null || !matchesApkCallbackUri(uri, publicWebHost)) return null
    return uri.getQueryParameter("state")?.trim()?.takeIf { it.isNotBlank() }
}

private fun encode(value: String): String =
    java.net.URLEncoder.encode(value, Charsets.UTF_8.name()).replace("+", "%20")

/** host+path only — never log query strings (they carry code/state secrets). */
fun redactUrl(url: String): String = runCatching {
    val u = Uri.parse(url)
    val host = u.host.orEmpty()
    val path = u.path.orEmpty()
    if (host.isBlank() && path.isBlank()) "(none)" else "$host$path"
}.getOrDefault("(unparseable)")

/**
 * Log the WebView cookie state for the public origin without leaking values:
 * only cookie NAMES and whether deng_sid is present.
 */
fun logWebViewCookieState(marker: String, publicWebUrl: String) {
    CookieManager.getInstance().flush()
    val base = publicWebUrl.trimEnd('/')
    val raw = CookieManager.getInstance().getCookie(base)
        ?: CookieManager.getInstance().getCookie(publicWebUrl).orEmpty()
    val names = raw.split(';')
        .mapNotNull { it.substringBefore('=').trim().takeIf { n -> n.isNotEmpty() } }
        .joinToString(",")
    val hasDengSid = raw.contains("deng_sid=")
    val host = runCatching { Uri.parse(base).host }.getOrNull().orEmpty()
    Log.i(
        APK_AUTH_LOG_TAG,
        "$marker cookieNames=[$names] hasDengSid=$hasDengSid domain=$host",
    )
}

/**
 * Build the first-party WebView session-bootstrap URL. Loading this in the
 * WebView lets aio.deng.my.id set the real `deng_sid` cookie via its own HTTP
 * response (303 -> target), instead of the app injecting a cookie natively.
 */
fun buildMobileConsumeUrl(
    publicWebUrl: String,
    code: String,
    state: String,
    target: String = "/tracker",
): String {
    val base = publicWebUrl.trimEnd('/')
    return "$base/mobile-auth/consume?code=${encode(code)}&state=${encode(state)}&target=${encode(target)}"
}

fun mapApkHandoffFailure(err: Throwable): String = when (err) {
    is ApiException -> when (err.statusCode) {
        401 -> "handoff_exchange_failed"
        500 -> if (err.safeMessage.contains("bootstrap", ignoreCase = true)) {
            "handoff_bootstrap_failed"
        } else {
            "exchange_failed"
        }
        else -> "exchange_failed"
    }
    else -> "handoff_error"
}

/**
 * Resolve the WebView bootstrap URL from a Discord deep-link callback.
 *
 * New first-party lane: when the callback carries a transaction `state`, build
 * the /mobile-auth/consume URL directly — the WebView loads it and the backend
 * sets the session cookie on its own 303 response (no native cookie injection,
 * no native token exchange). Falls back to the legacy exchange+bootstrap lane
 * for older backends that do not return a state.
 */
suspend fun completeApkOAuthFromDeepLink(
    api: MonitorApi,
    sessionStore: SessionStore,
    code: String,
    state: String,
): ApkOAuthHandoffResult {
    if (state.isNotBlank()) {
        val consumeUrl = buildMobileConsumeUrl(BuildConfig.PUBLIC_WEB_URL, code, state)
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_CONSUME_URL_BUILT marker=$APK_MOBILE_AUTH_MARKER ${redactUrl(consumeUrl)} codeLen=${code.length}",
        )
        return ApkOAuthHandoffResult.Ready(consumeUrl)
    }
    return completeApkOAuthLegacy(api, sessionStore, code)
}

private suspend fun completeApkOAuthLegacy(
    api: MonitorApi,
    sessionStore: SessionStore,
    code: String,
): ApkOAuthHandoffResult {
    return try {
        Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_START marker=$APK_AUTH_HANDOFF_MARKER codeLen=${code.length}")
        val exchange = api.aioAuthExchange(code)
        if (!exchange.ok || exchange.appSessionToken.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=exchange_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return ApkOAuthHandoffResult.Failed("handoff_exchange_failed")
        }
        sessionStore.saveSession(exchange.appSessionToken, exchange.user.discordUserId)
        val bootstrap = api.aioWebBootstrap(exchange.appSessionToken)
        if (!bootstrap.ok || bootstrap.bridgeUrl.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=bootstrap_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return ApkOAuthHandoffResult.Failed("handoff_bootstrap_failed")
        }
        sessionStore.setPendingWebBootstrapUrl(bootstrap.bridgeUrl)
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_HANDOFF_READY marker=$APK_AUTH_HANDOFF_MARKER bridge=${bootstrap.bridgeUrl.take(96)}",
        )
        ApkOAuthHandoffResult.Ready(bootstrap.bridgeUrl)
    } catch (err: Exception) {
        val reason = mapApkHandoffFailure(err)
        Log.w(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_FAIL_STAGE=$reason marker=$APK_AUTH_HANDOFF_MARKER err=${err.message}",
        )
        ApkOAuthHandoffResult.Failed(reason)
    }
}

fun webViewHasDengSidCookie(publicWebUrl: String): Boolean {
    CookieManager.getInstance().flush()
    val cookie = CookieManager.getInstance().getCookie(publicWebUrl.trimEnd('/'))
        ?: CookieManager.getInstance().getCookie(publicWebUrl)
    return cookie?.contains("deng_sid=") == true
}

suspend fun verifyApkWebSession(api: MonitorApi, publicWebUrl: String): Boolean {
    if (!webViewHasDengSidCookie(publicWebUrl)) {
        Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=web_bridge_cookie_missing")
        return false
    }
    // Authoritative first-party identity check inside the WebView cookie jar.
    val me = try {
        api.aioAuthMe(publicWebUrl)
    } catch (err: Exception) {
        Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_ME_RESULT status=error err=${err.message}")
        null
    }
    if (me == null || !me.ok || !me.authenticated) {
        Log.w(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_ME_RESULT status=unauthenticated ok=${me?.ok ?: false} authenticated=${me?.authenticated ?: false}",
        )
        return false
    }
    Log.i(
        APK_AUTH_LOG_TAG,
        "APK_AUTH_ME_RESULT status=200 marker=$APK_MOBILE_AUTH_MARKER discordUserId=${me.user?.discordUserId ?: "?"}",
    )
    return true
}

suspend fun finalizeApkWebSession(sessionStore: SessionStore, finishedUrl: String) {
    CookieManager.getInstance().flush()
    sessionStore.setWebLoggedIn(true)
    sessionStore.setPendingWebBootstrapUrl(null)
    Log.i(
        APK_AUTH_LOG_TAG,
        "APK_AUTH_WEBVIEW_OK marker=$APK_AUTH_HANDOFF_MARKER url=${finishedUrl.take(96)} build=${BuildConfig.APK_RELEASE_MARKER}",
    )
}

fun apkAuthFailureMessage(reason: String?): String = when (reason?.substringBefore(':')?.trim()) {
    "handoff_exchange_failed", "invalid_or_expired_code", "exchange_failed" ->
        "Sign-in link expired. Tap Discord login to try again."
    "handoff_bootstrap_failed", "bootstrap_failed", "bootstrap_timeout" ->
        "Could not start your session. Check your connection and retry."
    "cookie_not_seen_by_webview", "missing_session_cookie", "web_bridge_cookie_missing" ->
        "Signed in in browser but the app did not receive your session. Tap retry."
    "web_session_not_authenticated" ->
        "Session did not activate inside the app. Tap retry."
    "deep_link_not_received" ->
        "Discord sign-in finished in the browser but the app was not opened. Tap retry."
    "return_path_rejected", "state_invalid", "return_path_failed" ->
        "Login security check failed. Please try again."
    "handoff_error" ->
        "Discord sign-in did not finish in the app. Tap retry."
    else -> "Discord sign-in did not finish in the app. Tap retry."
}
