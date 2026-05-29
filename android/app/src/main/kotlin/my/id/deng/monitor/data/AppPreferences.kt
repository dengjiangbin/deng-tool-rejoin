package my.id.deng.monitor.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.appPrefsStore by preferencesDataStore(name = "deng_monitor_prefs")

/** Theme preference. SYSTEM follows the OS dark-mode setting. */
enum class ThemeMode { SYSTEM, LIGHT, DARK }

/**
 * Local-only UI preferences (theme + username privacy). These are device
 * preferences and NEVER change backend identity, stats matching, or the
 * Discord account linking — they only affect what is displayed.
 */
class AppPreferences(private val context: Context) {
    companion object {
        private val KEY_THEME = stringPreferencesKey("theme_mode")
        private val KEY_HIDE_USERNAME = booleanPreferencesKey("hide_username")
    }

    val themeModeFlow: Flow<ThemeMode> = context.appPrefsStore.data.map { prefs ->
        when (prefs[KEY_THEME]) {
            "light" -> ThemeMode.LIGHT
            "dark" -> ThemeMode.DARK
            else -> ThemeMode.SYSTEM
        }
    }

    val hideUsernameFlow: Flow<Boolean> = context.appPrefsStore.data.map { prefs ->
        prefs[KEY_HIDE_USERNAME] ?: false
    }

    suspend fun setThemeMode(mode: ThemeMode) {
        context.appPrefsStore.edit { prefs ->
            prefs[KEY_THEME] = when (mode) {
                ThemeMode.LIGHT -> "light"
                ThemeMode.DARK -> "dark"
                ThemeMode.SYSTEM -> "system"
            }
        }
    }

    suspend fun setHideUsername(hide: Boolean) {
        context.appPrefsStore.edit { prefs -> prefs[KEY_HIDE_USERNAME] = hide }
    }
}
