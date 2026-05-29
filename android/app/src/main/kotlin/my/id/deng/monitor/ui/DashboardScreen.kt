package my.id.deng.monitor.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import my.id.deng.monitor.data.DeviceSummary
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

/**
 * v1.0.6 redesign — a compact, device-centric dashboard.
 *
 * Shows ONLY the metrics the user asked for: Last Update, Interval, TOTAL,
 * ONLINE, DEAD, overall RAM, and a per-device RAM list. Counts are device
 * counts (TTL-based connection from the backend); a snapshot failure never
 * marks a device dead. No debug fields here — diagnostics live in the
 * Snapshot tab + probe.
 */
@Composable
fun DashboardScreen(api: MonitorApi, sessionStore: SessionStore) {
    val handle = rememberDeviceListHandle(api)
    val state by handle.state
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Dashboard",
                style = MaterialTheme.typography.headlineMedium,
                color = DengColors.TextPrimary,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            RefreshPill(onClick = { scope.launch { handle.refreshNow() } })
        }

        when (val s = state) {
            is DeviceListFetchState.Loading -> DashboardSkeleton()

            is DeviceListFetchState.Error -> ErrorCard(
                message = s.message,
                onRetry = { scope.launch { handle.refreshNow() } },
            )

            is DeviceListFetchState.Ready -> {
                if (s.devices.isEmpty()) {
                    EmptyDevicesCard()
                } else {
                    DashboardContent(s.devices, s.packageSummary)
                }
            }
        }
    }
}

@Composable
private fun DashboardContent(
    devices: List<DeviceSummary>,
    packageSummary: my.id.deng.monitor.data.DashboardPackageSummary,
) {
    // v1.0.8: headline cards are PACKAGE counts (configured Roblox packages),
    // not device counts. 8 configured packages all dead → TOTAL 8 / ONLINE 0 /
    // DEAD 8. Device connectivity is shown secondarily below.
    val pkgTotal = packageSummary.total
    val pkgOnline = packageSummary.online
    val pkgDead = packageSummary.dead

    val deviceTotal = devices.size
    val deviceOnline = devices.count { it.isConnected }
    val online = deviceOnline // drives the sync/offline indicator

    // Most recent heartbeat across all devices → "Last Update".
    val freshest = devices.minByOrNull { it.secondsSinceLastSeen ?: Long.MAX_VALUE }
    val lastSeenIso = freshest?.lastSeenAt
    val secsSince = freshest?.secondsSinceLastSeen
    val stale = deviceOnline == 0 && deviceTotal > 0

    // Overall RAM: sum(used)/sum(total) when totals are known; otherwise the
    // mean of reported percents. Null when no device reported RAM.
    val ramDevices = devices.mapNotNull { it.deviceRam }
    val totalUsed = ramDevices.sumOf { it.usedMb }
    val totalTotal = ramDevices.sumOf { it.totalMb }
    val overallPercent: Int? = when {
        totalTotal > 0 -> ((totalUsed.toLong() * 100) / totalTotal).toInt()
        ramDevices.any { it.effectivePercent != null } ->
            ramDevices.mapNotNull { it.effectivePercent }.average().toInt()
        else -> null
    }

    // Top section — header / sync indicator / Last Update / Interval.
    DengCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(RoundedCornerShape(999.dp))
                    .then(Modifier),
                contentAlignment = Alignment.Center,
            ) {
                Surface(
                    color = if (online > 0) DengColors.Success else DengColors.Danger,
                    shape = RoundedCornerShape(999.dp),
                    modifier = Modifier.size(10.dp),
                ) {}
            }
            Spacer(Modifier.width(8.dp))
            Text(
                if (online > 0) "Syncing" else "Offline",
                style = MaterialTheme.typography.labelLarge,
                color = if (online > 0) DengColors.Success else DengColors.Danger,
            )
            Spacer(Modifier.weight(1f))
            if (stale) {
                Surface(
                    color = DengColors.Warning.copy(alpha = 0.18f),
                    shape = RoundedCornerShape(999.dp),
                ) {
                    Text(
                        "STALE",
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        color = DengColors.Warning,
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        }
        Spacer(Modifier.height(12.dp))
        LabeledValue("Last Update", buildString {
            append(Format.timestamp(lastSeenIso))
            val rel = Format.relativeAgo(secsSince)
            if (rel != "—") append("  •  $rel")
        })
        Spacer(Modifier.height(4.dp))
        LabeledValue("Interval", "${DASHBOARD_POLL_SECONDS}s")
        Spacer(Modifier.height(4.dp))
        // Device connectivity is secondary to package stats.
        LabeledValue("Devices", "$deviceOnline / $deviceTotal online")
    }

    // Main stats row — PACKAGE TOTAL / ONLINE / DEAD + overall RAM.
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        CompactStat("TOTAL", pkgTotal.toString(), DengColors.Cyan, Modifier.weight(1f))
        CompactStat("ONLINE", pkgOnline.toString(), DengColors.Success, Modifier.weight(1f))
        CompactStat("DEAD", pkgDead.toString(), DengColors.Danger, Modifier.weight(1f))
        CompactStat(
            "RAM",
            overallPercent?.let { "$it%" } ?: "—",
            DengColors.Purple,
            Modifier.weight(1f),
        )
    }

    // RAM section — overall summary + per-device list (tap to expand).
    DengCard {
        Text(
            "RAM Details",
            style = MaterialTheme.typography.titleMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            overallPercent?.let { "Overall: $it% across $deviceTotal device${if (deviceTotal == 1) "" else "s"}" }
                ?: "Overall: RAM not reported yet",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
        Spacer(Modifier.height(12.dp))
        devices.forEach { device ->
            DeviceRamRow(device)
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun DeviceRamRow(device: DeviceSummary) {
    var expanded by remember(device.id) { mutableStateOf(false) }
    val accent = if (device.isConnected) DengColors.Success else DengColors.Danger
    val ramText = device.deviceRam?.displayText ?: "—"

    Surface(
        color = DengColors.CardBg.copy(alpha = 0.5f),
        border = BorderStroke(1.dp, accent.copy(alpha = 0.30f)),
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .clickable { expanded = !expanded }
            .animateContentSize(),
    ) {
        Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(
                    color = accent,
                    shape = RoundedCornerShape(999.dp),
                    modifier = Modifier.size(8.dp),
                ) {}
                Spacer(Modifier.width(10.dp))
                // "62/100% - Cloud Phone 1" style line.
                Text(
                    "$ramText - ${device.displayName}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = DengColors.TextPrimary,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.weight(1f),
                )
                ConnectionBadge(device.connectionLabel, device.isConnected)
            }
            if (expanded) {
                Spacer(Modifier.height(10.dp))
                DetailLine("Last seen", buildString {
                    append(Format.timestamp(device.lastSeenAt))
                    val rel = Format.relativeAgo(device.secondsSinceLastSeen)
                    if (rel != "—") append("  •  $rel")
                })
                DetailLine("RAM", device.deviceRam?.displayText ?: "not reported")
                DetailLine("Version", "${device.toolVersion ?: "—"}  •  ${device.channel ?: "stable"}")
                DetailLine("Snapshot", snapshotResultLabel(device.snapshotLastResult))
            }
        }
    }
}

private fun snapshotResultLabel(result: String?): String = when (result) {
    null -> "—"
    "success" -> "OK"
    "failed_no_screencap" -> "screencap unavailable"
    "failed_root_denied" -> "root denied"
    "failed_empty_output" -> "empty output"
    "failed_invalid_png" -> "invalid PNG"
    "failed_upload_http" -> "upload failed"
    "failed_timeout" -> "timed out"
    "capture_failed" -> "capture failed"
    else -> result
}

@Composable
private fun DetailLine(label: String, value: String) {
    Row(modifier = Modifier.padding(vertical = 2.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
            modifier = Modifier.width(86.dp),
        )
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextPrimary,
        )
    }
}

