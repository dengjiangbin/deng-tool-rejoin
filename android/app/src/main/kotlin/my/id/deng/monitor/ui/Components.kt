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

/**
 * Package state badge.
 *
 * v1.0.4: the APK-visible state vocabulary is exactly five values —
 * Dead, Launching, Joining, Online, No Heartbeat. Older state names
 * (Relaunching, etc.) still get a sensible color so legacy backends
 * don't render as a grey "unknown" pill, but the canonical 5 cover
 * everything the Termux supervisor will emit going forward.
 *
 * The lobby state is intentionally NOT a branch — per user requirement,
 * lobby maps to Dead at the bridge level, never reaches the APK.
 */
@Composable
fun StateBadge(state: String) {
    val (bg, fg) = when (state) {
        "Online"        -> DengColors.Success.copy(alpha = 0.18f) to DengColors.Success
        "Dead"          -> DengColors.Danger.copy(alpha = 0.18f) to DengColors.Danger
        "Launching"     -> DengColors.Cyan.copy(alpha = 0.18f) to DengColors.Cyan
        "Joining"       -> DengColors.Purple.copy(alpha = 0.18f) to DengColors.Purple
        "No Heartbeat"  -> DengColors.Warning.copy(alpha = 0.18f) to DengColors.Warning
        // Legacy / transitional — keep colored so users see SOMETHING.
        "Relaunching"   -> DengColors.Cyan.copy(alpha = 0.18f) to DengColors.Cyan
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

/**
 * Device-level connection badge ("Connected" / "Disconnected").
 *
 * v1.0.4: split out from StateBadge because the package state
 * vocabulary (Dead / Online / Launching / Joining / No Heartbeat) and
 * the device link state are unrelated concepts. The Dashboard used to
 * reuse StateBadge with "Online"/"Dead" — that was confusing because
 * a cloud phone could be "Connected" while every package was "Dead".
 */
@Composable
fun ConnectionBadge(label: String, connected: Boolean) {
    val (bg, fg) = if (connected) {
        DengColors.Success.copy(alpha = 0.18f) to DengColors.Success
    } else {
        DengColors.Danger.copy(alpha = 0.18f) to DengColors.Danger
    }
    Surface(color = bg, shape = RoundedCornerShape(999.dp)) {
        Text(
            text = label,
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

/**
 * v1.0.5: full error card with an explicit Retry action.
 *
 * The polling loop already auto-retries on its next tick, but a user staring
 * at a network error has no idea that's happening — so before v1.0.5 it felt
 * like the app was "stuck loading forever". This card gives them an immediate,
 * obvious way to retry (and surfaces the safe, host-named reason) instead of
 * an opaque spinner.
 */
@Composable
fun ErrorCard(
    message: String,
    onRetry: () -> Unit,
    title: String = "Can't reach the backend",
) {
    Surface(
        color = DengColors.Danger.copy(alpha = 0.12f),
        border = BorderStroke(1.dp, DengColors.Danger.copy(alpha = 0.4f)),
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                title,
                color = DengColors.Danger,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                message,
                color = DengColors.TextMuted,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(Modifier.height(14.dp))
            DengGradientButton(text = "Retry", onClick = onRetry)
        }
    }
}
