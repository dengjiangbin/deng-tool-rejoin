pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

// User-facing product name is "DENG Tool: Rejoin APK". Gradle does not allow
// ':' or spaces in rootProject.name, so we use a slug here. The actual app
// label seen on the device comes from res/values/strings.xml (app_name +
// app_launcher_label).
rootProject.name = "deng-tool-rejoin-apk"
include(":app")
