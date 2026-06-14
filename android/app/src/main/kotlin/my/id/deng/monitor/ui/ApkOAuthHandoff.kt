package my.id.deng.monitor.ui

import android.util.Log
import android.webkit.CookieManager
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

const val APK_AUTH_LOG_TAG = "DengApkAuth"
const val APK_AUTH_HANDOFF_MARKER = "APK_DISCORD_AUTH_LOGIN_LOOP_REAL_FIX_2026_06_14"

sealed class ApkOAuthHandoffResult {
    data class Ready(val bridgeUrl: String) : ApkOAuthHandoffResult()
    data class Failed(val reason: String) : ApkOAuthHandoffResult()
}

suspend fun completeApkOAuthFromDeepLink(
    api: MonitorApi,
    sessionStore: SessionStore,
    code: String,
): ApkOAuthHandoffResult {
    return try {
        Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_START marker=$APK_AUTH_HANDOFF_MARKER")
        val exchange = api.aioAuthExchange(code)
        if (!exchange.ok || exchange.appSessionToken.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL reason=handoff_exchange_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return ApkOAuthHandoffResult.Failed("handoff_exchange_failed")
        }
        sessionStore.saveSession(exchange.appSessionToken, exchange.user.discordUserId)
        val bootstrap = api.aioWebBootstrap(exchange.appSessionToken)
        if (!bootstrap.ok || bootstrap.bridgeUrl.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL reason=handoff_bootstrap_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return ApkOAuthHandoffResult.Failed("handoff_bootstrap_failed")
        }
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_HANDOFF_READY marker=$APK_AUTH_HANDOFF_MARKER bridge=${bootstrap.bridgeUrl.take(96)}",
        )
        ApkOAuthHandoffResult.Ready(bootstrap.bridgeUrl)
    } catch (err: Exception) {
        Log.w(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_FAIL reason=handoff_error marker=$APK_AUTH_HANDOFF_MARKER err=${err.message}",
        )
        ApkOAuthHandoffResult.Failed(err.message ?: "handoff_error")
    }
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

fun apkAuthFailureMessage(reason: String?): String = when (reason?.substringBefore(':')) {
    "handoff_exchange_failed", "invalid_or_expired_code" -> "Sign-in link expired. Tap Discord login to try again."
    "handoff_bootstrap_failed", "bootstrap_failed" -> "Could not start your session. Check your connection and retry."
    "cookie_not_seen_by_webview", "missing_session_cookie" -> "Signed in in browser but the app did not receive your session. Tap retry."
    "return_path_rejected", "state_invalid" -> "Login security check failed. Please try again."
    else -> "Discord sign-in did not finish in the app. Tap retry."
}
