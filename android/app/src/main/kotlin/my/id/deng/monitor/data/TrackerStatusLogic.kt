package my.id.deng.monitor.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

enum class TrackerIndicatorColor {
    Green,
    Red,
}

object TrackerStatusLogic {
    fun effectiveStats(row: TrackerAccountRow): TrackerStatsSnapshot? {
        val snap = row.snapshot?.stats
        if (snap != null && snap.statsProven) return snap
        if (row.statsProven && (row.coinsText != null || row.totalCaughtText != null)) {
            return TrackerStatsSnapshot(
                coinsText = row.coinsText,
                totalCaughtText = row.totalCaughtText,
                rarestFishChance = row.rarestFishChance,
                statsProven = true,
            )
        }
        return snap
    }

    fun hasValidCachedData(row: TrackerAccountRow): Boolean {
        val stats = effectiveStats(row)
        if (stats?.statsProven == true) return true
        val snap = row.snapshot
        return snap != null && (
            snap.fishItems.isNotEmpty()
                || snap.stoneItems.isNotEmpty()
                || snap.totemItems.isNotEmpty()
            )
    }

    fun accountStatusLabel(row: TrackerAccountRow): String =
        if (row.accountPresenceLive) "Online" else "Offline"

    fun accountIndicator(row: TrackerAccountRow): TrackerIndicatorColor =
        if (row.accountPresenceLive) TrackerIndicatorColor.Green else TrackerIndicatorColor.Red

    fun statsSyncIndicator(row: TrackerAccountRow): TrackerIndicatorColor =
        if (row.statsUploadFresh) TrackerIndicatorColor.Green else TrackerIndicatorColor.Red

    fun inventoryIndicator(row: TrackerAccountRow): TrackerIndicatorColor =
        if (row.inventoryUploadFresh) TrackerIndicatorColor.Green else TrackerIndicatorColor.Red

    fun statsSyncDurationText(row: TrackerAccountRow, nowMs: Long): String {
        val age = row.statsUploadAgeSeconds ?: row.secondsSinceLastSuccess
        return if (age != null) "Stats sync: ${formatAge(age)} ago" else "Stats sync: —"
    }

    fun inventorySyncDurationText(row: TrackerAccountRow, nowMs: Long): String {
        val age = row.inventoryUploadAgeSeconds ?: row.secondsSinceLastSuccess
        return if (age != null) "Inventory sync: ${formatAge(age)} ago" else "Inventory sync: —"
    }

    private fun formatAge(seconds: Long): String {
        if (seconds < 60) return "${seconds}s"
        val mins = seconds / 60
        if (mins < 60) return "${mins}m"
        return "${mins / 60}h"
    }
}
