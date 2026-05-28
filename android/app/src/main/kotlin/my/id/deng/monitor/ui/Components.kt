package my.id.deng.monitor.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import my.id.deng.monitor.ui.theme.DengColors

/**
 * Card matching the website `.section-card` look — rounded glass surface
 * with cyan/pink hairline border.
 */
@Composable
fun DengCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(20.dp)),
        color = DengColors.CardBg,
        border = BorderStroke(1.dp, DengColors.BorderCyan),
        shadowElevation = 0.dp,
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            content = content,
        )
    }
}

/**
 * Gradient pill button (cyan → purple → pink) — mirrors the website
 * `--button-gradient`.
 */
@Composable
fun DengGradientButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Button(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(DengColors.GradientButton),
        enabled = enabled,
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.Transparent,
            disabledContainerColor = Color.Transparent,
            contentColor = Color.White,
            disabledContentColor = Color.White.copy(alpha = 0.45f),
        ),
        elevation = ButtonDefaults.buttonElevation(0.dp, 0.dp, 0.dp, 0.dp),
    ) {
        Text(
            text = text,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(vertical = 4.dp),
        )
    }
}

@Composable
fun StateBadge(state: String) {
    val (bg, fg) = when (state) {
        "Online"        -> DengColors.Success.copy(alpha = 0.18f) to DengColors.Success
        "Dead"          -> DengColors.Danger.copy(alpha = 0.18f) to DengColors.Danger
        "Relaunching"   -> DengColors.Warning.copy(alpha = 0.18f) to DengColors.Warning
        "No Heartbeat"  -> DengColors.Warning.copy(alpha = 0.18f) to DengColors.Warning
        "Launching"    -> DengColors.Cyan.copy(alpha = 0.18f) to DengColors.Cyan
        else            -> DengColors.TextMuted.copy(alpha = 0.18f) to DengColors.TextMuted
    }
    Surface(
        color = bg,
        shape = RoundedCornerShape(999.dp),
    ) {
        Text(
            text = state,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            color = fg,
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

@Composable
fun StatTile(
    label: String,
    value: String,
    accent: Color = DengColors.Cyan,
    modifier: Modifier = Modifier,
) {
    DengCard(modifier = modifier) {
        Text(label.uppercase(), style = MaterialTheme.typography.labelMedium, color = DengColors.TextMuted)
        Spacer(Modifier.height(6.dp))
        Text(value, style = MaterialTheme.typography.headlineMedium, color = accent, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun ErrorBanner(message: String) {
    Surface(
        color = DengColors.Danger.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, DengColors.Danger.copy(alpha = 0.4f)),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(message, color = DengColors.Danger, style = MaterialTheme.typography.bodyMedium)
        }
    }
}
