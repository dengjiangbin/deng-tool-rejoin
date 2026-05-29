import SwiftUI

struct DengPalette {
    let bgA: Color
    let bgB: Color
    let bgC: Color
    let cyan: Color
    let pink: Color
    let purple: Color
    let cardBg: Color
    let textPrimary: Color
    let textMuted: Color
    let success: Color
    let warning: Color
    let danger: Color
}

enum DengTheme {
    static let dark = DengPalette(
        bgA: Color(red: 5/255, green: 8/255, blue: 22/255),
        bgB: Color(red: 17/255, green: 24/255, blue: 39/255),
        bgC: Color(red: 37/255, green: 10/255, blue: 38/255),
        cyan: Color(red: 0, green: 207/255, blue: 1),
        pink: Color(red: 1, green: 47/255, blue: 179/255),
        purple: Color(red: 123/255, green: 92/255, blue: 1),
        cardBg: Color(red: 15/255, green: 23/255, blue: 42/255).opacity(0.8),
        textPrimary: Color(red: 248/255, green: 251/255, blue: 1),
        textMuted: Color(red: 159/255, green: 176/255, blue: 201/255),
        success: Color(red: 22/255, green: 163/255, blue: 74/255),
        warning: Color(red: 217/255, green: 119/255, blue: 6/255),
        danger: Color(red: 239/255, green: 68/255, blue: 68/255)
    )

    static let light = DengPalette(
        bgA: Color(red: 223/255, green: 246/255, blue: 1),
        bgB: Color(red: 238/255, green: 242/255, blue: 1),
        bgC: Color(red: 1, green: 228/255, blue: 246/255),
        cyan: Color(red: 14/255, green: 143/255, blue: 191/255),
        pink: Color(red: 192/255, green: 24/255, blue: 122/255),
        purple: Color(red: 97/255, green: 67/255, blue: 178/255),
        cardBg: Color.white.opacity(0.95),
        textPrimary: Color(red: 15/255, green: 23/255, blue: 42/255),
        textMuted: Color(red: 71/255, green: 85/255, blue: 105/255),
        success: Color(red: 21/255, green: 128/255, blue: 61/255),
        warning: Color(red: 180/255, green: 83/255, blue: 9/255),
        danger: Color(red: 220/255, green: 38/255, blue: 38/255)
    )

    static func palette(for scheme: ColorScheme) -> DengPalette {
        scheme == .dark ? dark : light
    }
}

struct DengBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        let p = DengTheme.palette(for: scheme)
        LinearGradient(colors: [p.bgA, p.bgB, p.bgC], startPoint: .topLeading, endPoint: .bottomTrailing)
            .ignoresSafeArea()
    }
}

struct DengCard<Content: View>: View {
    @Environment(\.colorScheme) private var scheme
    @ViewBuilder let content: Content

    var body: some View {
        let p = DengTheme.palette(for: scheme)
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(p.cardBg)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(p.cyan.opacity(0.25), lineWidth: 1)
            )
    }
}
