import SwiftUI

struct SettingsView: View {
    @ObservedObject var api: MonitorAPI
    @ObservedObject var session: SessionManager
    @EnvironmentObject var prefs: AppPreferences

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Settings").font(.largeTitle.bold())

                DengCard {
                    Text("Account").font(.headline)
                    if let owner = KeychainStore.loadOwnerId() {
                        Text("Discord ID: \(owner)").font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                    Button("Sign out", role: .destructive) { session.signOut() }
                }

                DengCard {
                    Text("Appearance").font(.headline)
                    Picker("Theme", selection: $prefs.themeMode) {
                        ForEach(ThemeMode.allCases) { m in
                            Text(m.label).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    Toggle("Hide Username", isOn: $prefs.hideUsername)
                }

                DengCard {
                    Text("About").font(.headline)
                    LabeledContent("App version", value: "v\(AppConfig.appMarketingVersion)")
                    LabeledContent("Backend", value: AppConfig.baseURL)
                    Link("Website", destination: URL(string: AppConfig.websiteURL)!)
                    Link("Download Android APK", destination: URL(string: AppConfig.downloadURL)!)
                    Text("iOS: see website for TestFlight / coming soon")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(16)
        }
    }
}
