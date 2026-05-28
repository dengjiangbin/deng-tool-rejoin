package my.id.deng.monitor.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import android.graphics.BitmapFactory
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

@Composable
fun SnapshotScreen(api: MonitorApi, sessionStore: SessionStore) {
    val state by rememberDeviceStatus(api, sessionStore)
    var bitmap by remember { mutableStateOf<ImageBitmap?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var lastFetchedAt by remember { mutableStateOf<Long?>(null) }
    val scope = rememberCoroutineScope()

    val deviceId = (state as? DeviceFetchState.Ready)?.status?.device?.id
    val intervalSec = (state as? DeviceFetchState.Ready)?.status?.settings?.snapshotIntervalSeconds ?: 0

    suspend fun fetch() {
        if (deviceId == null) return
        loading = true
        error = null
        try {
            val bytes = api.snapshotBytes(deviceId)
            if (bytes == null) {
                bitmap = null
            } else {
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (bmp != null) {
                    bitmap = bmp.asImageBitmap()
                    lastFetchedAt = System.currentTimeMillis()
                }
            }
        } catch (t: Throwable) {
            error = "Snapshot fetch failed."
        } finally {
            loading = false
        }
    }

    // Auto-refresh when we have a device and interval > 0
    LaunchedEffect(deviceId, intervalSec) {
        if (deviceId == null) return@LaunchedEffect
        fetch()
        if (intervalSec > 0) {
            while (true) {
                delay(intervalSec * 1_000L)
                fetch()
            }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Snapshot",
                style = MaterialTheme.typography.headlineMedium,
                color = DengColors.TextPrimary,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            TextButton(
                onClick = { scope.launch { fetch() } },
                enabled = !loading && deviceId != null,
            ) {
                Text("Refresh", color = DengColors.Cyan)
            }
        }

        if (error != null) ErrorBanner(error!!)
        if (state is DeviceFetchState.NoDevices) {
            DengCard { Text("No cloud phone connected.", color = DengColors.TextMuted) }
            return@Column
        }

        DengCard {
            val bmp = bitmap
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(360.dp)
                    .clip(RoundedCornerShape(16.dp)),
                contentAlignment = Alignment.Center,
            ) {
                if (bmp != null) {
                    Image(
                        bitmap = bmp,
                        contentDescription = "Latest cloud phone snapshot",
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                    )
                } else if (loading) {
                    CircularProgressIndicator(color = DengColors.Cyan, strokeWidth = 2.dp)
                } else if (intervalSec == 0) {
                    // Snapshot uploads are explicitly disabled — guide the
                    // user instead of showing a silent empty placeholder.
                    Text(
                        "Snapshot is off. Enable it in Settings.",
                        color = DengColors.TextMuted,
                    )
                } else {
                    Text("No snapshot yet.", color = DengColors.TextMuted)
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "Interval: " + (if (intervalSec == 0) "Off" else "${intervalSec}s") +
                "  •  Last: " + Format.timestamp(lastFetchedAt),
                style = MaterialTheme.typography.bodySmall,
                color = DengColors.TextMuted,
            )
        }
    }
}
