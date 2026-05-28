package my.id.deng.monitor.data

import android.content.Context
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
    }

    val tokenFlow: Flow<String?> = context.dataStore.data.map { it[KEY_TOKEN] }
    val ownerFlow: Flow<String?> = context.dataStore.data.map { it[KEY_OWNER] }
    val lastDeviceFlow: Flow<String?> = context.dataStore.data.map { it[KEY_LAST_DEVICE] }

    suspend fun saveSession(token: String, owner: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_TOKEN] = token
            prefs[KEY_OWNER] = owner
        }
    }

    suspend fun rememberDevice(deviceId: String) {
        context.dataStore.edit { prefs -> prefs[KEY_LAST_DEVICE] = deviceId }
    }

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }

    /**
     * Synchronous accessor for the network interceptor. Reads the latest
     * persisted token blocking the calling network thread briefly.
     */
    fun cachedToken(): String? = runBlocking {
        context.dataStore.data.first()[KEY_TOKEN]
    }
}
