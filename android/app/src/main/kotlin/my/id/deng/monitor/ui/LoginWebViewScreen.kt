package my.id.deng.monitor.ui

import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.launch
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

private fun apkOAuthStartUrl(): String {
    val bridge = BuildConfig.BRIDGE_URL.trimEnd('/')
    return "$bridge/auth/discord?apk=1&public_return=1"
}

private fun isDiscordOAuthUrl(url: String): Boolean {
    val uri = runCatching { Uri.parse(url) }.getOrNull() ?: return false
    val path = uri.path.orEmpty()
    return path.startsWith("/auth/discord")
}

@Composable
fun LoginWebViewScreen(
    api: MonitorApi,
    sessionStore: SessionStore,
    onLoggedIn: () -> Unit,
) {
    val loginUrl = remember { aioWebUrl("/login") }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var handled by remember { mutableStateOf(false) }

    fun openExternalOAuth() {
        val customTabs = CustomTabsIntent.Builder().build()
        customTabs.launchUrl(context, Uri.parse(apkOAuthStartUrl()))
    }

    fun markLoggedIn(url: String) {
        if (handled) return
        if (!isAuthenticatedWebUrl(url, publicWebHost())) return
        handled = true
        scope.launch {
            sessionStore.setWebLoggedIn(true)
            onLoggedIn()
        }
    }

    AioWebViewScreen(
        startUrl = loginUrl,
        onUrlChanged = { url ->
            if (isDiscordOAuthUrl(url)) {
                openExternalOAuth()
                return@AioWebViewScreen
            }
            markLoggedIn(url)
        },
        onPageFinished = ::markLoggedIn,
        shouldOverrideUrl = { url ->
            if (isDiscordOAuthUrl(url)) {
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

suspend fun completeApkOAuthFromDeepLink(
    api: MonitorApi,
    sessionStore: SessionStore,
    code: String,
): Boolean {
    return try {
        val exchange = api.aioAuthExchange(code)
        if (!exchange.ok || exchange.appSessionToken.isBlank()) return false
        sessionStore.saveSession(exchange.appSessionToken, exchange.user.discordUserId)
        val bootstrap = api.aioWebBootstrap(exchange.appSessionToken)
        if (!bootstrap.ok || bootstrap.bridgeUrl.isBlank()) return false
        sessionStore.setPendingWebBootstrapUrl(bootstrap.bridgeUrl)
        sessionStore.setWebLoggedIn(true)
        true
    } catch (_: Exception) {
        false
    }
}
