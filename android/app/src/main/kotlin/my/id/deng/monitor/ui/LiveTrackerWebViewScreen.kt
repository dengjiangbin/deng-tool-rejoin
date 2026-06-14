package my.id.deng.monitor.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import my.id.deng.monitor.data.SessionStore

private val WebBg = Color(0xFF0D0F14)

@Composable
fun LiveTrackerWebViewScreen(sessionStore: SessionStore? = null) {
    var startUrl by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(sessionStore) {
        val bootstrap = sessionStore?.consumePendingWebBootstrapUrl()
        startUrl = if (!bootstrap.isNullOrBlank()) bootstrap else aioWebUrl("/tracker")
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(WebBg),
    ) {
        val url = startUrl
        if (url != null) {
            AioWebViewScreen(startUrl = url)
        }
    }
}
