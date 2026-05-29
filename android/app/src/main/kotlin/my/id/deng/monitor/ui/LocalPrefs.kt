package my.id.deng.monitor.ui

import androidx.compose.runtime.compositionLocalOf

/**
 * Whether Discord usernames should be masked in the UI. This is a display-only
 * privacy flag — it never affects backend identity or stats matching.
 */
val LocalHideUsername = compositionLocalOf { false }
