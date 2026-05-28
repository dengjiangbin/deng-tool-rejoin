package my.id.deng.monitor

import android.app.Application
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

/**
 * Tiny app-wide container. Avoids pulling in a DI framework for a small app.
 */
class MonitorApp : Application() {
    lateinit var sessionStore: SessionStore
        private set
    lateinit var api: MonitorApi
        private set

    override fun onCreate() {
        super.onCreate()
        sessionStore = SessionStore(applicationContext)
        api = MonitorApi(
            baseUrl = BuildConfig.BRIDGE_URL,
            tokenProvider = { sessionStore.cachedToken() },
        )
    }
}
