import ApplicationServices
import CoreGraphics
import Foundation
import os

// MARK: - Hotkey Definitions

struct HotkeyDef {
    let keyCode: Int64
    let isModifier: Bool
    let flagMask: UInt64
}

let keyRegistry: [String: HotkeyDef] = [
    "fn":  HotkeyDef(keyCode: 63,  isModifier: true,  flagMask: 0x80_0000),
    "f13": HotkeyDef(keyCode: 105, isModifier: false, flagMask: 0),
    "f14": HotkeyDef(keyCode: 107, isModifier: false, flagMask: 0),
    "f15": HotkeyDef(keyCode: 113, isModifier: false, flagMask: 0),
    "f16": HotkeyDef(keyCode: 106, isModifier: false, flagMask: 0),
    "f17": HotkeyDef(keyCode: 64,  isModifier: false, flagMask: 0),
    "f18": HotkeyDef(keyCode: 79,  isModifier: false, flagMask: 0),
    "f19": HotkeyDef(keyCode: 80,  isModifier: false, flagMask: 0),
    "f20": HotkeyDef(keyCode: 90,  isModifier: false, flagMask: 0),
]

private let cmdLeftKeyCode: Int64 = 55
private let cmdRightKeyCode: Int64 = 54
private let cmdMask: UInt64 = 0x10_0000

// MARK: - Callback (must be @convention(c))

private let hotkeyCallback: CGEventTapCallBack = {
    proxy, type, event, refcon -> Unmanaged<CGEvent>? in

    guard let refcon else { return Unmanaged.passUnretained(event) }
    let manager = Unmanaged<HotkeyManager>.fromOpaque(refcon).takeUnretainedValue()

    // Re-enable tap if system disabled it
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        manager.logger.warning("CGEventTap was disabled by system, re-enabling")
        if let tap = manager.eventTap {
            CGEvent.tapEnable(tap: tap, enable: true)
        }
        return Unmanaged.passUnretained(event)
    }

    return manager.handleEvent(type: type, event: event)
}

// MARK: - HotkeyManager

final class HotkeyManager {
    // Callbacks
    var onPressed: (() -> Void)?
    var onReleased: (() -> Void)?
    var onDoubleCmd: (() -> Void)?

    // Internal state
    fileprivate var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var tapThread: Thread?
    private var running = false
    private var starting = false
    private let startSemaphore = DispatchSemaphore(value: 0)
    private let stateLock = NSLock()

    // Lookup tables
    private var modifierLookup: [Int64: UInt64] = [:]
    private var regularLookup: Set<Int64> = []
    private var doubleCmdActive = false

    // Custom key (separate from built-in lookup tables)
    private var customKeyDef: HotkeyDef?

    // State tracking
    private var keyStates: [Int64: Bool] = [:]
    private var heldKeys: Set<Int64> = []
    private var lastCmdTime: TimeInterval = 0
    private var cmdTapCount = 0
    private var doubleTapThreshold: TimeInterval = 0.3

    let logger = Logger(subsystem: "com.vocal-more", category: "HotkeyManager")

    init() {}

    private func withStateLock<T>(_ body: () -> T) -> T {
        stateLock.lock()
        defer { stateLock.unlock() }
        return body()
    }

    // MARK: - Configuration

    func setActiveHotkeys(_ hotkeys: [String], threshold: TimeInterval = 0.3) {
        withStateLock {
            doubleTapThreshold = threshold
            updateLookupTables(hotkeys)
            keyStates.removeAll()
            heldKeys.removeAll()
            lastCmdTime = 0
            cmdTapCount = 0
        }
        logger.info("Active hotkeys: \(hotkeys.joined(separator: ", ")), threshold: \(threshold)")
    }

