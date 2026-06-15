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
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import my.id.deng.monitor.data.ThemeMode
import my.id.deng.monitor.ui.AioWebViewNavigator
import my.id.deng.monitor.ui.APK_AUTH_LOG_TAG
import my.id.deng.monitor.ui.APK_MOBILE_AUTH_MARKER
import my.id.deng.monitor.ui.AppRoot
import my.id.deng.monitor.ui.ApkOAuthHandoffResult
import my.id.deng.monitor.ui.LocalHideUsername
import my.id.deng.monitor.ui.apkOAuthStartUrl
import my.id.deng.monitor.ui.completeApkOAuthFromDeepLink
import my.id.deng.monitor.ui.extractApkOAuthCode
import my.id.deng.monitor.ui.extractApkOAuthState
import my.id.deng.monitor.ui.publicWebHost
import my.id.deng.monitor.ui.theme.DengMonitorTheme

class MainActivity : ComponentActivity() {
    private var pendingOAuthCode by mutableStateOf<String?>(null)
    private var pendingOAuthState by mutableStateOf<String?>(null)
    private var bootstrapBridgeUrl by mutableStateOf<String?>(null)
    private var authError by mutableStateOf<String?>(null)
    private var oauthWaitingDeepLink by mutableStateOf(false)
    private val processedOAuthCodes = LinkedHashSet<String>()
    private val handoffMutex = Mutex()

    // Mobile-auth transaction (first-party WebView session bootstrap).
    @Volatile private var mobileTxnId: String? = null
    @Volatile private var mobileState: String? = null
    private var pollJob: Job? = null

    // Success/failure are mutually exclusive. Once /api/aio/auth/me=200 verifies
    // the WebView session, the current login attempt is LOCKED as success and no
    // later timeout/poll/deeplink/failure handler may flip it to a failure UI.
    @Volatile private var authSucceeded = false

    /** Set a login failure ONLY when this attempt has not already succeeded. */
    private fun reportAuthFailure(reason: String) {
        if (authSucceeded) {
            Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_FAILURE_SUPPRESSED_AFTER_SUCCESS reason=$reason")
            return
        }
        authError = reason
    }

    /** Cancel every pending job/timer that could still emit a failure. */
    private fun cancelAuthFollowups() {
        pollJob?.cancel()
        pollJob = null
    }

