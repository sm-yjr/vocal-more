import SwiftUI

struct ShortcutsSettingsTab: View {
    @Environment(AppState.self) private var appState
    @State private var isRecordingCustomKey = false
    @State private var keyMonitor: Any?

    private let hotkeyLabels: [(key: String, label: String, description: String)] = [
        ("fn", "Fn Key", "Hold (walkie-talkie) or toggle (real-time)"),
        ("double_cmd", "Double \u{2318}", "Tap Command twice quickly"),
        ("f13", "F13 (PrintScreen)", ""),
        ("f14", "F14", ""),
        ("f15", "F15", ""),
        ("f16", "F16", ""),
        ("f17", "F17", ""),
        ("f18", "F18", ""),
        ("f19", "F19", ""),
        ("f20", "F20", ""),
    ]

    var body: some View {
        @Bindable var state = appState

        Form {
            Section("Active Hotkeys") {
                ForEach(hotkeyLabels, id: \.key) { hotkey in
                    Toggle(isOn: hotkeyBinding(for: hotkey.key)) {
                        if hotkey.description.isEmpty {
                            Text(hotkey.label)
                        } else {
                            VStack(alignment: .leading) {
                                Text(hotkey.label)
                                Text(hotkey.description)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            Section {
                HStack {
                    VStack(alignment: .leading) {
                        Text("Custom Key")
                        if isRecordingCustomKey {
                            Text("Press any key...")
                                .font(.caption)
                                .foregroundStyle(.orange)
                        } else if let custom = appState.customHotkey {
                            Text(custom.displayName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        } else {
                            Text("None")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Spacer()

                    if isRecordingCustomKey {
                        Button("Cancel") {
                            stopRecording()
                        }
                    } else {
                        if appState.customHotkey != nil {
                            Button("Clear") {
                                clearCustomKey()
                            }
                        }
                        Button("Record Key...") {
                            startRecording()
                        }
                    }
                }
            } header: {
                Text("Custom Key")
            } footer: {
                Text("Record a single key to use as a recording trigger. Key combinations are not supported.")
            }

            Section("Timing") {
                LabeledContent {
                    HStack {
                        Slider(value: $state.doubleTapThreshold, in: 0.15...0.5, step: 0.05)
                            .frame(width: 120)
                        Text(String(format: "%.2fs", appState.doubleTapThreshold))
                            .frame(width: 40, alignment: .trailing)
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                    }
                } label: {
                    VStack(alignment: .leading) {
                        Text("Double-tap Threshold")
                        Text("Maximum interval between taps")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .onChange(of: appState.doubleTapThreshold) { _, newValue in
                    guard appState.hasLoadedInitialConfig else { return }
                    sendConfig("hotkey.double_tap_threshold", value: newValue)
                    updateHotkeyManager()
                }
            }
        }
        .formStyle(.grouped)
        .onDisappear {
            stopRecording()
        }
    }

    // MARK: - Custom Key Recording

    private func startRecording() {
        isRecordingCustomKey = true
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .flagsChanged]) { event in
            let keyCode = event.keyCode
            let isModifier: Bool
            let flagMask: UInt
            let displayName: String

            if event.type == .flagsChanged {
                isModifier = true
                // Determine which modifier was pressed based on keycode
                switch keyCode {
                case 63: displayName = "Fn"; flagMask = 0x80_0000
                case 55, 54: displayName = "Command"; flagMask = 0x10_0000
                case 56, 60: displayName = "Shift"; flagMask = 0x2_0000
                case 58, 61: displayName = "Option"; flagMask = 0x8_0000
                case 59, 62: displayName = "Control"; flagMask = 0x4_0000
                case 57: displayName = "Caps Lock"; flagMask = 0x1_0000
                default: displayName = "Modifier \(keyCode)"; flagMask = 0
                }
            } else {
                isModifier = false
                flagMask = 0
                displayName = Self.displayNameForKeyCode(keyCode)
            }

            let customDef = CustomHotkeyDef(
                keyCode: keyCode,
                displayName: displayName,
                isModifier: isModifier,
                flagMask: UInt(flagMask)
            )

            self.applyCustomKey(customDef)
            self.stopRecording()
            return nil // Consume the event
        }
    }

    private func stopRecording() {
        isRecordingCustomKey = false
        if let monitor = keyMonitor {
            NSEvent.removeMonitor(monitor)
            keyMonitor = nil
        }
    }

    private func clearCustomKey() {
        appState.customHotkey = nil
        if let appDelegate = NSApp.delegate as? AppDelegate {
            appDelegate.hotkeyManager.setCustomKey(nil)
        }
        sendConfig("hotkey.custom_key", value: NSNull())
    }

    private func applyCustomKey(_ def: CustomHotkeyDef) {
        appState.customHotkey = def
        if let appDelegate = NSApp.delegate as? AppDelegate {
            appDelegate.hotkeyManager.setCustomKey(def)
        }
        let dictValue: [String: Any] = [
            "key_code": Int(def.keyCode),
            "display_name": def.displayName,
            "is_modifier": def.isModifier,
            "flag_mask": Int(def.flagMask),
        ]
        sendConfig("hotkey.custom_key", value: dictValue)
    }

    private static func displayNameForKeyCode(_ keyCode: UInt16) -> String {
        // Common key code to display name mapping
        let names: [UInt16: String] = [
            0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X",
            8: "C", 9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R",
            16: "Y", 17: "T", 18: "1", 19: "2", 20: "3", 21: "4", 22: "6",
            23: "5", 24: "=", 25: "9", 26: "7", 27: "-", 28: "8", 29: "0",
            30: "]", 31: "O", 32: "U", 33: "[", 34: "I", 35: "P",
            36: "Return", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";",
            42: "\\", 43: ",", 44: "/", 45: "N", 46: "M", 47: ".",
            48: "Tab", 49: "Space", 50: "`", 51: "Delete", 53: "Escape",
            64: "F17", 65: "Numpad .", 67: "Numpad *", 69: "Numpad +",
            71: "Numpad Clear", 75: "Numpad /", 76: "Numpad Enter",
            78: "Numpad -", 79: "F18", 80: "F19", 81: "Numpad =",
            82: "Numpad 0", 83: "Numpad 1", 84: "Numpad 2", 85: "Numpad 3",
            86: "Numpad 4", 87: "Numpad 5", 88: "Numpad 6", 89: "Numpad 7",
            90: "F20", 91: "Numpad 8", 92: "Numpad 9",
            96: "F5", 97: "F6", 98: "F7", 99: "F3", 100: "F8", 101: "F9",
            103: "F11", 105: "F13", 106: "F16", 107: "F14",
            109: "F10", 111: "F12", 113: "F15", 114: "Help",
            115: "Home", 116: "Page Up", 117: "Forward Delete",
            118: "F4", 119: "End", 120: "F2", 121: "Page Down", 122: "F1",
            123: "Left Arrow", 124: "Right Arrow", 125: "Down Arrow", 126: "Up Arrow",
        ]
        return names[keyCode] ?? "Key \(keyCode)"
    }

    private func hotkeyBinding(for key: String) -> Binding<Bool> {
        Binding(
            get: { appState.activeHotkeys.contains(key) },
            set: { newValue in
                var active = appState.activeHotkeys
                if newValue {
                    if !active.contains(key) {
                        active.append(key)
                    }
                } else {
                    guard active.count > 1 else { return }
                    active.removeAll { $0 == key }
                }
                appState.activeHotkeys = active
                sendActiveHotkeys(active)
                updateHotkeyManager()
            }
        )
    }

    private func sendActiveHotkeys(_ hotkeys: [String]) {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        Task {
            do {
                _ = try await appDelegate.backend.sendRequest(
                    method: "set_active_hotkeys",
                    params: ["hotkeys": hotkeys]
                )
            } catch {
                appState.lastError = "Failed to save hotkeys: \(error.localizedDescription)"
            }
        }
    }

    private func sendConfig(_ key: String, value: Any) {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        Task {
            await appDelegate.sendConfigChange(key: key, value: value)
        }
    }

    private func updateHotkeyManager() {
        guard let appDelegate = NSApp.delegate as? AppDelegate else { return }
        appDelegate.hotkeyManager.setActiveHotkeys(
            appState.activeHotkeys,
            threshold: appState.doubleTapThreshold
        )
    }
}
