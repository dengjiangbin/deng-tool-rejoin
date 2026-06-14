package my.id.deng.monitor.ui

import androidx.compose.runtime.Composable

@Composable
fun DashboardWebViewScreen() {
    AioWebViewScreen(startUrl = aioWebUrl("/dashboard"))
}
