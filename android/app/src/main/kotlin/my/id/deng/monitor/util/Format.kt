package my.id.deng.monitor.util

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
            String.format("%.1f GB", gb)
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
            String.format("%02d:%02d:%02d", h, m, s)
        } else {
            String.format("%02d:%02d", m, s)
        }
    }

    fun shortPackage(pkg: String): String {
        // "com.litec.client" -> ".litec"
        val parts = pkg.split('.')
        return if (parts.size >= 2) ".${parts[parts.size - 2]}" else pkg
    }

    fun safeUsername(name: String?): String = name?.takeIf { it.isNotBlank() } ?: "—"
}
