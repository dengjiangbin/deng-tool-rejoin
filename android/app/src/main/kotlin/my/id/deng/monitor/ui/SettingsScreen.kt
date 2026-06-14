package my.id.deng.monitor.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import my.id.deng.monitor.BuildConfig
import my.id.deng.monitor.R
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.AppPreferences
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.MonitorSettings
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.data.ThemeMode
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
fun SettingsScreen(api: MonitorApi, sessionStore: SessionStore, appPreferences: AppPreferences) {
    // v1.0.4: we need the *handle* (not just the state) so we can ask
    // the poller to re-fetch immediately after a successful save.
    // Previously the radio button only flipped to its new position on
    // the next regular poll (2–60s later), which felt like the save
    // had silently failed.
    val handle = rememberDeviceStatusHandle(api, sessionStore)
    val state by handle.state
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

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

        AppearanceCard(appPreferences = appPreferences, scope = scope)

        when (val s = state) {
            is DeviceFetchState.Loading -> Text("Loading…", color = DengColors.TextMuted)
            is DeviceFetchState.Error -> ErrorCard(
                message = s.message,
                onRetry = { scope.launch { handle.refreshNow() } },
            )
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
            Text("DENG All In One · App v${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})",
                color = DengColors.TextMuted, style = MaterialTheme.typography.bodySmall)
            Text("Release: ${context.getString(R.string.apk_release_marker)}",
                color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
            // Show the API host so connectivity issues are easy to diagnose.
            Spacer(Modifier.height(4.dp))
            Text("API host: ${api.host}",
                color = DengColors.Cyan, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            Text("Backend: ${BuildConfig.BRIDGE_URL}",
                color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(4.dp))
            Text("Monitoring companion only. Rejoin package versions are selected via the website / Discord.",
                color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
        }

        // Bottom breathing room so the last card never sits flush against
        // the navigation-bar / gesture inset on edge-to-edge devices.
        Spacer(Modifier.height(48.dp))
    }
}

@Composable
private fun AppearanceCard(appPreferences: AppPreferences, scope: kotlinx.coroutines.CoroutineScope) {
    val themeMode by appPreferences.themeModeFlow.collectAsState(initial = ThemeMode.SYSTEM)
    val hideUsername by appPreferences.hideUsernameFlow.collectAsState(initial = false)

    DengCard {
        Text("Appearance", style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
        Spacer(Modifier.height(8.dp))
        Text("Theme", color = DengColors.TextMuted, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            listOf(
                ThemeMode.SYSTEM to "System",
                ThemeMode.LIGHT to "Light",
                ThemeMode.DARK to "Dark",
            ).forEach { (mode, label) ->
                val selected = themeMode == mode
                Surface(
                    color = if (selected) DengColors.Cyan.copy(alpha = 0.18f) else DengColors.CardSoft,
                    border = androidx.compose.foundation.BorderStroke(1.dp, if (selected) DengColors.Cyan else DengColors.BorderMuted),
                    shape = androidx.compose.foundation.shape.RoundedCornerShape(10.dp),
                    modifier = Modifier.weight(1f).clickable { scope.launch { appPreferences.setThemeMode(mode) } },
                ) {
                    Text(
                        label,
                        modifier = Modifier.padding(vertical = 10.dp),
                        color = if (selected) DengColors.TextPrimary else DengColors.TextMuted,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        style = MaterialTheme.typography.labelLarge,
                    )
                }
            }
        }
        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                Text("Hide Username", color = DengColors.TextPrimary, style = MaterialTheme.typography.bodyLarge)
                Text(
                    "Mask your Discord name in the app (e.g. d*****n). Does not change your account or stats.",
                    color = DengColors.TextMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Spacer(Modifier.width(12.dp))
            Switch(
                checked = hideUsername,
                onCheckedChange = { scope.launch { appPreferences.setHideUsername(it) } },
                colors = SwitchDefaults.colors(
                    checkedThumbColor = DengColors.Cyan,
                    checkedTrackColor = DengColors.Cyan.copy(alpha = 0.4f),
                ),
            )
        }
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
