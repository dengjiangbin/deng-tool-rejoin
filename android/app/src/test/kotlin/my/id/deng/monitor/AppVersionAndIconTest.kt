package my.id.deng.monitor

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AppVersionAndIconTest {
    private fun readGradle(): String {
        val f = File("build.gradle.kts")
        require(f.exists()) { "missing build.gradle.kts" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `versionName is bumped to 2_2_6`() {
        val gradle = readGradle()
        assertTrue(
            "expected versionName = \"2.2.6\" in build.gradle.kts",
            gradle.contains(Regex("""versionName\s*=\s*"2\.2\.6"""")),
        )
    }

    @Test
    fun `versionCode is bumped to 23`() {
        val gradle = readGradle()
        assertTrue(
            "expected versionCode = 23 in build.gradle.kts",
            gradle.contains(Regex("""versionCode\s*=\s*23\b""")),
        )
    }
}
