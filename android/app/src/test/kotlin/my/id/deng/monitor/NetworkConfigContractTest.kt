package my.id.deng.monitor

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * v2.2 network + public-site contract for DENG All In One APK.
 */
class NetworkConfigContractTest {
    private fun read(path: String): String {
        val f = File(path)
        require(f.exists()) { "expected file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    private fun gradle() = read("build.gradle.kts")
    private fun manifest() = read("src/main/AndroidManifest.xml")

    @Test
    fun `default backend base URL is exactly https aio_deng_my_id`() {
        assertTrue(
            "build.gradle.kts default bridgeUrl must be https://aio.deng.my.id",
            gradle().contains(Regex("""\?:\s*"https://aio\.deng\.my\.id"""")),
        )
    }

    @Test
    fun `default public web URL is aio_deng_my_id`() {
        val matches = Regex("""publicWebUrl[\s\S]*?\?:\s*"(https?://[^"]+)"""")
            .findAll(gradle())
            .map { it.groupValues[1] }
            .toList()
        assertTrue(matches.any { it == "https://aio.deng.my.id" })
    }

    @Test
    fun `default backend URL is not staging localhost or 127_0_0_1`() {
        val fallback = Regex("""\?:\s*"(https?://[^"]+)"""").find(gradle())?.groupValues?.get(1)
        requireNotNull(fallback) { "could not locate the default bridgeUrl fallback" }
        assertFalse("default URL must not be staging", fallback.contains("staging.example.com"))
        assertFalse("default URL must not be localhost", fallback.contains("localhost"))
        assertFalse("default URL must not be 127.0.0.1", fallback.contains("127.0.0.1"))
        assertFalse("default URL must be HTTPS, not plain http", fallback.startsWith("http://"))
    }

    @Test
    fun `monitor API never points at rejoin_deng_my_id`() {
        val gradleSrc = gradle()
        assertFalse(
            "default bridgeUrl must not be rejoin.deng.my.id",
            gradleSrc.contains(Regex("""\?:\s*"https://rejoin\.deng\.my\.id"""")),
        )
    }

    @Test
    fun `AndroidManifest declares INTERNET permission`() {
        assertTrue(
            "AndroidManifest must declare android.permission.INTERNET",
            manifest().contains("android.permission.INTERNET"),
        )
    }

    @Test
    fun `AndroidManifest declares ACCESS_NETWORK_STATE for offline UX`() {
        assertTrue(
            "AndroidManifest should declare ACCESS_NETWORK_STATE",
            manifest().contains("android.permission.ACCESS_NETWORK_STATE"),
        )
    }

    @Test
    fun `network security config exists and is HTTPS only`() {
        val nsc = read("src/main/res/xml/network_security_config.xml")
        assertTrue(
            "network_security_config must keep cleartextTrafficPermitted=false (HTTPS only)",
            nsc.contains("cleartextTrafficPermitted=\"false\""),
        )
    }

    @Test
    fun `UnknownHostException maps to a safe host-named message`() {
        val api = read("src/main/kotlin/my/id/deng/monitor/data/MonitorApi.kt")
        assertTrue("must import UnknownHostException", api.contains("java.net.UnknownHostException"))
        assertTrue("must map UnknownHostException", api.contains("is UnknownHostException"))
        assertTrue(
            "UnknownHostException message must be the friendly 'Cannot reach' copy",
            api.contains("Cannot reach \$host"),
        )
        assertTrue(
            "MonitorApi must expose a host property for UI display + error copy",
            api.contains(Regex("""val\s+host\s*:\s*String""")),
        )
    }

    @Test
    fun `device polling renders the friendly error and never the raw exception class`() {
        val ds = read("src/main/kotlin/my/id/deng/monitor/ui/DeviceState.kt")
        assertTrue(
            "DeviceState must use friendlyNetworkError",
            ds.contains("friendlyNetworkError(e, api.host)"),
        )
        assertFalse(
            "DeviceState must not surface the raw 'Network error: \${e.javaClass.simpleName}'",
            ds.contains("e.javaClass.simpleName"),
        )
    }

    @Test
    fun `error UI offers a Retry action`() {
        val comp = read("src/main/kotlin/my/id/deng/monitor/ui/Components.kt")
        assertTrue("Components must define an ErrorCard", comp.contains("fun ErrorCard("))
        assertTrue("ErrorCard must render a Retry button", comp.contains("text = \"Retry\""))
    }

    @Test
    fun `dashboard and settings render the retry-capable ErrorCard`() {
        val dash = read("src/main/kotlin/my/id/deng/monitor/ui/DashboardScreen.kt")
        val settings = read("src/main/kotlin/my/id/deng/monitor/ui/SettingsScreen.kt")
        assertTrue("Dashboard must use ErrorCard", dash.contains("ErrorCard("))
        assertTrue("Dashboard retry must call refreshNow()", dash.contains("handle.refreshNow()"))
        assertTrue("Settings must use ErrorCard", settings.contains("ErrorCard("))
    }

    @Test
    fun `settings about card displays the backend host safely`() {
        val settings = read("src/main/kotlin/my/id/deng/monitor/ui/SettingsScreen.kt")
        assertTrue(
            "Settings/About must display the API host",
            settings.contains("API host: \${api.host}"),
        )
        assertFalse(
            "Settings/About must not display any token",
            settings.contains("cachedToken") || settings.contains("appSessionToken"),
        )
    }
}
