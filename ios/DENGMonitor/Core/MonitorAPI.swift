import Foundation

enum APIError: LocalizedError {
    case missingToken
    case http(Int, String)
    case decode(String)
    case network(String)

    var errorDescription: String? {
        switch self {
        case .missingToken: return "Sign in required."
        case .http(let code, let msg): return msg.isEmpty ? "HTTP \(code)" : msg
        case .decode: return "Unexpected response from server. Please update the app or try again."
        case .network(let msg): return msg
        }
    }
}

@MainActor
final class MonitorAPI: ObservableObject {
    let baseURL: String
    private let tokenProvider: () -> String?
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    var host: String {
        URL(string: baseURL)?.host ?? baseURL
    }

    init(baseURL: String = AppConfig.baseURL, tokenProvider: @escaping () -> String?) {
        self.baseURL = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.tokenProvider = tokenProvider
    }

    func pair(code: String, deviceName: String?) async throws -> PairResponse {
        let body = PairRequest(code: code, deviceName: deviceName)
        return try await request(path: "/api/monitor/pairing/redeem", method: "POST", body: body, auth: false)
    }

    func listDevices() async throws -> DeviceListResponse {
        try await request(path: "/api/monitor/devices", auth: true)
    }

    func deviceStatus(deviceId: String) async throws -> DeviceStatus {
        try await request(path: "/api/monitor/devices/\(deviceId)/status", auth: true)
    }

    func snapshotData(deviceId: String) async throws -> Data? {
        var req = try buildRequest(path: "/api/monitor/devices/\(deviceId)/snapshot/latest", method: "GET", auth: true)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw APIError.network("Invalid response") }
        if http.statusCode == 204 { return nil }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(http.statusCode, parseErrorMessage(data: data, code: http.statusCode))
        }
        return data
    }

    func fishDaily(period: String) async throws -> FishDaily {
        try await request(path: "/api/fishit/me/daily?period=\(period)", auth: true)
    }

    func fishStats() async throws -> FishStats {
        try await request(path: "/api/fishit/me/stats", auth: true)
    }

    func fishGrid(search: String?, rarity: String?, sort: String, page: Int, limit: Int = 24) async throws -> FishGrid {
        var q = "/api/fishit/me/fish?sort=\(sort)&page=\(page)&limit=\(limit)"
        if let s = search?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty {
            q += "&search=\(s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s)"
        }
        if let r = rarity, !r.isEmpty { q += "&rarity=\(r)" }
        return try await request(path: q, auth: true)
    }

    func fishFriendlyMessage(_ error: Error) -> String {
        if let e = error as? APIError {
            switch e {
            case .http(401, _), .missingToken:
                return "Sign in with Discord to view your Fish It stats."
            case .http(_, let m): return m
            default: return e.localizedDescription
            }
        }
        return "Network error reaching \(host) — check your connection and try again."
    }

    private func request<T: Decodable>(
        path: String,
        method: String = "GET",
        body: Encodable? = nil,
        auth: Bool
    ) async throws -> T {
        let data = try await rawRequest(path: path, method: method, body: body, auth: auth)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decode("decode_failed")
        }
    }

    private func rawRequest(path: String, method: String, body: Encodable?, auth: Bool) async throws -> Data {
        var req = try buildRequest(path: path, method: method, auth: auth)
        if let body {
            req.httpBody = try encoder.encode(AnyEncodable(body))
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { throw APIError.network("Invalid response") }
            guard (200..<300).contains(http.statusCode) else {
                let msg = parseErrorMessage(data: data, code: http.statusCode)
                if http.statusCode == 401 { throw APIError.http(401, msg) }
                throw APIError.http(http.statusCode, msg)
            }
            let ct = http.value(forHTTPHeaderField: "Content-Type") ?? ""
            if ct.contains("text/html") {
                throw APIError.http(http.statusCode, "Server returned HTML instead of JSON.")
            }
            return data
        } catch let e as APIError {
            throw e
        } catch {
            throw APIError.network("Network error reaching \(host).")
        }
    }

    private func buildRequest(path: String, method: String, auth: Bool) throws -> URLRequest {
        guard let url = URL(string: baseURL + path) else { throw APIError.network("Invalid URL") }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if auth {
            guard let token = tokenProvider(), !token.isEmpty else { throw APIError.missingToken }
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return req
    }

    private func parseErrorMessage(data: Data, code: Int) -> String {
        if let body = try? decoder.decode(APIErrorBody.self, from: data) {
            return body.message ?? body.error ?? "http_\(code)"
        }
        return "http_\(code)"
    }
}

private struct AnyEncodable: Encodable {
    let wrapped: Encodable
    init(_ wrapped: Encodable) { self.wrapped = wrapped }
    func encode(to encoder: Encoder) throws { try wrapped.encode(to: encoder) }
}
