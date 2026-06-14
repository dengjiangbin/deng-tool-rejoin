package my.id.deng.monitor

import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.addCallback
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import my.id.deng.monitor.data.ThemeMode
import my.id.deng.monitor.ui.AppRoot
import my.id.deng.monitor.ui.AioWebViewNavigator
import my.id.deng.monitor.ui.LocalHideUsername
import my.id.deng.monitor.ui.completeApkOAuthFromDeepLink
import my.id.deng.monitor.ui.theme.DengMonitorTheme

class MainActivity : ComponentActivity() {
    private var pendingOAuthCode by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        captureOAuthDeepLink(intent)

        CookieManager.getInstance().setAcceptCookie(true)

        val app = application as MonitorApp
        setContent {
            val pendingCode = pendingOAuthCode
            LaunchedEffect(pendingCode) {
                val code = pendingCode ?: return@LaunchedEffect
                pendingOAuthCode = null
                completeApkOAuthFromDeepLink(app.api, app.sessionStore, code)
            }

            val webLoggedIn by app.sessionStore.webLoggedInFlow.collectAsState(
                initial = app.sessionStore.isWebLoggedInBlocking(),
            )
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
                        isLoggedIn = webLoggedIn,
                    )
                }
            }
        }

        onBackPressedDispatcher.addCallback(this) {
            if (AioWebViewNavigator.canGoBack()) {
                AioWebViewNavigator.goBack()
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
                isEnabled = true
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        captureOAuthDeepLink(intent)
    }

    private fun captureOAuthDeepLink(intent: Intent?) {
        val uri = intent?.data ?: return
        if (uri.scheme != BuildConfig.DENG_AIO_APP_SCHEME) return
        if (uri.host != "auth" || uri.path != "/callback") return
        val code = uri.getQueryParameter("code")?.trim().orEmpty()
        if (code.isNotBlank()) pendingOAuthCode = code
    }
}
