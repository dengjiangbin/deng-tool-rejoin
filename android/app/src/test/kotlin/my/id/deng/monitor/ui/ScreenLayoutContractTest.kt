package my.id.deng.monitor.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * v1.0.2 layout-contract regression tests.
 *
 * These read the actual Compose source files (we can't spin up Robolectric
 * here cheaply) and assert structural invariants that the user explicitly
 * required:
 *
 *   • SettingsScreen must be vertically scrollable AND apply
 *     navigation-bar / IME padding so the Save / interval rows are always
 *     reachable on small phones and cloud phones.
 *   • PackagesScreen rows must show the *username* as the main line and
 *     the *package name* as the muted subtitle (not the inverted layout
 *     from v1.0.1).
 *   • SnapshotScreen must surface the "Snapshot is off. Enable it in
 *     Settings." copy when the interval is 0.
 */
class ScreenLayoutContractTest {
    private fun ui(name: String): String {
        val f = File("src/main/kotlin/my/id/deng/monitor/ui/$name")
        require(f.exists()) { "missing UI source: ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `SettingsScreen wraps content in verticalScroll`() {
        val src = ui("SettingsScreen.kt")
        assertTrue(
            "SettingsScreen must use Column + verticalScroll(rememberScrollState())",
            src.contains("verticalScroll(rememberScrollState())"),
        )
    }

    @Test
    fun `SettingsScreen applies navigationBars and ime padding`() {
        val src = ui("SettingsScreen.kt")
        assertTrue(
            "SettingsScreen must call .navigationBarsPadding()",
            src.contains(".navigationBarsPadding()"),
        )
        assertTrue(
            "SettingsScreen must call .imePadding()",
            src.contains(".imePadding()"),
        )
    }

    @Test
    fun `SettingsScreen still offers every snapshot interval option including Off`() {
        val src = ui("SettingsScreen.kt")
        for (label in listOf("\"Off\"", "\"15 seconds\"", "\"30 seconds\"", "\"60 seconds\"", "\"5 minutes\"")) {
            assertTrue("missing snapshot option $label", src.contains(label))
        }
    }

    @Test
    fun `PackagesScreen row uses username as title and package name as subtitle`() {
        val src = ui("PackagesScreen.kt")
        // Title now reads username (safeUsername wraps null -> "Unknown").
        assertTrue(
            "PackagesScreen title must call Format.safeUsername(pkg.username)",
            src.contains("Format.safeUsername(pkg.username)"),
        )
        // Subtitle is the raw package name (full path) at bodySmall / muted.
        assertTrue(
            "PackagesScreen subtitle must render pkg.packageName",
            src.contains("pkg.packageName"),
        )
        // Old v1.0.1 layout had the package short-name as the *title* —
        // make sure we don't regress.
        assertFalse(
            "PackagesScreen must not put Format.shortPackage in the title slot",
            Regex("""titleMedium[\s\S]{0,200}Format\.shortPackage""").containsMatchIn(src),
        )
    }

    @Test
    fun `SnapshotScreen shows interval-off guidance instead of silent empty`() {
        val src = ui("SnapshotScreen.kt")
        assertTrue(
            "SnapshotScreen must show the explicit Off guidance copy",
            src.contains("Snapshot is off. Enable it in Settings."),
        )
    }

    @Test
    fun `SnapshotScreen uses Format timestamp formatter for last-fetched label`() {
        val src = ui("SnapshotScreen.kt")
        // No raw SimpleDateFormat instantiation anymore — all formatting
        // goes through the central Indonesian / 12h Format.timestamp helper.
        assertFalse(
            "SnapshotScreen must not call SimpleDateFormat directly",
            src.contains("SimpleDateFormat"),
        )
        assertTrue(
            "SnapshotScreen must format timestamps via Format.timestamp",
            src.contains("Format.timestamp(lastFetchedAt)"),
        )
    }

    @Test
    fun `DashboardScreen renders lastSeenAt via Format timestamp`() {
        val src = ui("DashboardScreen.kt")
        assertTrue(
            "DashboardScreen must call Format.timestamp(lastSeenAt)",
            src.contains("Format.timestamp(lastSeenAt)"),
        )
        assertFalse(
            "DashboardScreen must not still print raw lastSeenAt ?: \"—\"",
            src.contains("\${lastSeenAt ?: \"—\"}"),
        )
    }
}
