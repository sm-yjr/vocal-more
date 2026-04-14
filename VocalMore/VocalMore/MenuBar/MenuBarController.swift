import AppKit
import SwiftUI
import os

/// Manages the NSStatusItem and NSMenu in the menu bar.
@MainActor
final class MenuBarController: NSObject {
    private let appState: AppState
    private weak var backend: PythonBackend?
    private let updatesEnabled: Bool
    private let onCheckForUpdates: (() -> Void)?
    private let logger = Logger(subsystem: "com.vocal-more", category: "MenuBar")

    private var statusItem: NSStatusItem!
    private var settingsWindow: NSWindow?

    // Menu items that need updating
    private var statusMenuItem: NSMenuItem!
    private var checkForUpdatesItem: NSMenuItem!

    init(
        appState: AppState,
        backend: PythonBackend,
        updatesEnabled: Bool,
        onCheckForUpdates: (() -> Void)?
    ) {
        self.appState = appState
        self.backend = backend
        self.updatesEnabled = updatesEnabled
        self.onCheckForUpdates = onCheckForUpdates
        super.init()
        setupStatusItem()
    }

    // MARK: - Setup

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

        if let button = statusItem.button {
            updateIcon(for: .idle)
            button.toolTip = "Vocal-More"
        }

        buildMenu()
    }

    private func buildMenu() {
        let menu = NSMenu()

        // Version + Status
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let versionItem = NSMenuItem(title: "Vocal-More \(version)", action: nil, keyEquivalent: "")
        versionItem.isEnabled = false
        menu.addItem(versionItem)

        statusMenuItem = NSMenuItem(title: NSLocalizedString("Status: Idle", comment: ""), action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        menu.addItem(NSMenuItem.separator())

        // Settings... (opens SwiftUI Settings panel)
        let settingsItem = NSMenuItem(title: NSLocalizedString("Settings...", comment: ""), action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        menu.addItem(settingsItem)

        checkForUpdatesItem = NSMenuItem(title: NSLocalizedString("Check for Updates…", comment: ""), action: #selector(checkForUpdates), keyEquivalent: "")
        checkForUpdatesItem.target = self
        checkForUpdatesItem.isEnabled = updatesEnabled && onCheckForUpdates != nil
        menu.addItem(checkForUpdatesItem)

        menu.addItem(NSMenuItem.separator())

        // Quit
        let quitItem = NSMenuItem(title: NSLocalizedString("Quit Vocal-More", comment: ""), action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)

        statusItem.menu = menu
    }

    // MARK: - State Updates

    func updateIcon(for state: ModeState) {
        let imageName: String
        switch state {
        case .idle:
            imageName = "StatusIdle"
        case .recording:
            imageName = "StatusRecording"
        case .processing:
            imageName = "StatusProcessing"
        }

        if let image = NSImage(named: imageName) {
            image.isTemplate = true
            statusItem.button?.image = image
        }
    }

    func updateStatus(for state: ModeState) {
        let text: String
        switch state {
        case .idle:
            text = NSLocalizedString("Status: Idle", comment: "")
        case .recording:
            text = NSLocalizedString("Status: Recording...", comment: "")
        case .processing:
            text = NSLocalizedString("Status: Processing...", comment: "")
        }
        statusMenuItem?.title = text
        updateIcon(for: state)
    }

    func refreshFromState() {
        // No-op: all configuration is in Settings view now
    }

    // MARK: - Settings Window

    @objc private func openSettings() {
        if let window = settingsWindow, window.isVisible {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        guard let backend else {
            logger.error("Cannot open settings: backend unavailable")
            return
        }

        let settingsView = SettingsView(
            backend: backend,
            refreshDictionaryEntries: { [weak self] in
                await self?.refreshDictionaryEntries()
            }
        )
        .environment(appState)

        let hostingController = NSHostingController(rootView: settingsView)

        let window = NSWindow(contentViewController: hostingController)
        window.title = NSLocalizedString("Vocal-More Settings", comment: "")
        window.styleMask = [.titled, .closable, .fullSizeContentView]
        window.titlebarAppearsTransparent = true
        window.toolbarStyle = .unified
        window.center()
        self.settingsWindow = window

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func refreshDictionaryEntries() async {
        guard let backend else { return }

        do {
            let response = try await backend.sendRequest(method: "get_dictionary")
            let items = response.result?.arrayValue ?? []
            let entries = items.compactMap { item -> DictEntry? in
                guard let dict = item as? [String: Any],
                      let term = dict["term"] as? String else { return nil }
                let aliases = dict["aliases"] as? [String] ?? []
                return DictEntry(term: term, aliases: aliases)
            }

            appState.dictionaryEntries = entries
            appState.lastError = nil
        } catch {
            logger.error("Failed to refresh dictionary from settings window: \(error.localizedDescription)")
            appState.lastError = "Failed to load dictionary: \(error.localizedDescription)"
        }
    }

    @objc private func checkForUpdates() {
        guard let onCheckForUpdates else {
            logger.info("Check for Updates selected, but updater is not configured")
            return
        }
        onCheckForUpdates()
    }

    // MARK: - Quit

    @objc private func quitApp() {
        Task {
            await backend?.shutdown()
            NSApp.terminate(nil)
        }
    }
}
