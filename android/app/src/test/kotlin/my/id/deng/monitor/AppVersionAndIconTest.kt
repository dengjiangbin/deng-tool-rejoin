package my.id.deng.monitor

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Locks down APK app-version and launcher-icon invariants so the app
 * can never quietly regress to the default Android icon or skip a
 * version bump on a republish.
 *
 * Pure JVM test — reads the actual build script and icon resources.
 */
class AppVersionAndIconTest {
    private fun buildGradle(): String {
        val f = File("build.gradle.kts")
        require(f.exists()) { "expected build.gradle.kts at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `versionName is bumped to 1_0_6`() {
        val gradle = buildGradle()
        assertTrue(
            "expected versionName = \"1.0.6\" in build.gradle.kts",
            gradle.contains(Regex("""versionName\s*=\s*"1\.0\.6"""")),
        )
    }

    @Test
    fun `versionCode is bumped to 7`() {
        val gradle = buildGradle()
        assertTrue(
            "expected versionCode = 7 in build.gradle.kts",
            gradle.contains(Regex("""versionCode\s*=\s*7\b""")),
        )
    }

    @Test
    fun `adaptive launcher icon foreground points at real mipmap bitmap (not the default vector)`() {
        val xml = File("src/main/res/mipmap-anydpi-v26/ic_launcher.xml").readText(Charsets.UTF_8)
        assertTrue(
            "adaptive ic_launcher.xml must reference @mipmap/ic_launcher_foreground (real DENG logo)",
            xml.contains(Regex("""<foreground[^/]*@mipmap/ic_launcher_foreground""")),
        )
        // Must NOT still reference the old default "@drawable/ic_launcher_foreground"
        // vector for the foreground layer (that was the placeholder D monogram).
        assertFalse(
            "adaptive foreground must not reference the placeholder vector drawable",
            xml.contains(Regex("""<foreground[^/]*@drawable/ic_launcher_foreground""")),
        )
    }

    @Test
    fun `real DENG launcher bitmaps exist at every required density`() {
        val densities = listOf("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi")
        val names = listOf("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png")
        for (d in densities) {
            for (n in names) {
                val f = File("src/main/res/mipmap-$d/$n")
                assertTrue("missing launcher bitmap: ${f.path}", f.exists())
                // Sanity: a non-trivial PNG, not an empty placeholder.
                assertTrue("${f.path} is too small to be a real icon", f.length() > 1024)
            }
        }
    }

    @Test
    fun `Android string resources hold the v1_0_4 app name and launcher label`() {
        val xml = File("src/main/res/values/strings.xml").readText(Charsets.UTF_8)
        assertNotNull(xml)
        assertTrue(
            "app_name must be 'DENG Tool: Rejoin' (since v1.0.3 dropped the APK suffix)",
            xml.contains(Regex("""<string name="app_name">\s*DENG Tool: Rejoin\s*</string>""")),
        )
        assertTrue(
            "app_launcher_label must be 'DENG Rejoin' (since v1.0.3 family branding)",
            xml.contains(Regex("""<string name="app_launcher_label">\s*DENG Rejoin\s*</string>""")),
        )
    }

    @Test
    fun `PairScreen text directs users to the Download APK page, never the License page`() {
        val pair = File("src/main/kotlin/my/id/deng/monitor/ui/PairScreen.kt").readText(Charsets.UTF_8)
        assertTrue(
            "PairScreen must mention the Download APK flow",
            pair.contains("Download APK page"),
        )
        assertFalse(
            "PairScreen must not tell users to use the License page",
            pair.contains("My License") || pair.contains("License page"),
        )
        assertFalse(
            "PairScreen must not use the legacy DENG Monitor product name",
            pair.contains("DENG Monitor"),
        )
    }

    @Test
    fun `launcher footer pin still points users to tool_deng_my_id slash download`() {
        val pair = File("src/main/kotlin/my/id/deng/monitor/ui/PairScreen.kt").readText(Charsets.UTF_8)
        assertTrue(
            "PairScreen footer must keep the official download URL",
            pair.contains("tool.deng.my.id/download"),
        )
    }
}
