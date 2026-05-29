package my.id.deng.monitor

import my.id.deng.monitor.data.DeviceRam
import my.id.deng.monitor.data.DeviceSummary
import my.id.deng.monitor.util.Format
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * v1.0.6 contract: the redesigned (device-centric) dashboard + the
 * "snapshot must finally work" Snapshot screen.
 *
 * Mixes real unit tests (DeviceRam math, Format helpers) with source scans
 * that lock the dashboard down to the required summary fields and the
 * snapshot retry/success UX.
 */
class DashboardSnapshotContractTest {
    private fun read(path: String): String {
        val f = File(path)
        require(f.exists()) { "expected file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    // ── DeviceRam model math ────────────────────────────────────────────

    @Test
    fun `DeviceRam renders MB over MB with percent when totals known`() {
        val ram = DeviceRam(usedMb = 2048, totalMb = 4096, percent = 50)
        assertEquals("2048MB/4096MB 50%", ram.displayText)
        assertEquals(50, ram.effectivePercent)
    }

    @Test
    fun `DeviceRam computes percent from used over total when percent missing`() {
        val ram = DeviceRam(usedMb = 750, totalMb = 1000, percent = null)
        assertEquals(75, ram.effectivePercent)
    }

    @Test
    fun `DeviceRam shows percent only when total unknown`() {
        val ram = DeviceRam(usedMb = 0, totalMb = 0, percent = 42)
        assertEquals("42%", ram.displayText)
    }

    @Test
    fun `DeviceRam never invents numbers when nothing reported`() {
        val ram = DeviceRam(usedMb = 0, totalMb = 0, percent = null)
        assertEquals("—", ram.displayText)
        assertNull(ram.effectivePercent)
    }

    // ── Device state rules (counts) ─────────────────────────────────────

    @Test
    fun `device connection prefers computed connected flag`() {
        val online = DeviceSummary(id = "a", connected = true, statusConnected = false)
        val dead = DeviceSummary(id = "b", connected = false, statusConnected = true)
        assertTrue(online.isConnected)
        assertFalse("snapshot/legacy sticky must not force online", dead.isConnected)
    }

    @Test
    fun `device display name falls back to Cloud Phone`() {
        assertEquals("Cloud Phone", DeviceSummary(id = "a").displayName)
        assertEquals("Phone 1", DeviceSummary(id = "a", deviceLabel = "Phone 1").displayName)
    }

    // ── Format helpers ──────────────────────────────────────────────────

    @Test
    fun `relativeAgo is human readable`() {
        assertEquals("just now", Format.relativeAgo(1))
        assertEquals("12s ago", Format.relativeAgo(12))
        assertEquals("3m ago", Format.relativeAgo(180))
        assertEquals("2h ago", Format.relativeAgo(7200))
        assertEquals("—", Format.relativeAgo(null))
        assertEquals("—", Format.relativeAgo(-5))
    }

    // ── Dashboard source contract ───────────────────────────────────────

    private fun dashboard() = read("src/main/kotlin/my/id/deng/monitor/ui/DashboardScreen.kt")

    @Test
    fun `dashboard uses the device-list handle (device-centric counts)`() {
        assertTrue(dashboard().contains("rememberDeviceListHandle"))
    }

    @Test
    fun `dashboard renders the required summary labels only`() {
        val src = dashboard()
        for (label in listOf("\"TOTAL\"", "\"ONLINE\"", "\"DEAD\"", "\"RAM\"", "Last Update", "Interval")) {
            assertTrue("dashboard must render $label", src.contains(label))
        }
        // RAM detail list per device.
        assertTrue("dashboard must render per-device RAM list", src.contains("RAM Details"))
        assertTrue("dashboard must iterate devices for RAM rows", src.contains("devices.forEach"))
    }

    @Test
    fun `dashboard headline cards show PACKAGE counts, not device counts (v1_0_8)`() {
        val src = dashboard()
        // Headline TOTAL/ONLINE/DEAD come from the backend package summary.
        assertTrue("TOTAL card uses package total", src.contains("CompactStat(\"TOTAL\", pkgTotal.toString()"))
        assertTrue("ONLINE card uses package online", src.contains("CompactStat(\"ONLINE\", pkgOnline.toString()"))
        assertTrue("DEAD card uses package dead", src.contains("CompactStat(\"DEAD\", pkgDead.toString()"))
        assertTrue("package counts read from summary", src.contains("packageSummary.total"))
        // Device count is still shown, but only as a secondary line.
        assertTrue("device count is secondary", src.contains("\"Devices\""))
        // Snapshot result must NOT be used to compute package counts.
        assertFalse(
            "snapshot failure must never affect package counts",
            src.contains("snapshotLastResult ==") && src.contains("pkgDead ="),
        )
    }

    @Test
    fun `dashboard interval uses backend monitor_interval_seconds not hardcoded poll`() {
        val src = dashboard()
        assertTrue("interval from device settings", src.contains("dashboardIntervalLabel"))
        assertTrue("device model exposes monitor interval", src.contains("monitorIntervalSeconds"))
        assertFalse(
            "must not hardcode poll constant in Interval label",
            src.contains("Interval\", \"\${") && src.contains("DASHBOARD_POLL_SECONDS"),
        )
    }

    @Test
    fun `dashboard offers refresh and never an infinite-only spinner`() {
        val src = dashboard()
        assertTrue("dashboard must have a refresh affordance", src.contains("RefreshPill"))
        assertTrue("dashboard error state must retry", src.contains("handle.refreshNow()"))
        assertTrue("dashboard loading uses a skeleton", src.contains("DashboardSkeleton"))
    }

    // ── Snapshot source contract ────────────────────────────────────────

    private fun snapshot() = read("src/main/kotlin/my/id/deng/monitor/ui/SnapshotScreen.kt")

    @Test
    fun `snapshot error state renders retry via ErrorCard`() {
        val src = snapshot()
        assertTrue(src.contains("ErrorCard("))
        assertTrue(src.contains("handle.refreshNow()"))
    }

    @Test
    fun `snapshot success renders image and metadata`() {
        val src = snapshot()
        assertTrue("must render the latest image", src.contains("Image("))
        assertTrue("must show Last timestamp", src.contains("Last: "))
        assertTrue("must show interval", src.contains("Interval: "))
    }

    @Test
    fun `snapshot maps v1_0_6 capture result vocabulary to clean reasons`() {
        val src = snapshot()
        for (result in listOf(
            "failed_no_screencap", "failed_root_denied", "failed_invalid_png",
            "failed_timeout", "failed_upload_http",
        )) {
            assertTrue("snapshot must handle $result", src.contains(result))
        }
        // No raw stack traces in the normal UI.
        assertFalse("snapshot UI must not print stack traces", src.contains("printStackTrace"))
    }
}
