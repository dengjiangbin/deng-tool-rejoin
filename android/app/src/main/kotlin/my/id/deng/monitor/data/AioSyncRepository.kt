package my.id.deng.monitor.data

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class AioSyncRepository(
    private val api: MonitorApi,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _tracker = MutableStateFlow<AioTrackerPayload?>(null)
    private val _lastError = MutableStateFlow<String?>(null)
    private val _syncing = MutableStateFlow(false)

    fun observeTracker(): StateFlow<AioTrackerPayload?> = _tracker.asStateFlow()
    val lastError: StateFlow<String?> = _lastError.asStateFlow()
    val isBackgroundSyncing: StateFlow<Boolean> = _syncing.asStateFlow()

    fun refreshInBackground(forceBootstrap: Boolean) {
        if (_syncing.value) return
        scope.launch {
            _syncing.value = true
            try {
                _tracker.value = api.fetchTrackerSync()
                _lastError.value = null
            } catch (e: Exception) {
                _lastError.value = friendlyNetworkError(e, api.host)
            } finally {
                _syncing.value = false
            }
        }
    }
}
