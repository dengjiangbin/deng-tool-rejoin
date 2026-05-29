import SwiftUI
import UIKit

struct PairView: View {
    @ObservedObject var api: MonitorAPI
    @ObservedObject var session: SessionManager
    @State private var code = ""
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        ZStack {
            DengBackground()
            ScrollView {
                VStack(spacing: 24) {
                    Text("DENG Tool: Rejoin")
                        .font(.largeTitle.bold())
                        .foregroundStyle(DengTheme.dark.cyan)
                    Text("Monitor your Rejoin packages from iPhone.")
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)

                    DengCard {
                        Text("Pair Device").font(.headline)
                        Text("Sign in at tool.deng.my.id, open Download, generate a pairing code, then enter it here.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        TextField("Pairing code", text: $code)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .padding(12)
                            .background(Color.primary.opacity(0.06))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        if let error {
                            Text(error).font(.footnote).foregroundStyle(DengTheme.dark.danger)
                        }
                        Button(action: { Task { await redeem() } }) {
                            if loading {
                                ProgressView().tint(.white)
                            } else {
                                Text("Pair").bold()
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(DengTheme.dark.cyan)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .disabled(loading || code.count < 6)

                        Link(destination: URL(string: AppConfig.downloadURL)!) {
                            Text("Open Download page on website")
                                .font(.footnote)
                        }
                    }
                }
                .padding(24)
            }
        }
    }

    private func redeem() async {
        loading = true
        error = nil
        defer { loading = false }
        do {
            let name = UIDevice.current.name
            let resp = try await api.pair(code: code.uppercased(), deviceName: name)
            session.savePairing(resp)
        } catch {
            error = (error as? LocalizedError)?.errorDescription ?? api.fishFriendlyMessage(error)
        }
    }
}
