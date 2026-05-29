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
            "SnapshotScreen must format the local fetched timestamp via Format.timestamp",
            src.contains("Format.timestamp(lastFetchedAt)"),
        )
    }

    @Test
    fun `SnapshotScreen v1_0_3 shows Waiting and Retrying copies, never silent`() {
        val src = ui("SnapshotScreen.kt")
        // v1.0.3 — three explicit user-facing states besides "Off":
        //   • first-frame:  "Waiting for first snapshot…"
        //   • retention/race:  "Snapshot temporarily unavailable. Retrying…"
        //   • fetch error:  "Snapshot fetch failed. Retrying…"
        // The bare "No snapshot yet." copy from v1.0.2 must be gone, because
        // it was the source of the user-reported silent-failure confusion.
        assertTrue(
            "SnapshotScreen must show 'Waiting for first snapshot…' when interval > 0 and no capture yet",
            src.contains("Waiting for first snapshot"),
        )
        assertTrue(
            "SnapshotScreen must show a Retrying… message when fetch fails",
            src.contains("Retrying"),
        )
        assertFalse(
            "SnapshotScreen must NOT use the misleading 'No snapshot yet.' copy from v1.0.2",
            src.contains("No snapshot yet."),
        )
    }

    @Test
    fun `SnapshotScreen reads server-reported last_snapshot_captured_at from device summary`() {
        val src = ui("SnapshotScreen.kt")
        assertTrue(
            "SnapshotScreen must consume lastSnapshotCapturedAt from DeviceSummary",
            src.contains("lastSnapshotCapturedAt"),
        )
    }

    @Test
    fun `DashboardScreen renders timestamps via Format timestamp`() {
        // v1.0.6 redesign: the dashboard is device-centric. It must still
        // route every timestamp through the central Format helper (never a
        // raw ISO string) and label it "Last Update".
        val src = ui("DashboardScreen.kt")
        assertTrue(
            "DashboardScreen must format last-seen via Format.timestamp(...)",
            src.contains("Format.timestamp("),
        )
        assertTrue(
            "DashboardScreen must label the freshest heartbeat as Last Update",
            src.contains("Last Update"),
        )
        assertFalse(
            "DashboardScreen must not still print raw lastSeenAt ?: \"—\"",
            src.contains("\${lastSeenAt ?: \"—\"}"),
        )
    }

    // ────────────────────────────────────────────────────────────────────
    // v1.0.4 contract additions
    // ────────────────────────────────────────────────────────────────────

    @Test
    fun `DashboardScreen v1_0_4 uses the new ConnectionBadge for device link state`() {
        val src = ui("DashboardScreen.kt")
        assertTrue(
            "DashboardScreen must use ConnectionBadge (not StateBadge) for the connection",
            src.contains("ConnectionBadge("),
        )
        assertFalse(
            "DashboardScreen must not reuse StateBadge to render the connection state",
            Regex("""StateBadge\(if \(connected\)""").containsMatchIn(src),
        )
    }

    @Test
    fun `DashboardScreen v1_0_4 reads the computed connectionLabel - not the legacy boolean only`() {
        val src = ui("DashboardScreen.kt")
        assertTrue(
            "DashboardScreen must read DeviceSummary.connectionLabel for the badge text",
            src.contains("connectionLabel"),
        )
        assertTrue(
            "DashboardScreen must surface secondsSinceLastSeen when disconnected",
            src.contains("secondsSinceLastSeen"),
        )
    }

    @Test
    fun `Components v1_0_4 StateBadge supports Joining and drops Relaunching-only behaviour`() {
        val src = ui("Components.kt")
        assertTrue(
            "Components.StateBadge must have a branch for \"Joining\"",
            src.contains("\"Joining\""),
        )
        assertTrue(
            "Components.StateBadge must have a branch for \"Launching\"",
            src.contains("\"Launching\""),
        )
        assertTrue(
            "Components must expose a ConnectionBadge composable for device link state",
            src.contains("fun ConnectionBadge("),
        )
        assertFalse(
            "Components must not contain a special \"In-Lobby\" StateBadge branch",
            src.contains("\"In-Lobby\""),
        )
    }

    @Test
    fun `SettingsScreen v1_0_4 uses the rememberDeviceStatusHandle + refreshNow pattern`() {
        val src = ui("SettingsScreen.kt")
        assertTrue(
            "SettingsScreen must use rememberDeviceStatusHandle for refreshNow access",
            src.contains("rememberDeviceStatusHandle"),
        )
        assertTrue(
            "SettingsScreen must call handle.refreshNow() after a successful save",
            src.contains("handle.refreshNow()"),
        )
        assertTrue(
            "SettingsScreen must apply an optimistic update so the radio flips immediately",
            src.contains("optimistic = next") || src.contains("optimistic = "),
        )
    }

    @Test
    fun `DeviceState v1_0_4 exposes a status handle with refreshNow`() {
        val src = ui("DeviceState.kt")
        assertTrue(
            "DeviceState must define a DeviceStatusHandle with refreshNow()",
            src.contains("class DeviceStatusHandle"),
        )
        assertTrue(
            "DeviceStatusHandle must expose suspend fun refreshNow",
            src.contains("suspend fun refreshNow"),
        )
    }

    @Test
    fun `SnapshotScreen v1_0_4 surfaces real capture and upload failure reasons`() {
        val src = ui("SnapshotScreen.kt")
        assertTrue(
            "SnapshotScreen must surface bridge-reported capture_failed reason",
            src.contains("Snapshot capture failed:"),
        )
        assertTrue(
            "SnapshotScreen must surface bridge-reported upload_failed reason",
            src.contains("Snapshot upload failed:"),
        )
        assertTrue(
            "SnapshotScreen must explicitly say it's waiting for the cloud phone when disconnected",
            src.contains("Waiting for cloud phone to reconnect"),
        )
        assertTrue(
            "SnapshotScreen must parse last_bridge_status into a BridgeStatusSnapshot",
            src.contains("BridgeStatusSnapshot"),
        )
    }
}
