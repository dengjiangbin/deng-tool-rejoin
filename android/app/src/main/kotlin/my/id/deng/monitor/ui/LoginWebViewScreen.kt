package my.id.deng.monitor.ui

import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.ui.DengGradientButton

private val WebBg = Color(0xFF0D0F14)

@Composable
fun LoginWebViewScreen(
    sessionStore: SessionStore,
    authError: String?,
    onClearAuthError: () -> Unit,
    onOAuthFlowStarted: () -> Unit = {},
    onResolveOAuthStartUrl: suspend () -> String = { apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL) },
) {
    val loginUrl = remember { aioWebUrl("/login") }
    val publicHost = remember { publicWebHost() }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    fun openExternalOAuth() {
        onClearAuthError()
        onOAuthFlowStarted()
        scope.launch {
            // Resolve the start URL first: this begins a mobile-auth transaction
            // (so the WebView can later load /mobile-auth/consume) and only then
            // hands Discord OAuth to the system browser / Custom Tabs.
            val startUrl = runCatching { onResolveOAuthStartUrl() }
                .getOrDefault(apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL))
                .ifBlank { apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL) }
            val customTabs = CustomTabsIntent.Builder().build()
            customTabs.launchUrl(context, Uri.parse(startUrl))
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
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

        if (!authError.isNullOrBlank()) {
            AuthErrorOverlay(
                message = apkAuthFailureMessage(authError),
                onRetry = {
                    onClearAuthError()
                    openExternalOAuth()
                },
            )
        }
    }
}

@Composable
fun ApkAuthBootstrapScreen(
    bridgeUrl: String,
    api: MonitorApi,
    sessionStore: SessionStore,
    onSuccess: () -> Unit,
    onFailure: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val publicHost = remember { publicWebHost() }
    val publicWebUrl = remember { BuildConfig.PUBLIC_WEB_URL.trimEnd('/') }
    var finished by remember(bridgeUrl) { mutableStateOf(false) }

    LaunchedEffect(bridgeUrl) {
        delay(90_000)
        if (!finished) {
            onFailure("bootstrap_timeout")
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WebBg),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(color = DengColors.Cyan)
            Spacer(Modifier.height(16.dp))
            Text(
                "Finishing Discord sign-in…",
                color = DengColors.TextPrimary,
                style = MaterialTheme.typography.bodyLarge,
            )
        }

        AioWebViewScreen(
            startUrl = bridgeUrl,
            modifier = Modifier.fillMaxSize(),
            onPageFinished = { url ->
                if (finished) return@AioWebViewScreen
                val path = runCatching { Uri.parse(url).path.orEmpty() }.getOrDefault("")
                when {
                    path.startsWith("/login") -> {
                        finished = true
                        onFailure("web_bridge_cookie_missing")
                    }
                    isAuthenticatedWebUrl(url, publicHost) -> {
                        scope.launch {
                            if (!verifyApkWebSession(api, publicWebUrl)) {
                                finished = true
                                onFailure(
                                    if (webViewHasDengSidCookie(publicWebUrl)) {
                                        "web_session_not_authenticated"
                                    } else {
                                        "web_bridge_cookie_missing"
                                    },
                                )
                                return@launch
                            }
                            finished = true
                            finalizeApkWebSession(sessionStore, url)
                            onSuccess()
                        }
                    }
                }
            },
        )
    }
}

@Composable
private fun AuthErrorOverlay(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC0D0F14))
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            message,
            color = DengColors.TextPrimary,
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyLarge,
        )
        Spacer(Modifier.height(16.dp))
        DengGradientButton(text = "Retry Discord login", onClick = onRetry)
    }
}

fun publicWebHost(): String = runCatching {
    Uri.parse(BuildConfig.PUBLIC_WEB_URL).host.orEmpty()
}.getOrDefault("aio.deng.my.id")