    func updateLookupTables(_ hotkeys: [String]) {
        modifierLookup.removeAll()
        regularLookup.removeAll()
        doubleCmdActive = false

        for name in hotkeys {
            if name == "double_cmd" {
                doubleCmdActive = true
            } else if let def = keyRegistry[name] {
                if def.isModifier {
                    modifierLookup[def.keyCode] = def.flagMask
                } else {
                    regularLookup.insert(def.keyCode)
                }
            }
        }

        logger.info("Lookup tables: modifiers=\(self.modifierLookup.count), regulars=\(self.regularLookup.count), doubleCmd=\(self.doubleCmdActive)")
    }

    func setCustomKey(_ def: CustomHotkeyDef?) {
        withStateLock {
            if let def {
                customKeyDef = HotkeyDef(
                    keyCode: Int64(def.keyCode),
                    isModifier: def.isModifier,
                    flagMask: UInt64(def.flagMask)
                )
            } else {
                customKeyDef = nil
            }
        }
        logger.info("Custom key: \(def?.displayName ?? "none")")
    }

    // MARK: - Start / Stop

    var isRunning: Bool {
        withStateLock { running }
    }

    func start(promptForAccessibility: Bool = true) -> Bool {
        let shouldStart = withStateLock {
            if running || starting {
                return false
            }
            starting = true
            return true
        }
        if !shouldStart {
            return isRunning
        }

        let accessGranted = checkAccessibility(prompt: promptForAccessibility)
        logger.info("Accessibility check: \(accessGranted)")

        if !accessGranted {
            withStateLock {
                starting = false
            }
            logger.warning("Accessibility permission not granted")
            return false
        }

        let thread = Thread { [weak self] in
            self?.runEventLoop()
        }
        thread.name = "HotkeyManager.EventTap"
        thread.qualityOfService = .userInteractive
        thread.start()
        tapThread = thread

        // Wait for event loop to signal ready (max 2 seconds)
        let result = startSemaphore.wait(timeout: .now() + 2.0)
        if result == .timedOut {
            withStateLock {
                starting = false
            }
            logger.error("CGEventTap failed to start within 2 seconds")
            return false
        }

        let didStart = withStateLock {
            starting = false
            return running
        }
        logger.info("HotkeyManager started, running=\(didStart)")
        return didStart
    }

    func stop() {
        let tap = withStateLock { () -> CFMachPort? in
            running = false
            starting = false
            keyStates.removeAll()
            heldKeys.removeAll()
            lastCmdTime = 0
            cmdTapCount = 0
            let tap = eventTap
            eventTap = nil
            runLoopSource = nil
            tapThread = nil
            return tap
        }

        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        logger.info("HotkeyManager stopped")
    }

    // MARK: - Accessibility

    func checkAccessibility(prompt: Bool = false) -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    // MARK: - Event Loop

    private func runEventLoop() {
        let eventMask: CGEventMask = (1 << CGEventType.flagsChanged.rawValue)
            | (1 << CGEventType.keyDown.rawValue)
            | (1 << CGEventType.keyUp.rawValue)

        logger.info("Creating CGEventTap...")

        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: eventMask,
            callback: hotkeyCallback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            logger.error("Failed to create CGEventTap — accessibility may not be granted or another issue")
            startSemaphore.signal()
            return
        }

        withStateLock {
            eventTap = tap
        }
        logger.info("CGEventTap created successfully")

        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        withStateLock {
            runLoopSource = source
        }

        let runLoop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(runLoop, source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)

        withStateLock {
            running = true
        }
        startSemaphore.signal()  // Signal that we're ready
        logger.info("CGEventTap enabled, entering run loop")

        CFRunLoopRun()

