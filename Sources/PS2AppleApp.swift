import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: BuilderModel?
    weak var updates: DesktopUpdates?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, model.isRunning else {
            updates?.finishOnQuit()
            return .terminateNow
        }
        model.requestQuit { [weak self] in
            self?.updates?.finishOnQuit()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
@MainActor
enum PS2AppleEntry {
    static func main() {
        let args = CommandLine.arguments
        if args.count == 5, args[1] == "--apply-update", let parent = Int32(args[2]),
           args[4] == "restart" || args[4] == "quit" {
            let target = URL(fileURLWithPath: args[3], isDirectory: true)
            let restart = args[4] == "restart"
            do {
                try PreparedMacUpdate.apply(parent: parent, target: target, restart: restart)
                exit(0)
            } catch {
                PreparedMacUpdate.report(error, target: target, restart: restart)
                exit(1)
            }
        }
        PS2AppleApp.main()
    }
}

@MainActor
struct PS2AppleApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = BuilderModel()
    @StateObject private var updates = DesktopUpdates()

    var body: some Scene {
        Window("PS2Apple", id: "builder") {
            ContentView(model: model, desktopUpdates: updates)
                .frame(minWidth: 980, minHeight: 610)
                .preferredColorScheme(.dark)
                .onAppear {
                    delegate.model = model
                    delegate.updates = updates
                    model.start()
                    updates.start()
                }
        }
        .defaultSize(width: 1100, height: 720)
    }
}
