# Keep kotlinx-serialization metadata + serializers
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
# Keep all data model classes annotated with @Serializable
-keep,includedescriptorclasses class my.id.deng.monitor.data.** { *; }

# Keep BuildConfig release marker and public URLs for support diagnostics
-keepclassmembers class my.id.deng.monitor.BuildConfig {
    public static final java.lang.String APK_RELEASE_MARKER;
    public static final java.lang.String PUBLIC_WEB_URL;
    public static final java.lang.String BRIDGE_URL;
}
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
