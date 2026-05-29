import Foundation

enum UsernameMask {
    static func mask(_ name: String?) -> String {
        let s = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return "Unknown" }
        switch s.count {
        case 1: return "\(s)*"
        case 2: return "\(s.first!)*"
        default:
            let mid = String(repeating: "*", count: s.count - 2)
            return "\(s.first!)\(mid)\(s.last!)"
        }
    }

    static func display(_ name: String?, hide: Bool) -> String {
        hide ? mask(name) : ((name?.isEmpty == false) ? name! : "Unknown")
    }
}
