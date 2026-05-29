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
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

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
    /**
     * Bare host of [baseUrl] (e.g. "tool.deng.my.id") for display in the
     * Settings/About card and in user-facing network-error messages. Falls
     * back to the raw baseUrl if it can't be parsed — never throws.
     */
    val host: String = runCatching {
        baseUrl.substringAfter("://").substringBefore('/').ifBlank { baseUrl }
    }.getOrDefault(baseUrl)

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

    // ── Fish It (authenticated by the same app session token) ───────────────
    suspend fun fishProfile(): FishProfile =
        execJson("/api/fishit/me", auth = true)

    suspend fun fishDaily(period: String): FishDaily =
        execJson("/api/fishit/me/daily?period=${period}", auth = true)

    suspend fun fishStats(): FishStats =
        execJson("/api/fishit/me/stats", auth = true)

    suspend fun fishGrid(
        search: String? = null,
        rarity: String? = null,
        sort: String = "amount",
        page: Int = 1,
        limit: Int = 24,
    ): FishGrid {
        val sb = StringBuilder("/api/fishit/me/fish?sort=").append(sort)
            .append("&page=").append(page).append("&limit=").append(limit)
        if (!search.isNullOrBlank()) sb.append("&search=").append(urlEncode(search))
        if (!rarity.isNullOrBlank()) sb.append("&rarity=").append(rarity)
        return execJson(sb.toString(), auth = true)
    }

    private fun urlEncode(s: String): String =
        runCatching { java.net.URLEncoder.encode(s, "UTF-8") }.getOrDefault(s)

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
        return try {
            json.decodeFromString<T>(raw)
        } catch (e: kotlinx.serialization.SerializationException) {
            // The HTTP call succeeded but the body didn't match our model.
            // Surface a specific, non-"can't reach backend" error so the user
            // sees Retry + an honest reason instead of a fake network failure.
            throw ApiException(0, "Unexpected response from server. Please update the app or try again.")
        }
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

/**
 * Maps a low-level network/HTTP throwable to a short, safe, human-readable
 * message that names the backend [host] — never a raw stack trace or an
 * opaque class name like "UnknownHostException".
 *
 * This is what turns the v1.0.4 "Network error: UnknownHostException"
 * (which left users with no idea what to do) into actionable copy such as
 * "Cannot reach tool.deng.my.id — check your internet/DNS and try again."
 */
/** Fish It + monitor API errors with auth-specific copy (not generic network). */
fun fishFriendlyError(e: Throwable, host: String): String = when (e) {
    is ApiException -> when (e.statusCode) {
        401 -> "Sign in with Discord to view your Fish It stats."
        else -> e.safeMessage
    }
    else -> friendlyNetworkError(e, host)
}

fun friendlyNetworkError(e: Throwable, host: String): String = when (e) {
    is ApiException -> e.safeMessage
    is UnknownHostException ->
        "Cannot reach $host — check your internet/DNS connection and try again."
    is SocketTimeoutException ->
        "Connection to $host timed out — check your network and try again."
    is SSLException ->
        "Secure connection to $host failed — check your network and try again."
    is IOException ->
        "Network error reaching $host — check your connection and try again."
    else ->
        "Network error reaching $host — check your connection and try again."
}
