package my.id.deng.monitor.util

import org.junit.Assert.assertEquals
import org.junit.Test

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

    @Test fun `safeUsername coerces null and blank to em-dash`() {
        assertEquals("—", Format.safeUsername(null))
        assertEquals("—", Format.safeUsername(""))
        assertEquals("—", Format.safeUsername("   "))
        assertEquals("deng1629", Format.safeUsername("deng1629"))
    }
}
