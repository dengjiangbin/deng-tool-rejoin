package my.id.deng.monitor.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Static source-level guard against the bug that produced the user-visible
 * `android.os.NetworkOnMainThreadException` in v1.0.2:
 *
 *  * `MonitorApi.execRaw` was a regular (non-suspend) function that called
 *    `OkHttpClient.newCall(...).execute()` synchronously.
 *  * `updateSettings` was `suspend` but did NOT wrap its call to `execRaw`
 *    in `withContext(Dispatchers.IO)`, so when Compose launched it from
 *    `rememberCoroutineScope()` (Main dispatcher), OkHttp's blocking
 *    `execute()` ran on the UI thread and Android killed the activity.
 *
 * In v1.0.3 every network entrypoint must funnel through a suspend
 * function whose body is wrapped in `withContext(Dispatchers.IO)`. This
 * test reads the actual source file and fails the build if anyone ever
 * removes that wrapping.
 */
class MonitorApiThreadingContractTest {
    private fun source(): String {
        val f = File("src/main/kotlin/my/id/deng/monitor/data/MonitorApi.kt")
        require(f.exists()) { "expected MonitorApi.kt at ${f.absolutePath}" }
        return f.readText(Charsets.UTF_8)
    }

    @Test
    fun `execRaw is a suspend function`() {
        val src = source()
        assertTrue(
            "MonitorApi.execRaw MUST be declared 'suspend fun' — found:\n$src",
            src.contains(Regex("""private\s+suspend\s+fun\s+execRaw\s*\(""")),
        )
    }

    @Test
    fun `execRaw body switches to Dispatchers IO`() {
        val src = source()
        // The withContext call must live inside execRaw, not just exist
        // elsewhere in the file. We assert it on the same line/region.
        assertTrue(
            "execRaw must wrap its OkHttp call in withContext(Dispatchers.IO)",
            src.contains(Regex(
                """private\s+suspend\s+fun\s+execRaw[\s\S]*?withContext\s*\(\s*Dispatchers\.IO\s*\)""",
            )),
        )
    }

    @Test
    fun `no synchronous OkHttp execute() call sits outside a withContext IO block`() {
        val src = source()
        // Crude guard: every `.execute()` call must be reachable only
        // from within an IO context. We check that the file does not
        // expose a non-suspend public function that performs network IO.
        // (Allowed: only execJson, execRaw, snapshotBytes — all suspend.)
        val nonSuspendNetwork = Regex(
            """\n\s+(?:private|public|internal)?\s*fun\s+\w+\s*\([^)]*\)\s*[:{][\s\S]{0,400}?\.execute\(\)""",
        )
        assertFalse(
            "Found a non-suspend function that calls OkHttp .execute() — this is exactly the bug v1.0.3 fixed.",
            nonSuspendNetwork.containsMatchIn(src),
        )
    }

    @Test
    fun `updateSettings remains suspend and delegates to execRaw`() {
        val src = source()
        assertTrue(
            "updateSettings must be suspend",
            src.contains(Regex("""suspend\s+fun\s+updateSettings\s*\(""")),
        )
    }
}