        withStateLock {
            running = false
            starting = false
            eventTap = nil
            runLoopSource = nil
            tapThread = nil
        }
        logger.info("CGEventTap run loop exited")
    }

    // MARK: - Event Handling

    func handleEvent(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        let keycode = event.getIntegerValueField(.keyboardEventKeycode)

        switch type {
        case .flagsChanged:
            return handleFlagsChanged(event: event, keycode: keycode)

        case .keyDown:
            var shouldConsume = false
            var shouldFire = false
            withStateLock {
                let isRegistered = regularLookup.contains(keycode)
                let isCustomRegular = customKeyDef.map { !$0.isModifier && $0.keyCode == keycode } ?? false
                if isRegistered || isCustomRegular {
                    shouldConsume = true
                    if !heldKeys.contains(keycode) {
                        heldKeys.insert(keycode)
                        shouldFire = true
                    }
                }
            }
            if shouldFire {
                logger.debug("Key down: \(keycode) → onPressed")
                fireCallback(onPressed)
            }
            if shouldConsume {
                return nil // Consume
            }

        case .keyUp:
            var shouldConsume = false
            var shouldFire = false
            withStateLock {
                let isRegistered = regularLookup.contains(keycode)
                let isCustomRegular = customKeyDef.map { !$0.isModifier && $0.keyCode == keycode } ?? false
                if isRegistered || isCustomRegular {
                    shouldConsume = true
                    if heldKeys.contains(keycode) {
                        heldKeys.remove(keycode)
                        shouldFire = true
                    }
                }
            }
            if shouldFire {
                logger.debug("Key up: \(keycode) → onReleased")
                fireCallback(onReleased)
            }
            if shouldConsume {
                return nil // Consume
            }

        default:
            break
        }

        return Unmanaged.passUnretained(event)
    }

    private func handleFlagsChanged(event: CGEvent, keycode: Int64) -> Unmanaged<CGEvent>? {
        let flags = event.flags.rawValue
        var shouldConsumeModifier = false
        var shouldFirePressed = false
        var shouldFireReleased = false
        var shouldFireDoubleCmd = false

        withStateLock {
            // Modifier keys (Fn, etc.) — check built-in lookup first, then custom key
            var resolvedFlagMask: UInt64?
            if let builtIn = modifierLookup[keycode] {
                resolvedFlagMask = builtIn
            } else if let custom = customKeyDef, custom.isModifier, custom.keyCode == keycode {
                resolvedFlagMask = custom.flagMask
            }

            if let flagMask = resolvedFlagMask {
                shouldConsumeModifier = true
                let pressed = (flags & flagMask) != 0
                let prev = keyStates[keycode] ?? false

                if pressed && !prev {
                    keyStates[keycode] = true
                    shouldFirePressed = true
                } else if !pressed && prev {
                    keyStates[keycode] = false
                    shouldFireReleased = true
                }
                return
            }

            // Double-Cmd detection (do NOT consume)
            if doubleCmdActive && (keycode == cmdLeftKeyCode || keycode == cmdRightKeyCode) {
                let cmdPressed = (flags & cmdMask) != 0

                if !cmdPressed { // Key released
                    let currentTime = ProcessInfo.processInfo.systemUptime

                    if currentTime - lastCmdTime < doubleTapThreshold {
                        cmdTapCount += 1
                        if cmdTapCount >= 2 {
                            cmdTapCount = 0
                            shouldFireDoubleCmd = true
                        }
                    } else {
                        cmdTapCount = 1
                    }

                    lastCmdTime = currentTime
                }
            }
        }

        if shouldFirePressed {
            logger.debug("Modifier pressed: keycode=\(keycode), flags=0x\(String(flags, radix: 16))")
            fireCallback(onPressed)
        } else if shouldFireReleased {
            logger.debug("Modifier released: keycode=\(keycode)")
            fireCallback(onReleased)
        }

        if shouldFireDoubleCmd {
            logger.debug("Double-Cmd detected")
            fireCallback(onDoubleCmd)
        }

        if shouldConsumeModifier {
            return nil // Consume modifier hotkey events
        }

        return Unmanaged.passUnretained(event)
    }

    private func fireCallback(_ callback: (() -> Void)?) {
        guard let callback else { return }
        DispatchQueue.global(qos: .userInteractive).async {
            callback()
        }
    }

    // MARK: - Query

    var isAnyKeyPressed: Bool {
        withStateLock {
            if keyStates.values.contains(true) { return true }
            return !heldKeys.isEmpty
        }
    }

    fileprivate func reenableEventTap() {
        let tap = withStateLock { eventTap }
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: true)
        }
    }
}
