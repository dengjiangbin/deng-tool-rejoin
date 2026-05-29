package my.id.deng.monitor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import my.id.deng.monitor.data.ThemeMode
import my.id.deng.monitor.ui.AppRoot
import my.id.deng.monitor.ui.LocalHideUsername
import my.id.deng.monitor.ui.theme.DengMonitorTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = application as MonitorApp
        setContent {
            val token by app.sessionStore.tokenFlow.collectAsState(initial = null)
            val themeMode by app.appPreferences.themeModeFlow.collectAsState(initial = ThemeMode.SYSTEM)
            val hideUsername by app.appPreferences.hideUsernameFlow.collectAsState(initial = false)

            val systemDark = isSystemInDarkTheme()
            val darkTheme = when (themeMode) {
                ThemeMode.LIGHT -> false
                ThemeMode.DARK -> true
                ThemeMode.SYSTEM -> systemDark
            }

            DengMonitorTheme(darkTheme = darkTheme) {
                CompositionLocalProvider(LocalHideUsername provides hideUsername) {
                    AppRoot(
                        api = app.api,
                        sessionStore = app.sessionStore,
                        appPreferences = app.appPreferences,
                        isPaired = !token.isNullOrBlank(),
                    )
                }
            }
        }
    }
}
