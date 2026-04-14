import SwiftUI

struct DictionarySettingsTab: View {
    let backend: PythonBackend
    let refreshDictionaryEntries: @MainActor () async -> Void

    @Environment(AppState.self) private var appState
    @State private var showingAddSheet = false

    var body: some View {
        Form {
            Section("Custom Terms") {
                TagCloudView(entries: appState.dictionaryEntries, onRemove: removeEntry)
                    .padding(.vertical, 4)

                Button {
                    showingAddSheet = true
                } label: {
                    Label("Add Term", systemImage: "plus")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .formStyle(.grouped)
        .sheet(isPresented: $showingAddSheet) {
            AddTermSheet(onAdd: addEntry)
                .environment(appState)
        }
        .onAppear { refreshDictionary() }
        .onChange(of: appState.backendConnected) { _, connected in
            if connected { refreshDictionary() }
        }
    }

    private func removeEntry(_ term: String) {
        Task {
            _ = try? await backend.sendRequest(
                method: "remove_dict_entry",
                params: ["term": term]
            )
            await refreshDictionaryEntries()
        }
    }

    @MainActor
    private func addEntry(term: String, aliases: [String]) async throws {
        var params: [String: Any] = ["term": term]
        if !aliases.isEmpty {
            params["aliases"] = aliases
        }

        _ = try await backend.sendRequest(
            method: "add_dict_entry",
            params: params
        )

        if let index = appState.dictionaryEntries.firstIndex(where: { $0.term == term }) {
            var mergedAliases = Set(appState.dictionaryEntries[index].aliases)
            mergedAliases.formUnion(aliases)
            appState.dictionaryEntries[index].aliases = mergedAliases.sorted()
        } else {
            appState.dictionaryEntries.append(DictEntry(term: term, aliases: aliases))
        }

        await refreshDictionaryEntries()
    }

    private func refreshDictionary() {
        Task {
            await refreshDictionaryEntries()
        }
    }
}

struct TagCloudView: View {
    let entries: [DictEntry]
    let onRemove: (String) -> Void

    var body: some View {
        if entries.isEmpty {
            Text("No dictionary entries yet")
                .foregroundStyle(.secondary)
                .font(.system(size: 13))
        } else {
            GlassEffectContainer(spacing: 6) {
                FlowLayout(spacing: 6) {
                    ForEach(entries) { entry in
                        DictTagView(entry: entry, onRemove: { onRemove(entry.term) })
                    }
                }
            }
        }
    }
}

struct DictTagView: View {
    let entry: DictEntry
    let onRemove: () -> Void
    @State private var isHovered = false

    var body: some View {
        HStack(spacing: 4) {
            Text(entry.term)
                .font(.system(size: 12))

            if !entry.aliases.isEmpty {
                Text("(\(entry.aliases.joined(separator: ", ")))")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }

            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.secondary.opacity(isHovered ? 0.8 : 0.4))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .glassEffect(.regular, in: .capsule)
        .onHover { isHovered = $0 }
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = layout(subviews: subviews, containerWidth: proposal.width ?? .infinity)
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = layout(subviews: subviews, containerWidth: bounds.width)
        for (index, position) in result.positions.enumerated() {
            subviews[index].place(
                at: CGPoint(x: bounds.minX + position.x, y: bounds.minY + position.y),
                proposal: ProposedViewSize(subviews[index].sizeThatFits(.unspecified))
            )
        }
    }

    private func layout(subviews: Subviews, containerWidth: CGFloat) -> (size: CGSize, positions: [CGPoint]) {
        var positions: [CGPoint] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var maxWidth: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > containerWidth && x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            positions.append(CGPoint(x: x, y: y))
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
            maxWidth = max(maxWidth, x - spacing)
        }

        return (CGSize(width: maxWidth, height: y + rowHeight), positions)
    }
}
