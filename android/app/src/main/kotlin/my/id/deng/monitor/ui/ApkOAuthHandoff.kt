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

sealed class ApkOAuthHandoffResult {
    data class Ready(val bridgeUrl: String) : ApkOAuthHandoffResult()
    data class Failed(val reason: String) : ApkOAuthHandoffResult()
}

fun extractApkOAuthCode(uri: Uri?, publicWebHost: String): String? {
    if (uri == null) return null
    val scheme = uri.scheme?.lowercase().orEmpty()
    val host = uri.host?.lowercase().orEmpty()
    val path = uri.path.orEmpty()
    val matchesCustomScheme = scheme == BuildConfig.DENG_AIO_APP_SCHEME.lowercase()
        && host == "auth"
        && (path == "/callback" || path.startsWith("/callback/"))
    val matchesHttpsHandoff = (scheme == "http" || scheme == "https")
        && host == publicWebHost.lowercase()
        && path.startsWith("/auth/apk-open")
    if (!matchesCustomScheme && !matchesHttpsHandoff) return null
    return uri.getQueryParameter("code")?.trim()?.takeIf { it.isNotBlank() }
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

suspend fun completeApkOAuthFromDeepLink(
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
    val probe = api.aioWebSession(publicWebUrl)
    if (!probe.ok || !probe.authenticated) {
        Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=web_session_not_authenticated")
        return false
    }
    Log.i(
        APK_AUTH_LOG_TAG,
        "APK_AUTH_WEB_SESSION_OK marker=${probe.handoffMarker ?: APK_AUTH_HANDOFF_MARKER} discordUserId=${probe.discordUserId ?: "?"}",
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