@Composable
private fun LabeledValue(label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            label.uppercase(),
            style = MaterialTheme.typography.labelMedium,
            color = DengColors.TextMuted,
            modifier = Modifier.width(110.dp),
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun CompactStat(label: String, value: String, accent: Color, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.clip(RoundedCornerShape(16.dp)),
        color = DengColors.CardBg,
        border = BorderStroke(1.dp, accent.copy(alpha = 0.35f)),
    ) {
        Column(
            modifier = Modifier.padding(vertical = 14.dp, horizontal = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(value, style = MaterialTheme.typography.titleLarge, color = accent, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Text(label, style = MaterialTheme.typography.labelSmall, color = DengColors.TextMuted)
        }
    }
}

@Composable
private fun RefreshPill(onClick: () -> Unit) {
    Surface(
        color = DengColors.Cyan.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, DengColors.Cyan.copy(alpha = 0.4f)),
        shape = RoundedCornerShape(999.dp),
        modifier = Modifier.clip(RoundedCornerShape(999.dp)).clickable { onClick() },
    ) {
        Text(
            "Refresh",
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp),
            color = DengColors.Cyan,
            style = MaterialTheme.typography.labelLarge,
        )
    }
}

@Composable
private fun EmptyDevicesCard() {
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

/** Modern skeleton — shimmer-free but clearly a placeholder, never forever. */
@Composable
private fun DashboardSkeleton() {
    DengCard {
        SkeletonBar(0.4f)
        Spacer(Modifier.height(12.dp))
        SkeletonBar(0.7f)
        Spacer(Modifier.height(8.dp))
        SkeletonBar(0.55f)
    }
    Spacer(Modifier.height(12.dp))
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        repeat(4) {
            Surface(
                modifier = Modifier.weight(1f).height(72.dp).clip(RoundedCornerShape(16.dp)),
                color = DengColors.CardBg,
                border = BorderStroke(1.dp, DengColors.BorderCyan),
            ) {}
        }
    }
    Spacer(Modifier.height(12.dp))
    DengCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(
                color = DengColors.Cyan,
                strokeWidth = 2.dp,
                modifier = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(12.dp))
            Text("Loading devices…", color = DengColors.TextMuted)
        }
    }
}

@Composable
private fun SkeletonBar(widthFraction: Float) {
    Surface(
        color = DengColors.TextMuted.copy(alpha = 0.12f),
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(widthFraction).height(14.dp),
    ) {}
}
