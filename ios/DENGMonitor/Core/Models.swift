import Foundation

struct APIErrorBody: Codable {
    let error: String?
    let message: String?
}

struct DeviceRam: Codable {
    let usedMb: Int
    let totalMb: Int
    let percent: Int?

    enum CodingKeys: String, CodingKey {
        case usedMb = "used_mb"
        case totalMb = "total_mb"
        case percent
    }

    var effectivePercent: Int? {
        if let p = percent { return p }
        guard totalMb > 0 else { return nil }
        return Int((Int64(usedMb) * 100) / Int64(totalMb))
    }

    var displayText: String {
        if totalMb > 0 {
            return "\(usedMb)MB/\(totalMb)MB \(effectivePercent ?? 0)%"
        }
        if let p = effectivePercent { return "\(p)%" }
        return "—"
    }
}

struct DashboardPackageSummary: Codable {
    let total: Int
    let online: Int
    let dead: Int
    let launching: Int?
    let joining: Int?
    let noHeartbeat: Int?
    let totalRamMb: Int?

    enum CodingKeys: String, CodingKey {
        case total, online, dead, launching, joining
        case noHeartbeat = "no_heartbeat"
        case totalRamMb = "total_ram_mb"
    }
}

struct DeviceSummary: Codable, Identifiable {
    let id: String
    let deviceLabel: String?
    let connected: Bool?
    let connectionState: String?
    let secondsSinceLastSeen: Int?
    let lastSeenAt: String?
    let deviceRam: DeviceRam?
    let monitorIntervalSeconds: Int?
    let connectionTtlSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case deviceLabel = "device_label"
        case connected
        case connectionState = "connection_state"
        case secondsSinceLastSeen = "seconds_since_last_seen"
        case lastSeenAt = "last_seen_at"
        case deviceRam = "device_ram"
        case monitorIntervalSeconds = "monitor_interval_seconds"
        case connectionTtlSeconds = "connection_ttl_seconds"
    }

    var isConnected: Bool {
        connected ?? (connectionState == "Connected")
    }

    var displayName: String {
        let l = deviceLabel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return l.isEmpty ? "Cloud Phone" : l
    }
}

struct DeviceListResponse: Codable {
    let devices: [DeviceSummary]
    let packageSummary: DashboardPackageSummary

    enum CodingKeys: String, CodingKey {
        case devices
        case packageSummary = "package_summary"
    }
}

struct PackageState: Codable, Identifiable {
    var id: String { packageName }
    let packageName: String
    let displayName: String?
    let username: String?
    let state: String
    let ramMb: Int
    let runtimeSeconds: Int

    enum CodingKeys: String, CodingKey {
        case packageName = "package_name"
        case displayName = "display_name"
        case username, state
        case ramMb = "ram_mb"
        case runtimeSeconds = "runtime_seconds"
    }
}

struct PackageSummary: Codable {
    let total: Int
    let online: Int
    let dead: Int
}

struct MonitorSettings: Codable {
    var snapshotIntervalSeconds: Int
    var monitorEnabled: Bool
    var appRefreshIntervalSeconds: Int

    enum CodingKeys: String, CodingKey {
        case snapshotIntervalSeconds = "snapshot_interval_seconds"
        case monitorEnabled = "monitor_enabled"
        case appRefreshIntervalSeconds = "app_refresh_interval_seconds"
    }
}

struct DeviceStatus: Codable {
    let device: DeviceSummary
    let summary: PackageSummary
    let packages: [PackageState]
    let settings: MonitorSettings
}

struct PairRequest: Encodable {
    let code: String
    let deviceName: String?

    enum CodingKeys: String, CodingKey {
        case code
        case deviceName = "device_name"
    }
}

struct OwnerInfo: Codable {
    let discordUserId: String
    enum CodingKeys: String, CodingKey { case discordUserId = "discord_user_id" }
}

struct PairResponse: Codable {
    let appSessionToken: String
    let expiresAt: String
    let owner: OwnerInfo

    enum CodingKeys: String, CodingKey {
        case appSessionToken = "app_session_token"
        case expiresAt = "expires_at"
        case owner
    }
}

// MARK: - Fish It

struct FishRank: Codable {
    let rank: Int
    let of: Int
}

struct FishStatCard: Codable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let amount: Int
    let count: Int
    let imageUrl: String?
    let fallbackUrl: String?

    var displayAmount: Int { amount != 0 ? amount : count }
}

struct FishStats: Codable {
    let ok: Bool?
    let hasData: Bool
    let username: String?
    let totalFish: Int
    let rank: FishRank?
    let summaryCards: [FishStatCard]
    let rarityCards: [FishStatCard]
    let rodCards: [FishStatCard]
}

struct FishDailySummary: Codable {
    let totalFish: Int
    let secretFish: Int
    let forgottenFish: Int
}

struct FishDailyCard: Codable, Identifiable {
    var id: String { speciesKey }
    let speciesKey: String
    let name: String
    let rarity: String
    let count: Int
    let imageUrl: String?
    let maxWeight: String?
    let fallbackUrl: String?
}

struct FishDaily: Codable {
    let ok: Bool?
    let hasData: Bool
    let period: String
    let periodLabel: String
    let summary: FishDailySummary
    let cards: [FishDailyCard]
    let emptyMessage: String?
}

struct FishCard: Codable, Identifiable {
    var id: String { speciesKey }
    let speciesKey: String
    let name: String
    let rarity: String
    let count: Int
    let imageUrl: String?
    let maxWeight: String?
    let mutation: String?
    let fallbackUrl: String?
}

struct FishGrid: Codable {
    let ok: Bool?
    let hasData: Bool
    let items: [FishCard]
    let page: Int
    let pages: Int
}
