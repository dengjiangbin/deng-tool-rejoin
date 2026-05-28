package my.id.deng.monitor.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

class FormatTest {
    @Test fun `ram below 1024 MB formats as integer MB`() {
        assertEquals("642 MB", Format.ram(642))
        assertEquals("1023 MB", Format.ram(1023))
    }

    @Test fun `ram at or above 1024 MB formats as GB`() {
        assertEquals("1.0 GB", Format.ram(1024))
        assertEquals("5.0 GB", Format.ram(5120))
    }

    @Test fun `ram never throws on edge inputs`() {
        assertEquals("—", Format.ram(0))
        assertEquals("—", Format.ram(-99))
    }

    @Test fun `runtime under one hour formats MM SS`() {
        assertEquals("00:42", Format.runtime(42))
        assertEquals("10:00", Format.runtime(600))
        assertEquals("59:59", Format.runtime(3599))
    }

    @Test fun `runtime over one hour formats HH MM SS`() {
        assertEquals("02:14:33", Format.runtime(2 * 3600 + 14 * 60 + 33))
        assertEquals("99:00:00", Format.runtime(99 * 3600))
    }

    @Test fun `runtime never throws on edge inputs`() {
        assertEquals("—", Format.runtime(0))
        assertEquals("—", Format.runtime(-1))
    }

    @Test fun `shortPackage prefixes a dot to the second-to-last segment`() {
        assertEquals(".litec", Format.shortPackage("com.litec.client"))
        assertEquals(".moons", Format.shortPackage("my.moons.helper"))
    }

    @Test fun `shortPackage falls back to original on too-short input`() {
        assertEquals("foo", Format.shortPackage("foo"))
    }

    @Test fun `safeUsername coerces null and blank to Unknown`() {
        // Was em-dash in v1.0.1; the public APK now uses the explicit
        // word "Unknown" because it is the user-facing main line on each
        // package row (much better signal than a tiny dash).
        assertEquals("Unknown", Format.safeUsername(null))
        assertEquals("Unknown", Format.safeUsername(""))
        assertEquals("Unknown", Format.safeUsername("   "))
        assertEquals("deng1629", Format.safeUsername("deng1629"))
    }

    // ── Timestamp formatter (v1.0.2 hardcoded Indonesian, 12-h AM/PM) ───
    private val JKT = ZoneId.of("Asia/Jakarta")  // UTC+7 -> predictable test fixture

    @Test fun `timestamp formats Indonesian month with 12 hour AMPM`() {
        // 2026-05-28T09:35:00Z = 16:35 in Asia/Jakarta -> "28 Mei 2026, 4:35 PM"
        assertEquals(
            "28 Mei 2026, 4:35 PM",
            Format.timestamp("2026-05-28T09:35:00Z", JKT),
        )
    }

    @Test fun `timestamp morning case uses AM`() {
        // 2026-01-01T00:05:00Z = 07:05 in Asia/Jakarta -> "1 Januari 2026, 7:05 AM"
        assertEquals(
            "1 Januari 2026, 7:05 AM",
            Format.timestamp("2026-01-01T00:05:00Z", JKT),
        )
    }

    @Test fun `timestamp midnight noon edge cases use 12 not 0`() {
        // 17:00 UTC = 00:00 next day in Jakarta -> "1 Januari 2026, 12:00 AM"
        assertEquals(
            "1 Januari 2026, 12:00 AM",
            Format.timestamp("2025-12-31T17:00:00Z", JKT),
        )
        // 05:00 UTC = 12:00 in Jakarta -> "1 Januari 2026, 12:00 PM"
        assertEquals(
            "1 Januari 2026, 12:00 PM",
            Format.timestamp("2026-01-01T05:00:00Z", JKT),
        )
    }

    @Test fun `timestamp uses all Indonesian month names`() {
        val expectedMonths = listOf(
            "Januari", "Februari", "Maret", "April", "Mei", "Juni",
            "Juli", "Agustus", "September", "Oktober", "November", "Desember",
        )
        for (m in 1..12) {
            val iso = "2026-${"%02d".format(m)}-15T05:00:00Z"
            val formatted = Format.timestamp(iso, JKT)
            assertTrue(
                "month $m formatted='$formatted' should contain '${expectedMonths[m - 1]}'",
                formatted.contains(expectedMonths[m - 1]),
            )
        }
    }

    @Test fun `timestamp day has no leading zero`() {
        // 2026-05-03T05:00:00Z = 12:00 in Jakarta
        val out = Format.timestamp("2026-05-03T05:00:00Z", JKT)
        assertEquals("3 Mei 2026, 12:00 PM", out)
        assertTrue("day must not be zero-padded: $out", !out.startsWith("03"))
    }

    @Test fun `timestamp missing or unparseable values render as em-dash`() {
        assertEquals("—", Format.timestamp(null as String?))
        assertEquals("—", Format.timestamp(""))
        assertEquals("—", Format.timestamp("   "))
        assertEquals("—", Format.timestamp("not-an-iso-date"))
        assertEquals("—", Format.timestamp(0L as Long?))
        assertEquals("—", Format.timestamp(null as Long?))
        assertEquals("—", Format.timestamp(-1L as Long?))
    }

    @Test fun `timestamp accepts ISO with milliseconds and offset`() {
        // 2026-05-28T09:35:00.123Z and 2026-05-28T16:35:00+07:00 — both 4:35 PM Jakarta
        assertEquals(
            "28 Mei 2026, 4:35 PM",
            Format.timestamp("2026-05-28T09:35:00.123Z", JKT),
        )
        assertEquals(
            "28 Mei 2026, 4:35 PM",
            Format.timestamp("2026-05-28T16:35:00+07:00", JKT),
        )
    }

    @Test fun `timestamp epoch millis matches ISO version`() {
        // 2026-05-28T09:35:00Z = 1779960900000 ms (verified with Python).
        val ms = 1779960900000L
        assertEquals(
            "28 Mei 2026, 4:35 PM",
            Format.timestamp(ms, JKT),
        )
    }

    @Test fun `timestamp output never contains raw ISO punctuation`() {
        val out = Format.timestamp("2026-05-28T09:35:00Z", JKT)
        // Spec: no T, no leading 2026-..., no Z marker, no "pukul" Indonesian.
        assertTrue("must not contain 'T': $out", !out.contains("T"))
        assertTrue("must not contain '-' between digits: $out", !out.contains("2026-"))
        assertTrue("must not contain 'Z': $out", !out.contains("Z"))
        assertTrue("must not contain 'pukul': $out", !out.contains("pukul"))
    }
}
