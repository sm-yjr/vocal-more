import Foundation
import Observation

enum ModeState: String {
    case idle
    case recording
    case processing
}

struct CustomHotkeyDef: Codable, Equatable {
    let keyCode: UInt16
    let displayName: String
    let isModifier: Bool
    let flagMask: UInt

    enum CodingKeys: String, CodingKey {
        case keyCode = "key_code"
        case displayName = "display_name"
        case isModifier = "is_modifier"
        case flagMask = "flag_mask"
    }
}

struct AudioDevice: Identifiable, Codable, Equatable {
    let index: Int
    let name: String
    let isDefault: Bool

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index
        case name
        case isDefault = "is_default"
    }
}

struct DictEntry: Identifiable, Codable, Equatable {
    let term: String
    var aliases: [String]

    var id: String { term }
}

@Observable @MainActor
final class AppState {
    // Backend connection
    var backendConnected = false
    var apiKeyConfigured = false
    var hasLoadedInitialConfig = false

    // Mode state
    var modeState: ModeState = .idle

    // Configuration (synced with Python config.yaml)
    var currentMode: String = "walkie_talkie"
    var enablePolish: Bool = true
    var autoPaste: Bool = true
    var inputDevice: String? = nil
    var activeHotkeys: [String] = ["fn", "double_cmd"]

    // Audio
    var audioGain: Double = 2.0
    var noiseGate: Double = 0.005

    // ASR
    var asrBackend: String = "realtime_ws"
    var asrModel: String = "qwen3-asr-flash-realtime-2026-02-10"
    var asrLanguage: String = "zh"

    // LLM
    var llmModel: String = "qwen3.5-plus"
    var llmTemperature: Double = 0.0
    var llmEnableThinking: Bool = false
    var polishMode: String = "smart"
    var polishLevel: String = "minimal"
    var polishStructured: Bool = false
    var polishTone: String = "neutral"
    var polishPersona: String = "default"

    // Hotkey
    var doubleTapThreshold: Double = 0.3
    var customHotkey: CustomHotkeyDef? = nil

    // Model catalogs (from backend initialize response)
    var llmModelCatalog: [[String: Any]] = []
    var asrModelCatalog: [[String: Any]] = []

    var selectedASRHandlesInlinePolish: Bool {
        if let entry = asrModelCatalog.first(where: { ($0["id"] as? String) == asrModel }) {
            return entry["handles_inline_polish"] as? Bool ?? false
        }
        return asrModel == "qwen3.5-omni-plus-realtime"
    }

    // Dictionary
    var dictionaryEntries: [DictEntry] = []

    // Devices
    var availableDevices: [AudioDevice] = []

    // Audio level for capsule waveform
    var currentAudioLevel: Float = 0.0

    // Results
    var lastResult: String? = nil
    var lastError: String? = nil

    // Update from config dict received from backend
    func updateFromConfig(_ config: [String: Any]) {
        if let apiKey = config["api_key"] as? String {
            apiKeyConfigured = !apiKey.isEmpty
        }
        if let mode = config["default_mode"] as? String {
            currentMode = mode
        }
        if let polish = config["enable_polish"] as? Bool {
            enablePolish = polish
        }
        if let paste = config["auto_paste"] as? Bool {
            autoPaste = paste
        }

        if let audio = config["audio"] as? [String: Any] {
            inputDevice = audio["input_device"] as? String
            audioGain = audio["gain"] as? Double ?? 2.0
            noiseGate = audio["noise_gate"] as? Double ?? 0.005
        }

        if let asr = config["asr"] as? [String: Any] {
            asrBackend = asr["backend"] as? String ?? "realtime_ws"
            asrModel = asr["model"] as? String ?? "qwen3-asr-flash-realtime-2026-02-10"
            asrLanguage = asr["language"] as? String ?? "zh"
        }

        if let llm = config["llm"] as? [String: Any] {
            llmModel = llm["model"] as? String ?? "qwen3.5-plus"
            llmTemperature = llm["temperature"] as? Double ?? 0.0
            llmEnableThinking = llm["enable_thinking"] as? Bool ?? false
            polishMode = llm["polish_mode"] as? String ?? "smart"
            polishLevel = llm["level"] as? String ?? "minimal"
            polishStructured = llm["structured"] as? Bool ?? false
            polishTone = llm["tone"] as? String ?? "neutral"
            polishPersona = llm["persona"] as? String ?? "default"
        }

        if let hotkey = config["hotkey"] as? [String: Any] {
            activeHotkeys = hotkey["active_hotkeys"] as? [String] ?? ["fn", "double_cmd"]
            doubleTapThreshold = hotkey["double_tap_threshold"] as? Double ?? 0.3

            if let ck = hotkey["custom_key"] as? [String: Any],
               let keyCode = ck["key_code"] as? Int,
               let displayName = ck["display_name"] as? String,
               let isModifier = ck["is_modifier"] as? Bool,
               let flagMask = ck["flag_mask"] as? Int {
                customHotkey = CustomHotkeyDef(
                    keyCode: UInt16(keyCode),
                    displayName: displayName,
                    isModifier: isModifier,
                    flagMask: UInt(flagMask)
                )
            } else {
                customHotkey = nil
            }
        }

        hasLoadedInitialConfig = true
    }
}
