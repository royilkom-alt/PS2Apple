import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers

struct ToolStatus {
    let ok: Bool
    let detail: String
}

struct LogLine: Identifiable {
    let id: Int
    let text: String
}

struct UpdateResult: Identifiable {
    var id: String { detail }
    let detail: String
    let url: URL?
}

enum DiscChannel: String, Decodable {
    case testedPack = "tested-pack"
    case preview = "ios-preview"
    case dedicated, general, research

    var isTestBuild: Bool { self == .preview || self == .general || self == .research }

    var label: String {
        switch self {
        case .testedPack: "Dedicated game pack"
        case .preview: "Audited port · iPhone test"
        case .dedicated: "Dedicated port"
        case .general: "General AOT engine"
        case .research: "Research port"
        }
    }
}

private struct BackendEvent: Decodable {
    let kind: String
    let key: String?
    let detail: String?
    let ok: Bool?
    let value: Double?
    let current: Int?
    let total: Int?
    let serial: String?
    let title: String?
    let region: String?
    let bootSHA256: String?
    let bootSize: Int?
    let profile: String?
    let channel: DiscChannel?
    let note: String?
    let path: String?
    let url: URL?
}

struct BootProgress {
    let current: Int
    let total: Int
    let startedAt: Date

    var fraction: Double { Double(current) / Double(total) }
}

@MainActor
final class BuilderModel: ObservableObject {
    enum Job: String {
        case check = "Checking toolchain"
        case inspect = "Reading PS2 disc"
        case setup = "Installing dependencies"
        case build = "Building Native IPA"
        case buildMac = "Building Mac app"
        case updates = "Checking for updates"
    }

    private(set) var catalog: [CatalogGame] = []
    @Published private(set) var checks: [String: ToolStatus] = [:]
    @Published private(set) var iso: URL?
    @Published private(set) var discDetail = "Select your own PlayStation 2 ISO."
    @Published private(set) var discTitle: String?
    @Published private(set) var discRegion: String?
    @Published private(set) var discSerial: String?
    @Published private(set) var discVerified = false
    @Published private(set) var discNote: String?
    @Published private(set) var discChannel: DiscChannel?
    @Published private(set) var output: URL
    @Published private(set) var ipa: URL?
    @Published private(set) var bootCheck: ToolStatus?
    @Published private(set) var job: Job?
    @Published private(set) var progress: Double?
    @Published private(set) var bootProgress: BootProgress?
    @Published private(set) var status = "Ready"
    @Published private(set) var error: String?
    @Published private(set) var cancelling = false
    @Published private(set) var logs: [LogLine] = []
    @Published private(set) var logFile: URL?
    @Published private(set) var updates: [UpdateResult] = []

    let version: String = {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"
        return version == "1.0.0" ? "1.0" : version
    }()
    private let defaults = UserDefaults.standard
    private let files = FileManager.default
    private let process = BuildProcess()
    private var pendingIPA: URL?
    private var pendingDMG: URL?
    @Published private(set) var dmg: URL?
    private var startupISO: URL?
    private var matchingProfileID: String?
    private var selectedProfileID: String?
    private var logHandle: FileHandle?
    private var nextLine = 0
    private var started = false
    private var onQuit: (() -> Void)?

    private var backend: URL? { Bundle.main.resourceURL?.appendingPathComponent("Backend", isDirectory: true) }
    var support: URL {
        // Existing venvs and CMake caches refer to this absolute path.
        files.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/PS2 Native Easy Builder", isDirectory: true)
    }

    var isRunning: Bool { job != nil }
    var toolCount: Int { checks.values.filter { $0.ok }.count }
    var canBuild: Bool { discVerified && iso != nil && !isRunning }
    var canCheckPortUpdates: Bool { discVerified && discChannel != .general && selectedProfileID != nil && !isRunning }

    init() {
        let defaults = UserDefaults.standard
        let documents = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents")
        output = defaults.url(forKey: "outputDirectory") ?? documents.appendingPathComponent("PS2Apple")
        loadCatalog()
        let legacyISO = defaults.string(forKey: "selectedProfile").flatMap { defaults.url(forKey: "iso.\($0)") }
        startupISO = (defaults.url(forKey: "lastISO") ?? legacyISO).flatMap {
            files.fileExists(atPath: $0.path) ? $0 : nil
        }
    }

    func start() {
        guard !started else { return }
        started = true
        check()
    }

    private func loadCatalog() {
        if let url = backend?.appendingPathComponent("game-catalog.json"),
           let data = try? Data(contentsOf: url),
           let index = try? JSONDecoder().decode(GameCatalog.self, from: data), index.schema == 1 {
            catalog = index.games
        }
    }

