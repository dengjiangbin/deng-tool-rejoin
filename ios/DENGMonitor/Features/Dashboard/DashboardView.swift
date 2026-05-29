import SwiftUI

@MainActor
final class DashboardViewModel: ObservableObject {
    @Published var loading = true
    @Published var error: String?
    @Published var devices: [DeviceSummary] = []
    @Published var packageSummary = DashboardPackageSummary(total: 0, online: 0, dead: 0, launching: nil, joining: nil, noHeartbeat: nil, totalRamMb: nil)

    private let api: MonitorAPI
    private var pollTask: Task<Void, Never>?

    init(api: MonitorAPI) { self.api = api }

    func start() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                await refresh()
                let interval = max(devices.compactMap(\.monitorIntervalSeconds).max() ?? 5, 5)
                try? await Task.sleep(nanoseconds: UInt64(interval) * 1_000_000_000)
            }
        }
    }

    func stop() { pollTask?.cancel() }

    func refresh() async {
        do {
            let resp = try await api.listDevices()
            devices = resp.devices
            packageSummary = resp.packageSummary
            error = nil
            loading = false
        } catch {
            error = (error as? LocalizedError)?.errorDescription ?? "Could not load dashboard."
            loading = false
        }
    }

    var intervalLabel: String {
        let secs = devices.map { $0.monitorIntervalSeconds ?? 5 }
        let unique = Set(secs)
        if unique.count == 1, let one = unique.first { return "\(one)s" }
        if unique.count > 1 { return "Mixed" }
        return "—"
    }

    var lastUpdateText: String {
        guard let d = devices.min(by: { ($0.secondsSinceLastSeen ?? Int.max) < ($1.secondsSinceLastSeen ?? Int.max) }) else {
            return "—"
        }
        if let s = d.secondsSinceLastSeen { return "\(s)s ago" }
        return "—"
    }
}

struct DashboardView: View {
    @ObservedObject var api: MonitorAPI
    @StateObject private var vm: DashboardViewModel

    init(api: MonitorAPI) {
        self.api = api
        _vm = StateObject(wrappedValue: DashboardViewModel(api: api))
    }

    var body: some View {
        let p = DengTheme.palette(for: .dark)
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Dashboard").font(.largeTitle.bold())
                    Spacer()
                    Button("Refresh") { Task { await vm.refresh() } }
                        .font(.subheadline.weight(.semibold))
                }
                if vm.loading {
                    ProgressView("Loading…")
                } else if let err = vm.error {
                    DengCard {
                        Text(err)
                        Button("Retry") { Task { await vm.refresh() } }
                    }
                } else {
                    DengCard {
                        LabeledRow("Last Update", vm.lastUpdateText)
                        LabeledRow("Interval", vm.intervalLabel)
                        HStack(spacing: 8) {
                            StatPill("TOTAL", vm.packageSummary.total, p.cyan)
                            StatPill("ONLINE", vm.packageSummary.online, p.success)
                            StatPill("DEAD", vm.packageSummary.dead, p.danger)
                        }
                    }
                    Text("RAM Details").font(.headline)
                    ForEach(vm.devices) { d in
                        DengCard {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(d.displayName).font(.subheadline.weight(.semibold))
                                    if let ram = d.deviceRam {
                                        Text(ram.displayText).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                                Text(d.isConnected ? "Connected" : "Offline")
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(d.isConnected ? p.success : p.danger)
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .onAppear { vm.start() }
        .onDisappear { vm.stop() }
    }
}

private struct LabeledRow: View {
    let label: String
    let value: String
    init(_ label: String, _ value: String) { self.label = label; self.value = value }
    var body: some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).fontWeight(.semibold)
        }
    }
}

private struct StatPill: View {
    let title: String
    let value: Int
    let color: Color
    var body: some View {
        VStack(spacing: 4) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(NumberFormat.exact(value)).font(.title3.bold()).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity)
    }
}
