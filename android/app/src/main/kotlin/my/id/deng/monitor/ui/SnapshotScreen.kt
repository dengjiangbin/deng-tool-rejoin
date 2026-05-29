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
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

@Composable
fun SnapshotScreen(api: MonitorApi, sessionStore: SessionStore) {
    val handle = rememberDeviceStatusHandle(api, sessionStore)
    val state by handle.state
    var bitmap by remember { mutableStateOf<ImageBitmap?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var lastFetchedAt by remember { mutableStateOf<Long?>(null) }
    val scope = rememberCoroutineScope()

    val ready = state as? DeviceFetchState.Ready
    val deviceId = ready?.status?.device?.id
    val intervalSec = ready?.status?.settings?.snapshotIntervalSeconds ?: 30
    val isConnected = ready?.status?.device?.isConnected == true
    // v1.0.3: backend now reports when the bridge last uploaded a
    // snapshot — we use this to differentiate "interval is on, but the
    // bridge hasn't sent the first frame yet" from a genuine empty
    // state. Without this, every user saw the misleading first-frame
    // copy forever, even when the bridge was actively retrying.
    val lastCapturedAtIso = ready?.status?.device?.lastSnapshotCapturedAt
    val lastCapturedAgeSec = ready?.status?.device?.lastSnapshotAgeSeconds
    // v1.0.4: bridge-reported diagnostics — what the cloud-phone-side
    // capture pipeline actually saw. This is the field that finally
    // breaks the "Waiting for first snapshot…" forever bug: if the
    // bridge says capture_failed / upload_failed, we show that reason
    // instead of pretending nothing is happening.
    val bridgeStatus = parseBridgeStatus(ready?.status?.device?.lastBridgeStatus)

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

        // v1.0.5: surface a network/device-status error (e.g. cannot reach
        // tool.deng.my.id) with a Retry action instead of silently rendering
        // the "Waiting for cloud phone to reconnect…" placeholder, which made
        // a DNS failure look like an idle-but-healthy screen.
        (state as? DeviceFetchState.Error)?.let { errState ->
            ErrorCard(
                message = errState.message,
                onRetry = { scope.launch { handle.refreshNow() } },
            )
            return@Column
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
                    Text(
                        "Snapshot is off. Enable it in Settings.",
                        color = DengColors.TextMuted,
                    )
                } else if (!isConnected) {
                    // v1.0.4: don't claim "Waiting for first snapshot…"
                    // when the cloud phone hasn't pushed anything in
                    // 30s — the answer is "we're disconnected from the
                    // cloud phone first". Snapshot can't possibly
                    // arrive until the bridge comes back.
                    Text(
                        "Waiting for cloud phone to reconnect…",
                        color = DengColors.TextMuted,
                    )
                } else if (bridgeStatus?.captureFailedReason != null) {
                    // v1.0.4: real reason surfaced from the Termux
                    // bridge — replaces "Waiting for first snapshot…"
                    // forever when capture is broken (screencap missing,
                    // permission denied, etc.).
                    Text(
                        "Snapshot capture failed: ${bridgeStatus.captureFailedReason}",
                        color = DengColors.Danger,
                    )
                } else if (bridgeStatus?.uploadFailedReason != null) {
                    Text(
                        "Snapshot upload failed: ${bridgeStatus.uploadFailedReason}. Retrying…",
                        color = DengColors.Warning,
                    )
                } else if (lastCapturedAtIso == null) {
                    // Interval is on AND we're connected AND no error
                    // — we're inside the first interval window.
                    Text(
                        "Waiting for first snapshot…",
                        color = DengColors.TextMuted,
                    )
                } else {
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
            // v1.0.4: tiny one-liner diagnostic so the user can see how
            // many capture attempts the bridge has made + any safe
            // error string, without needing to open Termux.
            if (bridgeStatus != null) {
                Text(
                    "Captures attempted: ${bridgeStatus.calledCount} • " +
                    "Last result: ${bridgeStatus.lastResult ?: "—"}" +
                    (bridgeStatus.provider?.let { " • via $it" } ?: "") +
                    (bridgeStatus.lastBytes?.let { " • ${it / 1024} KB" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    color = DengColors.TextDim,
                )
            }
        }
    }
}

/**
 * Subset of `device.last_bridge_status` that the Snapshot screen needs.
 * Defensive parsing — the backend already scrubs the payload, but we
 * still treat every field as nullable and string-coerce where it makes
 * sense so a future schema bump can't crash the APK.
 */
private data class BridgeStatusSnapshot(
    val captureFailedReason: String?,
    val uploadFailedReason: String?,
    val calledCount: Int,
    val lastResult: String?,
    val lastBytes: Int?,
    val provider: String?,
)

// v1.0.6: clean, user-facing reason for each capture result enum. No raw
// stack traces — those live in the probe. The provider (which screencap
// rung was tried) is appended when known.
private fun humanCaptureReason(result: String?, error: String?, provider: String?): String {
    val base = when (result) {
        "failed_no_screencap" -> "screencap not available on this device"
        "failed_root_denied" -> "root screencap was denied"
        "failed_empty_output" -> "screencap returned no image"
        "failed_invalid_png" -> "screencap did not return a valid PNG"
        "failed_timeout" -> "screencap timed out"
        "capture_failed", "failed_unknown" -> error ?: "screencap unavailable"
        else -> error ?: result ?: "screencap unavailable"
    }
    return if (!provider.isNullOrBlank()) "$base ($provider)" else base
}

private fun parseBridgeStatus(raw: JsonElement?): BridgeStatusSnapshot? {
    val obj = (raw as? JsonObject) ?: return null
    val s = { key: String -> (obj[key] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() && it != "null" } }
    val i = { key: String -> (obj[key] as? JsonPrimitive)?.intOrNull }
    val lastResult = s("snapshot_last_result")
    val lastError = s("snapshot_last_error")
    val uploadStatus = s("snapshot_last_upload_status")
    val provider = s("snapshot_provider")

    // Any "failed_*" / "capture_failed" that is NOT an upload failure is a
    // capture failure; failed_upload_http (+ legacy upload_failed) is upload.
    val isUploadFailure = lastResult == "failed_upload_http" || lastResult == "upload_failed"
    val isCaptureFailure = lastResult != null &&
        lastResult != "success" &&
        !isUploadFailure

    val captureFailedReason = if (isCaptureFailure) humanCaptureReason(lastResult, lastError, provider) else null
    val uploadFailedReason = if (isUploadFailure) (uploadStatus ?: "upload failed") else null

    return BridgeStatusSnapshot(
        captureFailedReason = captureFailedReason,
        uploadFailedReason = uploadFailedReason,
        calledCount = i("snapshot_provider_called_count") ?: 0,
        lastResult = lastResult,
        lastBytes = i("snapshot_last_bytes"),
        provider = provider,
    )
}
