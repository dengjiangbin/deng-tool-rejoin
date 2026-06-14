package my.id.deng.monitor.ui

fun resolveAssetUrl(baseUrl: String, imageUrl: String?): String? {
    val raw = imageUrl?.trim().orEmpty()
    if (raw.isBlank()) return null
    if (raw.startsWith("http://") || raw.startsWith("https://")) return raw
    val base = baseUrl.trimEnd('/')
    return if (raw.startsWith("/")) "$base$raw" else "$base/$raw"
}
