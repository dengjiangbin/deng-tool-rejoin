package my.id.deng.monitor.ui

import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import my.id.deng.monitor.BuildConfig
import android.util.Log
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

@Composable
fun LoginWebViewScreen(
    api: MonitorApi,
    sessionStore: SessionStore,
    onLoggedIn: () -> Unit,
) {
    val loginUrl = remember { aioWebUrl("/login") }
    val publicHost = remember { publicWebHost() }
    val context = LocalContext.current
    fun openExternalOAuth() {
        val customTabs = CustomTabsIntent.Builder().build()
        customTabs.launchUrl(context, Uri.parse(apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL)))
    }

    AioWebViewScreen(
        startUrl = loginUrl,
        onUrlChanged = { url ->
            if (isExternalOAuthUrl(url, publicHost)) {
                openExternalOAuth()
            }
        },
        shouldOverrideUrl = { url ->
            if (isExternalOAuthUrl(url, publicHost)) {
                openExternalOAuth()
                true
            } else {
                false
            }
        },
    )
}

fun publicWebHost(): String = runCatching {
    Uri.parse(BuildConfig.PUBLIC_WEB_URL).host.orEmpty()
}.getOrDefault("aio.deng.my.id")

private const val APK_AUTH_LOG_TAG = "DengApkAuth"
private const val APK_AUTH_HANDOFF_MARKER = "APK_DISCORD_AUTH_HANDOFF_FIX_2026_06_14"

suspend fun completeApkOAuthFromDeepLink(
    api: MonitorApi,
    sessionStore: SessionStore,
    code: String,
): Boolean {
    return try {
        Log.i(APK_AUTH_LOG_TAG, "handoff_start marker=$APK_AUTH_HANDOFF_MARKER")
        val exchange = api.aioAuthExchange(code)
        if (!exchange.ok || exchange.appSessionToken.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "handoff_exchange_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return false
        }
        sessionStore.saveSession(exchange.appSessionToken, exchange.user.discordUserId)
        val bootstrap = api.aioWebBootstrap(exchange.appSessionToken)
        if (!bootstrap.ok || bootstrap.bridgeUrl.isBlank()) {
            Log.w(APK_AUTH_LOG_TAG, "handoff_bootstrap_failed marker=$APK_AUTH_HANDOFF_MARKER")
            return false
        }
        sessionStore.setPendingWebBootstrapUrl(bootstrap.bridgeUrl)
        sessionStore.setWebLoggedIn(true)
        Log.i(
            APK_AUTH_LOG_TAG,
            "handoff_ok marker=$APK_AUTH_HANDOFF_MARKER bridge=${bootstrap.bridgeUrl.take(80)}",
        )
        true
    } catch (err: Exception) {
        Log.w(APK_AUTH_LOG_TAG, "handoff_error marker=$APK_AUTH_HANDOFF_MARKER err=${err.message}")
        false
    }
}
