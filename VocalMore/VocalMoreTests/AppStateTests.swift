import XCTest
@testable import VocalMore

final class AppStateTests: XCTestCase {

    @MainActor
    func testDefaultValues() {
        let state = AppState()

        XCTAssertFalse(state.backendConnected)
        XCTAssertFalse(state.hasLoadedInitialConfig)
        XCTAssertEqual(state.modeState, .idle)
        XCTAssertEqual(state.currentMode, "walkie_talkie")
        XCTAssertTrue(state.enablePolish)
        XCTAssertTrue(state.autoPaste)
        XCTAssertFalse(state.polishStructured)
        XCTAssertNil(state.inputDevice)
        XCTAssertEqual(state.activeHotkeys, ["fn", "double_cmd"])
        XCTAssertTrue(state.dictionaryEntries.isEmpty)
        XCTAssertTrue(state.availableDevices.isEmpty)
        XCTAssertEqual(state.currentAudioLevel, 0.0)
    }

    @MainActor
    func testUpdateFromConfig() {
        let state = AppState()
        XCTAssertFalse(state.hasLoadedInitialConfig)

        let config: [String: Any] = [
            "default_mode": "realtime_long",
            "enable_polish": false,
            "auto_paste": false,
            "audio": [
                "input_device": "USB Mic",
                "gain": 3.0,
                "noise_gate": 0.01,
            ],
            "asr": [
                "backend": "short_file",
                "language": "en",
            ],
            "llm": [
                "temperature": 0.5,
                "enable_thinking": true,
                "polish_mode": "always",
                "level": "strong",
                "structured": true,
                "tone": "gentle",
                "persona": "technical",
            ],
            "hotkey": [
                "active_hotkeys": ["fn"],
                "double_tap_threshold": 0.4,
            ],
        ]

        state.updateFromConfig(config)

        XCTAssertTrue(state.hasLoadedInitialConfig)
        XCTAssertEqual(state.currentMode, "realtime_long")
        XCTAssertFalse(state.enablePolish)
        XCTAssertFalse(state.autoPaste)
        XCTAssertEqual(state.inputDevice, "USB Mic")
        XCTAssertEqual(state.audioGain, 3.0)
        XCTAssertEqual(state.noiseGate, 0.01)
        XCTAssertEqual(state.asrBackend, "short_file")
        XCTAssertEqual(state.asrLanguage, "en")
        XCTAssertEqual(state.llmTemperature, 0.5)
        XCTAssertTrue(state.llmEnableThinking)
        XCTAssertEqual(state.polishMode, "always")
        XCTAssertEqual(state.polishLevel, "strong")
        XCTAssertTrue(state.polishStructured)
        XCTAssertEqual(state.polishTone, "gentle")
        XCTAssertEqual(state.polishPersona, "technical")
        XCTAssertEqual(state.activeHotkeys, ["fn"])
        XCTAssertEqual(state.doubleTapThreshold, 0.4)
    }

    @MainActor
    func testHasLoadedInitialConfigStartsFalseAndBecomesTrue() {
        let state = AppState()
        XCTAssertFalse(state.hasLoadedInitialConfig, "Should start false before any config is loaded")

        // Even a minimal/empty config should mark it as loaded
        state.updateFromConfig([:])
        XCTAssertTrue(state.hasLoadedInitialConfig, "Should become true after updateFromConfig")
    }

    @MainActor
    func testPolishDimensionsDefaultValues() {
        let state = AppState()

        XCTAssertEqual(state.polishLevel, "minimal")
        XCTAssertFalse(state.polishStructured)
        XCTAssertEqual(state.polishTone, "neutral")
        XCTAssertEqual(state.polishPersona, "default")

        state.updateFromConfig(["llm": [:]])

        XCTAssertEqual(state.polishLevel, "minimal")
        XCTAssertFalse(state.polishStructured)
        XCTAssertEqual(state.polishTone, "neutral")
        XCTAssertEqual(state.polishPersona, "default")
    }

    @MainActor
    func testModelCatalogDefaultsEmpty() {
        let state = AppState()
        XCTAssertTrue(state.llmModelCatalog.isEmpty)
        XCTAssertTrue(state.asrModelCatalog.isEmpty)
    }

    @MainActor
    func testInlinePolishCapabilityTracksSelectedASRModel() {
        let state = AppState()
        state.asrModelCatalog = [
            [
                "id": "qwen3.5-omni-plus-realtime",
                "handles_inline_polish": true,
            ],
            [
                "id": "qwen3-asr-flash-realtime-2026-02-10",
                "handles_inline_polish": false,
            ],
        ]

        state.asrModel = "qwen3-asr-flash-realtime-2026-02-10"
        XCTAssertFalse(state.selectedASRHandlesInlinePolish)

        state.asrModel = "qwen3.5-omni-plus-realtime"
        XCTAssertTrue(state.selectedASRHandlesInlinePolish)
    }

    @MainActor
    func testModeStateRawValues() {
        XCTAssertEqual(ModeState(rawValue: "idle"), .idle)
        XCTAssertEqual(ModeState(rawValue: "recording"), .recording)
        XCTAssertEqual(ModeState(rawValue: "processing"), .processing)
        XCTAssertNil(ModeState(rawValue: "unknown"))
    }

    func testAudioDeviceCodable() throws {
        let device = AudioDevice(index: 0, name: "Built-in", isDefault: true)
        let data = try JSONEncoder().encode(device)
        let decoded = try JSONDecoder().decode(AudioDevice.self, from: data)
        XCTAssertEqual(decoded.name, "Built-in")
        XCTAssertTrue(decoded.isDefault)
        XCTAssertEqual(decoded.id, 0)
    }

    func testDictEntryCodable() throws {
        let entry = DictEntry(term: "Claude", aliases: ["可劳德"])
        let data = try JSONEncoder().encode(entry)
        let decoded = try JSONDecoder().decode(DictEntry.self, from: data)
        XCTAssertEqual(decoded.term, "Claude")
        XCTAssertEqual(decoded.aliases, ["可劳德"])
        XCTAssertEqual(decoded.id, "Claude")
    }
}
