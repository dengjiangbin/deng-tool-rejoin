package my.id.deng.monitor.ui

import java.net.URI

/** APK OAuth must never run inside WebView — open these in Custom Tabs / system browser. */
private val OAUTH_SITE_HOSTS = setOf(
    "aio.deng.my.id",
    "tool.deng.my.id",
)

fun apkOAuthStartUrl(publicWebUrl: String): String {
    val base = publicWebUrl.trimEnd('/')
    return "$base/auth/discord?client=apk&apk=1&public_return=1&return=${encodeURIComponent("/tracker")}"
}

private fun encodeURIComponent(value: String): String =
    java.net.URLEncoder.encode(value, Charsets.UTF_8.name()).replace("+", "%20")

fun isExternalOAuthUrl(url: String, publicWebHost: String): Boolean {
    val uri = runCatching { URI(url) }.getOrNull() ?: return false
    val host = uri.host?.lowercase().orEmpty()
    val path = uri.path?.lowercase().orEmpty()

    if (host == "discord.com" || host == "discordapp.com") {
        if (path.contains("oauth2") || path.contains("/login") || path.contains("/authorize")) {
            return true
        }
    }

    val hosts = OAUTH_SITE_HOSTS + publicWebHost.lowercase()
    if (host.isNotEmpty() && host !in hosts) return false

    return path.startsWith("/auth/discord")
        || path.startsWith("/api/aio/auth/callback")
        || path.startsWith("/api/aio/auth/start")
}
