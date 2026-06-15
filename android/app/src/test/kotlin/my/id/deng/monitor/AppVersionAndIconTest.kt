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
    fun `versionName is bumped to 2_2_7`() {
        val gradle = readGradle()
        assertTrue(
            "expected versionName = \"2.2.7\" in build.gradle.kts",
            gradle.contains(Regex("""versionName\s*=\s*"2\.2\.7"""")),
        )
    }

    @Test
    fun `versionCode is bumped to 24`() {
        val gradle = readGradle()
        assertTrue(
            "expected versionCode = 24 in build.gradle.kts",
            gradle.contains(Regex("""versionCode\s*=\s*24\b""")),
        )
    }
}
