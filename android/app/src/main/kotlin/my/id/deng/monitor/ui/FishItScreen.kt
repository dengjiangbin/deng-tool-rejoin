package my.id.deng.monitor.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import kotlinx.coroutines.launch
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.FishCard
import my.id.deng.monitor.data.FishDaily
import my.id.deng.monitor.data.FishGrid
import my.id.deng.monitor.data.FishStats
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.fishFriendlyError
import my.id.deng.monitor.ui.theme.DengColors
import my.id.deng.monitor.util.Format

private val DAILY_PERIODS = listOf(
    "today" to "Today",
    "yesterday" to "Yesterday",
    "7d" to "7 Days",
    "30d" to "30 Days",
    "all" to "All Time",
)

private val FISH_SORTS = listOf(
    "amount" to "Most caught",
    "value" to "Highest value",
    "name" to "Name (A–Z)",
    "rarity" to "Rarity",
    "recent" to "Recently caught",
)

private fun exactCount(n: Long): String = Format.formatExact(n)

private fun exactCount(n: Int): String = Format.formatExact(n)

@Composable
fun FishItScreen(api: MonitorApi) {
    var sub by rememberSaveable { mutableStateOf("daily") }

    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(16.dp))
        Text(
            "Stats",
            style = MaterialTheme.typography.headlineMedium,
            color = DengColors.TextPrimary,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            "Linked to your Discord account.",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextMuted,
        )
        Spacer(Modifier.height(14.dp))

        SubTabRow(sub) { sub = it }
        Spacer(Modifier.height(14.dp))

        when (sub) {
            "daily" -> DailySection(api)
            "stats" -> StatsSection(api)
            "fish" -> FishSection(api)
        }
    }
}

@Composable
private fun SubTabRow(current: String, onSelect: (String) -> Unit) {
    val tabs = listOf("daily" to "Daily", "stats" to "Stats", "fish" to "Fish")
    Surface(
        color = DengColors.CardSoft,
        shape = RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(modifier = Modifier.padding(5.dp), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            tabs.forEach { (key, label) ->
                val selected = key == current
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .then(if (selected) Modifier.background(DengColors.GradientButton) else Modifier)
                        .clickable { onSelect(key) }
                        .padding(vertical = 9.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        label,
                        color = if (selected) Color.White else DengColors.TextMuted,
                        fontWeight = FontWeight.SemiBold,
                        style = MaterialTheme.typography.labelLarge,
                    )
                }
            }
        }
    }
}

// ── Shared states ─────────────────────────────────────────────────────────────
@Composable
private fun LoadingCard() {
    DengCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(color = DengColors.Cyan, strokeWidth = 2.dp, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(12.dp))
            Text("Loading…", color = DengColors.TextMuted)
        }
    }
}

@Composable
private fun EmptyCard(message: String) {
    DengCard { Text(message, color = DengColors.TextMuted) }
}

