import SwiftUI

struct AudioSettingsTab: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var state = appState

        Form {
            Section("Input") {
                Picker("Input Device", selection: $state.inputDevice) {
                    Text("System Default").tag(nil as String?)
                    ForEach(appState.availableDevices) { device in
                        Text(device.isDefault ? "\(device.name) (\(NSLocalizedString("default", comment: "")))" : device.name)
                            .tag(device.name as String?)
                    }
                }
                .onChange(of: appState.inputDevice) { _, newValue in
                    setDevice(newValue)
                }
            }

            Section("Processing") {
                LabeledContent {
                    HStack {
                        Slider(value: $state.audioGain, in: 0.5...4.0, step: 0.1)
                            .frame(width: 120)
                        Text(String(format: "%.1fx", appState.audioGain))
                            .frame(width: 40, alignment: .trailing)
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                } label: {
                    VStack(alignment: .leading) {
                        Text("Software Gain")
                        Text("Amplify input signal")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.audioGain) { _, newValue in
                    sendConfig("audio.gain", value: newValue)
                }

                LabeledContent {
                    HStack {
                        Slider(value: $state.noiseGate, in: 0...0.05, step: 0.001)
                            .frame(width: 120)
                        Text(String(format: "%.3f", appState.noiseGate))
                            .frame(width: 40, alignment: .trailing)
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                } label: {
                    VStack(alignment: .leading) {
                        Text("Noise Gate")
                        Text("RMS threshold to suppress background noise")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.noiseGate) { _, newValue in
                    sendConfig("audio.noise_gate", value: newValue)
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { refreshDevices() }
        .onChange(of: appState.backendConnected) { _, connected in
            if connected { refreshDevices() }
        }
    }

    private func setDevice(_ name: String?) {
        guard let appDelegate = AppDelegate.shared else { return }
        Task {
            let params: [String: Any] = name.map { ["device": $0] } ?? ["device": NSNull()]
            _ = try? await appDelegate.backend.sendRequest(method: "set_device", params: params)
        }
    }

    private func refreshDevices() {
        guard let appDelegate = AppDelegate.shared else { return }
        Task {
            await appDelegate.refreshAvailableDevices()
        }
    }

    private func sendConfig(_ key: String, value: Any) {
        guard let appDelegate = AppDelegate.shared else { return }
        Task {
            _ = try? await appDelegate.backend.sendRequest(
                method: "set_config",
                params: ["key": key, "value": value]
            )
        }
    }
}
