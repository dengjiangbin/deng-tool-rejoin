package my.id.deng.monitor.ui

import androidx.compose.runtime.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
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
 * v1.0.4: Lightweight handle that consumers (Settings, Dashboard, etc.)
 * use to talk to the polling effect:
 *   * `state` — the current fetch state.
 *   * `refreshNow()` — interrupt the current sleep and re-poll immediately.
 *     This is what fixes the v1.0.3 "saved settings only appear after I
 *     leave the screen and come back" bug. Settings calls it after a
 *     successful PATCH so the UI reflects the new server-side row on the
 *     very next frame.
 */
class DeviceStatusHandle internal constructor(
    val state: State<DeviceFetchState>,
    private val refreshChannel: MutableSharedFlow<Unit>,
) {
    suspend fun refreshNow() {
        refreshChannel.emit(Unit)
    }
}

/**
 * Polls the backend for the current device's status. Used by Dashboard /
 * Packages / Snapshot screens.
 *
 * The poll interval comes from the device's own monitor_settings, so the
 * user can tune it from the Settings screen.
 */
@Composable
fun rememberDeviceStatusHandle(
    api: MonitorApi,
    sessionStore: SessionStore,
    pollSeconds: Int = 5,
): DeviceStatusHandle {
    val state = remember { mutableStateOf<DeviceFetchState>(DeviceFetchState.Loading) }
    val refreshChannel = remember { MutableSharedFlow<Unit>(extraBufferCapacity = 4) }

    LaunchedEffect(Unit) {
        var interval = pollSeconds.coerceIn(2, 60)
        var deviceId: String? = sessionStore.lastDeviceFlow.first()

        while (true) {
            try {
                if (deviceId == null) {
                    val devices = api.listDevices().devices
                    if (devices.isEmpty()) {
                        state.value = DeviceFetchState.NoDevices()
                        // No device yet — wait either for a refresh
                        // request or 5s, whichever comes first.
                        sleepOrRefresh(5_000L, refreshChannel)
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
                    deviceId = null
                    sessionStore.rememberDevice("")
                } else {
                    state.value = DeviceFetchState.Error(e.safeMessage)
                }
            } catch (ce: CancellationException) {
                throw ce
            } catch (e: Throwable) {
                state.value = DeviceFetchState.Error("Network error: ${e.javaClass.simpleName}")
            }
            sleepOrRefresh(interval * 1_000L, refreshChannel)
        }
    }
    return remember(state, refreshChannel) {
        DeviceStatusHandle(state, refreshChannel)
    }
}

/**
 * Backwards-compatible wrapper so screens that only need the state
 * (Dashboard, Packages, Snapshot) don't have to learn about the handle.
 */
@Composable
fun rememberDeviceStatus(
    api: MonitorApi,
    sessionStore: SessionStore,
    pollSeconds: Int = 5,
): State<DeviceFetchState> = rememberDeviceStatusHandle(api, sessionStore, pollSeconds).state

/**
 * Suspend until [millis] elapses OR the refresh channel emits — whichever
 * comes first. Cancellable. Returns silently in both cases.
 *
 * Implementation note: we use `withTimeoutOrNull` + `first()` because it
 * composes cleanly with structured cancellation. If the timeout fires,
 * `first()` is cancelled (which is fine — we just want the loop to tick).
 * If `first()` returns before the timeout, the timeout block returns
 * Unit early and we re-poll immediately. No race with the shared flow's
 * buffer because we created it with extraBufferCapacity=4.
 */
private suspend fun sleepOrRefresh(
    millis: Long,
    refreshChannel: MutableSharedFlow<Unit>,
) {
    // withTimeoutOrNull returns null on expiry (no exception) and the
    // wrapped first() suspends until either expiry or a refresh emit.
    withTimeoutOrNull(millis) { refreshChannel.first() }
}
