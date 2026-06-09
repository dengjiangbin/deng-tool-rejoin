plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.compose.compiler)
}

android {
    namespace = "my.id.deng.monitor"
    compileSdk = 34

    defaultConfig {
        applicationId = "my.id.deng.monitor"
        minSdk = 26
        targetSdk = 34
        // APK app versioning ONLY. Independent of any Rejoin
        // Termux / package version. Bump versionCode whenever the
        // APK is rebuilt and republished, even for branding-only
        // changes, so Android sees it as a real upgrade.
        versionCode = 12
        versionName = "1.0.11"

        // Default backend URL. Can be overridden at build time:
        //   ./gradlew assembleRelease -PbridgeUrl=https://staging.example.com
        val bridgeUrl = (project.findProperty("bridgeUrl") as String?)
            ?: "https://tool.deng.my.id"
        buildConfigField("String", "BRIDGE_URL", "\"$bridgeUrl\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Release signing pulls credentials from machine-local Gradle properties
    // (typically ~/.gradle/gradle.properties or -P CLI flags). Nothing
    // sensitive is committed; the repo only sees property *names*.
    //
    // Required properties for a real signed release:
    //   DENG_KEYSTORE_PATH      absolute path to the .jks keystore
    //   DENG_KEYSTORE_PASSWORD  keystore password
    //   DENG_KEY_ALIAS          key alias inside the keystore
    //   DENG_KEY_PASSWORD       key password
    //
    // SAFETY: A release build NEVER silently falls back to debug signing.
    // If any credential is missing when a release-producing task is
    // requested, the build fails loudly (see `gradle.taskGraph.whenReady`
    // block below). Debug builds (`assembleDebug`, `test`) are unaffected
    // and run without release credentials.
    val keystorePath = project.findProperty("DENG_KEYSTORE_PATH") as String?
    val keystorePassword = project.findProperty("DENG_KEYSTORE_PASSWORD") as String?
    val keyAlias = project.findProperty("DENG_KEY_ALIAS") as String?
    val keyPassword = project.findProperty("DENG_KEY_PASSWORD") as String?
    val hasReleaseSigning = !keystorePath.isNullOrBlank() &&
        !keystorePassword.isNullOrBlank() &&
        !keyAlias.isNullOrBlank() &&
        !keyPassword.isNullOrBlank() &&
        file(keystorePath!!).exists()

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(keystorePath!!)
                storePassword = keystorePassword
                this.keyAlias = keyAlias
                this.keyPassword = keyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            // Only wire the real release keystore when fully configured.
            // When credentials are missing we deliberately leave
            // signingConfig = null so debug keys can never sneak into a
            // public APK. The whenReady guard below converts that into
            // a clear, human-readable failure before AGP gets a chance
            // to produce a misleading error.
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            applicationIdSuffix = ".debug"
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)

    val composeBom = platform(libs.androidx.compose.bom)
    implementation(composeBom)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    debugImplementation(libs.androidx.compose.ui.tooling)

    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.coil.compose)

    testImplementation(libs.junit)
}

// ---------------------------------------------------------------------------
// Release signing guard
// ---------------------------------------------------------------------------
// Public release builds must NEVER silently fall back to debug signing.
// Debug builds (`assembleDebug`, `test`) intentionally remain free of this
// check so day-to-day development does not require a keystore.
//
// This guard fires after the task graph is built but before any task runs.
// If the user requested a task that would produce a public release artifact
// AND any of the required signing properties are missing, the build halts
// with an actionable, human-readable error.
val releaseProducingTaskNames = setOf(
    "assembleRelease",
    "bundleRelease",
    "packageRelease",
    "publishReleaseApk",
    "uploadArchives",
)

gradle.taskGraph.whenReady {
    val triggersReleaseSigning = allTasks.any { task ->
        // Match both bare names (`assembleRelease`) and fully-qualified
        // names (`:app:assembleRelease`) so the guard works from anywhere
        // in a multi-project build.
        releaseProducingTaskNames.contains(task.name) ||
            task.path.endsWith(":assembleRelease") ||
            task.path.endsWith(":bundleRelease") ||
            task.path.endsWith(":packageRelease")
    }
    if (!triggersReleaseSigning) return@whenReady

    val required = linkedMapOf(
        "DENG_KEYSTORE_PATH" to (project.findProperty("DENG_KEYSTORE_PATH") as String?),
        "DENG_KEYSTORE_PASSWORD" to (project.findProperty("DENG_KEYSTORE_PASSWORD") as String?),
        "DENG_KEY_ALIAS" to (project.findProperty("DENG_KEY_ALIAS") as String?),
        "DENG_KEY_PASSWORD" to (project.findProperty("DENG_KEY_PASSWORD") as String?),
    )
    val missing = required.filterValues { it.isNullOrBlank() }.keys.toList()

    val keystorePathValue = required["DENG_KEYSTORE_PATH"]
    val keystoreMissingOnDisk = missing.isEmpty() &&
        !keystorePathValue.isNullOrBlank() &&
        !file(keystorePathValue).exists()

    if (missing.isNotEmpty() || keystoreMissingOnDisk) {
        val bullets = buildString {
            required.keys.forEach { name ->
                val state = if (missing.contains(name)) "MISSING" else "ok"
                append("  * ").append(name).append("  (").append(state).append(")\n")
            }
            if (keystoreMissingOnDisk) {
                append("\nKeystore file does not exist at: ")
                append(keystorePathValue)
                append('\n')
            }
        }
        throw GradleException(
            """

            Missing DENG Tool: Rejoin APK release signing config.

            A release-producing task was requested (assembleRelease /
            bundleRelease / packageRelease) but the release keystore
            credentials are not available.

            Required Gradle properties:
            $bullets
            Set them in one of the following places (in order of preference):

              1. ~/.gradle/gradle.properties (recommended; machine-local)
              2. Environment variables ORG_GRADLE_PROJECT_DENG_KEYSTORE_PATH
                 (etc.), which Gradle maps to project properties.
              3. -P CLI flags on the gradlew invocation.

            Never commit keystore files, .jks, or signing passwords.
            Release builds will NOT fall back to debug signing — debug
            keys must never end up in a public APK.

            """.trimIndent()
        )
    }
}
