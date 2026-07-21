import AppKit
import Foundation

let projectRoot = "/Users/davidfiore/Documents/Hedge Fund/current-ai-hedge-fund"
let refreshInterval: TimeInterval = 60

struct PortfolioStatus: Decodable {
    let created_at: String
    let net_liquidation_value: Double
    let cash_balance: Double
    let market_value: Double
    let unrealized_pnl: Double
    let realized_pnl: Double
    let total_pnl: Double
    let total_pnl_pct: Double
    let open_positions: Int
    let planned_orders: Int
    let open_risk: Double
    let symbols: [String]
    let refreshed_symbols: [String]
    let dashboard_url: String
    let price_errors: [String]
}

enum TickerError: Error {
    case message(String)
}

final class PortfolioTickerApp: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private var timer: Timer?
    private var latestDashboardURL = "http://localhost:8501"

    private let netLiqItem = NSMenuItem(title: "Net Liq: loading...", action: nil, keyEquivalent: "")
    private let cashItem = NSMenuItem(title: "Cash: loading...", action: nil, keyEquivalent: "")
    private let marketValueItem = NSMenuItem(title: "Market Value: loading...", action: nil, keyEquivalent: "")
    private let pnlItem = NSMenuItem(title: "P&L: loading...", action: nil, keyEquivalent: "")
    private let positionsItem = NSMenuItem(title: "Positions: loading...", action: nil, keyEquivalent: "")
    private let updatedItem = NSMenuItem(title: "Updated: loading...", action: nil, keyEquivalent: "")
    private let warningItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureMenu()
        statusItem.button?.title = "AIHF ..."
        refresh(nil)
        timer = Timer.scheduledTimer(timeInterval: refreshInterval, target: self, selector: #selector(refresh(_:)), userInfo: nil, repeats: true)
    }

    private func configureMenu() {
        menu.addItem(netLiqItem)
        menu.addItem(cashItem)
        menu.addItem(marketValueItem)
        menu.addItem(pnlItem)
        menu.addItem(positionsItem)
        menu.addItem(updatedItem)
        menu.addItem(warningItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Refresh Now", action: #selector(refresh(_:)), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard(_:)), keyEquivalent: "d"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit Portfolio Ticker", action: #selector(quit(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    @objc private func refresh(_ sender: Any?) {
        statusItem.button?.title = "AIHF updating..."
        DispatchQueue.global(qos: .utility).async {
            let result = self.loadStatus()
            DispatchQueue.main.async {
                switch result {
                case .success(let status):
                    self.apply(status)
                case .failure(let error):
                    self.statusItem.button?.title = "AIHF error"
                    self.warningItem.title = "Error: \(errorMessage(error))"
                }
            }
        }
    }

    private func loadStatus() -> Result<PortfolioStatus, TickerError> {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [
            "-lc",
            "cd \"\(projectRoot)\" && .venv/bin/python main.py ticker status --json"
        ]

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
        } catch {
            return .failure(.message(error.localizedDescription))
        }
        process.waitUntilExit()

        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        let errorOutput = stderr.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            let message = String(data: errorOutput, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            return .failure(.message(message?.isEmpty == false ? message! : "ticker command failed"))
        }

        do {
            let status = try JSONDecoder().decode(PortfolioStatus.self, from: output)
            return .success(status)
        } catch {
            return .failure(.message("could not read ticker data"))
        }
    }

    private func apply(_ status: PortfolioStatus) {
        latestDashboardURL = status.dashboard_url
        let pnlPrefix = status.total_pnl >= 0 ? "+" : "-"
        statusItem.button?.title = "AIHF \(compactMoney(status.net_liquidation_value)) \(pnlPrefix)\(compactMoney(abs(status.total_pnl)))"

        netLiqItem.title = "Net Liq: \(money(status.net_liquidation_value))"
        cashItem.title = "Cash: \(money(status.cash_balance))"
        marketValueItem.title = "Market Value: \(money(status.market_value))"
        pnlItem.title = "Total P&L: \(signedMoney(status.total_pnl)) (\(String(format: "%+.2f", status.total_pnl_pct))%)"
        positionsItem.title = "Positions: \(status.open_positions) | \(status.symbols.joined(separator: ", "))"
        updatedItem.title = "Updated: \(friendlyTime(status.created_at))"

        if status.price_errors.isEmpty {
            warningItem.title = "Prices refreshed: \(status.refreshed_symbols.count) symbol(s)"
        } else {
            warningItem.title = "Price warning: \(status.price_errors.first ?? "unknown")"
        }
    }

    @objc private func openDashboard(_ sender: Any?) {
        if let url = URL(string: latestDashboardURL) {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func quit(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}

func money(_ value: Double) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .currency
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSNumber(value: value)) ?? String(format: "$%.2f", value)
}

func signedMoney(_ value: Double) -> String {
    let prefix = value >= 0 ? "+" : "-"
    return "\(prefix)\(money(abs(value)))"
}

func compactMoney(_ value: Double) -> String {
    let absValue = abs(value)
    if absValue >= 1_000_000 {
        return String(format: "$%.2fM", value / 1_000_000)
    }
    if absValue >= 1_000 {
        return String(format: "$%.1fk", value / 1_000)
    }
    return String(format: "$%.0f", value)
}

func friendlyTime(_ iso: String) -> String {
    let formatter = ISO8601DateFormatter()
    if let date = formatter.date(from: iso) {
        let display = DateFormatter()
        display.timeStyle = .medium
        display.dateStyle = .none
        return display.string(from: date)
    }
    return iso
}

func errorMessage(_ error: TickerError) -> String {
    switch error {
    case .message(let message):
        return message
    }
}

let app = NSApplication.shared
let delegate = PortfolioTickerApp()
app.delegate = delegate
app.run()
