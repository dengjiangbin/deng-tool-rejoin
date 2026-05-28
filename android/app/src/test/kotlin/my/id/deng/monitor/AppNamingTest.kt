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
    fun `app_name string resource is the v1_0_3 App Info label`() {
        val xml = stringsXml()
        // v1.0.3: dropped the trailing "APK" suffix per user feedback —
        // the .apk extension was redundant noise inside the installed
        // app's Android App Info screen.
        assertTrue(
            "app_name must be exactly 'DENG Tool: Rejoin' — got: $xml",
            xml.contains(Regex("""<string name="app_name">\s*DENG Tool: Rejoin\s*</string>""")),
        )
        // Old v1.0.2 wording must be gone from the installed-app label.
        assertFalse(
            "app_name must NOT still say 'DENG Tool: Rejoin APK'",
            xml.contains(Regex("""<string name="app_name">\s*DENG Tool: Rejoin APK\s*</string>""")),
        )
    }

    @Test
    fun `app_launcher_label is the v1_0_3 launcher label`() {
        val xml = stringsXml()
        // v1.0.3: home-screen icon now reads "DENG Rejoin" — short
        // enough not to wrap, and immediately recognisable as part of
        // the DENG product family.
        assertTrue(
            "app_launcher_label must be exactly 'DENG Rejoin'",
            xml.contains(Regex("""<string name="app_launcher_label">\s*DENG Rejoin\s*</string>""")),
        )
        assertFalse(
            "app_launcher_label must NOT still say 'Rejoin APK'",
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
        assertFalse(
            "strings.xml still contains legacy 'Monitor App' wording",
            xml.contains("Monitor App"),
        )
    }
}
