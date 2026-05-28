package my.id.deng.monitor.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class DeviceSummary(
    val id: String,
    @SerialName("device_label") val deviceLabel: String? = null,
    @SerialName("tool_version") val toolVersion: String? = null,
    val channel: String? = null,
    @SerialName("status_connected") val statusConnected: Boolean = false,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    // v1.0.3: backend tells the APK when the bridge last successfully
    // uploaded a snapshot, so SnapshotScreen can render an honest
    // "Waiting for first snapshot…" / "Captured: …" line instead of the
    // misleading "No snapshot yet." copy from v1.0.2. Both fields are
    // nullable — `null` means the device has never had a snapshot.
    @SerialName("last_snapshot_captured_at") val lastSnapshotCapturedAt: String? = null,
    @SerialName("last_snapshot_age_seconds") val lastSnapshotAgeSeconds: Long? = null,
)

@Serializable
data class DeviceListResponse(
    val devices: List<DeviceSummary> = emptyList(),
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
    val relaunching: Int = 0,
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
