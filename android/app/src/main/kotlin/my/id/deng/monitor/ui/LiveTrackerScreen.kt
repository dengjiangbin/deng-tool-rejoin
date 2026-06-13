package my.id.deng.monitor.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import kotlinx.coroutines.delay
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import my.id.deng.monitor.data.AioSyncRepository
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.TrackerAccountRow
import my.id.deng.monitor.data.TrackerFishItem
import my.id.deng.monitor.data.TrackerIndicatorColor
import my.id.deng.monitor.data.TrackerStatsSnapshot
import my.id.deng.monitor.data.TrackerStatusLogic
import my.id.deng.monitor.data.TrackerStoneItem
import my.id.deng.monitor.data.TrackerTotemItem
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

/**
 * Live Tracker tab — native local-first screen from Room cache + background sync.
 */
@Composable
fun LiveTrackerScreen(
    api: MonitorApi,
    aioSync: AioSyncRepository,
) {
    val tracker by aioSync.observeTracker().collectAsState(initial = null)
    val syncError by aioSync.lastError.collectAsState(initial = null)
    val syncing by aioSync.isBackgroundSyncing.collectAsState(initial = false)
    val accounts = tracker?.accounts.orEmpty()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 12.dp),
    ) {
        Text(
            "Live Tracker",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            "Account status, stats, and inventory across watched players.",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
        if (syncing) {
            Spacer(Modifier.height(6.dp))
            Text("Syncing in background…", style = MaterialTheme.typography.bodySmall, color = DengColors.Cyan)
        }
        syncError?.let { err ->
            Spacer(Modifier.height(8.dp))
            Text(err, style = MaterialTheme.typography.bodySmall, color = DengColors.Warning)
        }
        Spacer(Modifier.height(12.dp))

        if (accounts.isEmpty()) {
            DengCard {
                Text(
                    if (tracker == null) "Waiting for first sync" else "No tracked accounts",
                    style = MaterialTheme.typography.titleMedium,
                    color = DengColors.TextPrimary,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    if (tracker == null) {
                        "Live Tracker data will appear after your first successful sync."
                    } else {
                        "Add accounts on the website to start tracking."
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = DengColors.TextMuted,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(accounts, key = { it.canonicalKey ?: it.username ?: it.hashCode() }) { row ->
                    TrackerAccountCard(api = api, row = row)
                }
                item {
                    DengGradientButton(
                        text = "Refresh tracker",
                        onClick = { aioSync.refreshInBackground(forceBootstrap = false) },
                    )
                    Spacer(Modifier.height(8.dp))
                }
            }
        }
    }
}

@Composable
private fun TrackerAccountCard(api: MonitorApi, row: TrackerAccountRow) {
    var expanded by remember(row.canonicalKey) { mutableStateOf(false) }
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            tick += 1
        }
    }
    val nowMs = remember(tick) { System.currentTimeMillis() }
    val stats = TrackerStatusLogic.effectiveStats(row)
    val hasCached = TrackerStatusLogic.hasValidCachedData(row)
    val showLoading = !hasCached

    DengCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    Format.safeUsername(row.username),
                    style = MaterialTheme.typography.titleMedium,
                    color = DengColors.TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    TrackerStatusLogic.accountStatusLabel(row),
                    style = MaterialTheme.typography.bodySmall,
                    color = DengColors.TextMuted,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TrackerIndicatorDot(
                    label = "Account",
                    color = TrackerStatusLogic.accountIndicator(row),
                )
                TrackerIndicatorDot(
                    label = "Stats",
                    color = TrackerStatusLogic.statsSyncIndicator(row),
                )
                TrackerIndicatorDot(
                    label = "Inventory",
                    color = TrackerStatusLogic.inventoryIndicator(row),
                )
            }
        }

        Spacer(Modifier.height(8.dp))
        Text(
            TrackerStatusLogic.statsSyncDurationText(row, nowMs),
            style = MaterialTheme.typography.bodySmall,
            color = if (row.statsUploadFresh) DengColors.Success else DengColors.Danger,
        )
        Text(
            TrackerStatusLogic.inventorySyncDurationText(row, nowMs),
            style = MaterialTheme.typography.bodySmall,
            color = if (row.inventoryUploadFresh) DengColors.Success else DengColors.Danger,
        )

        if (showLoading) {
            Spacer(Modifier.height(10.dp))
            Text(
                "Waiting for first valid upload…",
                style = MaterialTheme.typography.bodyMedium,
                color = DengColors.TextMuted,
            )
        } else {
            Spacer(Modifier.height(10.dp))
            TrackerStatsRow(stats = stats)
            val fish = row.snapshot?.fishItems.orEmpty()
            val stone = row.snapshot?.stoneItems.orEmpty()
            val totem = row.snapshot?.totemItems.orEmpty()
            val fishTotal = fish.sumOf { it.count.coerceAtLeast(1) }
            val stoneTotal = stone.sumOf { it.count.coerceAtLeast(1) }
            val totemTotal = totem.sumOf { it.count.coerceAtLeast(1) }
            if (fish.isNotEmpty() || stone.isNotEmpty() || totem.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                TextButton(onClick = { expanded = !expanded }) {
                    Text(
                        if (expanded) {
                            "Hide inventory"
                        } else {
                            "Show inventory ($fishTotal fish, $stoneTotal stone, $totemTotal totem)"
                        },
                        color = DengColors.Cyan,
                    )
                }
            }
            if (expanded) {
                if (fish.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    TrackerFishGrid(api = api, items = fish, totalCount = fishTotal)
                }
                if (stone.isNotEmpty() || totem.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Item Grid",
                        style = MaterialTheme.typography.labelLarge,
                        color = DengColors.TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                if (stone.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    TrackerStoneGrid(api = api, items = stone, totalCount = stoneTotal)
                }
                if (totem.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    TrackerTotemGrid(api = api, items = totem, totalCount = totemTotal)
                }
            }
        }
    }
}