    /** Reorder our (singleTask) task above the Chrome Custom Tab so the user
     *  returns to the app automatically — no manual browser closing. */
    private fun bringAppToForeground() {
        runCatching {
            val intent = Intent(this, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                        or Intent.FLAG_ACTIVITY_SINGLE_TOP
                        or Intent.FLAG_ACTIVITY_NEW_TASK,
                )
            }
            startActivity(intent)
            Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_BRING_TO_FOREGROUND")
        }
    }

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
                val stateNonce = pendingOAuthState.orEmpty()
                pendingOAuthCode = null
                pendingOAuthState = null
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
                    if (!authSucceeded) authError = null
                    when (val result = completeApkOAuthFromDeepLink(app.api, app.sessionStore, code, stateNonce)) {
                        is ApkOAuthHandoffResult.Ready -> {
                            cancelAuthFollowups()
                            // Deep link path: ensure the app is on top of the Custom Tab.
                            bringAppToForeground()
                            bootstrapBridgeUrl = result.bridgeUrl
                        }
                        is ApkOAuthHandoffResult.Failed -> {
                            reportAuthFailure(result.reason)
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
                            // LOCK success: auth/me verified in the WebView. From here
                            // no failure UI may appear for this attempt.
                            authSucceeded = true
                            Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_SUCCESS_LOCKED")
                            cancelAuthFollowups()
                            mobileTxnId = null
                            mobileState = null
                            bootstrapBridgeUrl = null
                            authError = null
                            oauthWaitingDeepLink = false
                        },
                        onBootstrapFailure = { reason ->
                            cancelAuthFollowups()
                            bootstrapBridgeUrl = null
                            oauthWaitingDeepLink = false
                            if (authSucceeded) {
                                Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_FAILURE_SUPPRESSED_AFTER_SUCCESS reason=$reason")
                            } else {
                                Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=$reason")
                                authError = reason
                            }
                        },
                        onClearAuthError = { authError = null },
                        onOAuthFlowStarted = {
                            // Fresh attempt — re-arm so logout/login works repeatedly.
                            authSucceeded = false
                            oauthWaitingDeepLink = true
                            authError = null
                        },
                        onResolveOAuthStartUrl = { resolveOAuthStartUrl(app) },
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
        if (authSucceeded) return
        if (!oauthWaitingDeepLink || pendingOAuthCode != null || bootstrapBridgeUrl != null) return
        val app = application as MonitorApp
        if (app.sessionStore.isWebLoggedInBlocking()) {
            oauthWaitingDeepLink = false
            return
        }
        // With an active mobile-auth transaction, status polling drives the
        // login even when the deep link is never delivered — never false-fail.
        if (mobileTxnId != null) return
        window.decorView.postDelayed({
            if (!authSucceeded
                && oauthWaitingDeepLink
                && pendingOAuthCode == null
                && bootstrapBridgeUrl == null
                && mobileTxnId == null
                && !app.sessionStore.isWebLoggedInBlocking()
            ) {
                oauthWaitingDeepLink = false
                reportAuthFailure("deep_link_not_received")
                Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_FAIL_STAGE=deep_link_not_received")
            }
        }, 2500)
    }

    /**
     * Begin a mobile-auth transaction and return the Discord OAuth URL to open
     * in the system browser. Stores {transactionId, state} and starts polling so
     * login completes via the first-party /mobile-auth/consume URL even if the
     * deep link is not delivered. Falls back to the legacy start URL on error.
     */
    private suspend fun resolveOAuthStartUrl(app: MonitorApp): String {
        return try {
            val started = app.api.mobileAuthStart("/tracker?apk=1")
            if (started.ok && started.authUrl.isNotBlank() && started.transactionId.isNotBlank()) {
                mobileTxnId = started.transactionId
                mobileState = started.state
                val authHost = runCatching { Uri.parse(started.authUrl).host }.getOrNull().orEmpty()
                Log.i(
                    APK_AUTH_LOG_TAG,
                    "APK_AUTH_START marker=$APK_MOBILE_AUTH_MARKER txn=${started.transactionId.take(8)} stateLen=${started.state.length} authUrlHost=$authHost",
                )
                startStatusPolling(app, started.transactionId, started.state)
                started.authUrl
            } else {
                mobileTxnId = null
                mobileState = null
                apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL)
            }
        } catch (err: Exception) {
            Log.w(APK_AUTH_LOG_TAG, "APK_AUTH_MOBILE_START_FALLBACK err=${err.message}")
            mobileTxnId = null
            mobileState = null
            apkOAuthStartUrl(BuildConfig.PUBLIC_WEB_URL)
        }
    }

    private fun startStatusPolling(app: MonitorApp, txnId: String, state: String) {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            var attempts = 0
            while (attempts < 90) {
                attempts++
                // Faster cadence early so login feels instant if the deep link
                // never arrives; back off slightly after the first ~15s.
                delay(if (attempts <= 12) 1_200 else 2_500)
                if (authSucceeded) return@launch
                if (bootstrapBridgeUrl != null || app.sessionStore.isWebLoggedInBlocking()) return@launch
                val status = try {
                    app.api.mobileAuthStatus(txnId, state)
                } catch (_: Exception) {
                    null
                }
                if (status != null && status.status == "complete" && !status.consumeUrl.isNullOrBlank()) {
                    if (bootstrapBridgeUrl == null && !authSucceeded) {
                        Log.i(APK_AUTH_LOG_TAG, "APK_AUTH_MOBILE_POLL_COMPLETE marker=$APK_MOBILE_AUTH_MARKER")
                        oauthWaitingDeepLink = false
                        // Polling path: the Custom Tab is still in front — pull the
                        // app forward so the bootstrap WebView + Live Tracker show
                        // without the user having to close the browser.
                        bringAppToForeground()
                        bootstrapBridgeUrl = status.consumeUrl
                    }
                    return@launch
                }
                if (status != null && status.status == "consumed") return@launch
            }
        }
    }

    private fun captureOAuthDeepLink(intent: Intent?) {
        val uri = intent?.data ?: return
        // Log receipt for ANY incoming intent data (redacted) before parsing.
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_DEEPLINK_RECEIVED action=${intent.action} scheme=${uri.scheme} host=${uri.host} path=${uri.path}",
        )
        val code = extractApkOAuthCode(uri, publicWebHost())
        val stateNonce = extractApkOAuthState(uri, publicWebHost()).orEmpty()
        val stateMatches = stateNonce.isNotBlank() && stateNonce == mobileState
        Log.i(
            APK_AUTH_LOG_TAG,
            "APK_AUTH_DEEPLINK_PARSED hasCode=${code != null} hasState=${stateNonce.isNotBlank()} stateMatches=$stateMatches",
        )
        if (code == null) return
        oauthWaitingDeepLink = false
        pendingOAuthState = stateNonce
        pendingOAuthCode = code
    }
}
