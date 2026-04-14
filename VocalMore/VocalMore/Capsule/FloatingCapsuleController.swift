import AppKit
import os
import WebKit

// MARK: - Custom subclasses for click-through support

/// Borderless non-activating panel that can still become key (required for WKWebView clicks).
private class ClickablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

/// WKWebView that accepts the first mouse click immediately (no need to "activate" first).
private class ClickableWebView: WKWebView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        // WKWebView needs the window to be key to fire HTML onclick events.
        // nonactivatingPanel allows makeKey() without activating the app.
        window?.makeKey()
        super.mouseDown(with: event)
    }
}

/// Controls the floating capsule panel using WKWebView and capsule.html.
@MainActor
final class FloatingCapsuleController: NSObject, WKScriptMessageHandler {
    private let appState: AppState
    private let logger = Logger(subsystem: "com.vocal-more", category: "Capsule")

    private var panel: NSPanel?
    private var webView: WKWebView?
    private var rmsTimer: Timer?

    var onCancel: (() -> Void)?
    var onFinish: (() -> Void)?

    private static let capsuleWidth: CGFloat = 200
    private static let capsuleHeight: CGFloat = 80

    init(appState: AppState) {
        self.appState = appState
        super.init()
        setupPanel()
    }

    // MARK: - Setup

    private func setupPanel() {
        let screenFrame = NSScreen.main?.frame ?? NSRect(x: 0, y: 0, width: 1920, height: 1080)
        let panelX = (screenFrame.width - Self.capsuleWidth) / 2
        let panelY: CGFloat = 20

        let panelFrame = NSRect(x: panelX, y: panelY, width: Self.capsuleWidth, height: Self.capsuleHeight)

        let panel = ClickablePanel(
            contentRect: panelFrame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        panel.level = NSWindow.Level(rawValue: 1000) // Above everything
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]

        // Create WKWebView
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "capsule")

        let wv = ClickableWebView(frame: NSRect(x: 0, y: 0, width: Self.capsuleWidth, height: Self.capsuleHeight), configuration: config)
        wv.setValue(false, forKey: "drawsBackground")

        // Load capsule.html from bundle
        if let htmlURL = Bundle.main.url(forResource: "capsule", withExtension: "html") {
            wv.loadFileURL(htmlURL, allowingReadAccessTo: htmlURL.deletingLastPathComponent())
        } else {
            logger.error("capsule.html not found in bundle")
        }

        panel.contentView = wv
        self.panel = panel
        self.webView = wv
    }

    // MARK: - Public API

    func show(mode: String = "pushToTalk") {
        // Position on the screen containing the mouse cursor
        let mouseLocation = NSEvent.mouseLocation
        let activeScreen = NSScreen.screens.first(where: { $0.frame.contains(mouseLocation) })
            ?? NSScreen.main
            ?? NSScreen.screens.first
        if let screen = activeScreen {
            let panelX = screen.frame.origin.x + (screen.frame.width - Self.capsuleWidth) / 2
            let panelY = screen.frame.origin.y + 20
            panel?.setFrameOrigin(NSPoint(x: panelX, y: panelY))
        }

        panel?.ignoresMouseEvents = (mode == "pushToTalk")
        evalJS("setMode('\(mode)'); updateState('recording')")
        if mode == "handsFree" {
            panel?.makeKeyAndOrderFront(nil)
        } else {
            panel?.orderFront(nil)
        }
        startRMSTimer()
    }

    func hide() {
        stopRMSTimer()
        evalJS("updateState('hidden')")

        // Defer orderOut to allow fade-out animation
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.panel?.orderOut(nil)
        }
    }

    func updateState(_ state: String) {
        if state == "hidden" {
            hide()
            return
        }

        evalJS("updateState('\(state)')")

        if state == "processing" {
            stopRMSTimer()
            panel?.ignoresMouseEvents = true
        }
    }

    func updateStreamingText(_ text: String) {
        let escaped = text
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "'", with: "\\'")
            .replacingOccurrences(of: "\n", with: " ")
        evalJS("updateStreamingText('\(escaped)')")
    }

    // MARK: - RMS Timer

    private func startRMSTimer() {
        stopRMSTimer()
        rmsTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                let rms = self.appState.currentAudioLevel
                self.evalJS("updateAudioLevel(\(rms))")
            }
        }
    }

    private func stopRMSTimer() {
        rmsTimer?.invalidate()
        rmsTimer = nil
    }

    // MARK: - JS Bridge

    private func evalJS(_ js: String) {
        webView?.evaluateJavaScript(js, completionHandler: nil)
    }

    // MARK: - WKScriptMessageHandler (JS → Swift)

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }

        switch action {
        case "cancel":
            onCancel?()
        case "finish":
            onFinish?()
        default:
            break
        }
    }
}
