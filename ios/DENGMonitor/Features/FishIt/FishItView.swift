import SwiftUI

enum FishItTab: String, CaseIterable {
    case daily, stats, fish
    var label: String {
        switch self {
        case .daily: return "Daily"
        case .stats: return "Stats"
        case .fish: return "Fish"
        }
    }
}

struct FishItView: View {
    @ObservedObject var api: MonitorAPI
    @EnvironmentObject var prefs: AppPreferences
    @State private var tab: FishItTab = .daily

    var body: some View {
        VStack(spacing: 0) {
            Picker("Fish It", selection: $tab) {
                ForEach(FishItTab.allCases, id: \.self) { t in
                    Text(t.label).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.top, 8)

            switch tab {
            case .daily: FishDailyView(api: api)
            case .stats: FishStatsView(api: api, hideUsername: prefs.hideUsername)
            case .fish: FishGridView(api: api)
            }
        }
    }
}

struct FishDailyView: View {
    @ObservedObject var api: MonitorAPI
    @State private var period = "today"
    @State private var daily: FishDaily?
    @State private var loading = true
    @State private var error: String?

    private let periods = [
        ("today", "Today"), ("yesterday", "Yesterday"), ("7d", "7 Days"),
        ("30d", "30 Days"), ("all", "All Time"),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(periods, id: \.0) { p in
                            Button(p.1) { period = p.0; Task { await load() } }
                                .buttonStyle(.bordered)
                                .tint(period == p.0 ? DengTheme.dark.cyan : .gray)
                        }
                    }
                }
                if loading { ProgressView() }
                else if let error { authOrError(error) }
                else if let d = daily {
                    HStack {
                        summaryTile("Total", d.summary.totalFish)
                        summaryTile("Secret", d.summary.secretFish)
                        summaryTile("Forgotten", d.summary.forgottenFish)
                    }
                    if d.cards.isEmpty {
                        Text(d.emptyMessage ?? "No catches for this period.").foregroundStyle(.secondary)
                    } else {
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                            ForEach(d.cards) { card in
                                fishCard(name: card.name, rarity: card.rarity, count: card.count,
                                         imageUrl: card.imageUrl, fallback: "fish.fill")
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .task { await load() }
    }

    private func load() async {
        loading = true
        error = nil
        do {
            daily = try await api.fishDaily(period: period)
        } catch {
            error = api.fishFriendlyMessage(error)
        }
        loading = false
    }

    private func summaryTile(_ label: String, _ n: Int) -> some View {
        DengCard {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(NumberFormat.exact(n)).font(.title2.bold())
        }
    }

    @ViewBuilder
    private func authOrError(_ msg: String) -> some View {
        DengCard { Text(msg) }
    }
}

struct FishStatsView: View {
    @ObservedObject var api: MonitorAPI
    let hideUsername: Bool
    @State private var stats: FishStats?
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if loading { ProgressView() }
                else if let error { DengCard { Text(error) } }
                else if let s = stats {
                    if let u = s.username {
                        Text(UsernameMask.display(u, hide: hideUsername))
                            .font(.headline)
                    }
                    DengCard {
                        Text("Total Fish Caught").font(.caption).foregroundStyle(.secondary)
                        Text(NumberFormat.exact(s.totalFish)).font(.largeTitle.bold())
                        if let r = s.rank {
                            Text("Rank #\(NumberFormat.exact(r.rank)) of \(NumberFormat.exact(r.of))")
                                .font(.footnote)
                        }
                    }
                    Text("Rarity").font(.headline)
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        ForEach(s.rarityCards) { card in
                            statCard(card)
                        }
                    }
                    Text("Rods").font(.headline)
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        ForEach(s.rodCards) { card in
                            statCard(card)
                        }
                    }
                }
            }
            .padding(16)
        }
        .task { await load() }
    }

    private func load() async {
        loading = true
        do { stats = try await api.fishStats(); error = nil }
        catch { error = api.fishFriendlyMessage(error) }
        loading = false
    }

    private func statCard(_ card: FishStatCard) -> some View {
        DengCard {
            RemoteImage(url: card.imageUrl, fallbackSystemName: "figure.fishing", alt: card.label)
                .frame(height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            Text(card.label).font(.caption)
            Text(NumberFormat.exact(card.displayAmount)).font(.title3.bold())
        }
    }
}

struct FishGridView: View {
    @ObservedObject var api: MonitorAPI
    @State private var search = ""
    @State private var items: [FishCard] = []
    @State private var page = 1
    @State private var pages = 1
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                TextField("Search fish", text: $search)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await reload() } }
                if loading && items.isEmpty { ProgressView() }
                else if let error { DengCard { Text(error) } }
                else if items.isEmpty {
                    Text("No fish match your filters.").foregroundStyle(.secondary)
                } else {
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        ForEach(items) { f in
                            fishCard(name: f.name, rarity: f.rarity, count: f.count,
                                     imageUrl: f.imageUrl, fallback: "fish.fill")
                        }
                    }
                    if page < pages {
                        Button("Load more") { page += 1; Task { await loadMore() } }
                    }
                }
            }
            .padding(16)
        }
        .task { await reload() }
    }

    private func reload() async {
        page = 1
        items = []
        await loadMore()
    }

    private func loadMore() async {
        loading = true
        do {
            let grid = try await api.fishGrid(search: search, rarity: nil, sort: "amount", page: page)
            items.append(contentsOf: grid.items)
            pages = grid.pages
            error = nil
        } catch {
            error = api.fishFriendlyMessage(error)
        }
        loading = false
    }
}

@ViewBuilder
private func fishCard(name: String, rarity: String, count: Int, imageUrl: String?, fallback: String) -> some View {
    DengCard {
        RemoteImage(url: imageUrl, fallbackSystemName: fallback, alt: name)
            .frame(height: 72)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        Text(name).font(.subheadline.weight(.semibold)).lineLimit(2)
        Text(rarity).font(.caption2).foregroundStyle(.secondary)
        Text("x\(NumberFormat.exact(count))").font(.headline).foregroundStyle(DengTheme.dark.cyan)
    }
}