// ── DAILY ─────────────────────────────────────────────────────────────────────
@Composable
private fun DailySection(api: MonitorApi) {
    val scope = rememberCoroutineScope()
    var period by rememberSaveable { mutableStateOf("today") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var daily by remember { mutableStateOf<FishDaily?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            runCatching { api.fishDaily(period) }
                .onSuccess { daily = it; loading = false }
                .onFailure { error = fishFriendlyError(it, api.host); loading = false }
        }
    }
    LaunchedEffect(period) { load() }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        FlowChips(DAILY_PERIODS, period) { period = it }
        when {
            loading -> LoadingCard()
            error != null -> ErrorCard(message = error!!, onRetry = { load() })
            daily == null || (!daily!!.hasData && daily!!.cards.isEmpty()) ->
                EmptyCard(daily?.emptyMessage ?: "No catches found for this period.")
            else -> {
                val d = daily!!
                // Summary row — Total / Secret / Forgotten for the period.
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                    StatTile("Total", exactCount(d.summary.totalFish), modifier = Modifier.weight(1f))
                    StatTile("Secret", exactCount(d.summary.secretFish), accent = DengColors.Pink, modifier = Modifier.weight(1f))
                    StatTile("Forgotten", exactCount(d.summary.forgottenFish), accent = DengColors.Warning, modifier = Modifier.weight(1f))
                }
                // One card per fish species caught in the period.
                if (d.cards.isEmpty()) {
                    EmptyCard(d.emptyMessage ?: "No catches found for this period.")
                } else {
                    DailyCardGrid(d.cards)
                }
                d.lastUpdated?.let {
                    Text("Updated ${Format.timestamp(it)}", color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        Spacer(Modifier.height(48.dp))
    }
}

@Composable
private fun DailyCardGrid(cards: List<my.id.deng.monitor.data.FishDailyCard>) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        cards.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
                row.forEach { card ->
                    Box(modifier = Modifier.weight(1f)) { DailyFishCard(card) }
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun DailyFishCard(card: my.id.deng.monitor.data.FishDailyCard) {
    DengCard {
        Box(modifier = Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(12.dp))) {
            FishImage(card.imageUrl, card.rarity, Modifier.fillMaxSize())
            RarityBadge(card.rarity, modifier = Modifier.align(Alignment.TopStart).padding(6.dp))
        }
        Spacer(Modifier.height(8.dp))
        Text(card.name, color = DengColors.TextPrimary, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
            Text("x${exactCount(card.count)}", color = DengColors.Cyan, fontWeight = FontWeight.Bold)
            card.maxWeight?.let { Text("Wt $it", color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

// ── STATS ───────────────────────────────────────────────────────────────────
@Composable
private fun StatsSection(api: MonitorApi) {
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var stats by remember { mutableStateOf<FishStats?>(null) }

    fun load() {
        loading = true; error = null
        scope.launch {
            runCatching { api.fishStats() }
                .onSuccess { stats = it; loading = false }
                .onFailure { error = fishFriendlyError(it, api.host); loading = false }
        }
    }
    LaunchedEffect(Unit) { load() }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when {
            loading -> LoadingCard()
            error != null -> ErrorCard(message = error!!, onRetry = { load() })
            stats == null || !stats!!.hasData -> EmptyCard("You do not have Fish It stats yet.")
            else -> {
                val s = stats!!
                DengCard {
                    Text(
                        Format.displayUsername(s.username, LocalHideUsername.current),
                        style = MaterialTheme.typography.titleMedium,
                        color = DengColors.TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text("TOTAL FISH CAUGHT", style = MaterialTheme.typography.labelMedium, color = DengColors.TextMuted)
                    Spacer(Modifier.height(6.dp))
                    Text(exactCount(s.totalFish), style = MaterialTheme.typography.headlineLarge, color = DengColors.Cyan, fontWeight = FontWeight.Bold)
                    s.rank?.let {
                        Text("Rank #${it.rank} of ${it.of}", color = DengColors.TextMuted, style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (s.rarityCards.isNotEmpty()) {
                    Text("RARITY", style = MaterialTheme.typography.labelMedium, color = DengColors.TextMuted)
                    StatCardGrid(s.rarityCards)
                }
                if (s.rodCards.isNotEmpty()) {
                    Text("RODS", style = MaterialTheme.typography.labelMedium, color = DengColors.TextMuted)
                    StatCardGrid(s.rodCards)
                }
                Spacer(Modifier.height(4.dp))
            }
        }
        Spacer(Modifier.height(48.dp))
    }
}

@Composable
private fun StatCardGrid(cards: List<my.id.deng.monitor.data.FishStatCard>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        cards.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
                row.forEach { card ->
                    DengCard(modifier = Modifier.weight(1f)) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                            FishImage(card.imageUrl, card.key, Modifier.size(64.dp).clip(RoundedCornerShape(14.dp)))
                            Spacer(Modifier.height(8.dp))
                            Text(card.label, color = DengColors.TextMuted, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(exactCount(card.displayAmount), color = DengColors.TextPrimary, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge)
                        }
                    }
                }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

// ── FISH GRID ─────────────────────────────────────────────────────────────────
@Composable
private fun FishSection(api: MonitorApi) {
    val scope = rememberCoroutineScope()
    var search by rememberSaveable { mutableStateOf("") }
    var rarity by rememberSaveable { mutableStateOf("") }
    var sort by rememberSaveable { mutableStateOf("amount") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var grid by remember { mutableStateOf<FishGrid?>(null) }
    val cards = remember { mutableStateListOf<FishCard>() }
    var page by remember { mutableStateOf(1) }

    fun load(reset: Boolean) {
        if (reset) { page = 1; loading = true }
        error = null
        scope.launch {
            runCatching { api.fishGrid(search = search, rarity = rarity.ifBlank { null }, sort = sort, page = page, limit = 24) }
                .onSuccess { g ->
                    grid = g
                    if (reset) cards.clear()
                    cards.addAll(g.items)
                    loading = false
                }
                .onFailure { error = fishFriendlyError(it, api.host); loading = false }
        }
    }
    // Reload when filters change (debounced for search).
    LaunchedEffect(search, rarity, sort) {
        kotlinx.coroutines.delay(if (search.isBlank()) 0 else 300)
        load(reset = true)
    }

    Column(modifier = Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = search,
            onValueChange = { search = it },
            placeholder = { Text("Search fish by name…") },
            singleLine = true,
            keyboardOptions = KeyboardOptions.Default,
            modifier = Modifier.fillMaxWidth(),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = DengColors.Cyan,
                unfocusedBorderColor = DengColors.BorderMuted,
                focusedTextColor = DengColors.TextPrimary,
                unfocusedTextColor = DengColors.TextPrimary,
            ),
        )
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            RarityFilter(rarity) { rarity = it }
        }
        Spacer(Modifier.height(8.dp))
        FlowChips(FISH_SORTS, sort) { sort = it }
        Spacer(Modifier.height(12.dp))

        when {
            loading && cards.isEmpty() -> LoadingCard()
            error != null && cards.isEmpty() -> ErrorCard(message = error!!, onRetry = { load(true) })
            cards.isEmpty() -> EmptyCard(if (search.isNotBlank() || rarity.isNotBlank()) "No fish match your filters." else "You do not have any tracked fish yet.")
            else -> {
                LazyVerticalGrid(
                    columns = GridCells.Fixed(2),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    contentPadding = PaddingValues(bottom = 56.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    items(cards, key = { it.name + it.rarity }) { card -> FishGridCard(card) }
                    item(span = { androidx.compose.foundation.lazy.grid.GridItemSpan(2) }) {
                        val g = grid
                        if (g != null && page < g.pages) {
                            DengGradientButton(
                                text = if (loading) "Loading…" else "Load more",
                                onClick = { if (!loading) { page += 1; load(false) } },
                            )
                        } else {
                            Spacer(Modifier.height(8.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RarityFilter(current: String, onSelect: (String) -> Unit) {
    val options = listOf("" to "All", "secret" to "Secret", "forgotten" to "Forgotten")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, label) -> Chip(label, key == current) { onSelect(key) } }
    }
}

@Composable
private fun FlowChips(options: List<Pair<String, String>>, current: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        options.forEach { (key, label) -> Chip(label, key == current) { onSelect(key) } }
    }
}

@Composable
private fun Chip(label: String, selected: Boolean, onClick: () -> Unit) {
    Surface(
        color = if (selected) DengColors.Cyan.copy(alpha = 0.18f) else DengColors.CardSoft,
        border = BorderStroke(1.dp, if (selected) DengColors.Cyan else DengColors.BorderMuted),
        shape = RoundedCornerShape(999.dp),
        modifier = Modifier.clickable { onClick() },
    ) {
        Text(
            label,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            color = if (selected) DengColors.TextPrimary else DengColors.TextMuted,
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

@Composable
private fun FishGridCard(card: FishCard) {
    DengCard {
        Box(modifier = Modifier.fillMaxWidth().aspectRatio(1f).clip(RoundedCornerShape(12.dp))) {
            FishImage(card.imageUrl, card.rarity, Modifier.fillMaxSize())
            RarityBadge(card.rarity, modifier = Modifier.align(Alignment.TopStart).padding(6.dp))
        }
        Spacer(Modifier.height(8.dp))
        Text(card.name, color = DengColors.TextPrimary, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
            Text("x${exactCount(card.count)}", color = DengColors.Cyan, fontWeight = FontWeight.Bold)
            card.maxWeight?.let { Text("Wt $it", color = DengColors.TextDim, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun RarityBadge(rarity: String, modifier: Modifier = Modifier) {
    val (bg, label) = when (rarity.lowercase()) {
        "forgotten" -> DengColors.Warning to "Forgotten"
        else -> DengColors.Pink to "Secret"
    }
    Surface(color = bg.copy(alpha = 0.9f), shape = RoundedCornerShape(999.dp), modifier = modifier) {
        Text(label, modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp), color = Color.White, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
    }
}

/**
 * Loads a fish/rod image from the database (remote rbxcdn URL). On null/missing
 * or error, shows a themed fallback box with a fish emoji — never a broken image.
 */
@Composable
private fun FishImage(url: String?, rarity: String, modifier: Modifier = Modifier) {
    if (url.isNullOrBlank()) {
        FallbackBox(rarity, modifier)
        return
    }
    SubcomposeAsyncImage(
        model = url,
        contentDescription = null,
        contentScale = ContentScale.Crop,
        modifier = modifier.background(DengColors.CardSoft),
        loading = {
            Box(Modifier.fillMaxSize().background(DengColors.CardSoft), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = DengColors.Cyan, strokeWidth = 2.dp, modifier = Modifier.size(18.dp))
            }
        },
        error = { FallbackBox(rarity, Modifier.fillMaxSize()) },
    )
}

@Composable
private fun FallbackBox(rarity: String, modifier: Modifier = Modifier) {
    val tint = when (rarity.lowercase()) {
        "forgotten" -> DengColors.Warning
        "secret" -> DengColors.Pink
        "rod", "ghostfinn", "element", "diamond" -> DengColors.Purple
        else -> DengColors.Cyan
    }
    Box(
        modifier = modifier.background(tint.copy(alpha = 0.14f)),
        contentAlignment = Alignment.Center,
    ) {
        Text("\uD83D\uDC1F", style = MaterialTheme.typography.headlineMedium)
    }
}
