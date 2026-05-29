package my.id.deng.monitor.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

/**
 * DENG palette, mirroring the website CSS variables for dark, plus a
 * proper-contrast light variant (matches the website's
 * `:root[data-theme="light"]` overrides).
 *
 * v1.0.7: light/dark theme. To avoid touching ~110 `DengColors.X` call
 * sites across every screen, `DengColors` stays the single source of
 * color names but its properties now read from a swappable [current]
 * palette. [DengMonitorTheme] sets [DengColors.current] before composing
 * its content, so when the theme toggles the whole tree recomposes and
 * every screen recolors automatically — no per-screen edits needed.
 */
data class DengPalette(
    val bgA: Color,
    val bgB: Color,
    val bgC: Color,
    val cyan: Color,
    val pink: Color,
    val purple: Color,
    val magenta: Color,
    val cardBg: Color,
    val cardSoft: Color,
    val borderCyan: Color,
    val borderPink: Color,
    val borderMuted: Color,
    val textPrimary: Color,
    val textMuted: Color,
    val textDim: Color,
    val success: Color,
    val warning: Color,
    val danger: Color,
    val navBar: Color,
)

val DarkPalette = DengPalette(
    bgA = Color(0xFF050816),
    bgB = Color(0xFF111827),
    bgC = Color(0xFF250A26),
    cyan = Color(0xFF00CFFF),
    pink = Color(0xFFFF2FB3),
    purple = Color(0xFF7B5CFF),
    magenta = Color(0xFFC0187A),
    cardBg = Color(0xCC0F172A),
    cardSoft = Color(0x991E293B),
    borderCyan = Color(0x3D05C8FF),
    borderPink = Color(0x38FF2BAE),
    borderMuted = Color(0x4294A3B8),
    textPrimary = Color(0xFFF8FBFF),
    textMuted = Color(0xFF9FB0C9),
    textDim = Color(0xFF64748B),
    success = Color(0xFF16A34A),
    warning = Color(0xFFD97706),
    danger = Color(0xFFEF4444),
    navBar = Color(0xCC0F172A),
)

val LightPalette = DengPalette(
    bgA = Color(0xFFDFF6FF),
    bgB = Color(0xFFEEF2FF),
    bgC = Color(0xFFFFE4F6),
    cyan = Color(0xFF0E8FBF),
    pink = Color(0xFFC0187A),
    purple = Color(0xFF6143B2),
    magenta = Color(0xFFA31466),
    cardBg = Color(0xF2FFFFFF),
    cardSoft = Color(0xCCEEF2FF),
    borderCyan = Color(0x4D0E8FBF),
    borderPink = Color(0x40C0187A),
    borderMuted = Color(0x4094A3B8),
    textPrimary = Color(0xFF0F172A),
    textMuted = Color(0xFF475569),
    textDim = Color(0xFF64748B),
    success = Color(0xFF15803D),
    warning = Color(0xFFB45309),
    danger = Color(0xFFDC2626),
    navBar = Color(0xF2FFFFFF),
)

object DengColors {
    /** Active palette — swapped by [DengMonitorTheme]. */
    @Volatile
    var current: DengPalette = DarkPalette

    val BgA get() = current.bgA
    val BgB get() = current.bgB
    val BgC get() = current.bgC
    val Cyan get() = current.cyan
    val Pink get() = current.pink
    val Purple get() = current.purple
    val Magenta get() = current.magenta
    val CardBg get() = current.cardBg
    val CardSoft get() = current.cardSoft
    val BorderCyan get() = current.borderCyan
    val BorderPink get() = current.borderPink
    val BorderMuted get() = current.borderMuted
    val TextPrimary get() = current.textPrimary
    val TextMuted get() = current.textMuted
    val TextDim get() = current.textDim
    val Success get() = current.success
    val Warning get() = current.warning
    val Danger get() = current.danger
    val NavBar get() = current.navBar

    val GradientButton: Brush
        get() = Brush.horizontalGradient(colors = listOf(current.cyan, current.purple, current.pink))
}
