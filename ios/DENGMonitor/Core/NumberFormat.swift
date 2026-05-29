import Foundation

enum NumberFormat {
    /// Exact integer with thousands separators — never K/M/B.
    static func exact(_ value: Int) -> String {
        exact(Int64(value))
    }

    static func exact(_ value: Int64) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.groupingSeparator = ","
        f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: max(0, value))) ?? "0"
    }
}
