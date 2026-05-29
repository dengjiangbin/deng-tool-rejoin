import SwiftUI

@MainActor
final class PackagesViewModel: ObservableObject {
    @Published var loading = true
    @Published var error: String?
    @Published var packages: [PackageState] = []
    @Published var deviceLabel = ""

    private let api: MonitorAPI

    init(api: MonitorAPI) { self.api = api }

    func load() async {
        do {
            let list = try await api.listDevices()
            guard let device = list.devices.first else {
                error = "No cloud phone is connected yet."
                loading = false
                return
            }
            let id = KeychainStore.loadLastDeviceId() ?? device.id
            KeychainStore.saveLastDeviceId(id)
            let status = try await api.deviceStatus(deviceId: id)
            packages = status.packages
            deviceLabel = status.device.displayName
            error = nil
        } catch {
            error = (error as? LocalizedError)?.errorDescription ?? "Could not load packages."
        }
        loading = false
    }
}

struct PackagesView: View {
    @ObservedObject var api: MonitorAPI
    @EnvironmentObject var prefs: AppPreferences
    @StateObject private var vm: PackagesViewModel

    init(api: MonitorAPI) {
        self.api = api
        _vm = StateObject(wrappedValue: PackagesViewModel(api: api))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Packages").font(.largeTitle.bold())
                if vm.loading {
                    ProgressView()
                } else if let err = vm.error {
                    DengCard {
                        Text(err)
                        Button("Retry") { Task { await vm.load() } }
                    }
                } else if vm.packages.isEmpty {
                    Text("No packages reported yet.").foregroundStyle(.secondary)
                } else {
                    ForEach(vm.packages) { pkg in
                        DengCard {
                            Text(pkg.displayName ?? pkg.packageName)
                                .font(.headline)
                            if let user = pkg.username {
                                Text(UsernameMask.display(user, hide: prefs.hideUsername))
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                            }
                            HStack {
                                stateBadge(pkg.state)
                                Spacer()
                                Text("RAM \(NumberFormat.exact(pkg.ramMb)) MB")
                                    .font(.caption)
                            }
                            if pkg.runtimeSeconds > 0 {
                                Text("Runtime \(formatRuntime(pkg.runtimeSeconds))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .task { await vm.load() }
    }

    private func stateBadge(_ state: String) -> some View {
        Text(state)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.primary.opacity(0.08))
            .clipShape(Capsule())
    }

    private func formatRuntime(_ sec: Int) -> String {
        let h = sec / 3600
        let m = (sec % 3600) / 60
        let s = sec % 60
        if h > 0 { return String(format: "%02d:%02d:%02d", h, m, s) }
        return String(format: "%02d:%02d", m, s)
    }
}
