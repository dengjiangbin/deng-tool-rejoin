package my.id.deng.monitor.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Apps
import androidx.compose.material.icons.outlined.Backpack
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Waves
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import my.id.deng.monitor.data.AppPreferences
import my.id.deng.monitor.data.MonitorApi
import my.id.deng.monitor.data.SessionStore

private data class NavItem(
    val route: String,
    val label: String,
    val icon: @Composable () -> Unit,
)

private val NAV_ITEMS = listOf(
    NavItem("dashboard",  "Dashboard")  { Icon(Icons.Outlined.Dashboard, contentDescription = null) },
    NavItem("fishit",     "Stats")      { Icon(Icons.Outlined.Waves, contentDescription = null) },
    NavItem("packages",   "Packages")    { Icon(Icons.Outlined.Apps, contentDescription = null) },
    NavItem("inventory",  "Inventory")  { Icon(Icons.Outlined.Backpack, contentDescription = null) },
    NavItem("settings",   "Settings")   { Icon(Icons.Outlined.Settings, contentDescription = null) },
)

@Composable
fun AppRoot(
    api: MonitorApi,
    sessionStore: SessionStore,
    appPreferences: AppPreferences,
    isPaired: Boolean,
) {
    if (!isPaired) {
        PairScreen(api = api, sessionStore = sessionStore)
        return
    }

    val nav = rememberNavController()
    val backStack by nav.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route ?: "dashboard"

    Scaffold(
        containerColor = Color.Transparent,
        bottomBar = {
            NavigationBar(
                containerColor = my.id.deng.monitor.ui.theme.DengColors.NavBar,
                tonalElevation = 0.dp,
            ) {
                NAV_ITEMS.forEach { item ->
                    NavigationBarItem(
                        selected = currentRoute == item.route,
                        onClick = {
                            if (currentRoute != item.route) {
                                nav.navigate(item.route) {
                                    popUpTo(nav.graph.startDestinationId) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        },
                        icon = item.icon,
                        label = { Text(item.label) },
                    )
                }
            }
        },
    ) { inner ->
        NavHost(
            navController = nav,
            startDestination = "dashboard",
            modifier = Modifier.fillMaxSize().padding(inner),
        ) {
            composable("dashboard") { DashboardScreen(api = api, sessionStore = sessionStore) }
            composable("fishit")    { FishItScreen(api = api) }
            composable("packages")  { PackagesScreen(api = api, sessionStore = sessionStore) }
            composable("inventory") { InventoryScreen(api = api) }
            composable("settings")  { SettingsScreen(api = api, sessionStore = sessionStore, appPreferences = appPreferences) }
        }
    }
}

