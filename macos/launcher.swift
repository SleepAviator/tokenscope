import AppKit
import Foundation
import Darwin

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate {
    private let dashboardURL = URL(string: "http://127.0.0.1:8765/")!
    private let liveURL = URL(string: "http://127.0.0.1:8765/api/live")!
    private var window: NSWindow!
    private var statusLabel: NSTextField!
    private var openButton: NSButton!
    private var showMeterCheckbox: NSButton!
    private let meterVisibilityPreference = "showMenuBarMeter"
    private var server = Process()
    private var serverOutput: RAMServerOutput?
    private var configURL: URL!
    private var isStopping = false
    private var didOpenDashboard = false
    private var statusItem: NSStatusItem!
    private let meterTextView = MeterTextView()
    private var meterTimer: Timer?
    private var meterRequest: URLSessionDataTask?
    private var meterEpoch: UInt = 0
    private var meterSnapshot: LiveSnapshot?
    private var meterError: String?
    private var meterUpdatedAt: Date?
    private let meterSession = URLSession(configuration: .ephemeral)

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeWindow()
        makeMeter()
        do {
            try startServer()
            startMeterPolling()
            statusLabel.stringValue = "Starting the local dashboard…"
            Timer.scheduledTimer(withTimeInterval: 0.8, repeats: true) { [weak self] timer in
                guard let self else {
                    timer.invalidate()
                    return
                }
                self.checkServer(timer: timer)
            }
        } catch {
            statusLabel.stringValue = "TokenScope could not start."
            meterError = error.localizedDescription
            updateMeterTitle()
            let diagnostics = serverOutput?.finish()
            showError(error.localizedDescription + diagnosticSuffix(diagnostics))
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showLauncher()
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        stopMeterPolling()
        guard server.isRunning else {
            _ = serverOutput?.finish()
            serverOutput = nil
            return .terminateNow
        }
        if isStopping { return .terminateLater }
        isStopping = true
        statusLabel.stringValue = "Stopping dashboard and active collection…"
        openButton.isEnabled = false
        showMeterCheckbox.isEnabled = false
        let output = serverOutput
        server.terminationHandler = { [weak sender] _ in
            _ = output?.finish()
            DispatchQueue.main.async {
                sender?.reply(toApplicationShouldTerminate: true)
            }
        }
        server.interrupt() // SIGINT lets Python stop its HTTP server and collector cleanly.
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            guard let self, self.server.isRunning else { return }
            self.server.terminate()
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                if self.server.isRunning { self.server.interrupt() }
            }
        }
        return .terminateLater
    }

    private func makeWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 540, height: 300),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "TokenScope"
        window.center()
        window.delegate = self
        window.isReleasedWhenClosed = false

        let content = window.contentView!
        let title = NSTextField(labelWithString: "TokenScope")
        title.font = .boldSystemFont(ofSize: 25)
        title.frame = NSRect(x: 34, y: 235, width: 470, height: 32)
        content.addSubview(title)

        statusLabel = NSTextField(wrappingLabelWithString: "Preparing TokenScope…")
        statusLabel.frame = NSRect(x: 36, y: 188, width: 466, height: 34)
        content.addSubview(statusLabel)

        let address = NSTextField(labelWithString: "http://127.0.0.1:8765/")
        address.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        address.frame = NSRect(x: 36, y: 162, width: 466, height: 20)
        content.addSubview(address)

        openButton = NSButton(title: "Open dashboard", target: self, action: #selector(openDashboard))
        openButton.frame = NSRect(x: 34, y: 112, width: 145, height: 34)
        openButton.bezelStyle = .rounded
        openButton.isEnabled = false
        content.addSubview(openButton)

        let settingsButton = NSButton(title: "Machine settings…", target: self, action: #selector(openSettings))
        settingsButton.frame = NSRect(x: 190, y: 112, width: 155, height: 34)
        settingsButton.bezelStyle = .rounded
        content.addSubview(settingsButton)

        let stopButton = NSButton(title: "Stop and quit", target: self, action: #selector(stopAndQuit))
        stopButton.frame = NSRect(x: 356, y: 112, width: 145, height: 34)
        stopButton.bezelStyle = .rounded
        content.addSubview(stopButton)

        showMeterCheckbox = NSButton(checkboxWithTitle: "Show menu bar TPS meter", target: self,
                                     action: #selector(toggleMeterVisibility(_:)))
        showMeterCheckbox.state = (UserDefaults.standard.object(forKey: meterVisibilityPreference) as? Bool ?? true) ? .on : .off
        showMeterCheckbox.frame = NSRect(x: 36, y: 82, width: 466, height: 22)
        content.addSubview(showMeterCheckbox)

        let note = NSTextField(wrappingLabelWithString: "Live readings and diagnostics stay in memory. Minute history is saved every 15 minutes; lifetime daily totals are saved daily and on quit. Settings and history stay in ~/Library/Application Support/TokenScope.")
        note.font = .systemFont(ofSize: 11)
        note.textColor = .secondaryLabelColor
        note.frame = NSRect(x: 36, y: 31, width: 466, height: 40)
        content.addSubview(note)
    }

    private func startServer() throws {
        let fileManager = FileManager.default
        let support = fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/TokenScope", isDirectory: true)
        try fileManager.createDirectory(at: support, withIntermediateDirectories: true)
        configURL = support.appendingPathComponent("config.ini")
        if !fileManager.fileExists(atPath: configURL.path) {
            guard let template = Bundle.main.resourceURL?.appendingPathComponent("config.example.ini"),
                  fileManager.fileExists(atPath: template.path) else {
                throw LauncherError.missingConfigTemplate
            }
            try fileManager.copyItem(at: template, to: configURL)
        }

        let serverExecutable = Bundle.main.resourceURL!
            .appendingPathComponent("TokenScopeServer/TokenScopeServer")
        guard fileManager.isExecutableFile(atPath: serverExecutable.path) else {
            throw LauncherError.missingServer
        }

        let output = RAMServerOutput()
        serverOutput = output
        server.executableURL = serverExecutable
        server.arguments = ["--host", "0.0.0.0", "--port", "8765", "--config", configURL.path, "--live-meter",
                            "--meter-history", support.appendingPathComponent("meter-history", isDirectory: true).path,
                            "--daily-archive", support.appendingPathComponent("daily-usage", isDirectory: true).path]
        server.currentDirectoryURL = support
        server.standardOutput = output.pipe
        server.standardError = output.pipe
        server.terminationHandler = { [weak self] process in
            let diagnostics = output.finish()
            DispatchQueue.main.async {
                guard let self, !self.isStopping else { return }
                self.stopMeterPolling()
                self.meterSnapshot = nil
                self.meterError = "The TokenScope server stopped."
                self.updateMeterTitle()
                self.statusLabel.stringValue = "The dashboard process stopped."
                self.showError("TokenScope exited with code \(process.terminationStatus)." + self.diagnosticSuffix(diagnostics))
            }
        }
        do {
            try server.run()
        } catch {
            output.closeParentWriter()
            throw error
        }
        output.closeParentWriter()
    }

    private func diagnosticSuffix(_ diagnostics: String?) -> String {
        guard let diagnostics, !diagnostics.isEmpty else { return "" }
        return "\n\nRecent server diagnostics (held in memory):\n\(diagnostics)"
    }

    private func checkServer(timer: Timer) {
        guard server.isRunning else {
            timer.invalidate()
            return
        }
        var request = URLRequest(url: dashboardURL.appendingPathComponent("api/status"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            guard let self else { return }
            DispatchQueue.main.async {
                guard self.server.isRunning else {
                    timer.invalidate()
                    return
                }
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { return }
                timer.invalidate()
                self.statusLabel.stringValue = "Running. Closing this window keeps TokenScope running."
                self.openButton.isEnabled = true
                if !self.didOpenDashboard {
                    self.didOpenDashboard = true
                    NSWorkspace.shared.open(self.dashboardURL)
                }
            }
        }.resume()
    }

    private func makeMeter() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.autosaveName = "TokenScopeLiveTPS"
        statusItem.isVisible = showMeterCheckbox.state == .on
        if let button = statusItem.button {
            button.title = ""
            meterTextView.frame = button.bounds
            meterTextView.autoresizingMask = [.width, .height]
            meterTextView.setAccessibilityElement(false)
            button.addSubview(meterTextView)
        }
        let menu = NSMenu(title: "TokenScope live TPS")
        menu.delegate = self
        statusItem.menu = menu
        updateMeterTitle()
    }

    private func startMeterPolling() {
        guard statusItem.isVisible, meterTimer == nil, !isStopping else { return }
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            self?.pollMeter()
        }
        timer.tolerance = 0.1
        meterTimer = timer
        RunLoop.main.add(timer, forMode: .common)
        pollMeter()
    }

    private func stopMeterPolling() {
        meterEpoch &+= 1
        meterTimer?.invalidate()
        meterTimer = nil
        meterRequest?.cancel()
        meterRequest = nil
    }

    private func pollMeter() {
        guard !isStopping, meterRequest == nil, server.isRunning else { return }
        let requestEpoch = meterEpoch
        var request = URLRequest(url: liveURL, cachePolicy: .reloadIgnoringLocalCacheData)
        request.timeoutInterval = 2
        meterRequest = meterSession.dataTask(with: request) { [weak self] data, response, error in
            let result: Result<LiveSnapshot, Error>
            if let error {
                result = .failure(error)
            } else if let response = response as? HTTPURLResponse, response.statusCode != 200 {
                result = .failure(MeterError.httpStatus(response.statusCode))
            } else if let data {
                do {
                    let snapshot = try JSONDecoder().decode(LiveSnapshot.self, from: data)
                    try snapshot.validate()
                    result = .success(snapshot)
                } catch {
                    result = .failure(MeterError.invalidSnapshot)
                }
            } else {
                result = .failure(MeterError.invalidSnapshot)
            }
            DispatchQueue.main.async {
                guard let self, !self.isStopping, self.meterTimer != nil,
                      self.meterEpoch == requestEpoch else { return }
                self.meterRequest = nil
                switch result {
                case .success(let snapshot):
                    self.meterSnapshot = snapshot
                    self.meterError = snapshot.enabled ? nil : "Live monitoring is disabled."
                    self.meterUpdatedAt = Date()
                case .failure(let error):
                    self.meterSnapshot = nil
                    self.meterError = "Live readings unavailable: \(error.localizedDescription)"
                }
                self.updateMeterTitle()
            }
        }
        meterRequest?.resume()
    }

    private func updateMeterTitle() {
        let snapshot = meterError == nil ? meterSnapshot : nil
        let total = meterRate(snapshot?.total_tps)
        let average = meterRate(snapshot?.average_tps)
        let incomplete = meterError != nil || snapshot?.status == "partial" || snapshot?.status == "unavailable"
        let awaitingUsage = snapshot?.pending_sessions.contains { $0.status == "awaiting_usage" } ?? false
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byClipping
        paragraph.minimumLineHeight = 10
        paragraph.maximumLineHeight = 10
        let title = NSAttributedString(
            string: "\(total.replacingOccurrences(of: "≈", with: "")) t/s\n\(average.replacingOccurrences(of: "≈", with: "")) t/s",
            attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 9, weight: .medium),
                         .foregroundColor: NSColor.labelColor,
                         .paragraphStyle: paragraph]
        )
        meterTextView.title = title
        statusItem.length = ceil(title.size().width)
        statusItem.button?.toolTip = meterError ?? "\(awaitingUsage ? "Waiting for reported token usage in active sessions. " : "")\(incomplete ? "Incomplete coverage. " : "")TokenScope: total \(total) tok/s, average \(average) tok/s per contributing session; passive estimates over five seconds."
        statusItem.button?.setAccessibilityLabel("TokenScope total \(total), average \(average) tokens per second\(incomplete ? ", incomplete coverage" : "")")
    }

    func menuWillOpen(_ menu: NSMenu) {
        menu.removeAllItems()
        addMeterText("TokenScope live output TPS", to: menu)
        if let error = meterError {
            addMeterText(error, to: menu)
        } else if let snapshot = meterSnapshot, snapshot.enabled {
            addMeterText("Total \(meterRate(snapshot.total_tps)) tok/s · average \(meterRate(snapshot.average_tps)) tok/s", to: menu)
            addMeterText("\(snapshot.contributing_sessions) contributing session\(snapshot.contributing_sessions == 1 ? "" : "s") · \(snapshot.status)", to: menu)
            if let updatedAt = meterUpdatedAt {
                let time = DateFormatter.localizedString(from: updatedAt, dateStyle: .none, timeStyle: .medium)
                addMeterText("Last received \(time)", to: menu)
            }
            menu.addItem(.separator())
            addMeterText("Machines", to: menu)
            for source in snapshot.sources {
                let age = source.age_seconds.map { String(format: " · %.0fs old", $0) } ?? ""
                addMeterText("\(source.name): \(meterRate(source.total_tps)) tok/s · \(source.status)\(age)", to: menu)
                for issue in source.issues.prefix(3) {
                    addMeterText("  \(issue)", to: menu)
                }
                if source.issues.count > 3 {
                    addMeterText("  \(source.issues.count - 3) more coverage issues", to: menu)
                }
            }
            if !snapshot.sessions.isEmpty {
                menu.addItem(.separator())
                addMeterText("Providers", to: menu)
                var providerRates: [String: Double] = [:]
                for session in snapshot.sessions {
                    providerRates[session.provider ?? "Unattributed provider", default: 0] += session.tps
                }
                let providers = providerRates.sorted { $0.key.localizedStandardCompare($1.key) == .orderedAscending }
                for (provider, tps) in providers.prefix(12) {
                    addMeterText("\(provider): \(meterRate(tps)) tok/s", to: menu)
                }
                if providers.count > 12 { addMeterText("\(providers.count - 12) more providers included in total", to: menu) }
                menu.addItem(.separator())
                addMeterText("Contributing sessions", to: menu)
                let sessions = snapshot.sessions.sorted { $0.tps > $1.tps }
                for session in sessions.prefix(12) {
                    let key = session.session_key.map { String($0.suffix(8)) } ?? "unattributed"
                    let model = session.model.map { " · \($0)" } ?? ""
                    addMeterText("\(session.source) · \(session.runtime) · \(key)\(model): \(meterRate(session.tps)) tok/s", to: menu)
                }
                if sessions.count > 12 { addMeterText("\(sessions.count - 12) more sessions included in total", to: menu) }
            }
            if !snapshot.pending_sessions.isEmpty {
                menu.addItem(.separator())
                addMeterText("Pending or unavailable sessions", to: menu)
                for session in snapshot.pending_sessions.prefix(8) {
                    let key = session.session_key.map { String($0.suffix(8)) } ?? "unattributed"
                    let state: String
                    switch session.status {
                    case "awaiting_usage": state = "waiting for reported token usage"
                    case "missing_timing": state = "response timing unavailable"
                    case "ambiguous_identity": state = "session identity unavailable"
                    default: state = session.status
                    }
                    addMeterText("\(session.source) · \(session.runtime) · \(key): \(state)", to: menu)
                }
                if snapshot.pending_sessions.count > 8 {
                    addMeterText("\(snapshot.pending_sessions.count - 8) more pending sessions", to: menu)
                }
            }
            for issue in snapshot.issues.prefix(5) { addMeterText(issue, to: menu) }
            if snapshot.issues.count > 5 { addMeterText("\(snapshot.issues.count - 5) more coverage issues", to: menu) }
        } else {
            addMeterText("Initializing live monitoring…", to: menu)
        }
        menu.addItem(.separator())
        addMeterText("Estimated output in the last five seconds ÷ 5.", to: menu)
        addMeterText("Average = total ÷ sessions contributing to that window.", to: menu)
        addMeterText("Usage reports can lag generation; missing readings are —.", to: menu)
        addMeterText("Thinking may finish before token usage is reported.", to: menu)
        menu.addItem(.separator())
        addMeterAction("Open dashboard", action: #selector(openDashboard), to: menu)
        addMeterAction("Machine settings…", action: #selector(openSettings), to: menu)
        addMeterAction("Show launcher", action: #selector(showLauncher), to: menu)
        addMeterAction("Stop and quit", action: #selector(stopAndQuit), to: menu)
    }

    private func addMeterText(_ text: String, to menu: NSMenu) {
        let compact = text.split(whereSeparator: { $0.isNewline }).joined(separator: " ")
        let item = NSMenuItem(title: String(compact.prefix(220)), action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }

    private func addMeterAction(_ title: String, action: Selector, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        menu.addItem(item)
    }

    @objc private func showLauncher() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func toggleMeterVisibility(_ sender: NSButton) {
        let visible = sender.state == .on
        statusItem.isVisible = visible
        UserDefaults.standard.set(visible, forKey: meterVisibilityPreference)
        if visible {
            meterSnapshot = nil
            meterError = nil
            meterUpdatedAt = nil
            updateMeterTitle()
            startMeterPolling()
        } else {
            stopMeterPolling()
        }
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(dashboardURL)
    }

    @objc private func openSettings() {
        guard let configURL else { return }
        NSWorkspace.shared.open(configURL)
    }

    @objc private func stopAndQuit() {
        NSApp.terminate(nil)
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "TokenScope could not start"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

// Compact output-speed status presentation adapted from Token Meter.
// See THIRD_PARTY_NOTICES.md for its MIT notice; no second service is required.
private func meterRate(_ value: Double?) -> String {
    guard let value, value.isFinite, value >= 0 else { return "—" }
    return "≈" + String(format: value < 10 && value > 0 ? "%.1f" : "%.0f", value)
}

/// Centers both rows while retaining the status button's native menu behavior.
private final class MeterTextView: NSView {
    var title = NSAttributedString() {
        didSet { needsDisplay = true }
    }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func draw(_ dirtyRect: NSRect) {
        let size = title.size()
        title.draw(at: NSPoint(x: floor((bounds.width - size.width) / 2),
                               y: floor((bounds.height - size.height) / 2)))
    }
}

/// Drains server output off the UI thread without writing a log to disk.
private final class RAMServerOutput {
    let pipe = Pipe()
    private let readLock = NSLock()
    private let tailLock = NSLock()
    private let limit = 64 * 1024
    private var tail = Data()
    private var finished = false
    private var writerClosed = false

    init() {
        pipe.fileHandleForReading.readabilityHandler = { [weak self] _ in
            self?.readReadyOutput()
        }
    }

    func closeParentWriter() {
        readLock.lock()
        defer { readLock.unlock() }
        guard !writerClosed else { return }
        writerClosed = true
        do {
            try pipe.fileHandleForWriting.close()
        } catch {
            append(Data("\nCould not close the server output writer: \(error.localizedDescription)\n".utf8))
        }
    }

    /// Called after the process exits, or after a failed launch closes the writer.
    @discardableResult
    func finish() -> String {
        // Keep final cleanup self-contained even if launch cleanup already ran.
        closeParentWriter()
        readLock.lock()
        if !finished {
            pipe.fileHandleForReading.readabilityHandler = nil
            do {
                var remaining = limit
                while remaining > 0 {
                    guard let data = try readAvailable(upTo: remaining),
                          !data.isEmpty else { break }
                    append(data)
                    remaining -= data.count
                }
            } catch {
                append(Data("\nCould not finish reading server output: \(error.localizedDescription)\n".utf8))
            }
            closeReader()
        }
        readLock.unlock()
        tailLock.lock()
        defer { tailLock.unlock() }
        return String(decoding: tail, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func readReadyOutput() {
        readLock.lock()
        defer { readLock.unlock() }
        guard !finished else { return }
        do {
            guard let data = try readAvailable(upTo: limit) else { return }
            guard !data.isEmpty else {
                closeReader()
                return
            }
            append(data)
        } catch {
            append(Data("\nCould not read server output: \(error.localizedDescription)\n".utf8))
            closeReader()
        }
    }

    /// POSIX pipe reads return available bytes; FileHandle can wait to fill its count.
    private func readAvailable(upTo count: Int) throws -> Data? {
        let descriptor = pipe.fileHandleForReading.fileDescriptor
        var readiness = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
        let ready = Darwin.poll(&readiness, 1, 0)
        if ready < 0 { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        guard ready > 0 else { return nil }
        if readiness.revents & Int16(POLLNVAL) != 0 {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(EBADF))
        }
        guard readiness.revents & Int16(POLLIN) != 0 else {
            if readiness.revents & Int16(POLLHUP) != 0 { return Data() }
            if readiness.revents & Int16(POLLERR) != 0 {
                throw NSError(domain: NSPOSIXErrorDomain, code: Int(EIO))
            }
            return nil
        }
        var data = Data(count: count)
        let bytesRead = data.withUnsafeMutableBytes { buffer in
            Darwin.read(descriptor, buffer.baseAddress, count)
        }
        if bytesRead < 0 { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        data.count = bytesRead
        return data
    }

    private func closeReader() {
        finished = true
        pipe.fileHandleForReading.readabilityHandler = nil
        do {
            try pipe.fileHandleForReading.close()
        } catch {
            append(Data("\nCould not close the server output reader: \(error.localizedDescription)\n".utf8))
        }
    }

    private func append(_ data: Data) {
        tailLock.lock()
        defer { tailLock.unlock() }
        tail.append(data)
        if tail.count > limit { tail.removeFirst(tail.count - limit) }
    }
}

private struct LiveSnapshot: Decodable {
    let enabled: Bool
    let status: String
    let window_seconds: Double
    let total_tps: Double?
    let average_tps: Double?
    let contributing_sessions: Int
    let sources: [LiveSource]
    let sessions: [LiveSession]
    let pending_sessions: [PendingSession]
    let issues: [String]

    func validate() throws {
        let rates = [total_tps, average_tps] + sources.map(\.total_tps) + sessions.map { Optional($0.tps) }
        guard ["initializing", "complete", "partial", "unavailable"].contains(status),
              window_seconds == 5, contributing_sessions >= 0,
              rates.compactMap({ $0 }).allSatisfy({ $0.isFinite && $0 >= 0 }),
              sources.allSatisfy({ ["fresh", "initializing", "stale", "error"].contains($0.status) }),
              sources.compactMap(\.age_seconds).allSatisfy({ $0.isFinite && $0 >= 0 }) else {
            throw MeterError.invalidSnapshot
        }
    }
}

private struct LiveSource: Decodable {
    let name: String
    let status: String
    let age_seconds: Double?
    let total_tps: Double?
    let issues: [String]
}

private struct LiveSession: Decodable {
    let session_key: String?
    let source: String
    let runtime: String
    let provider: String?
    let model: String?
    let tps: Double
    let status: String
}

private struct PendingSession: Decodable {
    let session_key: String?
    let source: String
    let runtime: String
    let provider: String?
    let model: String?
    let status: String
}

private enum MeterError: LocalizedError {
    case httpStatus(Int)
    case invalidSnapshot

    var errorDescription: String? {
        switch self {
        case .httpStatus(let code):
            return "The local server returned HTTP \(code)."
        case .invalidSnapshot:
            return "The local server returned an invalid live snapshot."
        }
    }
}

private enum LauncherError: LocalizedError {
    case missingConfigTemplate
    case missingServer

    var errorDescription: String? {
        switch self {
        case .missingConfigTemplate:
            return "The bundled example configuration is missing."
        case .missingServer:
            return "The bundled TokenScope server is missing. Re-download the app bundle."
        }
    }
}
