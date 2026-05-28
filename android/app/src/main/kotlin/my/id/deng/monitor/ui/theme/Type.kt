package my.id.deng.monitor.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val Base = TextStyle(fontFamily = FontFamily.SansSerif)

val DengTypography = Typography(
    headlineLarge   = Base.copy(fontSize = 32.sp, fontWeight = FontWeight.SemiBold),
    headlineMedium  = Base.copy(fontSize = 24.sp, fontWeight = FontWeight.SemiBold),
    titleLarge      = Base.copy(fontSize = 20.sp, fontWeight = FontWeight.Medium),
    titleMedium     = Base.copy(fontSize = 17.sp, fontWeight = FontWeight.Medium),
    bodyLarge       = Base.copy(fontSize = 15.sp, fontWeight = FontWeight.Normal),
    bodyMedium      = Base.copy(fontSize = 14.sp, fontWeight = FontWeight.Normal),
    bodySmall       = Base.copy(fontSize = 12.sp, fontWeight = FontWeight.Normal),
    labelLarge      = Base.copy(fontSize = 14.sp, fontWeight = FontWeight.SemiBold),
    labelMedium     = Base.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold),
)
