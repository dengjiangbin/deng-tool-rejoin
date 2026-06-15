package my.id.deng.monitor.ui

import android.annotation.SuppressLint
import android.net.Uri
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.viewinterop.AndroidView
import my.id.deng.monitor.BuildConfig

/** Shared WebView holder so MainActivity can delegate the system back button. */
object AioWebViewNavigator {
    @Volatile
    var canGoBack: () -> Boolean = { false }

    @Volatile
    var goBack: () -> Unit = {}

    @Volatile
    var reload: () -> Unit = {}
}

private val WebBg = Color(0xFF0D0F14)

fun isAuthenticatedWebUrl(url: String, publicWebHost: String): Boolean {
    val uri = runCatching { Uri.parse(url) }.getOrNull() ?: return false
    val host = uri.host?.lowercase().orEmpty()
    if (host.isNotBlank() && host != publicWebHost.lowercase()) return false
    val path = uri.path.orEmpty()
    if (path.isBlank() || path == "/") return false
    if (path.startsWith("/login")) return false
    if (path.startsWith("/auth/")) return false
    return path.startsWith("/dashboard")
        || path.startsWith("/tracker")
        || path.startsWith("/inventory")
        || path.startsWith("/fishit")
        || path.startsWith("/download")
        || path.startsWith("/license")
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun AioWebViewScreen(
    startUrl: String,
    modifier: Modifier = Modifier,
    onUrlChanged: ((String) -> Unit)? = null,
    onPageStarted: ((String) -> Unit)? = null,
    onPageFinished: ((String) -> Unit)? = null,
    shouldOverrideUrl: ((String) -> Boolean)? = null,
) {
    val publicHost = remember {
        runCatching {
            Uri.parse(BuildConfig.PUBLIC_WEB_URL).host.orEmpty()
        }.getOrDefault("aio.deng.my.id")
    }

    var webViewRef by remember { mutableStateOf<WebView?>(null) }

    DisposableEffect(webViewRef) {
        val webView = webViewRef
        if (webView != null) {
            AioWebViewNavigator.canGoBack = { webView.canGoBack() }
            AioWebViewNavigator.goBack = {
                if (webView.canGoBack()) webView.goBack()
            }
            AioWebViewNavigator.reload = { webView.reload() }
        }
        onDispose {
            AioWebViewNavigator.canGoBack = { false }
            AioWebViewNavigator.goBack = {}
            AioWebViewNavigator.reload = {}
        }
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(WebBg),
    ) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { ctx ->
                CookieManager.getInstance().setAcceptCookie(true)
                WebView(ctx).apply {
                    setBackgroundColor(android.graphics.Color.parseColor("#0D0F14"))
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.loadsImagesAutomatically = true
                    settings.databaseEnabled = true
                    // First-party aio.deng.my.id session; enable own-cookie storage.
                    // Accept third-party too (harmless here) but the flow does not depend on it.
                    CookieManager.getInstance().setAcceptCookie(true)
                    CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                    webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(
                            view: WebView?,
                            request: WebResourceRequest?,
                        ): Boolean {
                            val url = request?.url?.toString().orEmpty()
                            if (shouldOverrideUrl?.invoke(url) == true) {
                                return true
                            }
                            if (url.startsWith("http://") || url.startsWith("https://")) {
                                onUrlChanged?.invoke(url)
                                // false = let WebView handle redirects so Set-Cookie survives.
                                return false
                            }
                            return false
                        }

                        override fun onPageStarted(
                            view: WebView?,
                            url: String?,
                            favicon: android.graphics.Bitmap?,
                        ) {
                            onPageStarted?.invoke(url.orEmpty())
                        }

                        override fun onPageFinished(view: WebView?, url: String?) {
                            val finished = url.orEmpty()
                            onUrlChanged?.invoke(finished)
                            onPageFinished?.invoke(finished)
                            CookieManager.getInstance().flush()
                        }
                    }
                    loadUrl(startUrl)
                    webViewRef = this
                }
            },
            update = { webView ->
                webView.setBackgroundColor(android.graphics.Color.parseColor("#0D0F14"))
                CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
                if (webView.url.isNullOrBlank()) webView.loadUrl(startUrl)
                webViewRef = webView
            },
        )
    }
}

fun aioWebUrl(path: String): String {
    val base = BuildConfig.PUBLIC_WEB_URL.trimEnd('/')
    val normalized = if (path.startsWith("/")) path else "/$path"
    val separator = if (normalized.contains('?')) "&" else "?"
    return "$base$normalized${separator}apk=1"
}
