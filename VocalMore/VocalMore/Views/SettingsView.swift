import SwiftUI

enum SettingsTab: String, CaseIterable, Identifiable, Hashable {
    case general = "General"
    case audio = "Audio"
    case recognition = "Recognition"
    case polish = "Polish"
    case shortcuts = "Shortcuts"
    case dictionary = "Dictionary"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .general: return "gearshape.fill"
        case .audio: return "mic.fill"
        case .recognition: return "text.viewfinder"
        case .polish: return "sparkles"
        case .shortcuts: return "keyboard.fill"
        case .dictionary: return "book.fill"
        }
    }

    var localizedName: LocalizedStringKey {
        LocalizedStringKey(rawValue)
    }

    var iconColor: Color {
        switch self {
        case .general: return .secondary
        case .audio: return .orange
        case .recognition: return .blue
        case .polish: return .purple
        case .shortcuts: return .red
        case .dictionary: return .green
        }
    }
}

struct SettingsView: View {
    let backend: PythonBackend
    let refreshDictionaryEntries: @MainActor () async -> Void

    @Environment(AppState.self) private var appState
    @State private var selectedTab: SettingsTab? = .general

    var body: some View {
        NavigationSplitView {
            List(SettingsTab.allCases, selection: $selectedTab) { tab in
                Label {
                    Text(tab.localizedName)
                } icon: {
                    Image(systemName: tab.icon)
                        .foregroundStyle(tab.iconColor)
                }
                .tag(tab)
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 160, ideal: 180, max: 200)
        } detail: {
            Group {
                switch selectedTab ?? .general {
                case .general:
                    GeneralSettingsTab()
                case .audio:
                    AudioSettingsTab()
                case .recognition:
                    RecognitionSettingsTab()
                case .polish:
                    PolishSettingsTab()
                case .shortcuts:
                    ShortcutsSettingsTab()
                case .dictionary:
                    DictionarySettingsTab(
                        backend: backend,
                        refreshDictionaryEntries: refreshDictionaryEntries
                    )
                }
            }
            .environment(appState)
        }
        .scrollEdgeEffectStyle(.soft, for: .all)
        .frame(width: 680, height: 480)
    }
}
