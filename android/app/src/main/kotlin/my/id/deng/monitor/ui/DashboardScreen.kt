package my.id.deng.monitor.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

@Composable
fun DashboardScreen(api: MonitorApi, sessionStore: SessionStore) {
    val state by rememberDeviceStatus(api, sessionStore)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Dashboard",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )

        when (val s = state) {
            is DeviceFetchState.Loading -> {
                LoadingCard()
            }

            is DeviceFetchState.Error -> {
                ErrorBanner(s.message)
            }

            is DeviceFetchState.NoDevices -> {
                DengCard {
                    Text("No cloud phone connected", style = MaterialTheme.typography.titleLarge, color = DengColors.TextPrimary)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Run `deng-rejoin` on your cloud phone in Termux. It will connect " +
                        "automatically after license verification.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = DengColors.TextMuted,
                    )
                }
            }

            is DeviceFetchState.Ready -> {
                val st = s.status
                // v1.0.4: prefer the server-computed connection state.
                // If the backend doesn't provide it (older deploy), fall
                // back to the legacy sticky boolean.
                DeviceHeaderCard(
                    label = st.device.deviceLabel ?: "Cloud Phone",
                    connectionLabel = st.device.connectionLabel,
                    isConnected = st.device.isConnected,
                    lastSeenAt = st.device.lastSeenAt,
                    secondsSinceLastSeen = st.device.secondsSinceLastSeen,
                    version = st.device.toolVersion,
                    channel = st.device.channel,
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    StatTile(
                        label = "Total",
                        value = st.summary.total.toString(),
                        modifier = Modifier.weight(1f),
                    )
                    StatTile(
                        label = "Online",
                        value = st.summary.online.toString(),
                        accent = DengColors.Success,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    StatTile(
                        label = "Dead",
                        value = st.summary.dead.toString(),
                        accent = DengColors.Danger,
                        modifier = Modifier.weight(1f),
                    )
                    StatTile(
                        // v1.0.4: replaces "Relaunching" — the public
                        // 5-state model uses Launching.
                        label = "Launching",
                        value = (st.summary.launching + st.summary.relaunching).toString(),
                        accent = DengColors.Cyan,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    StatTile(
                        label = "Joining",
                        value = st.summary.joining.toString(),
                        accent = DengColors.Purple,
                        modifier = Modifier.weight(1f),
                    )
                    StatTile(
                        label = "No Heartbeat",
                        value = st.summary.noHeartbeat.toString(),
                        accent = DengColors.Warning,
                        modifier = Modifier.weight(1f),
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    StatTile(
                        label = "Total RAM",
                        value = Format.ram(st.summary.totalRamMb),
                        accent = DengColors.Pink,
                        modifier = Modifier.weight(1f),
                    )
                    StatTile(
                        label = "Average RAM",
                        value = Format.ram(st.summary.averageRamMb),
                        accent = DengColors.Purple,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

@Composable
private fun DeviceHeaderCard(
    label: String,
    connectionLabel: String,
    isConnected: Boolean,
    lastSeenAt: String?,
    secondsSinceLastSeen: Long?,
    version: String?,
    channel: String?,
) {
    DengCard {
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.titleLarge, color = DengColors.TextPrimary)
            Spacer(Modifier.weight(1f))
            // v1.0.4: badge uses the literal Connected / Disconnected
            // label so users can tell the difference instantly. The
            // old code reused the package badge (Online/Dead), which
            // overloaded two unrelated state vocabularies.
            ConnectionBadge(connectionLabel, isConnected)
        }
        Spacer(Modifier.height(8.dp))
        val staleSuffix = if (!isConnected && secondsSinceLastSeen != null) {
            " (no push for ${secondsSinceLastSeen}s)"
        } else ""
        Text(
            "Last update: ${my.id.deng.monitor.util.Format.timestamp(lastSeenAt)}$staleSuffix",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
        Text(
            "Version: ${version ?: "—"}  •  Channel: ${channel ?: "stable"}",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
    }
}

@Composable
private fun LoadingCard() {
    DengCard {
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            CircularProgressIndicator(
                color = DengColors.Cyan,
                strokeWidth = 2.dp,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(12.dp))
            Text("Loading…", color = DengColors.TextMuted)
        }
    }
}
