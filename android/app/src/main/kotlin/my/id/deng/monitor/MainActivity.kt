package my.id.deng.monitor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import my.id.deng.monitor.ui.AppRoot
import my.id.deng.monitor.ui.theme.DengMonitorTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as MonitorApp
        setContent {
            DengMonitorTheme {
                val token by app.sessionStore.tokenFlow.collectAsState(initial = null)
                AppRoot(
                    api = app.api,
                    sessionStore = app.sessionStore,
                    isPaired = !token.isNullOrBlank(),
                )
            }
        }
    }
}
