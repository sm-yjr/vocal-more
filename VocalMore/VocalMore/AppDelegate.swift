import AppKit
import os
import Observation
import Sparkle

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var shared: AppDelegate?
    let appState = AppState()
    var menuBarController: MenuBarController!
    var backend: PythonBackend!
    var hotkeyManager: HotkeyManager!
    var capsuleController: FloatingCapsuleController!

    private let logger = Logger(subsystem: "com.vocal-more", category: "AppDelegate")
    private var stateObservation: Any?
    private var configObservation: Any?
    private var accessibilityTimer: Timer?
    private var updaterController: SPUStandardUpdaterController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        logger.info("Vocal-More launching...")
        Self.shared = self

        // Setup notifications
        NotificationManager.shared.setup()

        // Initialize backend
        backend = PythonBackend(appState: appState)

        // Initialize menu bar
        menuBarController = MenuBarController(
            appState: appState,
            backend: backend,
            updatesEnabled: sparkleUpdatesEnabled,
            onCheckForUpdates: makeCheckForUpdatesAction()
        )

        // Initialize floating capsule
        capsuleController = FloatingCapsuleController(appState: appState)
        capsuleController.onCancel = { [weak self] in
            guard let self else { return }
            Task {
                _ = try? await self.backend.sendRequest(method: "cancel")
            }
        }
        capsuleController.onFinish = { [weak self] in
            guard let self else { return }
            Task {
                // Toggle off for realtime_long mode
                if self.appState.currentMode == "realtime_long" {
                    _ = try? await self.backend.sendRequest(method: "hotkey_pressed")
                }
            }
        }

        // Initialize hotkey manager
        hotkeyManager = HotkeyManager()
        hotkeyManager.setActiveHotkeys(appState.activeHotkeys, threshold: appState.doubleTapThreshold)

        hotkeyManager.onPressed = { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                // Show capsule when starting from idle
                if self.appState.modeState == .idle {
                    let mode = self.appState.currentMode == "walkie_talkie" ? "pushToTalk" : "handsFree"
                    self.capsuleController.show(mode: mode)
                }
            }
            Task {
                _ = try? await self.backend.sendRequest(method: "hotkey_pressed")
            }
        }

        hotkeyManager.onReleased = { [weak self] in
            guard let self else { return }
            Task {
                _ = try? await self.backend.sendRequest(method: "hotkey_released")
            }
        }

        hotkeyManager.onDoubleCmd = { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                if self.appState.modeState == .idle {
                    let mode = self.appState.currentMode == "walkie_talkie" ? "pushToTalk" : "handsFree"
                    self.capsuleController.show(mode: mode)
                }
            }
            Task {
                _ = try? await self.backend.sendRequest(method: "hotkey_pressed")
            }
        }

        // Set up backend notification handler for capsule/notifications
        backend.onNotification = { [weak self] method, params in
            Task { @MainActor in
                self?.handleBackendNotification(method: method, params: params)
            }
        }

        // Observe state changes
        observeModeState()
        observeConfigurationState()

        // Start backend
        Task {
            do {
                try await backend.start()
                logger.info("Backend started successfully")
                syncConfigurationFromState()
                await refreshAvailableDevices()
                await refreshDictionaryEntries()
            } catch {
                logger.error("Failed to start backend: \(error.localizedDescription)")
                appState.lastError = "Failed to start backend: \(error.localizedDescription)"
                NotificationManager.shared.send(
                    title: "Vocal-More",
                    subtitle: NSLocalizedString("Error", comment: ""),
                    body: "Failed to start backend: \(error.localizedDescription)"
                )
            }
        }

        attemptHotkeyManagerStartIfNeeded(promptForAccessibility: true)
    }

    private func observeModeState() {
        stateObservation = withObservationTracking {
            _ = appState.modeState
        } onChange: { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.menuBarController.updateStatus(for: self.appState.modeState)

                // Update capsule state
                switch self.appState.modeState {
                case .idle:
                    self.capsuleController.updateState("hidden")
                case .recording:
                    // Already shown on hotkey press
                    break
                case .processing:
                    self.capsuleController.updateState("processing")
                }

                self.observeModeState()
            }
        }
    }

    private func observeConfigurationState() {
        withObservationTracking {
            _ = appState.currentMode
            _ = appState.enablePolish
            _ = appState.polishLevel
            _ = appState.polishTone
            _ = appState.polishPersona
            _ = appState.autoPaste
            _ = appState.inputDevice
            _ = appState.activeHotkeys
            _ = appState.doubleTapThreshold
        } onChange: {
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.syncConfigurationFromState()
                self.observeConfigurationState()
            }
        }
    }

    private func syncConfigurationFromState() {
        menuBarController?.refreshFromState()
        hotkeyManager?.setActiveHotkeys(
            appState.activeHotkeys,
            threshold: appState.doubleTapThreshold
        )
        hotkeyManager?.setCustomKey(appState.customHotkey)

        if hotkeyManager?.isRunning == false, hotkeyManager?.checkAccessibility() == true {
            attemptHotkeyManagerStartIfNeeded(promptForAccessibility: false)
        }
    }

    private func attemptHotkeyManagerStartIfNeeded(promptForAccessibility: Bool) {
        guard let hotkeyManager, !hotkeyManager.isRunning else { return }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let started = hotkeyManager.start(promptForAccessibility: promptForAccessibility)
            if !started && promptForAccessibility {
                DispatchQueue.main.async {
                    self.logger.warning("Hotkey manager failed to start - polling for accessibility permission")
                    NotificationManager.shared.send(
                        title: "Vocal-More",
                        subtitle: NSLocalizedString("Accessibility Permission Required", comment: ""),
                        body: NSLocalizedString("Please open System Settings → Privacy & Security → Accessibility, toggle VocalMore OFF then ON again (or remove and re-add it).", comment: "")
                    )
                    self.startAccessibilityPolling()
                }
            }
        }
    }

    private func startAccessibilityPolling() {
        guard accessibilityTimer == nil else { return }
        accessibilityTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            if AXIsProcessTrusted() {
                self.accessibilityTimer?.invalidate()
                self.accessibilityTimer = nil
                self.logger.info("Accessibility permission granted, starting hotkey manager")
                self.attemptHotkeyManagerStartIfNeeded(promptForAccessibility: false)
            }
        }
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        attemptHotkeyManagerStartIfNeeded(promptForAccessibility: false)
    }

    private func handleBackendNotification(method: String, params: [String: Any]) {
        switch method {
        case "final_result":
            if let text = params["text"] as? String {
                let display = text.count > 50 ? String(text.prefix(50)) + "..." : text
                NotificationManager.shared.send(
                    title: "Vocal-More",
                    subtitle: NSLocalizedString("Transcription Complete", comment: ""),
                    body: display
                )
            }
        case "error":
            if let message = params["message"] as? String {
                NotificationManager.shared.send(
                    title: "Vocal-More",
                    subtitle: NSLocalizedString("Error", comment: ""),
                    body: message
                )
            }
        default:
            break // state_changed, partial_result, audio_level handled by AppState
        }
    }

    func refreshAvailableDevices() async {
        do {
            let response = try await backend.sendRequest(method: "list_devices")
            guard let items = response.result?.arrayValue else {
                appState.availableDevices = []
                return
            }

            appState.availableDevices = items.compactMap { item in
                guard let dict = item as? [String: Any],
                      let index = dict["index"] as? Int,
                      let name = dict["name"] as? String else { return nil }
                let isDefault = dict["is_default"] as? Bool ?? false
                return AudioDevice(index: index, name: name, isDefault: isDefault)
            }
        } catch {
            logger.error("Failed to refresh devices: \(error.localizedDescription)")
            appState.lastError = "Failed to load input devices: \(error.localizedDescription)"
            appState.availableDevices = []
        }
    }

    func refreshDictionaryEntries() async {
        do {
            let response = try await backend.sendRequest(method: "get_dictionary")
            guard let items = response.result?.arrayValue else {
                appState.dictionaryEntries = []
                return
            }

            appState.dictionaryEntries = items.compactMap { item in
                guard let dict = item as? [String: Any],
                      let term = dict["term"] as? String else { return nil }
                let aliases = (dict["aliases"] as? [Any])?.compactMap { $0 as? String } ?? []
                return DictEntry(term: term, aliases: aliases)
            }
        } catch {
            logger.error("Failed to refresh dictionary: \(error.localizedDescription)")
            appState.lastError = "Failed to load dictionary: \(error.localizedDescription)"
            appState.dictionaryEntries = []
        }
    }

    private var sparkleUpdatesEnabled: Bool {
        sparkleFeedURL != nil && sparklePublicEDKey != nil
    }

    private func makeCheckForUpdatesAction() -> (() -> Void)? {
        guard sparkleUpdatesEnabled else {
            logger.info("Sparkle not fully configured yet; update menu item remains disabled")
            return nil
        }

        updaterController = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )

        return { [weak self] in
            self?.updaterController?.checkForUpdates(nil)
        }
    }

    private var sparkleFeedURL: String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String else {
            return nil
        }
        return normalizedSparkleConfigValue(value)
    }

    private var sparklePublicEDKey: String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String else {
            return nil
        }
        return normalizedSparkleConfigValue(value)
    }

    private func normalizedSparkleConfigValue(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.hasPrefix("REPLACE_WITH_") else {
            return nil
        }
        return trimmed
    }

    /// Shared helper for settings tabs to send config changes with error feedback.
    func sendConfigChange(key: String, value: Any) async {
        do {
            _ = try await backend.sendRequest(
                method: "set_config",
                params: ["key": key, "value": value]
            )
        } catch {
            appState.lastError = "Failed to save setting \"\(key)\": \(error.localizedDescription)"
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        logger.info("Vocal-More terminating...")
        hotkeyManager?.stop()
        capsuleController?.hide()
        Task {
            await backend?.shutdown()
        }
    }
}
