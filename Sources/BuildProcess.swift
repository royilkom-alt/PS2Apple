import Darwin
import Foundation

/// Every job owns a process group so cancelling also stops compilers and package managers.
final class BuildProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var active: pid_t = 0

    struct SpawnError: LocalizedError {
        let code: Int32
        var errorDescription: String? { String(cString: strerror(code)) }
    }

    func start(
        script: URL,
        arguments: [String],
        onOutput: @escaping @MainActor ([String]) -> Void,
        onExit: @escaping @MainActor (Int32) -> Void
    ) throws {
        var descriptors: [Int32] = [-1, -1]
        let pipeResult = descriptors.withUnsafeMutableBufferPointer { pipe($0.baseAddress!) }
        guard pipeResult == 0 else { throw SpawnError(code: errno) }
        let reader = descriptors[0]
        let writer = descriptors[1]
        defer { close(writer) }
        var started = false
        defer { if !started { close(reader) } }

        guard reader > STDERR_FILENO, writer > STDERR_FILENO else { throw SpawnError(code: EBADF) }

        var actions: posix_spawn_file_actions_t?
        var attributes: posix_spawnattr_t?
        try require(posix_spawn_file_actions_init(&actions))
        defer { posix_spawn_file_actions_destroy(&actions) }
        try require(posix_spawnattr_init(&attributes))
        defer { posix_spawnattr_destroy(&attributes) }
        try require(posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0))
        try require(posix_spawn_file_actions_adddup2(&actions, writer, STDOUT_FILENO))
        try require(posix_spawn_file_actions_adddup2(&actions, writer, STDERR_FILENO))
        try require(posix_spawn_file_actions_addclose(&actions, reader))
        try require(posix_spawn_file_actions_addclose(&actions, writer))
        try require(posix_spawnattr_setpgroup(&attributes, 0))
        try require(posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT)))

        let program = "/bin/bash"
        var argv = ([program, script.path] + arguments).map { strdup($0) } + [nil]
        let excluded = ["BASH_ENV", "ENV", "PYTHONHOME", "PYTHONPATH", "PYTHONOPTIMIZE"]
        var environment = ProcessInfo.processInfo.environment
            .filter { !excluded.contains($0.key) && !$0.key.hasPrefix("DYLD_") }
            .map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            argv.forEach { free($0) }
            environment.forEach { free($0) }
        }
        var child: pid_t = 0
        let status = argv.withUnsafeMutableBufferPointer { args in
            environment.withUnsafeMutableBufferPointer { env in
                posix_spawn(&child, program, &actions, &attributes, args.baseAddress!, env.baseAddress!)
            }
        }
        try require(status)
        started = true
        let pid = child
        lock.lock()
        active = pid
        lock.unlock()

        DispatchQueue.global(qos: .userInitiated).async { [self] in
            var buffer = [UInt8](repeating: 0, count: 16_384)
            var pending = Data()
            var readFailed = false
            // A single pipe read returns the bytes available now, even while the writer is still open.
            while true {
                let count = buffer.withUnsafeMutableBytes { Darwin.read(reader, $0.baseAddress, $0.count) }
                if count == 0 { break }
                if count < 0 {
                    let failure = errno
                    if failure == EINTR { continue }
                    let message = String(cString: strerror(failure))
                    DispatchQueue.main.async { onOutput(["ERROR: Could not read build output. \(message)"]) }
                    readFailed = true
                    cancel()
                    break
                }
                pending.append(contentsOf: buffer.prefix(count))
                let lines = drain(&pending)
                if !lines.isEmpty {
                    DispatchQueue.main.async { onOutput(lines) }
                }
            }
            if !pending.isEmpty {
                let tail = String(decoding: pending, as: UTF8.self)
                DispatchQueue.main.async { onOutput([tail]) }
            }
            close(reader)
            var state: Int32 = 0
            var waited = waitpid(pid, &state, 0)
            while waited == -1 && errno == EINTR {
                waited = waitpid(pid, &state, 0)
            }
            lock.lock()
            active = 0
            lock.unlock()
            let signal = state & 0x7f
            let code = readFailed || waited == -1 ? 1 : (signal == 0 ? (state >> 8) & 0xff : 128 + signal)
            DispatchQueue.main.async { onExit(code) }
        }
    }

    func cancel() {
        lock.lock()
        let pid = active
        if pid > 0 { kill(-pid, SIGTERM) }
        lock.unlock()
        guard pid > 0 else { return }
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self else { return }
            lock.lock()
            if active == pid { kill(-pid, SIGKILL) }
            lock.unlock()
        }
    }

    private func drain(_ pending: inout Data) -> [String] {
        // Keep any split UTF-8 sequence with the rest of its line.
        guard let end = pending.lastIndex(where: { $0 == 10 || $0 == 13 }) else {
            // Bound memory for tools that emit long unterminated messages.
            if pending.count <= 256 * 1024 { return [] }
            let line = String(decoding: pending, as: UTF8.self)
            pending.removeAll(keepingCapacity: true)
            return [line]
        }
        let lines = pending[...end].split(whereSeparator: { $0 == 10 || $0 == 13 })
            .map { String(decoding: $0, as: UTF8.self) }
        pending.removeSubrange(...end)
        return lines
    }

    private func require(_ status: Int32) throws {
        if status != 0 { throw SpawnError(code: status) }
    }
}
