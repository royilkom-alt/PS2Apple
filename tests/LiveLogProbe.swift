import Foundation

@main
@MainActor
struct LiveLogProbe {
    static func main() async throws {
        let files = FileManager.default
        let directory = files.temporaryDirectory.appendingPathComponent("PS2 Live Log \(UUID().uuidString)")
        try files.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? files.removeItem(at: directory) }

        let script = directory.appendingPathComponent("probe.sh")
        let acknowledged = directory.appendingPathComponent("log-delivered")
        // The child stays alive until the Swift log callback receives its first short line.
        try """
        printf '%s\\n' 'first short log line'
        for ((attempt = 0; attempt < 25; attempt++)); do
            if [[ -f "$1" ]]; then exit 0; fi
            sleep 0.2
        done
        exit 23
        """.write(to: script, atomically: true, encoding: .utf8)

        let process = BuildProcess()
        let code: Int32 = try await withCheckedThrowingContinuation { continuation in
            do {
                try process.start(script: script, arguments: [acknowledged.path], onOutput: { lines in
                    if lines.contains("first short log line") {
                        _ = files.createFile(atPath: acknowledged.path, contents: nil)
                    }
                }, onExit: { continuation.resume(returning: $0) })
            } catch {
                continuation.resume(throwing: error)
            }
        }
        guard code == 0 else {
            throw NSError(domain: "PS2LiveLogProbe", code: Int(code), userInfo: [
                NSLocalizedDescriptionKey: "A short log line did not reach Swift while the child was alive."
            ])
        }
        print("PASS: the Swift log callback received a short line before process exit")
    }
}
