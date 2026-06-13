package my.id.deng.monitor.data

import android.content.Context
import android.webkit.CookieManager
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking

private val Context.dataStore by preferencesDataStore(name = "deng_monitor_session")

class SessionStore(private val context: Context) {
    companion object {
        private val KEY_TOKEN = stringPreferencesKey("app_session_token")
        private val KEY_OWNER = stringPreferencesKey("owner_discord_user_id")
        private val KEY_LAST_DEVICE = stringPreferencesKey("last_device_id")
        private val KEY_WEB_LOGGED_IN = booleanPreferencesKey("web_logged_in")
        private val KEY_WEB_BOOTSTRAP_URL = stringPreferencesKey("pending_web_bootstrap_url")
    }

    val tokenFlow: Flow<String?> = context.dataStore.data.map { it[KEY_TOKEN] }
    val ownerFlow: Flow<String?> = context.dataStore.data.map { it[KEY_OWNER] }
    val lastDeviceFlow: Flow<String?> = context.dataStore.data.map { it[KEY_LAST_DEVICE] }
    val webLoggedInFlow: Flow<Boolean> = context.dataStore.data.map { it[KEY_WEB_LOGGED_IN] == true }
    val pendingWebBootstrapUrlFlow: Flow<String?> = context.dataStore.data.map { it[KEY_WEB_BOOTSTRAP_URL] }

    suspend fun saveSession(token: String, owner: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_TOKEN] = token
            prefs[KEY_OWNER] = owner
        }
    }

    suspend fun setWebLoggedIn(loggedIn: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_WEB_LOGGED_IN] = loggedIn
        }
    }

    suspend fun setPendingWebBootstrapUrl(url: String?) {
        context.dataStore.edit { prefs ->
            if (url.isNullOrBlank()) prefs.remove(KEY_WEB_BOOTSTRAP_URL)
            else prefs[KEY_WEB_BOOTSTRAP_URL] = url
        }
    }

    suspend fun consumePendingWebBootstrapUrl(): String? {
        var out: String? = null
        context.dataStore.edit { prefs ->
            out = prefs[KEY_WEB_BOOTSTRAP_URL]
            prefs.remove(KEY_WEB_BOOTSTRAP_URL)
        }
        return out
    }

    suspend fun rememberDevice(deviceId: String) {
        context.dataStore.edit { prefs -> prefs[KEY_LAST_DEVICE] = deviceId }
    }

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
        clearWebCookies()
    }

    suspend fun clearWebSession() {
        context.dataStore.edit { prefs ->
            prefs.remove(KEY_WEB_LOGGED_IN)
        }
        clearWebCookies()
    }

    private fun clearWebCookies() {
        val manager = CookieManager.getInstance()
        manager.removeAllCookies(null)
        manager.flush()
    }

    /**
     * Synchronous accessor for the network interceptor. Reads the latest
     * persisted token blocking the calling network thread briefly.
     */
    fun cachedToken(): String? = runBlocking {
        context.dataStore.data.first()[KEY_TOKEN]
    }

    fun isWebLoggedInBlocking(): Boolean = runBlocking {
        context.dataStore.data.first()[KEY_WEB_LOGGED_IN] == true
    }
}
