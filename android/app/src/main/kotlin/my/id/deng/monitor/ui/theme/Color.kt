package my.id.deng.monitor.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

/**
 * Mirrors the DENG Tool website CSS variables so the app looks visually
 * identical:
 *   --bg-a   #050816
 *   --bg-b   #111827
 *   --bg-c   #250a26
 *   --cyan   #00cfff
 *   --pink   #ff2fb3
 *   --purple #6143b2  (mid-gradient)
 *   --button-gradient: cyan → purple → pink
 */
object DengColors {
    val BgA = Color(0xFF050816)
    val BgB = Color(0xFF111827)
    val BgC = Color(0xFF250A26)

    val Cyan = Color(0xFF00CFFF)
    val Pink = Color(0xFFFF2FB3)
    val Purple = Color(0xFF7B5CFF)
    val Magenta = Color(0xFFC0187A)

    val CardBg = Color(0xCC0F172A)
    val CardSoft = Color(0x991E293B)
    val BorderCyan = Color(0x3D05C8FF)
    val BorderPink = Color(0x38FF2BAE)
    val BorderMuted = Color(0x4294A3B8)

    val TextPrimary = Color(0xFFF8FBFF)
    val TextMuted = Color(0xFF9FB0C9)
    val TextDim = Color(0xFF64748B)

    val Success = Color(0xFF16A34A)
    val Warning = Color(0xFFD97706)
    val Danger = Color(0xFFEF4444)

    val GradientButton = Brush.horizontalGradient(
        colors = listOf(Cyan, Purple, Pink),
    )
}
