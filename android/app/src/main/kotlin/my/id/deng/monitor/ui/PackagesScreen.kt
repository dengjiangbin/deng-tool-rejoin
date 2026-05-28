package my.id.deng.monitor.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.PackageState
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

@Composable
fun PackagesScreen(api: MonitorApi, sessionStore: SessionStore) {
    val state by rememberDeviceStatus(api, sessionStore)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Packages",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )

        when (val s = state) {
            is DeviceFetchState.Loading -> Text("Loading…", color = DengColors.TextMuted)
            is DeviceFetchState.Error   -> ErrorBanner(s.message)
            is DeviceFetchState.NoDevices -> Text(s.message, color = DengColors.TextMuted)
            is DeviceFetchState.Ready -> {
                val pkgs = s.status.packages
                if (pkgs.isEmpty()) {
                    DengCard { Text("No packages reported yet.", color = DengColors.TextMuted) }
                } else {
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        items(pkgs, key = { it.packageName }) { pkg ->
                            PackageCard(pkg)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PackageCard(pkg: PackageState) {
    DengCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    Format.shortPackage(pkg.packageName),
                    style = MaterialTheme.typography.titleMedium,
                    color = DengColors.TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    "User: ${Format.safeUsername(pkg.username)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = DengColors.TextMuted,
                )
            }
            StateBadge(pkg.state)
        }

        Spacer(Modifier.height(12.dp))
        Row(modifier = Modifier.fillMaxWidth()) {
            Stat(label = "RAM",     value = Format.ram(pkg.ramMb))
            Stat(label = "Runtime", value = Format.runtime(pkg.runtimeSeconds))
            Stat(label = "Restarts", value = pkg.restartCount.toString())
        }
        pkg.safeErrorReason?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = DengColors.Danger, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun RowScope.Stat(label: String, value: String) {
    Column(modifier = Modifier.weight(1f)) {
        Text(label.uppercase(), style = MaterialTheme.typography.labelMedium, color = DengColors.TextMuted)
        Text(value, style = MaterialTheme.typography.titleMedium, color = DengColors.TextPrimary)
    }
}
