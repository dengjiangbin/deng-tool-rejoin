package my.id.deng.monitor

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AppNamingTest {
    private fun stringsXml(): String {
        val f = File("src/main/res/values/strings.xml")
        require(f.exists()) { "expected strings.xml at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `app_name string resource is DENG All In One`() {
        val xml = stringsXml()
        assertTrue(
            "app_name must be exactly 'DENG All In One' — got: $xml",
            xml.contains(Regex("""<string name="app_name">\s*DENG All In One\s*</string>""")),
        )
        assertFalse(
            "app_name must NOT still say 'DENG Tool: Rejoin'",
            xml.contains(Regex("""<string name="app_name">\s*DENG Tool: Rejoin\s*</string>""")),
        )
    }

    @Test
    fun `app_launcher_label is DENG AIO`() {
        val xml = stringsXml()
        assertTrue(
            "app_launcher_label must be exactly 'DENG AIO'",
            xml.contains(Regex("""<string name="app_launcher_label">\s*DENG AIO\s*</string>""")),
        )
        assertFalse(
            "app_launcher_label must NOT still say 'DENG Rejoin'",
            xml.contains(Regex("""<string name="app_launcher_label">\s*DENG Rejoin\s*</string>""")),
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

    @Test
    fun `pair help references aio download not legacy tool license URL`() {
        val xml = stringsXml()
        assertTrue(xml.contains("aio.deng.my.id/download"))
        assertFalse(xml.contains("tool.deng.my.id/license"))
    }
}
