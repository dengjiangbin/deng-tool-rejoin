import SwiftUI

@MainActor
final class SnapshotViewModel: ObservableObject {
    @Published var loading = true
    @Published var error: String?
    @Published var imageData: Data?
    @Published var statusLine = ""

    private let api: MonitorAPI

    init(api: MonitorAPI) { self.api = api }

    func load() async {
        loading = true
        defer { loading = false }
        do {
            let list = try await api.listDevices()
            guard let device = list.devices.first else {
                error = "No cloud phone connected."
                return
            }
            let id = KeychainStore.loadLastDeviceId() ?? device.id
            if let data = try await api.snapshotData(deviceId: id) {
                imageData = data
                error = nil
                statusLine = "Latest snapshot"
            } else {
                imageData = nil
                error = "Waiting for first snapshot from the cloud phone bridge…"
            }
        } catch {
            imageData = nil
            error = friendlySnapshotError(error)
        }
    }

    private func friendlySnapshotError(_ error: Error) -> String {
        let msg = (error as? LocalizedError)?.errorDescription ?? ""
        if msg.contains("503") { return "Snapshot upload failed: HTTP 503. Retrying on next bridge push…" }
        if msg.contains("401") { return "Sign in required." }
        return msg.isEmpty ? "Could not load snapshot." : msg
    }
}

struct SnapshotView: View {
    @ObservedObject var api: MonitorAPI
    @StateObject private var vm: SnapshotViewModel

    init(api: MonitorAPI) {
        self.api = api
        _vm = StateObject(wrappedValue: SnapshotViewModel(api: api))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                HStack {
                    Text("Snapshot").font(.largeTitle.bold())
                    Spacer()
                    Button("Refresh") { Task { await vm.load() } }
                }
                if vm.loading {
                    ProgressView("Loading snapshot…")
                        .frame(minHeight: 200)
                } else if let data = vm.imageData, let ui = UIImage(data: data) {
                    Image(uiImage: ui)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    Text(vm.statusLine).font(.footnote).foregroundStyle(.secondary)
                } else {
                    DengCard {
                        Text(vm.error ?? "No snapshot available yet.")
                            .foregroundStyle(.secondary)
                        Button("Retry") { Task { await vm.load() } }
                    }
                    .frame(minHeight: 200)
                }
            }
            .padding(16)
        }
        .task { await vm.load() }
    }
}
