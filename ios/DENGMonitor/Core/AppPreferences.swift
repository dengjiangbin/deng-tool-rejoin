import SwiftUI

enum ThemeMode: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var label: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}

/// Non-sensitive UI preferences (theme, hide username).
@MainActor
final class AppPreferences: ObservableObject {
    @AppStorage("theme_mode") var themeMode: ThemeMode = .system
    @AppStorage("hide_username") var hideUsername: Bool = false

    var colorScheme: ColorScheme? {
        switch themeMode {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}
