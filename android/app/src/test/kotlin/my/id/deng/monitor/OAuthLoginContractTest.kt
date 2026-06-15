package my.id.deng.monitor

import my.id.deng.monitor.ui.apkOAuthStartUrl
import my.id.deng.monitor.ui.isExternalOAuthUrl
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class OAuthLoginContractTest {
    private fun read(path: String): String {
        val f = File(path)
        require(f.exists()) { "expected file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    private val src = "src/main/kotlin/my/id/deng/monitor"
    private val marker = "APK_MOBILE_AUTH_WEBVIEW_BOOTSTRAP_2026_06_15"

    @Test
    fun `LoginWebViewScreen opens external browser for Discord OAuth`() {
        val login = read("$src/ui/LoginWebViewScreen.kt")
        assertTrue(login.contains("CustomTabsIntent"))
        assertTrue(login.contains("isExternalOAuthUrl"))
        assertTrue(login.contains("shouldOverrideUrl"))
        assertTrue(login.contains("AuthErrorOverlay"))
    }

    @Test
    fun `OAuth helper covers Discord authorize and site auth paths`() {
        assertTrue(
            isExternalOAuthUrl(
                "https://discord.com/oauth2/authorize?client_id=1",
                "aio.deng.my.id",
            ),
        )
        assertTrue(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/auth/discord?apk=1",
                "aio.deng.my.id",
            ),
        )
        assertFalse(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/dashboard?apk=1",
                "aio.deng.my.id",
            ),
        )
    }

    @Test
    fun `apk OAuth start uses aio public site with client apk`() {
        val url = apkOAuthStartUrl("https://aio.deng.my.id")
        assertTrue(url.contains("https://aio.deng.my.id/auth/discord"))
        assertTrue(url.contains("client=apk"))
        assertTrue(url.contains("apk=1"))
    }

    @Test
    fun `MainActivity handles deep link callback and mobile-auth transaction`() {
        val main = read("$src/MainActivity.kt")
        assertTrue(main.contains("captureOAuthDeepLink"))
        assertTrue(main.contains("completeApkOAuthFromDeepLink"))
        assertTrue(main.contains("bootstrapBridgeUrl"))
        // New first-party mobile-auth lane: starts a transaction, captures the
        // state nonce from the deep link, and polls status as a fallback.
        assertTrue(main.contains("extractApkOAuthState"))
        assertTrue(main.contains("mobileAuthStart"))
        assertTrue(main.contains("startStatusPolling"))
    }

    @Test
    fun `ApkOAuthHandoff builds first-party consume URL from code and state`() {
        val handoff = read("$src/ui/ApkOAuthHandoff.kt")
        assertTrue(handoff.contains("fun buildMobileConsumeUrl"))
        assertTrue(handoff.contains("/mobile-auth/consume"))
        assertTrue(handoff.contains("extractApkOAuthState"))
        // The deep-link completer takes a state nonce and verifies via auth/me.
        assertTrue(handoff.contains("code: String,\n    state: String,"))
        assertTrue(handoff.contains("aioAuthMe"))
        assertTrue(handoff.contains(marker))
    }

    @Test
    fun `AndroidManifest registers OAuth deep link intent filter`() {
        val manifest = read("src/main/AndroidManifest.xml")
        assertTrue(manifest.contains("android:scheme=\"deng-aio\""))
        assertTrue(manifest.contains("android:host=\"auth\""))
        assertTrue(manifest.contains("<queries>"))
    }

    @Test
    fun `ApkOAuthHandoff does not mark logged in before WebView bridge completes`() {
        val handoff = read("$src/ui/ApkOAuthHandoff.kt")
        val completeFn = Regex("suspend fun completeApkOAuthFromDeepLink[\\s\\S]*?\\n\\}")
            .find(handoff)?.value.orEmpty()
        assertTrue(completeFn.isNotBlank())
        assertFalse(completeFn.contains("setWebLoggedIn(true)"))
        assertTrue(handoff.contains("finalizeApkWebSession"))
        assertTrue(handoff.contains(marker))
    }

    @Test
    fun `ApkAuthBootstrapScreen finalizes session only after authenticated URL`() {
        val login = read("$src/ui/LoginWebViewScreen.kt")
        assertTrue(login.contains("ApkAuthBootstrapScreen"))
        assertTrue(login.contains("isAuthenticatedWebUrl"))
        assertTrue(login.contains("finalizeApkWebSession"))
    }

    @Test
    fun `AioWebViewScreen lets WebView handle https redirects for cookies`() {
        val web = read("$src/ui/AioWebViewScreen.kt")
        assertTrue(web.contains("return false"))
        assertTrue(web.contains("CookieManager.getInstance().flush()"))
    }

    @Test
    fun `release marker is baked into build config and string resources`() {
        val gradle = read("build.gradle.kts")
        assertTrue(gradle.contains(marker))
        val strings = read("src/main/res/values/strings.xml")
        assertTrue(strings.contains(marker))
    }
}
