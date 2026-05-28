package my.id.deng.monitor.ui

import androidx.compose.runtime.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.DeviceStatus
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

sealed interface DeviceFetchState {
    data object Loading : DeviceFetchState
    data class NoDevices(val message: String = "No cloud phone is connected yet.") : DeviceFetchState
    data class Ready(val status: DeviceStatus) : DeviceFetchState
    data class Error(val message: String) : DeviceFetchState
}

/**
 * Polls the backend for the current device's status. Used by Dashboard /
 * Packages / Snapshot screens.
 *
 * The poll interval comes from the device's own monitor_settings, so the
 * user can tune it from the Settings screen.
 */
@Composable
fun rememberDeviceStatus(
    api: MonitorApi,
    sessionStore: SessionStore,
    pollSeconds: Int = 5,
): State<DeviceFetchState> {
    val state = remember { mutableStateOf<DeviceFetchState>(DeviceFetchState.Loading) }

    LaunchedEffect(Unit) {
        var interval = pollSeconds.coerceIn(2, 60)
        var deviceId: String? = sessionStore.lastDeviceFlow.first()

        while (true) {
            try {
                if (deviceId == null) {
                    val devices = api.listDevices().devices
                    if (devices.isEmpty()) {
                        state.value = DeviceFetchState.NoDevices()
                        delay(5_000)
                        continue
                    }
                    deviceId = devices.first().id
                    sessionStore.rememberDevice(deviceId!!)
                }
                val status = api.deviceStatus(deviceId!!)
                state.value = DeviceFetchState.Ready(status)
                interval = status.settings.appRefreshIntervalSeconds.coerceIn(2, 60)
            } catch (e: ApiException) {
                if (e.statusCode == 404) {
                    // Saved device disappeared — reset and retry.
                    deviceId = null
                    sessionStore.rememberDevice("")
                } else {
                    state.value = DeviceFetchState.Error(e.safeMessage)
                }
            } catch (e: Throwable) {
                state.value = DeviceFetchState.Error("Network error: ${e.javaClass.simpleName}")
            }
            delay(interval * 1_000L)
        }
    }
    return state
}
