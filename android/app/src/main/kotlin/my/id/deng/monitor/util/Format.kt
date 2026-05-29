package my.id.deng.monitor.util

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeParseException
import java.util.Date
import java.util.Locale

/**
 * Pure formatting helpers — covered by `FormatTest` so the contract is locked
 * down (RAM and runtime are user-visible, must never crash on edge inputs).
 */
object Format {

    /** "642 MB", "1.4 GB", "—". Never negative, never throws. */
    fun ram(mb: Int): String {
        if (mb <= 0) return "—"
        return if (mb >= 1024) {
            val gb = mb.toDouble() / 1024.0
            String.format(Locale.US, "%.1f GB", gb)
        } else {
            "$mb MB"
        }
    }

    /** "02:14:33" / "00:42" / "—". Never negative, never throws. */
    fun runtime(seconds: Int): String {
        if (seconds <= 0) return "—"
        val s = seconds % 60
        val m = (seconds / 60) % 60
        val h = seconds / 3600
        return if (h > 0) {
            String.format(Locale.US, "%02d:%02d:%02d", h, m, s)
        } else {
            String.format(Locale.US, "%02d:%02d", m, s)
        }
    }

    fun shortPackage(pkg: String): String {
        // "com.litec.client" -> ".litec"
        val parts = pkg.split('.')
        return if (parts.size >= 2) ".${parts[parts.size - 2]}" else pkg
    }

    fun safeUsername(name: String?): String = name?.takeIf { it.isNotBlank() } ?: "Unknown"

    /**
     * Mask a username for the Hide Username privacy toggle, keeping the
     * first and last character: dengjiangbin -> d**********n, deng -> d**g.
     * UI-only — never used for identity/matching.
     */
    fun maskUsername(name: String?): String {
        val s = name?.trim().orEmpty()
        if (s.isEmpty()) return "Unknown"
        return when (s.length) {
            1 -> "${s}*"
            2 -> "${s[0]}*"
            else -> "${s[0]}${"*".repeat(s.length - 2)}${s[s.length - 1]}"
        }
    }

    /** Apply masking when [hide] is true, else the safe (non-empty) name. */
    fun displayUsername(name: String?, hide: Boolean): String =
        if (hide) maskUsername(name) else safeUsername(name)

    /**
     * Human-readable "time since" for the dashboard Last Update line:
     * "just now", "12s ago", "3m ago", "2h ago". Negative/None → "—".
     */
    fun relativeAgo(seconds: Long?): String {
        if (seconds == null || seconds < 0) return "—"
        return when {
            seconds < 3 -> "just now"
            seconds < 60 -> "${seconds}s ago"
            seconds < 3600 -> "${seconds / 60}m ago"
            seconds < 86_400 -> "${seconds / 3600}h ago"
            else -> "${seconds / 86_400}d ago"
        }
    }

    // ── Date/time ───────────────────────────────────────────────────────────
    //
    // Single user-facing timestamp format for the whole APK:
    //
    //     28 Mei 2026, 4:35 PM
    //
    // • Indonesian month names (no need for system locale to be Indonesian)
    // • Day number with no leading zero
    // • 12-hour clock with AM/PM (English casing for technical readability)
    // • Missing or unparseable timestamps render as "—".
    //
    // Implementation rules:
    // • Accepts any ISO-8601 server timestamp (with/without milliseconds, Z
    //   or ±HH:MM offset). Falls back gracefully on garbage input.
    // • Renders in the device's local time zone so the user sees their own
    //   "now" clock.
    private val ID_MONTHS = arrayOf(
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    )

    private fun parseInstant(iso: String): Instant? {
        return try {
            Instant.parse(iso)
        } catch (e: DateTimeParseException) {
            try {
                ZonedDateTime.parse(iso).toInstant()
            } catch (e2: DateTimeParseException) {
                null
            }
        } catch (e: Throwable) {
            null
        }
    }

    /**
     * Format an ISO-8601 timestamp as `28 Mei 2026, 4:35 PM`.
     * Returns `"—"` for null/blank/unparseable input.
     */
    fun timestamp(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String {
        val raw = iso?.trim().orEmpty()
        if (raw.isEmpty()) return "—"
        val instant = parseInstant(raw) ?: return "—"
        return formatInstant(instant, zone)
    }

    /** Same contract as [timestamp] but accepts an epoch-millis number. */
    fun timestamp(epochMillis: Long?, zone: ZoneId = ZoneId.systemDefault()): String {
        if (epochMillis == null || epochMillis <= 0L) return "—"
        return try {
            formatInstant(Instant.ofEpochMilli(epochMillis), zone)
        } catch (e: Throwable) {
            "—"
        }
    }

    private fun formatInstant(instant: Instant, zone: ZoneId): String {
        return try {
            val zdt = instant.atZone(zone)
            val day = zdt.dayOfMonth
            val monthName = ID_MONTHS[(zdt.monthValue - 1).coerceIn(0, 11)]
            val year = zdt.year
            var hour12 = zdt.hour % 12
            if (hour12 == 0) hour12 = 12
            val minute = zdt.minute
            val ampm = if (zdt.hour < 12) "AM" else "PM"
            String.format(
                Locale.US,
                "%d %s %d, %d:%02d %s",
                day, monthName, year, hour12, minute, ampm,
            )
        } catch (e: Throwable) {
            // Defensive: never crash a screen because of a date.
            try {
                Date.from(instant).toString()
            } catch (e2: Throwable) {
                "—"
            }
        }
    }
}
