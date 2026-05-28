package my.id.deng.monitor.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush

private val DengDarkScheme = darkColorScheme(
    primary = DengColors.Cyan,
    onPrimary = DengColors.BgA,
    secondary = DengColors.Pink,
    onSecondary = DengColors.BgA,
    tertiary = DengColors.Purple,
    background = DengColors.BgA,
    onBackground = DengColors.TextPrimary,
    surface = DengColors.CardBg,
    onSurface = DengColors.TextPrimary,
    surfaceVariant = DengColors.CardSoft,
    onSurfaceVariant = DengColors.TextMuted,
    error = DengColors.Danger,
)

@Composable
fun DengMonitorTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DengDarkScheme,
        typography = DengTypography,
        content = {
            // Body gradient background — matches website --body-bg.
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        brush = Brush.linearGradient(
                            colors = listOf(DengColors.BgA, DengColors.BgB, DengColors.BgC),
                        ),
                    )
            ) {
                content()
            }
        },
    )
}
