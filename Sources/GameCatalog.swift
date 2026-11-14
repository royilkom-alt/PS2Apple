import SwiftUI

struct GameCatalog: Decodable {
    let schema: Int
    let games: [CatalogGame]
}

struct CatalogGame: Decodable, Identifiable {
    var id: String { editions.first?.serial ?? title }
    let title: String
    let series: String
    let editions: [CatalogEdition]
}

struct CatalogEdition: Decodable, Identifiable {
    var id: String { serial }
    var isTestedPlayable: Bool { testedFPS != nil }
    var testDetails: String? {
        guard let testedFPS else { return nil }
        let fps = testedMinFPS.map { "\($0)–\(testedFPS) FPS" } ?? "\(testedFPS) FPS"
        return [testedDevice, fps].compactMap { $0 }.joined(separator: " · ")
    }
    let serial: String
    let region: String
    let testedFPS: Int?
    let testedMinFPS: Int?
    let testedDevice: String?
    let testedUnstable: Bool?
    let observedGameplay: String?
}

struct GameCatalogView: View {
    let games: [CatalogGame]
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""
    @State private var testedOnly = false

    private var matches: [CatalogGame] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        let matching = games.filter { game in
            let editions = visibleEditions(game)
            guard !editions.isEmpty else { return false }
            return query.isEmpty || game.title.localizedStandardContains(query) ||
                game.series.localizedStandardContains(query) || editions.contains { $0.serial.localizedStandardContains(query) }
        }
        let tested = matching.filter { $0.editions.contains(where: \.isTestedPlayable) }
        return tested + matching.filter { !$0.editions.contains(where: \.isTestedPlayable) }
    }

    private func visibleEditions(_ game: CatalogGame) -> [CatalogEdition] {
        testedOnly ? game.editions.filter(\.isTestedPlayable) : game.editions
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Game Catalog").font(.title2.bold())
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("Choose an ISO in the builder. PS2Apple matches the disc and its boot revision automatically.")
                .font(.callout)
                .foregroundStyle(.secondary)
            HStack {
                TextField("Search title, series or disc serial", text: $search)
                    .textFieldStyle(.roundedBorder)
                Toggle("Tested playable only", isOn: $testedOnly).toggleStyle(.checkbox)
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if matches.isEmpty {
                        Text("No matching games.").foregroundStyle(.secondary).padding(.vertical, 16)
                    }
                    ForEach(matches) { game in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(game.title).font(.headline)
                            ForEach(visibleEditions(game)) { disc in
                                HStack {
                                    Text("\(disc.serial)  ·  \(disc.region)").monospaced()
                                    Spacer()
                                    if let details = disc.testDetails {
                                        VStack(alignment: .trailing, spacing: 2) {
                                            Label("Tested by iSolution (Playable)",
                                                  systemImage: "checkmark.seal.fill")
                                                .foregroundStyle(Color.green)
                                            Text(details).foregroundStyle(.secondary)
                                        }
                                    } else if let result = disc.observedGameplay {
                                        VStack(alignment: .trailing, spacing: 2) {
                                            if disc.testedUnstable == true {
                                                Label("Tested by iSolution (Unstable)",
                                                      systemImage: "exclamationmark.triangle")
                                                    .foregroundStyle(Color.orange)
                                            }
                                            Text(result)
                                                .multilineTextAlignment(.trailing)
                                            if let device = disc.testedDevice {
                                                Text(device).foregroundStyle(.secondary)
                                            }
                                        }
                                    } else {
                                        Text("Not Tested")
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                .font(.caption)
                            }
                        }
                        .padding(.vertical, 8)
                        Divider()
                    }
                }
            }
        }
        .padding(20)
        .frame(width: 570, height: 520)
    }
}
