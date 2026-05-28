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

    val ready = state as? DeviceFetchState.Ready
    val deviceId = ready?.status?.device?.id
    val intervalSec = ready?.status?.settings?.snapshotIntervalSeconds ?: 0
    // v1.0.3: backend now reports when the bridge last uploaded a
    // snapshot — we use this to differentiate "interval is on, but the
    // bridge hasn't sent the first frame yet" from a genuine empty
    // state. Without this, every user saw the misleading first-frame
    // copy forever, even when the bridge was actively retrying.
    val lastCapturedAtIso = ready?.status?.device?.lastSnapshotCapturedAt
    val lastCapturedAgeSec = ready?.status?.device?.lastSnapshotAgeSeconds

    suspend fun fetch() {
        if (deviceId == null) return
        loading = true
        error = null
        try {
            val bytes = api.snapshotBytes(deviceId)
            if (bytes == null) {
                // 204 No Content — backend has no snapshot for this device.
                // Keep the previous bitmap if any; the placeholder text below
                // explains what's going on based on intervalSec + lastCapturedAt.
                bitmap = null
            } else {
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (bmp != null) {
                    bitmap = bmp.asImageBitmap()
                    lastFetchedAt = System.currentTimeMillis()
                }
            }
        } catch (t: Throwable) {
            // Never re-throw on the UI — keeps the screen responsive and
            // gives the user an honest "Retrying…" hint rather than a
            // silent empty placeholder.
            error = "Snapshot fetch failed. Retrying…"
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
                    // Snapshot uploads are explicitly disabled.
                    Text(
                        "Snapshot is off. Enable it in Settings.",
                        color = DengColors.TextMuted,
                    )
                } else if (lastCapturedAtIso == null) {
                    // Interval is on, but the bridge has never uploaded a
                    // snapshot for this device. This is the normal first-run
                    // state — e.g. user just paired or just turned snapshot
                    // back on, and we're inside the first interval window.
                    Text(
                        "Waiting for first snapshot…",
                        color = DengColors.TextMuted,
                    )
                } else {
                    // We know one was uploaded (backend told us so) but
                    // /snapshot/latest returned nothing on this attempt —
                    // most likely a transient retention/race. Tell the user
                    // honestly instead of pretending we have no idea.
                    Text(
                        "Snapshot temporarily unavailable. Retrying…",
                        color = DengColors.TextMuted,
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            // Bottom status line — always shows interval + the most useful
            // timestamp we have. Priority: server-reported capture time >
            // our local fetch time. Avoids the v1.0.2 confusion where
            // "Last: —" rendered forever even though uploads were
            // happening server-side.
            val statusTimestamp = lastCapturedAtIso
                ?.let { Format.timestamp(it) }
                ?: Format.timestamp(lastFetchedAt)
            Text(
                "Interval: " + (if (intervalSec == 0) "Off" else "${intervalSec}s") +
                "  •  Last: " + statusTimestamp,
                style = MaterialTheme.typography.bodySmall,
                color = DengColors.TextMuted,
            )
        }
    }
}
