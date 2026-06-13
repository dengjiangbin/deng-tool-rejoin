package my.id.deng.monitor.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AioDashboardPayload(
    val available: Boolean = false,
    @SerialName("statsState") val statsState: String? = null,
    val cards: AioDashboardCards? = null,
    @SerialName("fishCards") val fishCards: List<AioFishCard> = emptyList(),
    @SerialName("caughtFishCount") val caughtFishCount: Int? = null,
    @SerialName("fishCardCount") val fishCardCount: Int? = null,
    val period: String? = null,
)

@Serializable
data class AioDashboardCards(
    @SerialName("secretCaught") val secretCaught: Int = 0,
    @SerialName("forgottenCaught") val forgottenCaught: Int = 0,
)

@Serializable
data class AioFishCard(
    val name: String? = null,
    val rarity: String? = null,
    val count: Int = 0,
    val amount: Int? = null,
    @SerialName("imageUrl") val imageUrl: String? = null,
)

@Serializable
data class AioTrackerPayload(
    @SerialName("serverNow") val serverNow: String? = null,
    val accounts: List<TrackerAccountRow> = emptyList(),
)

@Serializable
data class TrackerAccountRow(
    val username: String? = null,
    @SerialName("robloxUserId") val robloxUserId: String? = null,
    @SerialName("canonicalKey") val canonicalKey: String? = null,
    @SerialName("accountPresenceLive") val accountPresenceLive: Boolean = false,
    @SerialName("statsUploadFresh") val statsUploadFresh: Boolean = false,
    @SerialName("inventoryUploadFresh") val inventoryUploadFresh: Boolean = false,
    @SerialName("statsUploadStatus") val statsUploadStatus: String? = null,
    @SerialName("inventoryUploadStatus") val inventoryUploadStatus: String? = null,
    @SerialName("statsRedSince") val statsRedSince: String? = null,
    @SerialName("inventoryRedSince") val inventoryRedSince: String? = null,
    @SerialName("statsUploadAgeSeconds") val statsUploadAgeSeconds: Long? = null,
    @SerialName("inventoryUploadAgeSeconds") val inventoryUploadAgeSeconds: Long? = null,
    @SerialName("intervalSeconds") val intervalSeconds: Int? = null,
    @SerialName("secondsSinceLastSuccess") val secondsSinceLastSuccess: Long? = null,
    @SerialName("coinsText") val coinsText: String? = null,
    @SerialName("totalCaughtText") val totalCaughtText: String? = null,
    @SerialName("rarestFishChance") val rarestFishChance: String? = null,
    @SerialName("statsProven") val statsProven: Boolean = false,
    val snapshot: TrackerAccountSnapshot? = null,
)

@Serializable
data class TrackerAccountSnapshot(
    val stats: TrackerStatsSnapshot? = null,
    @SerialName("fishItems") val fishItems: List<TrackerFishItem> = emptyList(),
    @SerialName("stoneItems") val stoneItems: List<TrackerStoneItem> = emptyList(),
    @SerialName("totemItems") val totemItems: List<TrackerTotemItem> = emptyList(),
)

@Serializable
data class TrackerStatsSnapshot(
    @SerialName("coinsText") val coinsText: String? = null,
    @SerialName("totalCaughtText") val totalCaughtText: String? = null,
    @SerialName("rarestFishChance") val rarestFishChance: String? = null,
    @SerialName("statsProven") val statsProven: Boolean = false,
    @SerialName("emptyReason") val emptyReason: String? = null,
)

@Serializable
data class TrackerFishItem(
    val name: String? = null,
    val rarity: String? = null,
    @SerialName("imageUrl") val imageUrl: String? = null,
    val count: Int = 1,
)

@Serializable
data class TrackerStoneItem(
    val name: String? = null,
    @SerialName("imageUrl") val imageUrl: String? = null,
    val count: Int = 1,
)

@Serializable
data class TrackerTotemItem(
    val name: String? = null,
    @SerialName("imageUrl") val imageUrl: String? = null,
    val count: Int = 1,
    val uuid: String? = null,
    @SerialName("itemId") val itemId: String? = null,
)
