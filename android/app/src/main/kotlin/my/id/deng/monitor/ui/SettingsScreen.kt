package my.id.deng.monitor.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.MonitorSettings
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors

private val SNAPSHOT_OPTIONS = listOf(
    0 to "Off",
    15 to "15 seconds",
    30 to "30 seconds",
    60 to "60 seconds",
    300 to "5 minutes",
)

private val REFRESH_OPTIONS = listOf(2, 3, 5, 10, 15, 30, 60)

@Composable
fun SettingsScreen(api: MonitorApi, sessionStore: SessionStore) {
    // v1.0.4: we need the *handle* (not just the state) so we can ask
    // the poller to re-fetch immediately after a successful save.
    // Previously the radio button only flipped to its new position on
    // the next regular poll (2–60s later), which felt like the save
    // had silently failed.
    val handle = rememberDeviceStatusHandle(api, sessionStore)
    val state by handle.state
    val scope = rememberCoroutineScope()

    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var saveOk by remember { mutableStateOf(false) }
    // Optimistic local override so the radio updates the instant the
    // PATCH returns 200, even before the next poll. We keep it nullable
    // and clear it as soon as the server-side row catches up.
    var optimistic by remember { mutableStateOf<MonitorSettings?>(null) }

    // Settings now contains many cards (snapshot interval x5, refresh x7,
    // logout, about) — on small phones / cloud phones they don't fit on one
    // screen. Wrap the whole content in a vertical scroll + nav-bar padding
    // so the bottom cards (snapshot interval / Save) are always reachable.
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .imePadding()
            .navigationBarsPadding()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Settings",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )

        when (val s = state) {
            is DeviceFetchState.Loading -> Text("Loading…", color = DengColors.TextMuted)
            is DeviceFetchState.Error -> ErrorBanner(s.message)
            is DeviceFetchState.NoDevices -> {
                DengCard { Text(s.message, color = DengColors.TextMuted) }
                LogoutCard(sessionStore = sessionStore, scope = scope)
            }
            is DeviceFetchState.Ready -> {
                val device = s.status.device
                // The server value is the source of truth. The
                // optimistic value is only "remembered" UNTIL the next
                // poll comes back agreeing with it, then we drop it so
                // we don't get permanently stuck on stale local state.
                val serverSettings = s.status.settings
                val current = optimistic ?: serverSettings
                LaunchedEffect(serverSettings) {
                    val o = optimistic
                    if (o != null && o == serverSettings) optimistic = null
                }

                if (error != null) ErrorBanner(error!!)
                if (saveOk) {
                    DengCard {
                        Text("Settings saved.", color = DengColors.Success)
                    }
                }

                // Shared save helper — captures the optimistic update +
                // immediate poll-refresh dance so both option lists
                // behave identically.
                fun saveSettings(next: MonitorSettings) {
                    error = null
                    saveOk = false
                    saving = true
                    optimistic = next  // instant UI feedback
                    scope.launch {
                        runCatching {
                            api.updateSettings(device.id, next)
                        }.onFailure { t ->
                            // Rollback the optimistic flip on failure
                            // so the radio doesn't lie about state.
                            optimistic = null
                            error = (t as? ApiException)?.safeMessage ?: t.javaClass.simpleName
                        }.onSuccess {
                            saveOk = true
                            // Kick the poller immediately so the
                            // settings card reflects the saved row in
                            // ~one round-trip instead of one full poll
                            // interval (2–60s).
                            handle.refreshNow()
                        }
                        saving = false
                    }
                }

                DengCard {
                    Text("Snapshot interval", style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "How often the cloud phone uploads a fresh screenshot.",
                        style = MaterialTheme.typography.bodySmall,
                        color = DengColors.TextMuted,
                    )
                    Spacer(Modifier.height(12.dp))
                    SNAPSHOT_OPTIONS.forEach { (sec, label) ->
                        OptionRow(
                            label = label,
                            selected = current.snapshotIntervalSeconds == sec,
                            enabled = !saving,
                            onSelect = {
                                saveSettings(current.copy(snapshotIntervalSeconds = sec))
                            },
                        )
                    }
                }

                DengCard {
                    Text("App refresh", style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "How often this app polls the backend for new status.",
                        style = MaterialTheme.typography.bodySmall,
                        color = DengColors.TextMuted,
                    )
                    Spacer(Modifier.height(12.dp))
                    REFRESH_OPTIONS.forEach { sec ->
                        OptionRow(
                            label = "${sec}s",
                            selected = current.appRefreshIntervalSeconds == sec,
                            enabled = !saving,
                            onSelect = {
                                saveSettings(current.copy(appRefreshIntervalSeconds = sec))
                            },
                        )
                    }
                }

                LogoutCard(sessionStore = sessionStore, scope = scope)
            }
        }

        DengCard {
            Text("About", style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
            Spacer(Modifier.height(6.dp))
            Text("DENG Tool: Rejoin · App v${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})",
                color = DengColors.TextMuted, style = MaterialTheme.typography.bodySmall)
            Text("Backend: ${BuildConfig.BRIDGE_URL}",
                color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
            Text("Monitoring companion only. Rejoin package versions are selected via the website / Discord.",
                color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
        }

        // Bottom breathing room so the last card never sits flush against
        // the navigation-bar / gesture inset on edge-to-edge devices.
        Spacer(Modifier.height(48.dp))
    }
}

@Composable
private fun OptionRow(label: String, selected: Boolean, enabled: Boolean, onSelect: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(
            selected = selected,
            onClick = onSelect,
            enabled = enabled,
            colors = RadioButtonDefaults.colors(
                selectedColor = DengColors.Cyan,
                unselectedColor = DengColors.BorderMuted,
            ),
        )
        Spacer(Modifier.width(8.dp))
        Text(label, color = DengColors.TextPrimary)
    }
}

@Composable
private fun LogoutCard(sessionStore: SessionStore, scope: kotlinx.coroutines.CoroutineScope) {
    DengCard {
        Text("Account", style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
        Spacer(Modifier.height(8.dp))
        Text(
            "Log out of this device. You'll need a new pairing code to sign in again.",
            color = DengColors.TextMuted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(12.dp))
        DengGradientButton(
            text = "Log out",
            onClick = { scope.launch { sessionStore.clear() } },
        )
    }
}
