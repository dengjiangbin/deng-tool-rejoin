package my.id.deng.monitor

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Source-scanning contract tests for the Fish It integration + theme +
 * Hide Username privacy. These lock the wiring that's hard to unit-test on the
 * JVM (Compose screens, DataStore keys, nav routes) so regressions are caught.
 *
 * Working directory for the test task is `android/app/`.
 */
class FishItContractTest {
    private fun read(path: String): String {
        val f = File(path)
        require(f.exists()) { "expected file at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    private val src = "src/main/kotlin/my/id/deng/monitor"

    // ── Navigation / screens ─────────────────────────────────────────────────
    @Test fun `app has exactly live tracker rejoin packages and settings nav`() {
        val appRoot = read("$src/ui/AppRoot.kt")
        assertTrue("live_tracker NavItem must exist", appRoot.contains(Regex(""""live_tracker",\s*"Live Tracker"""")))
        assertTrue("rejoin NavItem must exist", appRoot.contains(Regex(""""rejoin",\s*"Rejoin"""")))
        assertTrue("packages NavItem must exist", appRoot.contains(Regex(""""packages",\s*"Packages"""")))
        assertTrue("settings NavItem must exist", appRoot.contains(Regex(""""settings",\s*"Settings"""")))
        assertTrue("live tracker composable route must exist", appRoot.contains("composable(\"live_tracker\")"))
        assertTrue("rejoin composable route must exist", appRoot.contains("composable(\"rejoin\")"))
        assertTrue("LiveTrackerWebViewScreen must be wired", appRoot.contains("LiveTrackerWebViewScreen("))
        assertTrue("LoginWebViewScreen must be wired for signed-out users", appRoot.contains("LoginWebViewScreen("))
        // Dashboard / Inventory tabs are removed from the APK bottom nav.
        assertTrue("APK nav must not contain a Dashboard tab", !appRoot.contains("\"Dashboard\""))
        assertTrue("APK nav must not contain an inventory route", !appRoot.contains("composable(\"inventory\")"))
    }

    @Test fun `FishItScreen has Daily, Stats and Fish sub-tabs`() {
        val s = read("$src/ui/FishItScreen.kt")
        assertTrue(s.contains("\"daily\""))
        assertTrue(s.contains("\"stats\""))
        assertTrue(s.contains("\"fish\""))
        assertTrue("must render a fish grid", s.contains("LazyVerticalGrid"))
        // Daily filter periods.
        listOf("today", "yesterday", "7d", "30d", "all").forEach {
            assertTrue("daily period $it must be present", s.contains("\"$it\""))
        }
    }

    @Test fun `FishItScreen shows Retry on API errors and empty states`() {
        val s = read("$src/ui/FishItScreen.kt")
        assertTrue("uses ErrorCard with retry", s.contains("ErrorCard("))
        assertTrue("has empty-state copy", s.contains("No catches found for this period."))
        assertTrue("has not-yet-data copy", s.contains("You do not have Fish It stats yet."))
    }

    @Test fun `FishItScreen lazy-loads images with a fallback`() {
        val s = read("$src/ui/FishItScreen.kt")
        assertTrue("uses Coil async image", s.contains("SubcomposeAsyncImage"))
        assertTrue("renders a fallback box on missing/error image", s.contains("FallbackBox"))
    }

    // ── API ──────────────────────────────────────────────────────────────────
    @Test fun `FishItScreen uses fishFriendlyError for auth-aware API errors`() {
        val s = read("$src/ui/FishItScreen.kt")
        assertTrue("must use fishFriendlyError", s.contains("fishFriendlyError"))
        val api = read("$src/data/MonitorApi.kt")
        assertTrue("401 maps to Discord sign-in copy", api.contains("Sign in with Discord to view your Fish It stats."))
    }

    @Test fun `FishGrid model parses items not legacy fish array`() {
        val models = read("$src/data/Models.kt")
        assertTrue(models.contains("val items: List<FishCard>"))
        assertFalse("legacy fish field must be gone", models.contains("val fish: List<FishCard>"))
    }

    @Test fun `MonitorApi exposes authenticated Fish It endpoints`() {
        val api = read("$src/data/MonitorApi.kt")
        listOf(
            "/api/fishit/me",
            "/api/fishit/me/daily",
            "/api/fishit/me/stats",
            "/api/fishit/me/fish",
        ).forEach { assertTrue("missing endpoint $it", api.contains(it)) }
        assertTrue("fish endpoints must be authenticated", api.contains("fishProfile") && api.contains("auth = true"))
    }

    // ── Theme ──────────────────────────────────────────────────────────────────
    @Test fun `theme supports both light and dark palettes`() {
        val color = read("$src/ui/theme/Color.kt")
        assertTrue("LightPalette must exist", color.contains("val LightPalette"))
        assertTrue("DarkPalette must exist", color.contains("val DarkPalette"))
        val theme = read("$src/ui/theme/Theme.kt")
        assertTrue("theme must take a darkTheme flag", theme.contains("darkTheme: Boolean"))
        assertTrue("theme must build a lightColorScheme", theme.contains("lightColorScheme"))
    }

    @Test fun `theme mode + hide username are persisted in DataStore`() {
        val prefs = read("$src/data/AppPreferences.kt")
        assertTrue(prefs.contains("preferencesDataStore"))
        assertTrue(prefs.contains("theme_mode"))
        assertTrue(prefs.contains("hide_username"))
        assertTrue("ThemeMode enum exists", prefs.contains("enum class ThemeMode"))
    }

    @Test fun `MainActivity follows system theme by default and applies preference`() {
        val main = read("$src/MainActivity.kt")
        assertTrue("default follows system", main.contains("isSystemInDarkTheme"))
        assertTrue("ThemeMode honored", main.contains("ThemeMode.LIGHT") && main.contains("ThemeMode.DARK"))
        assertTrue("hide username provided", main.contains("LocalHideUsername provides"))
    }

    // ── Hide Username ───────────────────────────────────────────────────────────
    @Test fun `settings expose Appearance theme buttons and Hide Username switch`() {
        val s = read("$src/ui/SettingsScreen.kt")
        assertTrue(s.contains("Appearance"))
        assertTrue(s.contains("Hide Username"))
        assertTrue("theme options present", s.contains("ThemeMode.SYSTEM") && s.contains("ThemeMode.LIGHT") && s.contains("ThemeMode.DARK"))
        assertTrue("uses a Switch", s.contains("Switch("))
    }

    @Test fun `username display sites honor the hide username flag`() {
        val packages = read("$src/ui/PackagesScreen.kt")
        assertTrue("packages mask username", packages.contains("Format.displayUsername(pkg.username, LocalHideUsername.current)"))
        val fish = read("$src/ui/FishItScreen.kt")
        assertTrue("fish stats mask username", fish.contains("Format.displayUsername(s.username, LocalHideUsername.current)"))
    }
}
