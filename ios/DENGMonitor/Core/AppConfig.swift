import Foundation

/// Production monitor API — must not be changed to localhost/staging in release.
enum AppConfig {
    static let baseURL = "https://tool.deng.my.id"
    static let websiteURL = "https://tool.deng.my.id"
    static let downloadURL = "https://tool.deng.my.id/download"
    static let appMarketingVersion = "1.0.0"
    static let appBuildNumber = "1"

    /// Fails CI/tests if a forbidden host slips into source.
    static func validateProductionURL() -> Bool {
        let forbidden = ["localhost", "127.0.0.1", "staging.example.com", "rejoin.deng.my.id"]
        let u = baseURL.lowercased()
        return u == "https://tool.deng.my.id" && !forbidden.contains(where: { u.contains($0) })
    }
}