    func chooseISO() {
        guard !isRunning else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose PS2 Disc Image"
        panel.prompt = "Select ISO"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "iso") ?? .data]
        if let iso { panel.directoryURL = iso.deletingLastPathComponent() }
        guard panel.runModal() == .OK, let chosen = panel.url else { return }
        inspect(chosen)
    }

    private func inspect(_ chosen: URL) {
        iso = chosen
        defaults.set(chosen, forKey: "lastISO")
        ipa = nil
        bootCheck = nil
        updates = []
        discVerified = false
        discNote = nil
        discChannel = nil
        selectedProfileID = nil
        discTitle = nil
        discRegion = nil
        discSerial = nil
        discDetail = "Reading SYSTEM.CNF and the boot ELF…"
        execute(.inspect, ["identify", "--iso", chosen.path])
    }

    func chooseOutput() {
        let panel = NSOpenPanel()
        panel.title = "Choose Output Folder"
        panel.prompt = "Choose Folder"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.directoryURL = output
        guard panel.runModal() == .OK, let chosen = panel.url else { return }
        output = chosen
        defaults.set(chosen, forKey: "outputDirectory")
    }

    func check() {
        execute(.check, ["check"])
    }

    func install() {
        if discVerified, discChannel != .general, let selectedProfileID {
            execute(.setup, ["setup", "--profile", selectedProfileID])
        } else {
            execute(.setup, ["setup-aot"])
        }
    }

    func openTerminalSetup() {
        guard let url = backend?.appendingPathComponent("Setup-in-Terminal.command") else { return }
        NSWorkspace.shared.open(url)
    }

    func build() {
        guard canBuild, let iso else { return }
        ipa = nil
        bootCheck = nil
        execute(.build, ["build", "--iso", iso.path, "--output", output.path])
    }

    func checkPortUpdates() {
        guard canCheckPortUpdates, let selectedProfileID else { return }
        updates = []
        execute(.updates, ["updates", "--profile", selectedProfileID])
    }

    func cancel() {
        guard isRunning, !cancelling else { return }
        cancelling = true
        bootProgress = nil
        status = "Stopping the active job…"
        append(["Stopping the process group…"])
        process.cancel()
    }

    func requestQuit(then: @escaping () -> Void) {
        onQuit = then
        cancel()
    }

    func buildMac() {
        guard canBuild, let iso else { return }
        dmg = nil
        execute(.buildMac, ["build-mac", "--iso", iso.path, "--output", output.path])
    }

    func revealDMG() {
        if let dmg { NSWorkspace.shared.activateFileViewerSelecting([dmg]) }
    }

    func revealIPA() {
        if let ipa { NSWorkspace.shared.activateFileViewerSelecting([ipa]) }
    }

    func revealLog() {
        if let logFile { NSWorkspace.shared.activateFileViewerSelecting([logFile]) }
    }

    func copyLog() {
        let value = logs.map(\.text).joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }

    private func execute(_ action: Job, _ arguments: [String]) {
        guard job == nil, let script = backend?.appendingPathComponent("launch.sh") else { return }
        error = nil
        job = action
        cancelling = false
        pendingIPA = nil
        pendingDMG = nil
        matchingProfileID = nil
        progress = nil
        bootProgress = nil
        status = action.rawValue
        logs = []
        logFile = nil
        logHandle = nil
        do {
            let folder = support.appendingPathComponent("Logs")
            try files.createDirectory(at: folder, withIntermediateDirectories: true)
            let url = folder.appendingPathComponent("Build-\(UUID().uuidString.prefix(12)).log")
            guard files.createFile(atPath: url.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
            logHandle = try FileHandle(forWritingTo: url)
            logFile = url
        } catch {
            append(["Log file unavailable · \(error.localizedDescription)"])
        }
        append(["\(action.rawValue) · \(Date.now.formatted(date: .abbreviated, time: .standard))"])
        do {
            try process.start(script: script, arguments: arguments, onOutput: { [weak self] lines in
                self?.consume(lines)
            }, onExit: { [weak self] code in
                self?.finish(code)
            })
        } catch {
            self.error = "Could not start the build service. \(error.localizedDescription)"
            finish(1)
        }
    }

    private func consume(_ lines: [String]) {
        var readable: [String] = []
        for line in lines {
            guard line.hasPrefix("@@PS2 "),
                  let event = try? JSONDecoder().decode(BackendEvent.self, from: Data(line.dropFirst(6).utf8)) else {
                readable.append(line)
                continue
            }
            switch event.kind {
            case "check":
                if let key = event.key, let ok = event.ok, let detail = event.detail {
                    checks[key] = ToolStatus(ok: ok, detail: detail)
                }
            case "progress":
                if !cancelling {
                    if let value = event.value, value.isFinite {
                        progress = min(max(value, progress ?? 0), 1)
                    }
                    status = event.detail ?? status
                }
            case "boot-progress":
                if job == .build || job == .buildMac, !cancelling, let current = event.current, let total = event.total,
                   total > 0, (0...total).contains(current) {
                    let startedAt = current == 0 ? Date.now : bootProgress?.startedAt ?? .now
                    bootProgress = BootProgress(current: current, total: total, startedAt: startedAt)
                    status = "Checking game boot"
                }
            case "boot-progress-ended":
                bootProgress = nil
            case "disc":
                discVerified = event.ok == true
                discDetail = event.detail ?? discDetail
                if job == .inspect {
                    matchingProfileID = event.profile
                    selectedProfileID = event.profile
                    discChannel = event.channel
                    discNote = event.note
                    discTitle = event.title
                    discRegion = event.region
                    discSerial = event.serial
                }
                if let serial = event.serial, let hash = event.bootSHA256, let size = event.bootSize {
                    readable.append("\(serial) · Boot ELF SHA-256 \(hash) (\(size) bytes)")
                }
                if let note = event.note {
                    readable.append("Compatibility · \(note)")
                }
                if job == .build, event.ok == true {
                    matchingProfileID = event.profile
                    discChannel = event.channel
                }
            case "artifact":
                if let path = event.path, path.hasPrefix("/"), path.hasSuffix(".ipa") {
                    pendingIPA = URL(fileURLWithPath: path)
                } else if let path = event.path, path.hasPrefix("/"), path.hasSuffix(".dmg") {
                    pendingDMG = URL(fileURLWithPath: path)
                }
            case "boot":
                if job == .build, let ok = event.ok, let detail = event.detail {
                    bootCheck = ToolStatus(ok: ok, detail: detail)
                }
            case "error":
                error = event.detail
            case "update":
                if let detail = event.detail {
                    updates.append(UpdateResult(detail: detail, url: event.url))
                    readable.append(detail)
                }
            default:
                break
            }
        }
        append(readable)
    }

    private func append(_ lines: [String]) {
        guard !lines.isEmpty else { return }
        if let logHandle {
            try? logHandle.write(contentsOf: Data((lines.joined(separator: "\n") + "\n").utf8))
        }
        logs.append(contentsOf: lines.map {
            nextLine += 1
            return LogLine(id: nextLine, text: $0)
        })
        if logs.count > 900 { logs.removeFirst(logs.count - 900) }
    }

    private func finish(_ code: Int32) {
        bootProgress = nil
        let finished = job
        let wasCancelled = cancelling
        job = nil
        cancelling = false
        if finished == .inspect {
            if code == 0, !wasCancelled, discVerified, let id = matchingProfileID {
                ipa = defaults.url(forKey: "ipa.\(id)").flatMap { files.fileExists(atPath: $0.path) ? $0 : nil }
            } else {
                ipa = nil
                discVerified = false
                discChannel = nil
                selectedProfileID = nil
            }
        }
        var producedResult = false
        if code == 0, !wasCancelled, let pendingIPA, let id = matchingProfileID,
           files.fileExists(atPath: pendingIPA.path) {
            ipa = pendingIPA
            defaults.set(pendingIPA, forKey: "ipa.\(id)")
            producedResult = true
        }
        if code == 0, !wasCancelled, finished == .buildMac, let pendingDMG, files.fileExists(atPath: pendingDMG.path) {
            dmg = pendingDMG
            producedResult = true
        }
        if wasCancelled {
            status = "Cancelled"
            error = nil
            progress = nil
            if finished == .inspect { discDetail = "Disc verification cancelled." }
        } else if code != 0 || ((finished == .build || finished == .buildMac) && !producedResult) {
            status = "Action failed"
            error = error ?? "The job ended with status \(code). See the build log."
            if finished == .inspect { discDetail = error ?? "Disc verification failed." }
        } else {
            switch finished {
            case .check: status = "Toolchain check complete"
            case .inspect: status = discVerified ? "Disc verified" : "Disc identified"
            case .setup: status = "Dependencies ready"
            case .build: status = discChannel?.isTestBuild == true ? "Test IPA exported" : "Native IPA exported"
            case .buildMac: status = "Mac DMG exported"
            case .updates: status = "Update check complete"
            case nil: break
            }
        }
        append(["\(status) · exit \(code)"])
        try? logHandle?.close()
        logHandle = nil
        let quit = onQuit
        onQuit = nil
        if let quit { quit(); return }
        if finished == .check, let startupISO {
            self.startupISO = nil
            inspect(startupISO)
        }
    }
}
