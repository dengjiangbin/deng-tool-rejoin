package my.id.deng.monitor.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import my.id.deng.monitor.data.ApiException
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore
import my.id.deng.monitor.data.friendlyNetworkError
import my.id.deng.monitor.ui.theme.DengColors

@Composable
fun PairScreen(api: MonitorApi, sessionStore: SessionStore) {
    var code by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp)
            .padding(top = 64.dp, bottom = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            // v1.0.3: in-app header drops the redundant "APK" suffix to
            // match the new app_name. "DENG Tool: Rejoin APK" still
            // appears in the website download footer (line below) because
            // there it refers to the literal .apk artifact you install.
            "DENG All In One",
            style = MaterialTheme.typography.headlineLarge,
            color = DengColors.Cyan,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Monitor your Rejoin packages from Android.",
            style = MaterialTheme.typography.bodyLarge,
            color = DengColors.TextMuted,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(40.dp))

        DengCard {
            Text("Pair Device", style = MaterialTheme.typography.titleLarge, color = DengColors.TextPrimary)
            Spacer(Modifier.height(8.dp))
            Text(
                "Log in at aio.deng.my.id, open the Download page, " +
                "generate a pairing code, then enter it here.",
                style = MaterialTheme.typography.bodyMedium,
                color = DengColors.TextMuted,
            )
            Spacer(Modifier.height(16.dp))

            OutlinedTextField(
                value = code,
                onValueChange = { input ->
                    code = input.uppercase().filter { it.isLetterOrDigit() }.take(16)
                },
                singleLine = true,
                label = { Text("Pairing code") },
                placeholder = { Text("ABCD-WXYZ") },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = DengColors.Cyan,
                    unfocusedBorderColor = DengColors.BorderMuted,
                    cursorColor = DengColors.Cyan,
                ),
            )
            Spacer(Modifier.height(16.dp))

            error?.let {
                ErrorBanner(it)
                Spacer(Modifier.height(12.dp))
            }

            DengGradientButton(
                text = if (loading) "Pairing…" else "Pair",
                enabled = !loading && code.length >= 6,
                onClick = {
                    error = null
                    loading = true
                    scope.launch {
                        try {
                            val resp = api.pair(code = code, deviceName = android.os.Build.MODEL)
                            sessionStore.saveSession(resp.appSessionToken, resp.owner.discordUserId)
                        } catch (e: ApiException) {
                            error = e.safeMessage
                        } catch (e: Throwable) {
                            error = friendlyNetworkError(e, api.host)
                        } finally {
                            loading = false
                        }
                    }
                },
            )
        }

        Spacer(Modifier.weight(1f))
        Text(
            "Only install DENG All In One APK from aio.deng.my.id/download.",
            style = MaterialTheme.typography.bodySmall,
            color = DengColors.TextDim,
            textAlign = TextAlign.Center,
        )
    }
}
