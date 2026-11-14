import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: BuilderModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model, model.isRunning else { return .terminateNow }
        model.requestQuit { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
}

@main
@MainActor
struct PS2AppleApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = BuilderModel()

    var body: some Scene {
        Window("PS2Apple", id: "builder") {
            ContentView(model: model)
                .frame(minWidth: 980, minHeight: 610)
                .preferredColorScheme(.dark)
                .onAppear {
                    delegate.model = model
                    model.start()
                }
        }
        .defaultSize(width: 1100, height: 720)
    }
}
