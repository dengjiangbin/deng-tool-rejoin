package my.id.deng.monitor.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import my.id.deng.monitor.data.SessionStore

@Composable
fun LiveTrackerWebViewScreen(sessionStore: SessionStore? = null) {
    var startUrl by remember { mutableStateOf(aioWebUrl("/tracker")) }
    LaunchedEffect(sessionStore) {
        val bootstrap = sessionStore?.consumePendingWebBootstrapUrl()
        if (!bootstrap.isNullOrBlank()) startUrl = bootstrap
    }
    AioWebViewScreen(startUrl = startUrl)
}
