import Foundation

@MainActor
final class SessionManager: ObservableObject {
    @Published private(set) var isPaired: Bool

    init() {
        isPaired = KeychainStore.loadToken() != nil
    }

    func token() -> String? {
        KeychainStore.loadToken()
    }

    func savePairing(_ response: PairResponse) {
        KeychainStore.saveToken(response.appSessionToken)
        KeychainStore.saveOwnerId(response.owner.discordUserId)
        isPaired = true
    }

    func signOut() {
        KeychainStore.clearAll()
        isPaired = false
    }
}
