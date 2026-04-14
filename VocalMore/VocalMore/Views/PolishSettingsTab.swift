import SwiftUI

private extension [String: Any] {
    var modelID: String { self["id"] as? String ?? "" }
    var displayName: String { self["display_name"] as? String ?? modelID }
    var supportsThinking: Bool { self["supports_thinking"] as? Bool ?? true }
}

struct PolishSettingsTab: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState

        Form {
            Section("Text Polish") {
                Toggle("Enable Text Polish", isOn: $state.enablePolish)
                    .onChange(of: appState.enablePolish) { _, newValue in
                        guard appState.hasLoadedInitialConfig else { return }
                        sendConfig("enable_polish", value: newValue)
                    }

                Picker(selection: $state.polishMode) {
                    Text("Smart").tag("smart")
                    Text("Always").tag("always")
                } label: {
                    VStack(alignment: .leading) {
                        Text("Polish Mode")
                        Text("Smart only polishes when needed")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.polishMode) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.polish_mode", value: newValue)
                }
                .disabled(!appState.enablePolish)

                Picker(selection: $state.polishLevel) {
                    Text("Minimal").tag("minimal")
                    Text("Balanced").tag("balanced")
                    Text("Strong").tag("strong")
                } label: {
                    VStack(alignment: .leading) {
                        Text("Polish Level")
                        Text("Choose how strongly the text should be rewritten")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.polishLevel) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.level", value: newValue)
                }
                .disabled(!appState.enablePolish)

                Toggle(isOn: $state.polishStructured) {
                    VStack(alignment: .leading) {
                        Text("Structured Output")
                        Text("Format with headings, lists, and quotes when content has structure")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.polishStructured) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.structured", value: newValue)
                }
                .disabled(!appState.enablePolish)

                Picker(selection: $state.polishTone) {
                    Text("Neutral").tag("neutral")
                    Text("Gentle").tag("gentle")
                    Text("Direct").tag("direct")
                } label: {
                    VStack(alignment: .leading) {
                        Text("Tone")
                        Text("Choose how soft or direct the phrasing should feel")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.polishTone) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.tone", value: newValue)
                }
                .disabled(!appState.enablePolish)

                Picker(selection: $state.polishPersona) {
                    Text("Default").tag("default")
                    Text("Technical").tag("technical")
                    Text("Bilingual").tag("bilingual")
                    Text("Professional").tag("professional")
                    Text("Chat").tag("chat")
                } label: {
                    VStack(alignment: .leading) {
                        Text("Persona")
                        Text("Choose the writing identity for the rewrite")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.polishPersona) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.persona", value: newValue)
                }
                .disabled(!appState.enablePolish)
            }

            Section("LLM") {
                if appState.llmModelCatalog.isEmpty {
                    LabeledContent("Model") {
                        Text(appState.llmModel)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Picker(selection: $state.llmModel) {
                        ForEach(appState.llmModelCatalog, id: \.modelID) { entry in
                            Text(entry.displayName)
                                .tag(entry.modelID)
                        }
                    } label: {
                        VStack(alignment: .leading) {
                            Text("Model")
                            Text("LLM model for text polishing")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .onChange(of: appState.llmModel) { _, newValue in
                        guard appState.hasLoadedInitialConfig else { return }
                        sendConfig("llm.model", value: newValue)
                    }
                }

                LabeledContent("Temperature") {
                    HStack {
                        Slider(value: $state.llmTemperature, in: 0...1.0, step: 0.1)
                            .frame(width: 120)
                        Text(String(format: "%.1f", appState.llmTemperature))
                            .frame(width: 30, alignment: .trailing)
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.llmTemperature) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.temperature", value: newValue)
                }

                Toggle(isOn: $state.llmEnableThinking) {
                    VStack(alignment: .leading) {
                        Text("Enable Thinking")
                        Text("Chain-of-thought for complex text")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.llmEnableThinking) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("llm.enable_thinking", value: newValue)
                }
                .disabled(!selectedModelSupportsThinking)
            }
            .disabled(appState.selectedASRHandlesInlinePolish)
        }
        .formStyle(.grouped)
    }

    private var selectedModelSupportsThinking: Bool {
        guard let entry = appState.llmModelCatalog.first(where: { $0.modelID == appState.llmModel }) else {
            return true // default to enabled if model not in catalog
        }
        return entry.supportsThinking
    }

    private func sendConfig(_ key: String, value: Any) {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        Task {
            await appDelegate.sendConfigChange(key: key, value: value)
        }
    }
}
