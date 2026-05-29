package my.id.deng.monitor.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class DeviceSummary(
    val id: String,
    @SerialName("device_label") val deviceLabel: String? = null,
    @SerialName("tool_version") val toolVersion: String? = null,
    val channel: String? = null,
    // v1.0.4: `statusConnected` is the legacy sticky-true boolean —
    // kept for binary compatibility with old backends but the new code
    // should read `connected` / `connectionState` which are computed
    // from `last_seen_at` freshness server-side (30s TTL). The legacy
    // field was the bug class behind "APK still says Connected after
    // cloud phone reboot" — the watchdog can't post a "goodbye" so the
    // truth only lives in how long ago `last_seen_at` was.
    @SerialName("status_connected") val statusConnected: Boolean = false,
    val connected: Boolean? = null,
    @SerialName("connection_state") val connectionState: String? = null,
    @SerialName("seconds_since_last_seen") val secondsSinceLastSeen: Long? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    // v1.0.3: backend tells the APK when the bridge last successfully
    // uploaded a snapshot, so SnapshotScreen can render an honest
    // "Waiting for first snapshot…" / "Captured: …" line instead of the
    // misleading "No snapshot yet." copy from v1.0.2. Both fields are
    // nullable — `null` means the device has never had a snapshot.
    @SerialName("last_snapshot_captured_at") val lastSnapshotCapturedAt: String? = null,
    @SerialName("last_snapshot_age_seconds") val lastSnapshotAgeSeconds: Long? = null,
    // v1.0.4: bridge self-reported diagnostics — used by SnapshotScreen
    // to render real reasons ("capture_failed: screencap_unavailable",
    // "upload_failed: http_503") instead of "Waiting for first
    // snapshot…" forever. JsonElement so we can extend the bridge
    // payload without bumping the APK every time.
    @SerialName("last_bridge_status") val lastBridgeStatus: JsonElement? = null,
    // v1.0.6: device-level RAM for the redesigned dashboard's per-device
    // RAM list. Null when the bridge didn't report it (never invented).
    @SerialName("device_ram") val deviceRam: DeviceRam? = null,
    // v1.0.6: compact snapshot result on the device list row.
    @SerialName("snapshot_last_result") val snapshotLastResult: String? = null,
    // v1.0.8: per-device package summary (configured package counts).
    @SerialName("package_summary") val packageSummary: DashboardPackageSummary? = null,
) {
    /** Best-effort connection boolean: prefer the computed value. */
    val isConnected: Boolean
        get() = connected ?: ((connectionState == "Connected") || statusConnected)

    /** Best-effort display label for the connection. */
    val connectionLabel: String
        get() = connectionState ?: if (isConnected) "Connected" else "Disconnected"

    /** Display name for the dashboard list. */
    val displayName: String
        get() = deviceLabel?.takeIf { it.isNotBlank() } ?: "Cloud Phone"
}

@Serializable
data class DeviceRam(
    @SerialName("used_mb") val usedMb: Int = 0,
    @SerialName("total_mb") val totalMb: Int = 0,
    val percent: Int? = null,
) {
    /** Effective percent: explicit value, else computed from used/total. */
    val effectivePercent: Int?
        get() = percent ?: if (totalMb > 0) ((usedMb.toLong() * 100) / totalMb).toInt() else null

    /** Dashboard row text: "2048MB/4096MB 50%" or "50%" when only % known. */
    val displayText: String
        get() = if (totalMb > 0) {
            "${usedMb}MB/${totalMb}MB ${effectivePercent ?: 0}%"
        } else {
            effectivePercent?.let { "$it%" } ?: "—"
        }
}

/**
 * v1.0.8: package-level summary for the dashboard headline cards. TOTAL is the
 * number of CONFIGURED packages across the owner's device(s); ONLINE is those
 * running/healthy; DEAD is everything else (dead/launching/joining/no-heartbeat
 * /stale). This is what the dashboard's TOTAL/ONLINE/DEAD cards show — NOT the
 * device count.
 */
@Serializable
data class DashboardPackageSummary(
    val total: Int = 0,
    val online: Int = 0,
    val dead: Int = 0,
    val launching: Int = 0,
    val joining: Int = 0,
    @SerialName("no_heartbeat") val noHeartbeat: Int = 0,
    @SerialName("total_ram_mb") val totalRamMb: Int = 0,
)

@Serializable
data class DeviceListResponse(
    val devices: List<DeviceSummary> = emptyList(),
    @SerialName("package_summary") val packageSummary: DashboardPackageSummary = DashboardPackageSummary(),
)

@Serializable
data class PackageState(
    @SerialName("package_name") val packageName: String,
    @SerialName("display_name") val displayName: String? = null,
    val username: String? = null,
    val state: String = "Unknown",
    @SerialName("ram_mb") val ramMb: Int = 0,
    @SerialName("runtime_seconds") val runtimeSeconds: Int = 0,
    @SerialName("restart_count") val restartCount: Int = 0,
    @SerialName("private_url_configured") val privateUrlConfigured: Boolean = false,
    @SerialName("safe_error_reason") val safeErrorReason: String? = null,
    @SerialName("last_launch_at") val lastLaunchAt: String? = null,
    @SerialName("last_heartbeat_at") val lastHeartbeatAt: String? = null,
    @SerialName("last_state_change_at") val lastStateChangeAt: String? = null,
)

