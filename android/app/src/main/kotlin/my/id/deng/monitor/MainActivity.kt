package my.id.deng.monitor

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
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
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import my.id.deng.monitor.data.ThemeMode
import my.id.deng.monitor.ui.AioWebViewNavigator
import my.id.deng.monitor.ui.APK_AUTH_LOG_TAG
import my.id.deng.monitor.ui.AppRoot
import my.id.deng.monitor.ui.ApkOAuthHandoffResult
import my.id.deng.monitor.ui.LocalHideUsername
import my.id.deng.monitor.ui.completeApkOAuthFromDeepLink
import my.id.deng.monitor.ui.extractApkOAuthCode
import my.id.deng.monitor.ui.publicWebHost
import my.id.deng.monitor.ui.theme.DengMonitorTheme

class MainActivity : ComponentActivity() {
    private var pendingOAuthCode by mutableStateOf<String?>(null)
    private var bootstrapBridgeUrl by mutableStateOf<String?>(null)
    private var authError by mutableStateOf<String?>(null)
    private var oauthWaitingDeepLink by mutableStateOf(false)
    private val processedOAuthCodes = LinkedHashSet<String>()
    private val handoffMutex = Mutex()

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
                if (processedOAuthCodes.contains(code)) {
                    Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_DEDUP codeLen=${code.length}")
                    return@LaunchedEffect
                }
                processedOAuthCodes.add(code)
                while (processedOAuthCodes.size > 24) {
                    val oldest = processedOAuthCodes.first()
                    processedOAuthCodes.remove(oldest)
                }

                oauthWaitingDeepLink = false
                handoffMutex.withLock {
                    authError = null
                    when (val result = completeApkOAuthFromDeepLink(app.api, app.sessionStore, code)) {
                        is ApkOAuthHandoffResult.Ready -> {
                            bootstrapBridgeUrl = result.bridgeUrl
                        }
                        is ApkOAuthHandoffResult.Failed -> {
                            authError = result.reason
                        }
                    }
                }
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
                        bootstrapBridgeUrl = bootstrapBridgeUrl,
                        authError = authError,
                        onBootstrapSuccess = {
                            bootstrapBridgeUrl = null
                            authError = null
                            oauthWaitingDeepLink = false
                        },
                        onBootstrapFailure = { reason ->
                            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=$reason")
                            bootstrapBridgeUrl = null
                            authError = reason
                            oauthWaitingDeepLink = false
                        },
                        onClearAuthError = { authError = null },
                        onOAuthFlowStarted = {
                            oauthWaitingDeepLink = true
                            authError = null
                        },
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

    override fun onResume() {
        super.onResume()
        if (!oauthWaitingDeepLink || pendingOAuthCode != null || bootstrapBridgeUrl != null) return
        val app = application as MonitorApp
        if (app.sessionStore.isWebLoggedInBlocking()) {
            oauthWaitingDeepLink = false
            return
        }
        window.decorView.postDelayed({
            if (oauthWaitingDeepLink
                && pendingOAuthCode == null
                && bootstrapBridgeUrl == null
                && !app.sessionStore.isWebLoggedInBlocking()
            ) {
                oauthWaitingDeepLink = false
                authError = "deep_link_not_received"
                Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=deep_link_not_received")
            }
        }, 2500)
    }

    private fun captureOAuthDeepLink(intent: Intent?) {
        val uri = intent?.data ?: return
        val code = extractApkOAuthCode(uri, publicWebHost()) ?: return
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_CALLBACK_RECEIVED codeLen=${code.length} scheme=${uri.scheme} host=${uri.host} path=${uri.path}",
        )
        oauthWaitingDeepLink = false
        pendingOAuthCode = code
    }
}
