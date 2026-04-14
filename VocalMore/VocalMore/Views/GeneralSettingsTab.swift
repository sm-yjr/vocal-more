import ServiceManagement
import SwiftUI

struct GeneralSettingsTab: View {
    @Environment(AppState.self) private var appState
    @State private var apiKeyInput = ""
    @State private var apiKeySaved = false
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        @Bindable var state = appState

        Form {
            Section("API") {
                VStack(alignment: .leading, spacing: 8) {
                    SecureField(
                        "sk-...",
                        text: $apiKeyInput,
                        prompt: Text(appState.apiKeyConfigured ? "••••••••••••••••••••••••" : "sk-...")
                    )
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: apiKeyInput) { _, newValue in
                        let trimmed = newValue.trimmingCharacters(in: .whitespaces)
                        guard !trimmed.isEmpty else { return }
                        saveApiKey(trimmed)
                    }

                    HStack {
                        if appState.apiKeyConfigured {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                            Text(apiKeySaved ? "Saved" : "Configured")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                            Text("Required for voice recognition")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Text("Get your API key from [DashScope Console](https://dashscope.console.aliyun.com/apiKey)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Behavior") {
                Picker(selection: $state.currentMode) {
                    Text("Walkie-Talkie").tag("walkie_talkie")
                    Text("Real-time Long").tag("realtime_long")
                } label: {
                    VStack(alignment: .leading) {
                        Text("Default Mode")
                        Text("Recording mode on launch")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.currentMode) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("default_mode", value: newValue)
                }

                Toggle("Auto Paste", isOn: $state.autoPaste)
                    .onChange(of: appState.autoPaste) { _, newValue in
                        guard appState.hasLoadedInitialConfig else { return }
                        sendConfig("auto_paste", value: newValue)
                    }

                Toggle(isOn: $launchAtLogin) {
                    VStack(alignment: .leading) {
                        Text("Launch at Login")
                        Text("Start Vocal-More when you log in")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: launchAtLogin) { _, newValue in
                    do {
                        if newValue {
                            try SMAppService.mainApp.register()
                        } else {
                            try SMAppService.mainApp.unregister()
                        }
                    } catch {
                        launchAtLogin = !newValue // revert on failure
                    }
                }
            }
            Section {
                HStack {
                    Spacer()
                    Text("Vocal-More \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Spacer()
                }
            }
        }
        .formStyle(.grouped)
    }

    private func saveApiKey(_ key: String) {
        sendConfig("api_key", value: key)
        appState.apiKeyConfigured = true
        apiKeySaved = true
    }

    private func sendConfig(_ key: String, value: Any) {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        Task {
            await appDelegate.sendConfigChange(key: key, value: value)
        }
    }
}
