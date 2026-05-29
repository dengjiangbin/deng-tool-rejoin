import SwiftUI

@main
struct DENGMonitorApp: App {
    @StateObject private var session = SessionManager()
    @StateObject private var prefs = AppPreferences()
    @StateObject private var api: MonitorAPI

    init() {
        _api = StateObject(wrappedValue: MonitorAPI(tokenProvider: { KeychainStore.loadToken() }))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(prefs)
                .environmentObject(api)
                .preferredColorScheme(prefs.colorScheme)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var session: SessionManager
    @EnvironmentObject var api: MonitorAPI

    var body: some View {
        Group {
            if session.isPaired {
                MainTabView()
            } else {
                PairView(api: api, session: session)
            }
        }
    }
}

struct MainTabView: View {
    @EnvironmentObject var api: MonitorAPI
    @EnvironmentObject var session: SessionManager
    @EnvironmentObject var prefs: AppPreferences

    var body: some View {
        TabView {
            DashboardView(api: api)
                .tabItem { Label("Dashboard", systemImage: "gauge") }
            FishItView(api: api)
                .tabItem { Label("Fish It", systemImage: "fish") }
            PackagesView(api: api)
                .tabItem { Label("Packages", systemImage: "shippingbox") }
            SnapshotView(api: api)
                .tabItem { Label("Snapshot", systemImage: "camera.viewfinder") }
            SettingsView(api: api, session: session)
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
