import AppKit
import SwiftUI

@MainActor
struct ContentView: View {
    @ObservedObject var model: BuilderModel
    @Environment(\.openURL) private var openURL
    @State private var showUpdates = false
    @State private var showCatalog = false
    @State private var followLog = true

    private let mint = Color(red: 0.40, green: 0.89, blue: 0.75)
    private let canvas = Color(red: 0.070, green: 0.082, blue: 0.106)
    private let rail = Color(red: 0.095, green: 0.108, blue: 0.137)
    private let surface = Color(red: 0.113, green: 0.130, blue: 0.161)
    private let line = Color.white.opacity(0.085)

    private struct ToolItem: Identifiable {
        let id: String
        let name: String
        let symbol: String
    }

    private let tools = [
        ToolItem(id: "arm64", name: "Apple Silicon", symbol: "cpu"),
        ToolItem(id: "xcode", name: "Xcode", symbol: "hammer"),
        ToolItem(id: "sdk", name: "iPhoneOS SDK", symbol: "iphone"),
        ToolItem(id: "compiler", name: "ARM64 Compiler", symbol: "checkmark.shield"),
        ToolItem(id: "homebrew", name: "Homebrew", symbol: "shippingbox"),
        ToolItem(id: "tools", name: "Build Tools", symbol: "wrench.and.screwdriver")
    ]

