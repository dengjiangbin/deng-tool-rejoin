package my.id.deng.monitor

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Locks down the user-facing product name so it can never silently drift
 * back to "DENG Monitor". Reads the actual `res/values/strings.xml` and
 * fails the build if the wrong label sneaks in.
 *
 * Pure JVM test — no Android framework dependency required.
 */
class AppNamingTest {
    private fun stringsXml(): String {
        // Tests run with working dir = `android/app/`.
        val f = File("src/main/res/values/strings.xml")
        require(f.exists()) { "expected strings.xml at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `app_name string resource is the canonical product name`() {
        val xml = stringsXml()
        assertTrue(
            "app_name must be exactly 'DENG Tool: Rejoin APK' — got: $xml",
            xml.contains(Regex("""<string name="app_name">\s*DENG Tool: Rejoin APK\s*</string>""")),
        )
    }

    @Test
    fun `app_launcher_label is the shorter launcher fallback`() {
        val xml = stringsXml()
        assertTrue(
            "app_launcher_label must be exactly 'Rejoin APK'",
            xml.contains(Regex("""<string name="app_launcher_label">\s*Rejoin APK\s*</string>""")),
        )
    }

    @Test
    fun `string resources contain no legacy DENG Monitor wording`() {
        val xml = stringsXml()
        assertFalse(
            "strings.xml still contains legacy 'DENG Monitor' wording",
            xml.contains("DENG Monitor"),
        )
    }
}
