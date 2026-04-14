import SwiftUI

private extension [String: Any] {
    var modelID: String { self["id"] as? String ?? "" }
    var displayName: String { self["display_name"] as? String ?? modelID }
    var transport: String { self["transport"] as? String ?? "realtime_ws" }
}

struct RecognitionSettingsTab: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState

        Form {
            Section("ASR Engine") {
                if appState.asrModelCatalog.isEmpty {
                    LabeledContent("Model") {
                        Text(appState.asrModel)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Picker(selection: $state.asrModel) {
                        ForEach(appState.asrModelCatalog, id: \.modelID) { entry in
                            Text(entry.displayName)
                                .tag(entry.modelID)
                        }
                    } label: {
                        VStack(alignment: .leading) {
                            Text("Model")
                            Text("ASR model for speech recognition")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .onChange(of: appState.asrModel) { _, newValue in
                        guard appState.hasLoadedInitialConfig else { return }
                        sendConfig("asr.model", value: newValue)
                    }
                }

                LabeledContent("Backend") {
                    Text(selectedTransportLabel)
                        .foregroundStyle(.secondary)
                }

                Picker("Language", selection: $state.asrLanguage) {
                    Text("Chinese (zh)").tag("zh")
                    Text("English (en)").tag("en")
                }
                .onChange(of: appState.asrLanguage) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("asr.language", value: newValue)
                }
            }

            Section("Enhancement") {
                LabeledContent {
                    Text("Enabled")
                        .foregroundStyle(.secondary)
                } label: {
                    VStack(alignment: .leading) {
                        Text("Dictionary Corpus")
                        Text("Custom terms from Dictionary tab improve recognition")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var selectedTransportLabel: String {
        if let entry = appState.asrModelCatalog.first(where: { $0.modelID == appState.asrModel }) {
            switch entry.transport {
            case "realtime_ws": return "Realtime WebSocket"
            case "short_file": return "Short File Upload"
            default: return entry.transport
            }
        }
        switch appState.asrBackend {
        case "realtime_ws": return "Realtime WebSocket"
        case "short_file": return "Short File Upload"
        default: return appState.asrBackend
        }
    }

    private func sendConfig(_ key: String, value: Any) {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        Task {
            await appDelegate.sendConfigChange(key: key, value: value)
        }
    }
}