    var body: some View {
        HSplitView {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    buildCard
                    toolchain
                    if let error = model.error {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.callout)
                            .foregroundStyle(.orange)
                            .textSelection(.enabled)
                            .lineLimit(4)
                            .help(error)
                    }
                }
                .padding(22)
                .frame(maxWidth: 1020, alignment: .leading)
                .frame(maxWidth: .infinity)
            }
            .frame(minWidth: 630, idealWidth: 690)

            terminal.frame(minWidth: 300, idealWidth: 360)
        }
        .background(canvas)
        .tint(mint)
    }

    private func socialButton(_ icon: String, label: String, address: String) -> some View {
        Button {
            if let url = URL(string: address) { openURL(url) }
        } label: {
            Group {
                if let url = Bundle.main.url(forResource: icon, withExtension: "png"),
                   let logo = NSImage(contentsOf: url) {
                    Image(nsImage: logo).resizable().scaledToFit()
                } else {
                    Image(systemName: icon == "YouTube" ? "play.rectangle.fill" : "bubble.left.and.bubble.right.fill")
                }
            }
            .frame(width: 17, height: 17)
            .frame(width: 25, height: 25)
        }
        .buttonStyle(.bordered)
        .accessibilityLabel(label)
        .help(label)
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(nsImage: NSApplication.shared.applicationIconImage)
                .resizable()
                .scaledToFit()
                .frame(width: 44, height: 44)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text("PS2Apple")
                    .font(.system(size: 27, weight: .bold))
                Text("Created by iSolution")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            socialButton("YouTube", label: "iSolution on YouTube", address: "https://www.youtube.com/@iSolutional")
            socialButton("Discord", label: "iSolution Discord", address: "https://discord.gg/e58k5bkja3")
            Button {
                showCatalog = true
            } label: {
                Label("Game Catalog", systemImage: "rectangle.grid.1x2")
            }
            .sheet(isPresented: $showCatalog) {
                GameCatalogView(games: model.catalog)
            }
            Button {
                showUpdates.toggle()
            } label: {
                Label("Updates", systemImage: "arrow.clockwise.circle")
            }
            .popover(isPresented: $showUpdates, arrowEdge: .bottom) { updatesPanel }
            .disabled(model.isRunning)
        }
    }

    private var buildCard: some View {
        card {
            VStack(alignment: .leading, spacing: 13) {
                if let title = model.discTitle, let serial = model.discSerial {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(title).font(.system(size: 17, weight: .semibold))
                        Text([model.discRegion, serial].compactMap { $0 }.joined(separator: "  ·  "))
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    Divider()
                }
                fileRow("PS2 Disc Image", icon: "opticaldisc", value: model.iso?.lastPathComponent ?? "No ISO selected",
                        detail: model.discDetail, detailColor: model.discVerified ? mint : .secondary,
                        button: "Choose ISO…", action: model.chooseISO)
                if let note = model.discNote {
                    Label(note, systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.orange)
                }
                fileRow("IPA Output", icon: "folder", value: model.output.lastPathComponent,
                        detail: model.output.path, detailColor: .secondary,
                        button: "Choose Folder…", action: model.chooseOutput)
                if let result = model.ipa {
                    HStack(spacing: 9) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(mint)
                        Text(result.lastPathComponent)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .font(.system(size: 11, design: .monospaced))
                            .help(result.path)
                        Spacer()
                        Button("Reveal IPA", action: model.revealIPA)
                    }
                    .padding(9)
                    .background(mint.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                }
                if let boot = model.bootCheck {
                    Label(boot.detail, systemImage: boot.ok ? "rectangle.on.rectangle" : "exclamationmark.triangle")
                        .font(.system(size: 11))
                        .foregroundStyle(boot.ok ? Color.secondary : Color.orange)
                }
                Divider()
                HStack(spacing: 9) {
                    Button(action: model.build) {
                        Label(model.discChannel?.isTestBuild == true ? "Build Test IPA" : "Build Native IPA",
                              systemImage: "hammer.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .tint(mint)
                    .foregroundStyle(Color.black)
                    .disabled(!model.canBuild)
                    if model.isRunning {
                        Button("Cancel", role: .destructive, action: model.cancel)
                            .controlSize(.large)
                            .disabled(model.cancelling)
                    }
                    Spacer()
                    if model.discVerified, let channel = model.discChannel {
                        Text(channel.label)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func fileRow(_ title: String, icon: String, value: String, detail: String, detailColor: Color,
                         button: String, action: @escaping () -> Void) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(mint)
                .frame(width: 28, height: 28)
                .background(mint.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.secondary)
                Text(value).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                Text(detail)
                    .font(.system(size: 10))
                    .foregroundStyle(detailColor)
                    .lineLimit(1)
                    .help(detail)
            }
            Spacer(minLength: 8)
            Button(button, action: action).disabled(model.isRunning)
        }
    }

    private var toolchain: some View {
        card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Build Environment").font(.system(size: 15, weight: .semibold))
                        Text("\(model.toolCount) of \(tools.count) checks passed")
                            .font(.system(size: 10))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Check Again", action: model.check)
                        .disabled(model.isRunning)
                    Button(action: model.install) {
                        Label("Install Everything", systemImage: "arrow.down.to.line")
                    }
                    .disabled(model.isRunning)
                }
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 3), spacing: 7) {
                    ForEach(tools) { item in
                        toolRow(item.name, icon: item.symbol, status: model.checks[item.id])
                    }
                }
                HStack(spacing: 10) {
                    Button("Open Setup in Terminal", action: model.openTerminalSetup)
                        .buttonStyle(.borderless)
                        .disabled(model.isRunning)
                    Link("Get Xcode", destination: URL(string: "https://apps.apple.com/app/xcode/id497799835")!)
                    Spacer()
                }
                .font(.system(size: 10))
            }
        }
    }

    private func toolRow(_ name: String, icon: String, status: ToolStatus?) -> some View {
        let color: Color = status.map { $0.ok ? mint : .orange } ?? .secondary
        return HStack(spacing: 7) {
            Image(systemName: icon)
                .font(.system(size: 11))
                .foregroundStyle(color)
                .frame(width: 17)
            VStack(alignment: .leading, spacing: 3) {
                Text(name).font(.system(size: 11, weight: .medium)).lineLimit(1)
                Text(status?.detail ?? "Pending")
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: status.map { $0.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill" } ?? "circle")
                .foregroundStyle(color)
                .font(.system(size: 9))
        }
        .padding(7)
        .background(.black.opacity(0.14), in: RoundedRectangle(cornerRadius: 7))
        .help(status?.detail ?? "Pending")
    }

    private var terminal: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "terminal")
                    .foregroundStyle(mint)
                Text("Build Log").font(.system(size: 12, weight: .semibold))
                Spacer()
                Toggle("Follow", isOn: $followLog)
                    .toggleStyle(.checkbox)
                Button("Copy", action: model.copyLog).disabled(model.logs.isEmpty)
                Button("Reveal Log", action: model.revealLog).disabled(model.logFile == nil)
            }
            .font(.system(size: 10))
            .padding(.horizontal, 15)
            .padding(.vertical, 8)
            .background(rail)

            if model.isRunning {
                Group {
                    if let boot = model.bootProgress {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("Booting from SYSTEM.CNF")
                                Spacer()
                                if boot.current > 0 {
                                    Text("\(boot.current.formatted()) / \(boot.total.formatted()) frames")
                                        .monospacedDigit()
                                }
                                TimelineView(.periodic(from: boot.startedAt, by: 1)) { context in
                                    let seconds = max(0, Int(context.date.timeIntervalSince(boot.startedAt)))
                                    Text(Duration.seconds(seconds).formatted(.time(pattern: .minuteSecond)))
                                        .monospacedDigit()
                                        .accessibilityLabel("Elapsed time")
                                }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            if boot.current == 0 {
                                ProgressView()
                                    .accessibilityLabel("Starting the game boot check")
                            } else {
                                ProgressView(value: boot.fraction)
                                    .accessibilityLabel("Game boot check")
                            }
                        }
                    } else if let value = model.progress {
                        ProgressView(value: value)
                    } else {
                        ProgressView()
                    }
                }
                .progressViewStyle(.linear)
                .padding(.horizontal, 15)
                .padding(.top, 5)
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 3) {
                        if model.logs.isEmpty {
                            Text("Build output and diagnostics appear here.").foregroundStyle(.secondary)
                        }
                        ForEach(model.logs) { entry in
                            Text(entry.text)
                                .foregroundStyle(entry.text.hasPrefix("ERROR") ? .orange : .white.opacity(0.80))
                                .id(entry.id)
                                .textSelection(.enabled)
                        }
                        Color.clear.frame(height: 1).id("log-end")
                    }
                    .font(.system(size: 11, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                }
                .onChange(of: model.logs.last?.id) { _ in
                    if followLog { proxy.scrollTo("log-end", anchor: .bottom) }
                }
                .onChange(of: followLog) { enabled in
                    if enabled { proxy.scrollTo("log-end", anchor: .bottom) }
                }
            }
            HStack {
                Circle().fill(model.error == nil ? mint : .orange).frame(width: 6, height: 6)
                Text(model.status).lineLimit(1)
                Spacer()
                if let value = model.progress {
                    Text("\(Int(value * 100))%").monospacedDigit()
                }
            }
            .font(.system(size: 10))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 15)
            .padding(.vertical, 6)
            .background(rail)
        }
        .background(.black.opacity(0.28))
    }

    private var updatesPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("PS2Apple \(model.version)").font(.headline)
            Text("Check for a newer release of PS2Apple.")
                .font(.callout)
                .foregroundStyle(.secondary)
            TextField("Builder release repository (OWNER/REPO)", text: $model.releaseRepository)
                .textFieldStyle(.roundedBorder)
            ForEach(model.updates) { update in
                if let url = update.url {
                    Link(update.detail, destination: url).font(.callout)
                } else {
                    Text(update.detail).font(.callout).foregroundStyle(.orange)
                }
            }
            Button("Check Now", action: model.checkUpdates)
                .disabled(model.isRunning)
        }
        .padding(16)
        .frame(width: 355)
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .padding(15)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(line))
    }
}
