import XCTest
@testable import DENGMonitor

final class AppConfigTests: XCTestCase {
    func testProductionBaseURL() {
        XCTAssertEqual(AppConfig.baseURL, "https://tool.deng.my.id")
        XCTAssertTrue(AppConfig.validateProductionURL())
    }

    func testSourceDoesNotContainForbiddenHosts() {
        let forbidden = ["localhost", "127.0.0.1", "staging.example.com"]
        XCTAssertFalse(forbidden.contains(where: { AppConfig.baseURL.contains($0) }))
    }
}

final class NumberFormatTests: XCTestCase {
    func test2095RendersExact() {
        XCTAssertEqual(NumberFormat.exact(2095), "2,095")
        XCTAssertFalse(NumberFormat.exact(2095).contains("K"))
    }

    func test54203RendersExact() {
        XCTAssertEqual(NumberFormat.exact(54203), "54,203")
    }
}

final class UsernameMaskTests: XCTestCase {
    func testMaskLongName() {
        XCTAssertEqual(UsernameMask.mask("dengjiangbin"), "d**********n")
    }

    func testMaskShortName() {
        XCTAssertEqual(UsernameMask.mask("deng"), "d**g")
    }
}

final class ModelsDecodeTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    func testDashboardPackageSummary() throws {
        let json = """
        {"total":8,"online":0,"dead":8,"launching":0,"joining":0,"no_heartbeat":0,"total_ram_mb":1200}
        """.data(using: .utf8)!
        let s = try decoder.decode(DashboardPackageSummary.self, from: json)
        XCTAssertEqual(s.total, 8)
        XCTAssertEqual(s.dead, 8)
    }

    func testFishDailyPreservesImageUrl() throws {
        let json = """
        {"ok":true,"hasData":true,"period":"today","periodLabel":"Today","summary":{"totalFish":3,"secretFish":2,"forgottenFish":1},"cards":[{"speciesKey":"king-jelly","name":"King Jelly","rarity":"Secret","count":2,"imageUrl":"https://tr.rbxcdn.com/fish.png"}]}
        """.data(using: .utf8)!
        let d = JSONDecoder()
        let daily = try d.decode(FishDaily.self, from: json)
        XCTAssertEqual(daily.cards.first?.imageUrl, "https://tr.rbxcdn.com/fish.png")
    }

    func testFishStatsRodImageUrl() throws {
        let json = """
        {"ok":true,"hasData":true,"totalFish":100,"rodCards":[{"key":"ghostfinn","label":"Ghostfinn Rod","amount":5,"count":5,"imageUrl":"https://cdn.discordapp.com/emojis/1.webp"}]}
        """.data(using: .utf8)!
        let s = try JSONDecoder().decode(FishStats.self, from: json)
        XCTAssertTrue(s.rodCards.first?.imageUrl?.contains("discordapp.com") == true)
    }
}
