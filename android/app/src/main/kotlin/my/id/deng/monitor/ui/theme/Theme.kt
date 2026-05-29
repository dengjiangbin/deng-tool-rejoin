package my.id.deng.monitor.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush

private fun schemeFor(p: DengPalette, dark: Boolean) = if (dark) {
    darkColorScheme(
        primary = p.cyan,
        onPrimary = p.bgA,
        secondary = p.pink,
        onSecondary = p.bgA,
        tertiary = p.purple,
        background = p.bgA,
        onBackground = p.textPrimary,
        surface = p.cardBg,
        onSurface = p.textPrimary,
        surfaceVariant = p.cardSoft,
        onSurfaceVariant = p.textMuted,
        error = p.danger,
    )
} else {
    lightColorScheme(
        primary = p.cyan,
        onPrimary = Color_White,
        secondary = p.pink,
        onSecondary = Color_White,
        tertiary = p.purple,
        background = p.bgA,
        onBackground = p.textPrimary,
        surface = p.cardBg,
        onSurface = p.textPrimary,
        surfaceVariant = p.cardSoft,
        onSurfaceVariant = p.textMuted,
        error = p.danger,
    )
}

private val Color_White = androidx.compose.ui.graphics.Color.White

/**
 * App theme. When [darkTheme] flips, the swappable [DengColors.current]
 * palette is updated and the whole content tree recomposes, recoloring
 * every screen (dashboard, snapshot, packages, settings, fish it).
 */
@Composable
fun DengMonitorTheme(darkTheme: Boolean = true, content: @Composable () -> Unit) {
    val palette = if (darkTheme) DarkPalette else LightPalette
    // Set BEFORE composing children so their DengColors.X getters read the
    // active palette during this composition pass.
    DengColors.current = palette

    MaterialTheme(
        colorScheme = schemeFor(palette, darkTheme),
        typography = DengTypography,
        content = {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        brush = Brush.linearGradient(
                            colors = listOf(palette.bgA, palette.bgB, palette.bgC),
                        ),
                    )
            ) {
                content()
            }
        },
    )
}
