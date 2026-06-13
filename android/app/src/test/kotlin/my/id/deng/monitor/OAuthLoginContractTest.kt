package my.id.deng.monitor

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
        assertTrue(login.contains("BRIDGE_URL"))
        assertTrue(login.contains("/auth/discord?apk=1"))
        assertTrue(login.contains("shouldOverrideUrl"))
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
}
