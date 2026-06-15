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
import androidx.compose.foundation.layout.statusBarsPadding
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
            android.util.Log.i(
                APK_AUTH_LOG_TAG,
                "APK_AUTH_CUSTOM_TAB_OPENED host=${runCatching { Uri.parse(startUrl).host }.getOrNull().orEmpty()}",
            )
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WebBg)
            .statusBarsPadding(),
    ) {
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
        android.util.Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_WEBVIEW_LOAD_CONSUME marker=$APK_MOBILE_AUTH_MARKER ${redactUrl(bridgeUrl)}",
        )
        delay(90_000)
        if (!finished) {
            android.util.Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_REASON=bootstrap_timeout")
            onFailure("bootstrap_timeout")
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WebBg)
            .statusBarsPadding(),
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
            onPageStarted = { url ->
                android.util.Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_WEBVIEW_PAGE_STARTED ${redactUrl(url)}")
            },
            onPageFinished = { url ->
                if (finished) return@AioWebViewScreen
                android.util.Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_WEBVIEW_PAGE_FINISHED ${redactUrl(url)}")
                val path = runCatching { Uri.parse(url).path.orEmpty() }.getOrDefault("")
                when {
                    path.startsWith("/login") -> {
                        finished = true
                        logWebViewCookieState("APK_AUTH_COOKIE_AFTER_CONSUME", publicWebUrl)
                        android.util.Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_REASON=web_bridge_cookie_missing (landed on /login)")
                        onFailure("web_bridge_cookie_missing")
                    }
                    isAuthenticatedWebUrl(url, publicHost) -> {
                        // The consume bridge has already verified /api/aio/auth/me and
                        // redirected here; double-check from native before unlocking.
                        scope.launch {
                            logWebViewCookieState("APK_AUTH_COOKIE_AFTER_CONSUME", publicWebUrl)
                            if (!verifyApkWebSession(api, publicWebUrl)) {
                                finished = true
                                val reason = if (webViewHasDengSidCookie(publicWebUrl)) {
                                    "web_session_not_authenticated"
                                } else {
                                    "web_bridge_cookie_missing"
                                }
                                android.util.Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_REASON=$reason")
                                onFailure(reason)
                                return@launch
                            }
                            finished = true
                            android.util.Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_FINAL_TRACKER_URL ${redactUrl(url)}")
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
