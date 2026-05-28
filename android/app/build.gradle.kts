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
        versionCode = 1
        versionName = "1.0.0"

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
    // If any are missing, the release build falls back to debug signing
    // so local development never breaks.
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
            signingConfig = if (hasReleaseSigning) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
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
