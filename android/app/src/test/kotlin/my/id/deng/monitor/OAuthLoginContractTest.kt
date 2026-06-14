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

    @Test
    fun `LoginWebViewScreen opens external browser for Discord OAuth`() {
        val login = read("$src/ui/LoginWebViewScreen.kt")
        assertTrue(login.contains("CustomTabsIntent"))
        assertTrue(login.contains("PUBLIC_WEB_URL"))
        assertTrue(login.contains("isExternalOAuthUrl"))
        assertTrue(login.contains("shouldOverrideUrl"))
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
        assertTrue(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/api/aio/auth/callback?code=x",
                "aio.deng.my.id",
            ),
        )
        assertTrue(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/auth/discord/callback?code=x",
                "aio.deng.my.id",
            ),
        )
        assertFalse(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/dashboard?apk=1",
                "aio.deng.my.id",
            ),
        )
        assertFalse(
            isExternalOAuthUrl(
                "https://aio.deng.my.id/tracker?apk=1",
                "aio.deng.my.id",
            ),
        )
    }

    @Test
    fun `apk OAuth start uses aio public site`() {
        assertTrue(
            apkOAuthStartUrl("https://aio.deng.my.id")
                .contains("https://aio.deng.my.id/auth/discord?apk=1"),
        )
    }

    @Test
    fun `MainActivity handles deng-aio deep link callback`() {
        val main = read("$src/MainActivity.kt")
        assertTrue(main.contains("captureOAuthDeepLink"))
        assertTrue(main.contains("completeApkOAuthFromDeepLink"))
        assertTrue(main.contains("DENG_AIO_APP_SCHEME"))
    }

    @Test
    fun `AndroidManifest registers OAuth deep link intent filter`() {
        val manifest = read("src/main/AndroidManifest.xml")
        assertTrue(manifest.contains("android:scheme=\"deng-aio\""))
        assertTrue(manifest.contains("android:host=\"auth\""))
    }

    @Test
    fun `MainActivity does not consume pending web bootstrap URL`() {
        val main = read("$src/MainActivity.kt")
        assertFalse(main.contains("consumePendingWebBootstrapUrl"))
    }

    @Test
    fun `LiveTrackerWebViewScreen waits for bootstrap URL before WebView load`() {
        val live = read("$src/ui/LiveTrackerWebViewScreen.kt")
        assertTrue(live.contains("consumePendingWebBootstrapUrl"))
        assertTrue(live.contains("if (url != null)"))
    }

    @Test
    fun `LoginWebViewScreen uses deep link handoff not URL-only login`() {
        val login = read("$src/ui/LoginWebViewScreen.kt")
        val composable = login.substringBefore("fun completeApkOAuthFromDeepLink")
        assertFalse(composable.contains("setWebLoggedIn(true)"))
        assertTrue(login.contains("completeApkOAuthFromDeepLink"))
        assertTrue(login.contains("APK_DISCORD_AUTH_HANDOFF_FIX_2026_06_14"))
    }

    @Test
    fun `release marker is baked into build config and string resources`() {
        val gradle = read("build.gradle.kts")
        assertTrue(gradle.contains("APK_DISCORD_AUTH_LOGIN_LOOP_FIX_2026_06_14"))
        val strings = read("src/main/res/values/strings.xml")
        assertTrue(strings.contains("APK_DISCORD_AUTH_LOGIN_LOOP_FIX_2026_06_14"))
    }
}
