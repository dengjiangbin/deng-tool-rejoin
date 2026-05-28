package my.id.deng.monitor.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Thin OkHttp + kotlinx.serialization wrapper around the DENG Monitor
 * backend. All methods are suspend and run on Dispatchers.IO. Errors
 * surface as [ApiException].
 *
 * Auth: requests that need an app session token automatically attach a
 * Bearer header pulled from [tokenProvider]. Pairing endpoints don't.
 */
class MonitorApi(
    val baseUrl: String,
    private val tokenProvider: () -> String?,
) {
    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
    }

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    // ── Pairing ────────────────────────────────────────────────────────────
    suspend fun pair(code: String, deviceName: String?): PairResponse {
        val body = json.encodeToString(PairRequest(code = code, deviceName = deviceName))
        return execJson("/api/monitor/pairing/redeem", method = "POST", body = body, auth = false)
    }

    // ── App-authenticated reads ────────────────────────────────────────────
    suspend fun listDevices(): DeviceListResponse =
        execJson("/api/monitor/devices", auth = true)

    suspend fun deviceStatus(deviceId: String): DeviceStatus =
        execJson("/api/monitor/devices/$deviceId/status", auth = true)

    suspend fun snapshotBytes(deviceId: String): ByteArray? = withContext(Dispatchers.IO) {
        val req = newRequest("/api/monitor/devices/$deviceId/snapshot/latest", auth = true).build()
        client.newCall(req).execute().use { resp ->
            if (resp.code == 204) return@withContext null
            if (!resp.isSuccessful) throw ApiException(resp.code, "snapshot_fetch_failed")
            resp.body?.bytes()
        }
    }

    // ── Settings update ────────────────────────────────────────────────────
    suspend fun updateSettings(deviceId: String, settings: MonitorSettings) {
        val body = json.encodeToString(settings)
        execRaw("/api/monitor/devices/$deviceId/settings", method = "PATCH", body = body, auth = true)
    }

    // ── Internal helpers ───────────────────────────────────────────────────
    // v1.0.3 — every code path (execRaw, execJson, snapshotBytes) goes
    // through `withContext(Dispatchers.IO)`. Previously `execRaw` was a
    // plain function and `updateSettings` (which is suspend but does NOT
    // wrap itself) called it directly — so when a Compose
    // `rememberCoroutineScope().launch {}` invoked it, OkHttp's blocking
    // `execute()` ran on the Android Main thread and threw
    // `NetworkOnMainThreadException` the moment the user tapped a
    // snapshot-interval radio button. Forcing IO at the lowest layer
    // means no caller can ever accidentally run a sync HTTP call on
    // the UI thread again.
    private suspend inline fun <reified T> execJson(
        path: String,
        method: String = "GET",
        body: String? = null,
        auth: Boolean,
    ): T {
        val raw = execRaw(path, method, body, auth)
        return json.decodeFromString<T>(raw)
    }

    private suspend fun execRaw(
        path: String,
        method: String = "GET",
        body: String? = null,
        auth: Boolean,
    ): String = withContext(Dispatchers.IO) {
        val builder = newRequest(path, auth)
        when (method.uppercase()) {
            "GET"    -> builder.get()
            "POST"   -> builder.post((body ?: "{}").toRequestBody(JSON))
            "PATCH"  -> builder.patch((body ?: "{}").toRequestBody(JSON))
            "DELETE" -> builder.delete((body ?: "").toRequestBody(JSON))
            else     -> throw IllegalArgumentException("Unsupported method: $method")
        }
        client.newCall(builder.build()).execute().use { resp ->
            val payload = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val parsedMessage = try {
                    json.decodeFromString<ErrorResponse>(payload).let {
                        it.message ?: it.error ?: "http_${resp.code}"
                    }
                } catch (_: Throwable) {
                    "http_${resp.code}"
                }
                throw ApiException(resp.code, parsedMessage)
            }
            payload
        }
    }

    private fun newRequest(path: String, auth: Boolean): Request.Builder {
        val url = baseUrl.trimEnd('/') + path
        val b = Request.Builder().url(url).header("Accept", "application/json")
        if (auth) {
            val token = tokenProvider()
                ?: throw ApiException(401, "missing_app_token")
            b.header("Authorization", "Bearer $token")
        }
        return b
    }

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }
}

class ApiException(
    val statusCode: Int,
    val safeMessage: String,
) : IOException("$statusCode: $safeMessage")
