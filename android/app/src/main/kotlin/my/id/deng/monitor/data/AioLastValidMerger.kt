package my.id.deng.monitor.data

/**
 * Client-side permanent-data rule: never wipe last valid fish/stone/stats when
 * a newer sync payload is blank, failed, or offline.
 */
object AioLastValidMerger {

    fun mergeDashboard(old: AioDashboardPayload?, incoming: AioDashboardPayload?): AioDashboardPayload? {
        if (incoming == null) return old
        if (old == null) return incoming
        val mergedCards = if (incoming.cards != null && hasDashboardCards(incoming.cards)) {
            incoming.cards
        } else {
            old.cards
        }
        val mergedFish = if (incoming.fishCards.isNotEmpty()) incoming.fishCards else old.fishCards
        val mergedAvailable = old.available || incoming.available
        val mergedCaught = incoming.caughtFishCount?.takeIf { it > 0 }
            ?: old.caughtFishCount
            ?: mergedFish.sumOf { it.count.takeIf { c -> c > 0 } ?: (it.amount ?: 0) }
        return incoming.copy(
            available = mergedAvailable,
            cards = mergedCards,
            fishCards = mergedFish,
            caughtFishCount = mergedCaught,
            fishCardCount = mergedFish.size.takeIf { it > 0 } ?: old.fishCardCount,
        )
    }

    fun mergeTracker(old: AioTrackerPayload?, incoming: AioTrackerPayload?): AioTrackerPayload? {
        if (incoming == null) return old
        if (old == null) return incoming
        val oldByKey = old.accounts.associateBy { accountKey(it) }
        val mergedAccounts = incoming.accounts.map { inc ->
            mergeAccountRow(oldByKey[accountKey(inc)], inc)
        }
        return incoming.copy(accounts = mergedAccounts)
    }

    private fun mergeAccountRow(old: TrackerAccountRow?, incoming: TrackerAccountRow): TrackerAccountRow {
        if (old == null) return incoming
        val mergedSnapshot = mergeSnapshot(old.snapshot, incoming.snapshot, incoming)
        return incoming.copy(snapshot = mergedSnapshot)
    }

    private fun mergeSnapshot(
        oldSnap: TrackerAccountSnapshot?,
        incSnap: TrackerAccountSnapshot?,
        incomingRow: TrackerAccountRow,
    ): TrackerAccountSnapshot? {
        val old = oldSnap ?: TrackerAccountSnapshot()
        val inc = incSnap
        val mergedStats = when {
            inc?.stats?.statsProven == true && hasStats(inc.stats) -> inc.stats
            incomingRow.statsProven && hasStats(incomingRow) -> TrackerStatsSnapshot(
                coinsText = incomingRow.coinsText,
                totalCaughtText = incomingRow.totalCaughtText,
                rarestFishChance = incomingRow.rarestFishChance,
                statsProven = true,
            )
            old.stats != null && hasStats(old.stats) -> old.stats
            else -> inc?.stats ?: old.stats
        }
        val mergedFish = when {
            !inc?.fishItems.isNullOrEmpty() -> inc!!.fishItems
            old.fishItems.isNotEmpty() -> old.fishItems
            else -> emptyList()
        }
        val mergedStone = when {
            !inc?.stoneItems.isNullOrEmpty() -> inc!!.stoneItems
            old.stoneItems.isNotEmpty() -> old.stoneItems
            else -> emptyList()
        }
        val mergedTotem = when {
            !inc?.totemItems.isNullOrEmpty() -> inc!!.totemItems
            old.totemItems.isNotEmpty() -> old.totemItems
            else -> emptyList()
        }
        if (mergedStats == null && mergedFish.isEmpty() && mergedStone.isEmpty() && mergedTotem.isEmpty()) return null
        return TrackerAccountSnapshot(
            stats = mergedStats,
            fishItems = mergedFish,
            stoneItems = mergedStone,
            totemItems = mergedTotem,
        )
    }

    private fun hasStats(stats: TrackerStatsSnapshot): Boolean =
        stats.statsProven && (
            !stats.coinsText.isNullOrBlank()
                || !stats.totalCaughtText.isNullOrBlank()
                || !stats.rarestFishChance.isNullOrBlank()
            )

    private fun hasStats(row: TrackerAccountRow): Boolean =
        !row.coinsText.isNullOrBlank()
            || !row.totalCaughtText.isNullOrBlank()
            || !row.rarestFishChance.isNullOrBlank()

    private fun hasDashboardCards(cards: AioDashboardCards): Boolean =
        cards.secretCaught > 0 || cards.forgottenCaught > 0

    private fun accountKey(row: TrackerAccountRow): String =
        row.canonicalKey?.takeIf { it.isNotBlank() }
            ?: row.robloxUserId?.takeIf { it.isNotBlank() }
            ?: row.username?.trim()?.lowercase().orEmpty()
}