@Composable
private fun TrackerIndicatorDot(label: String, color: TrackerIndicatorColor) {
    val dotColor = when (color) {
        TrackerIndicatorColor.Green -> DengColors.Success
        TrackerIndicatorColor.Red -> DengColors.Danger
    }
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier
                .size(12.dp)
                .clip(CircleShape)
                .background(dotColor),
        )
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = DengColors.TextDim,
        )
    }
}

@Composable
private fun TrackerStatsRow(stats: TrackerStatsSnapshot?) {
    if (stats == null || !stats.statsProven) {
        Text(
            "Stats pending",
            style = MaterialTheme.typography.bodyMedium,
            color = DengColors.TextMuted,
        )
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        stats.coinsText?.takeIf { it.isNotBlank() }?.let {
            Text("Coins: $it", style = MaterialTheme.typography.bodyMedium, color = DengColors.TextPrimary)
        }
        stats.totalCaughtText?.takeIf { it.isNotBlank() }?.let {
            Text("Caught: $it", style = MaterialTheme.typography.bodyMedium, color = DengColors.TextPrimary)
        }
        stats.rarestFishChance?.takeIf { it.isNotBlank() }?.let {
            Text("Rarest: $it", style = MaterialTheme.typography.bodySmall, color = DengColors.TextMuted)
        }
    }
}

@Composable
private fun TrackerFishGrid(api: MonitorApi, items: List<TrackerFishItem>, totalCount: Int) {
    Text(
        "Fishes ($totalCount)",
        style = MaterialTheme.typography.labelLarge,
        color = DengColors.TextPrimary,
        fontWeight = FontWeight.SemiBold,
    )
    Spacer(Modifier.height(6.dp))
    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(max = 280.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        userScrollEnabled = false,
    ) {
        items(items.take(12), key = { it.name ?: it.hashCode() }) { fish ->
            TrackerItemTile(
                api = api,
                name = fish.name,
                subtitle = fish.rarity,
                imageUrl = fish.imageUrl,
            )
        }
    }
}

@Composable
private fun TrackerStoneGrid(api: MonitorApi, items: List<TrackerStoneItem>, totalCount: Int) {
    Text(
        "Enchant Stones ($totalCount)",
        style = MaterialTheme.typography.labelLarge,
        color = DengColors.TextPrimary,
        fontWeight = FontWeight.SemiBold,
    )
    Spacer(Modifier.height(6.dp))
    LazyVerticalGrid(
        columns = GridCells.Fixed(4),
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(max = 160.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        userScrollEnabled = false,
    ) {
        items(items.take(8), key = { it.name ?: it.hashCode() }) { stone ->
            TrackerItemTile(
                api = api,
                name = stone.name,
                subtitle = if (stone.count > 1) "×${stone.count}" else null,
                imageUrl = stone.imageUrl,
            )
        }
    }
}

@Composable
private fun TrackerTotemGrid(api: MonitorApi, items: List<TrackerTotemItem>, totalCount: Int) {
    Text(
        "Totem ($totalCount)",
        style = MaterialTheme.typography.labelMedium,
        color = DengColors.TextPrimary,
        fontWeight = FontWeight.SemiBold,
    )
    Spacer(Modifier.height(6.dp))
    LazyVerticalGrid(
        columns = GridCells.Fixed(4),
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(max = 160.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        userScrollEnabled = false,
    ) {
        items(
            items.take(8),
            key = { it.uuid ?: it.itemId ?: it.name ?: it.hashCode() },
        ) { totem ->
            TrackerItemTile(
                api = api,
                name = totem.name,
                subtitle = if (totem.count > 1) "×${totem.count}" else null,
                imageUrl = totem.imageUrl,
            )
        }
    }
}

@Composable
private fun TrackerItemTile(
    api: MonitorApi,
    name: String?,
    subtitle: String?,
    imageUrl: String?,
) {
    val url = resolveAssetUrl(api.baseUrl, imageUrl)
    Surface(
        color = DengColors.CardSoft,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(6.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (!url.isNullOrBlank()) {
                AsyncImage(
                    model = url,
                    contentDescription = name,
                    modifier = Modifier
                        .size(48.dp)
                        .clip(RoundedCornerShape(6.dp)),
                    contentScale = ContentScale.Crop,
                )
            } else {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(DengColors.BorderMuted),
                )
            }
            Spacer(Modifier.height(4.dp))
            Text(
                name ?: "—",
                style = MaterialTheme.typography.labelSmall,
                color = DengColors.TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            subtitle?.let {
                Text(it, style = MaterialTheme.typography.labelSmall, color = DengColors.TextMuted)
            }
        }
    }
}