@Serializable
data class PackageSummary(
    val total: Int = 0,
    val online: Int = 0,
    val dead: Int = 0,
    // v1.0.4: `relaunching` is kept for back-compat (old backends still
    // emit it). New `launching` + `joining` cover the 5-state model.
    val relaunching: Int = 0,
    val launching: Int = 0,
    val joining: Int = 0,
    @SerialName("no_heartbeat") val noHeartbeat: Int = 0,
    val other: Int = 0,
    @SerialName("total_ram_mb") val totalRamMb: Int = 0,
    @SerialName("average_ram_mb") val averageRamMb: Int = 0,
)

@Serializable
data class DeviceStatus(
    val device: DeviceSummary,
    val summary: PackageSummary,
    val packages: List<PackageState> = emptyList(),
    val settings: MonitorSettings = MonitorSettings(),
)

@Serializable
data class MonitorSettings(
    @SerialName("snapshot_interval_seconds") val snapshotIntervalSeconds: Int = 30,
    @SerialName("monitor_enabled") val monitorEnabled: Boolean = true,
    @SerialName("app_refresh_interval_seconds") val appRefreshIntervalSeconds: Int = 5,
    @SerialName("app_display_name") val appDisplayName: String? = null,
)

@Serializable
data class PairRequest(
    val code: String,
    @SerialName("device_name") val deviceName: String? = null,
)

@Serializable
data class PairResponse(
    @SerialName("app_session_token") val appSessionToken: String,
    @SerialName("expires_at") val expiresAt: String,
    val owner: OwnerInfo,
)

@Serializable
data class OwnerInfo(
    @SerialName("discord_user_id") val discordUserId: String,
)

@Serializable
data class ErrorResponse(
    val error: String? = null,
    val message: String? = null,
)

// ── Fish It ──────────────────────────────────────────────────────────────────

@Serializable
data class FishRank(val rank: Int = 0, val of: Int = 0)

@Serializable
data class FishProfile(
    @SerialName("has_data") val hasData: Boolean = false,
    @SerialName("discord_user_id") val discordUserId: String? = null,
    val username: String? = null,
    @SerialName("total_fish") val totalFish: Int = 0,
    @SerialName("secret_fish") val secretFish: Int = 0,
    @SerialName("forgotten_fish") val forgottenFish: Int = 0,
    val rank: FishRank? = null,
)

// Standardized Fish It JSON (v1.0.8 — Part 11). All keys are camelCase and
// every field has a default so a partial/extended payload never throws a
// SerializationException (which the UI used to mis-report as
// "can't reach backend"). `image`/weight are nullable strings — never numeric.
@Serializable
data class FishStatCard(
    val key: String = "",
    val label: String = "",
    val amount: Int = 0,
    val count: Int = 0,
    val imageUrl: String? = null,
    val fallbackUrl: String? = null,
) {
    /** Stats use `amount`; rod cards may use `count`. */
    val displayAmount: Int get() = if (amount != 0) amount else count
}

@Serializable
data class FishStats(
    val ok: Boolean = true,
    val hasData: Boolean = false,
    val username: String? = null,
    val totalFish: Int = 0,
    val rank: FishRank? = null,
    val summaryCards: List<FishStatCard> = emptyList(),
    val rarityCards: List<FishStatCard> = emptyList(),
    val rodCards: List<FishStatCard> = emptyList(),
)

@Serializable
data class FishDailySummary(
    val totalFish: Int = 0,
    val secretFish: Int = 0,
    val forgottenFish: Int = 0,
)

@Serializable
data class FishDailyCard(
    val speciesKey: String = "",
    val name: String = "",
    val rarity: String = "Secret",
    val count: Int = 0,
    val imageUrl: String? = null,
    val maxWeight: String? = null,
    val latestCaughtAt: String? = null,
    val fallbackUrl: String? = null,
)

@Serializable
data class FishDaily(
    val ok: Boolean = true,
    val hasData: Boolean = false,
    val period: String = "today",
    val periodLabel: String = "Today",
    val timezone: String = "Asia/Jakarta",
    val summary: FishDailySummary = FishDailySummary(),
    val cards: List<FishDailyCard> = emptyList(),
    val emptyMessage: String? = null,
    val lastUpdated: String? = null,
)

@Serializable
data class FishCard(
    val speciesKey: String = "",
    val name: String = "",
    val rarity: String = "Secret",
    val count: Int = 0,
    val imageUrl: String? = null,
    val maxWeight: String? = null,
    val mutation: String? = null,
    val latestCaughtAt: String? = null,
    val fallbackUrl: String? = null,
)

@Serializable
data class FishGrid(
    val ok: Boolean = true,
    val hasData: Boolean = false,
    val items: List<FishCard> = emptyList(),
    val total: Int = 0,
    val totalSpecies: Int = 0,
    val page: Int = 1,
    val limit: Int = 24,
    val pages: Int = 1,
)
