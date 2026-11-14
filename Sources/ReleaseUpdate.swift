import AppKit
import Combine
import CryptoKit
import Darwin
import Foundation

struct ReleaseUpdate: Equatable {
    static let page = URL(string: "https://github.com/royilkom-alt/PS2Apple/releases")!
    private static let feed = URL(string: "https://api.github.com/repos/royilkom-alt/PS2Apple/releases?per_page=30")!
    let version: String
    let url: URL
    let digest: String
    let size: Int64

    private struct Release: Decodable {
        struct Asset: Decodable {
            let name: String
            let size: Int64
            let state: String
            let digest: String?
        }
        let tag_name: String
        let draft: Bool
        let prerelease: Bool
        let assets: [Asset]
    }

    static func versionParts(_ version: String) -> [Int]? {
        guard version.range(of: #"\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z"#,
                            options: .regularExpression) != nil else { return nil }
        let parts = version.split(separator: ".").compactMap { Int($0) }
        return parts.count == 3 ? parts : nil
    }

    static func newest(in data: Data, after installed: String) throws -> ReleaseUpdate? {
        guard var latest = versionParts(installed) else { throw UpdateFailure("The installed version is invalid.") }
        var update: ReleaseUpdate?
        for release in try JSONDecoder().decode([Release].self, from: data) where !release.draft && !release.prerelease {
            guard release.tag_name.hasPrefix("v") else { continue }
            let version = String(release.tag_name.dropFirst())
            let name = "PS2Apple-\(version)-Mac.dmg"
            guard let parts = versionParts(version), latest.lexicographicallyPrecedes(parts),
                  let asset = release.assets.first(where: { $0.name == name && $0.state == "uploaded" && $0.size > 0 }),
                  let digest = asset.digest, digest.range(of: #"\Asha256:[0-9a-fA-F]{64}\z"#,
                                                         options: .regularExpression) != nil else { continue }
            let url = page.appendingPathComponent("download").appendingPathComponent(release.tag_name)
                .appendingPathComponent(name)
            update = ReleaseUpdate(version: version, url: url, digest: String(digest.dropFirst(7)).lowercased(), size: asset.size)
            latest = parts
        }
        return update
    }

    static func check(after installed: String) async throws -> ReleaseUpdate? {
        var request = URLRequest(url: feed, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("PS2Apple/\(installed)", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UpdateFailure("GitHub could not return available updates. Try again later.")
        }
        return try newest(in: data, after: installed)
    }
}

struct UpdateFailure: LocalizedError {
    var errorDescription: String? { detail }
    let detail: String
    init(_ detail: String) { self.detail = detail }
}

struct PreparedMacUpdate {
    let bundle: URL
    let target: URL

    private static let bundleID = "net.isolution.ps2nativeeasybuilder"
    static let installLog = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/PS2Apple-Update.log")

    static func download(_ release: ReleaseUpdate, to target: URL) async throws -> PreparedMacUpdate {
        let files = FileManager.default
        let (download, response) = try await URLSession.shared.download(from: release.url)
        defer { try? files.removeItem(at: download) }
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UpdateFailure("GitHub could not download PS2Apple \(release.version).")
        }
        try verify(download, size: release.size, digest: release.digest)
        let mount = files.temporaryDirectory.appendingPathComponent("PS2Apple-Mount-\(UUID().uuidString)")
        try files.createDirectory(at: mount, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? files.removeItem(at: mount) }
        try run("/usr/bin/hdiutil", ["attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", mount.path, download.path])
        defer { try? run("/usr/bin/hdiutil", ["detach", mount.path]) }
        let candidate = mount.appendingPathComponent("PS2Apple.app", isDirectory: true)
        try validate(candidate, expected: release.version)
        let folder = target.deletingLastPathComponent().appendingPathComponent(".PS2Apple-Update-\(UUID().uuidString)")
        do {
            try files.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            let bundle = folder.appendingPathComponent("PS2Apple.app", isDirectory: true)
            try run("/usr/bin/ditto", [candidate.path, bundle.path])
            try validate(bundle, expected: release.version)
            return PreparedMacUpdate(bundle: bundle, target: target)
        } catch {
            try? files.removeItem(at: folder)
            throw UpdateFailure("PS2Apple could not prepare the update in its current folder. Download the DMG from GitHub to install it there.")
        }
    }

    static func verify(_ file: URL, size: Int64, digest: String) throws {
        guard let length = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize, Int64(length) == size else {
            throw UpdateFailure("The update download is incomplete.")
        }
        let stream = try FileHandle(forReadingFrom: file)
        defer { try? stream.close() }
        var hash = SHA256()
        while let data = try stream.read(upToCount: 1 << 20), !data.isEmpty { hash.update(data: data) }
        let actual = hash.finalize().map { String(format: "%02x", $0) }.joined()
        guard actual == digest else { throw UpdateFailure("The update checksum did not match GitHub.") }
    }

    func launch(restart: Bool) throws {
        let process = Process()
        process.executableURL = bundle.appendingPathComponent("Contents/MacOS/PS2Apple")
        process.arguments = ["--apply-update", String(ProcessInfo.processInfo.processIdentifier), target.path, restart ? "restart" : "quit"]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
    }

