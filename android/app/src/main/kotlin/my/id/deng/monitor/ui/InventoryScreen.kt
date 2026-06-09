package my.id.deng.monitor.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.ui.theme.DengColors

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun InventoryScreen(api: MonitorApi) {
    val inventoryUrl = remember(api.baseUrl) {
        api.baseUrl.trimEnd('/') + "/tracker"
    }

    val context = LocalContext.current
    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(16.dp))
        Text(
            "Inventory",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            "Live fish and stone inventory from playerdata_gameitemdb.",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
        Spacer(Modifier.height(10.dp))
        OutlinedButton(
            onClick = {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(inventoryUrl)))
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Open in website")
        }
        Spacer(Modifier.height(10.dp))
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { context ->
                WebView(context).apply {
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
                    }
                    loadUrl(inventoryUrl)
                }
            },
            update = { webView ->
                if (webView.url.isNullOrBlank()) webView.loadUrl(inventoryUrl)
            },
        )
    }
}
