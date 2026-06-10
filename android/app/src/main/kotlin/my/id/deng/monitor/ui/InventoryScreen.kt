package my.id.deng.monitor.ui

import android.annotation.SuppressLint
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.ui.theme.DengColors

private val InventoryBg = Color(0xFF0D0F14)

@Composable
private fun InventoryLoadingSkeleton(modifier: Modifier = Modifier) {
    Column(modifier = modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        repeat(4) { row ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                repeat(2) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .height(104.dp)
                            .background(DengColors.CardSoft, RoundedCornerShape(10.dp)),
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun InventoryScreen(api: MonitorApi) {
    val inventoryUrl = remember(api.baseUrl) {
        api.baseUrl.trimEnd('/') + "/inventory?apk=1"
    }

    var webLoaded by remember { mutableStateOf(false) }
    var offlineNotice by remember { mutableStateOf<String?>(null) }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(InventoryBg),
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                Text(
                    "Inventory",
                    style = MaterialTheme.typography.headlineMedium,
                    color = DengColors.TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    "Fish and stone inventory across watched accounts.",
                    style = MaterialTheme.typography.bodySmall,
                    color = DengColors.TextMuted,
                )
                offlineNotice?.let { notice ->
                    Spacer(Modifier.height(8.dp))
                    Text(
                        notice,
                        style = MaterialTheme.typography.bodySmall,
                        color = DengColors.Warning,
                    )
                }
            }

            Box(modifier = Modifier.fillMaxSize()) {
                if (!webLoaded) {
                    InventoryLoadingSkeleton()
                }
                AndroidView(
                    modifier = Modifier
                        .fillMaxSize()
                        .alpha(if (webLoaded) 1f else 0f),
                    factory = { ctx ->
                        WebView(ctx).apply {
                            setBackgroundColor(android.graphics.Color.parseColor("#0D0F14"))
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.loadsImagesAutomatically = true
                            webViewClient = object : WebViewClient() {
                                override fun shouldOverrideUrlLoading(
                                    view: WebView?,
                                    request: WebResourceRequest?,
                                ): Boolean {
                                    val url = request?.url?.toString().orEmpty()
                                    if (url.startsWith("http://") || url.startsWith("https://")) {
                                        view?.loadUrl(url)
                                        return true
                                    }
                                    return false
                                }

                                override fun onPageFinished(view: WebView?, url: String?) {
                                    webLoaded = true
                                    offlineNotice = null
                                }

                                override fun onReceivedError(
                                    view: WebView?,
                                    errorCode: Int,
                                    description: String?,
                                    failingUrl: String?,
                                ) {
                                    if (!webLoaded) {
                                        offlineNotice = "Could not refresh inventory. Showing cached data if available."
                                    }
                                }
                            }
                            loadUrl(inventoryUrl)
                        }
                    },
                    update = { webView ->
                        webView.setBackgroundColor(android.graphics.Color.parseColor("#0D0F14"))
                        if (webView.url.isNullOrBlank()) webView.loadUrl(inventoryUrl)
                    },
                )
            }
        }
    }
}
