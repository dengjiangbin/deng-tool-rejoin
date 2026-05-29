import Foundation
import Security

/// Secure storage for the app session bearer token (never in UserDefaults).
enum KeychainStore {
    private static let service = "my.id.deng.monitor.session"
    private static let tokenKey = "app_session_token"
    private static let ownerKey = "owner_discord_user_id"
    private static let deviceKey = "last_device_id"

    static func saveToken(_ token: String) {
        save(token, account: tokenKey)
    }

    static func loadToken() -> String? {
        load(account: tokenKey)
    }

    static func saveOwnerId(_ id: String) {
        save(id, account: ownerKey)
    }

    static func loadOwnerId() -> String? {
        load(account: ownerKey)
    }

    static func saveLastDeviceId(_ id: String) {
        save(id, account: deviceKey)
    }

    static func loadLastDeviceId() -> String? {
        load(account: deviceKey)
    }

    static func clearAll() {
        delete(account: tokenKey)
        delete(account: ownerKey)
        delete(account: deviceKey)
    }

    private static func save(_ value: String, account: String) {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    private static func load(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