    static func apply(parent: Int32, target: URL, restart: Bool) throws {
        let candidate = Bundle.main.bundleURL.resolvingSymlinksInPath()
        let folder = candidate.deletingLastPathComponent()
        guard folder.lastPathComponent.hasPrefix(".PS2Apple-Update-"),
              folder.deletingLastPathComponent().path == target.deletingLastPathComponent().path,
              target.pathExtension == "app", let newVersion = version(of: candidate), let oldVersion = version(of: target),
              let newer = ReleaseUpdate.versionParts(newVersion), let older = ReleaseUpdate.versionParts(oldVersion),
              older.lexicographicallyPrecedes(newer), parent > 1 else { throw UpdateFailure("Invalid PS2Apple update.") }
        try validate(candidate, expected: newVersion)
        for _ in 0..<60 {
            if kill(parent, 0) != 0 { break }
            Thread.sleep(forTimeInterval: 1)
        }
        guard kill(parent, 0) != 0, errno == ESRCH, version(of: target) == oldVersion else {
            throw UpdateFailure("The running copy of PS2Apple could not be replaced.")
        }
        guard renamex_np(candidate.path, target.path, UInt32(RENAME_SWAP)) == 0 else {
            throw UpdateFailure("macOS could not replace PS2Apple in this folder.")
        }
        do {
            if restart { try run("/usr/bin/open", ["-n", target.path]) }
        } catch {
            _ = renamex_np(candidate.path, target.path, UInt32(RENAME_SWAP))
            throw error
        }
        try? FileManager.default.removeItem(at: folder)
        try? FileManager.default.removeItem(at: installLog)
    }

    static func report(_ error: Error, target: URL, restart: Bool) {
        let message = "PS2Apple could not install the update. \(error.localizedDescription)"
        try? FileManager.default.createDirectory(at: installLog.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? message.write(to: installLog, atomically: true, encoding: .utf8)
        if restart, version(of: target) != nil { try? run("/usr/bin/open", [target.path]) }
    }

    private static func version(of app: URL) -> String? {
        guard let bundle = Bundle(url: app), bundle.bundleIdentifier == bundleID else { return nil }
        return bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    }

    private static func validate(_ app: URL, expected: String) throws {
        guard try app.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == false,
              version(of: app) == expected else { throw UpdateFailure("The DMG contains a different version of PS2Apple.") }
        try run("/usr/bin/codesign", ["--verify", "--strict", app.path])
    }

    private static func run(_ command: String, _ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: command)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw UpdateFailure("\(URL(fileURLWithPath: command).lastPathComponent) could not complete the update.")
        }
    }
}

@MainActor
final class DesktopUpdates: ObservableObject {
    let installed = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.1"
    private static let preference = "automaticallyInstallUpdates"
    @Published var automatic = UserDefaults.standard.object(forKey: preference) as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(automatic, forKey: Self.preference)
            if automatic { download() }
        }
    }
    @Published private(set) var available: ReleaseUpdate?
    @Published private(set) var message = ""
    @Published private(set) var checking = false
    @Published private(set) var downloading = false
    @Published private(set) var prepared: PreparedMacUpdate?
    @Published private(set) var installFailed = false
    private var timer: Timer?
    private var launchedInstaller = false

    func start() {
        guard timer == nil else { return }
        if let failure = try? String(contentsOf: PreparedMacUpdate.installLog, encoding: .utf8) {
            message = failure
            installFailed = true
        }
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.check() }
        }
        check()
    }

    func check(manual: Bool = false) {
        guard !checking, !downloading, prepared == nil else { return }
        checking = true
        if manual { message = "Checking GitHub…" }
        Task {
            defer { checking = false }
            do {
                available = try await ReleaseUpdate.check(after: installed)
                if !installFailed {
                    message = available.map { "PS2Apple \($0.version) is available." } ?? "You have the latest Mac version."
                    if automatic { download() }
                }
            } catch {
                if manual { message = "Could not check GitHub. \(error.localizedDescription)" }
            }
        }
    }

    func download() {
        guard let release = available, prepared == nil, !downloading else { return }
        downloading = true
        installFailed = false
        try? FileManager.default.removeItem(at: PreparedMacUpdate.installLog)
        message = "Downloading PS2Apple \(release.version)…"
        let target = Bundle.main.bundleURL.resolvingSymlinksInPath()
        Task {
            defer { downloading = false }
            do {
                prepared = try await PreparedMacUpdate.download(release, to: target)
                message = "PS2Apple \(release.version) is ready to install."
            } catch {
                message = error.localizedDescription
            }
        }
    }

    func install(restart: Bool) -> Bool {
        guard !launchedInstaller, let prepared else { return false }
        do {
            try prepared.launch(restart: restart)
            launchedInstaller = true
            return true
        } catch {
            message = "Could not start the installer. \(error.localizedDescription)"
            return false
        }
    }

    func finishOnQuit() {
        guard !launchedInstaller, let prepared else { return }
        if automatic && install(restart: false) { return }
        try? FileManager.default.removeItem(at: prepared.bundle.deletingLastPathComponent())
    }
}
