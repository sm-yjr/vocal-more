import Foundation
import os

/// Manages the Python backend process lifecycle and JSON-RPC communication.
@MainActor
final class PythonBackend {
    private let appState: AppState
    private let rpcClient = JSONRPCClient()
    private let logger = Logger(subsystem: "com.vocal-more", category: "PythonBackend")

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var stdoutBuffer = Data()

    // Restart backoff
    private var restartCount = 0
    private var isShuttingDown = false

    // Notification handler (forwarded from rpc client)
    var onNotification: ((String, [String: Any]) -> Void)?

    init(appState: AppState) {
        self.appState = appState
    }

    // MARK: - Process Lifecycle

    func start() async throws {
        guard !isShuttingDown else { return }

        try await spawnProcess()

        // Initialize
        let response = try await rpcClient.sendRequest(method: "initialize", timeout: 10.0)
        if let result = response.result?.dictValue {
            if let config = result["config"] as? [String: Any] {
                appState.updateFromConfig(config)
            }
            if let llmModels = result["llm_models"] as? [[String: Any]] {
                appState.llmModelCatalog = llmModels
            }
            if let asrModels = result["asr_models"] as? [[String: Any]] {
                appState.asrModelCatalog = asrModels
            }
            if let state = result["state"] as? String {
                appState.modeState = ModeState(rawValue: state) ?? .idle
            }
            if let mode = result["current_mode"] as? String {
                appState.currentMode = mode
            }
        }

        appState.backendConnected = true
        restartCount = 0
        logger.info("Backend initialized successfully")
    }

    private func spawnProcess() async throws {
        let target = findBackend()

        let proc = Process()
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"

        switch target {
        case .bundled(let url):
            proc.executableURL = url
            proc.arguments = []
            // Avoid interference from host Python
            env.removeValue(forKey: "PYTHONPATH")
            env.removeValue(forKey: "PYTHONHOME")
        case .python(let url, let args):
            proc.executableURL = url
            proc.arguments = args + ["-m", "vocal_more.serve"]
            if let projectRoot = findProjectRoot() {
                proc.currentDirectoryURL = projectRoot
            }
        }

        proc.environment = env

        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()

        proc.standardInput = stdin
        proc.standardOutput = stdout
        proc.standardError = stderr

        self.stdinPipe = stdin
        self.stdoutPipe = stdout
        self.stderrPipe = stderr
        self.process = proc
        self.stdoutBuffer = Data()

        // Set up handlers
        setupStdoutHandler(stdout)
        setupStderrHandler(stderr)
        setupTerminationHandler(proc)

        // Set up RPC client BEFORE starting the process (must await, not fire-and-forget)
        await rpcClient.reset()
        await rpcClient.setWriteHandler { [weak self] data in
            self?.stdinPipe?.fileHandleForWriting.write(data)
        }
        await rpcClient.setNotificationHandler { [weak self] method, params in
            Task { @MainActor in
                self?.handleNotification(method: method, params: params)
            }
        }

        // Start the process — propagate errors to caller
        try proc.run()
        logger.info("Python backend started (PID: \(proc.processIdentifier))")
    }

    private func setupStdoutHandler(_ pipe: Pipe) {
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                // EOF
                handle.readabilityHandler = nil
                return
            }

            guard let self else { return }

            Task { @MainActor in
                self.stdoutBuffer.append(data)
                self.processStdoutBuffer()
            }
        }
    }

    private func processStdoutBuffer() {
        let newline = Data("\n".utf8)
        while let range = stdoutBuffer.range(of: newline) {
            let lineData = stdoutBuffer.subdata(in: stdoutBuffer.startIndex..<range.lowerBound)
            stdoutBuffer.removeSubrange(stdoutBuffer.startIndex..<range.upperBound)

            if !lineData.isEmpty {
                Task {
                    await rpcClient.handleIncomingMessage(lineData)
                }
            }
        }
    }

    private func setupStderrHandler(_ pipe: Pipe) {
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                handle.readabilityHandler = nil
                return
            }
            if let text = String(data: data, encoding: .utf8) {
                for line in text.split(separator: "\n") {
                    self?.logger.info("[Python] \(line)")
                }
            }
        }
    }

    private func setupTerminationHandler(_ proc: Process) {
        proc.terminationHandler = { [weak self] p in
            let reason = p.terminationReason
            let status = p.terminationStatus

            Task { @MainActor in
                guard let self, !self.isShuttingDown else { return }

                self.appState.backendConnected = false
                self.logger.warning("Backend exited (reason: \(reason.rawValue), status: \(status))")

                if reason == .uncaughtSignal || status != 0 {
                    self.scheduleRestart()
                }
            }
        }
    }

    // MARK: - Restart

    private func scheduleRestart() {
        guard self.restartCount < 5 else {
            logger.error("Backend crashed \(self.restartCount) times, giving up")
            appState.lastError = "Backend unavailable after multiple crashes"
            return
        }

        let delay = min(pow(2.0, Double(self.restartCount)), 30.0)
        self.restartCount += 1
        logger.info("Restarting backend in \(delay)s (attempt \(self.restartCount))")

        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !isShuttingDown else { return }
            try? await start()
        }
    }

    // MARK: - Public API

    func sendRequest(method: String, params: [String: Any] = [:]) async throws -> JSONRPCResponse {
        try await rpcClient.sendRequest(method: method, params: params, timeout: 10.0)
    }

    // MARK: - Shutdown

    func shutdown() async {
        isShuttingDown = true

        // 1. Send shutdown RPC
        do {
            _ = try await rpcClient.sendRequest(method: "shutdown", timeout: 2.0)
        } catch {
            logger.info("Shutdown RPC failed (expected): \(error.localizedDescription)")
        }

        // 2. Close stdin to trigger EOF
        stdinPipe?.fileHandleForWriting.closeFile()

        // 3. Wait briefly
        try? await Task.sleep(nanoseconds: 1_000_000_000)

        // 4. Terminate if still running
        if let proc = process, proc.isRunning {
            proc.terminate()
            try? await Task.sleep(nanoseconds: 1_000_000_000)

            if proc.isRunning {
                proc.interrupt()
            }
        }

        // Cancel pending requests
        await rpcClient.cancelAll(with: RPCClientError.notConnected)

        process = nil
        appState.backendConnected = false
    }

    // MARK: - Notification Handling

    private func handleNotification(method: String, params: [String: Any]) {
        switch method {
        case "state_changed":
            if let state = params["state"] as? String {
                appState.modeState = ModeState(rawValue: state) ?? .idle
            }
        case "partial_result":
            // Could update UI with interim text
            break
        case "final_result":
            if let text = params["text"] as? String {
                appState.lastResult = text
            }
        case "error":
            if let message = params["message"] as? String {
                appState.lastError = message
            }
        case "audio_level":
            if let rms = params["rms"] as? Double {
                appState.currentAudioLevel = Float(rms)
            }
        default:
            logger.info("Unknown notification: \(method)")
        }

        onNotification?(method, params)
    }

    // MARK: - Backend Discovery

    private enum BackendTarget {
        case bundled(URL)
        case python(URL, [String])
    }

    private func findBackend() -> BackendTarget {
        // 1. Bundled PyInstaller executable inside .app/Contents/Resources/
        if let resourceURL = Bundle.main.resourceURL {
            let bundledExe = resourceURL
                .appendingPathComponent("vocal-more-backend")
                .appendingPathComponent("vocal-more-backend")
            if FileManager.default.isExecutableFile(atPath: bundledExe.path) {
                logger.info("Using bundled backend: \(bundledExe.path)")
                return .bundled(bundledExe)
            }
        }

        // 2. Environment variable
        if let path = ProcessInfo.processInfo.environment["VOCAL_MORE_PYTHON"] {
            return .python(URL(fileURLWithPath: path), ["-u"])
        }

        // 3. Walk up from bundle looking for .venv
        if let projectRoot = findProjectRoot() {
            let venvPython = projectRoot.appendingPathComponent(".venv/bin/python")
            if FileManager.default.fileExists(atPath: venvPython.path) {
                return .python(venvPython, ["-u"])
            }
        }

        // 4. Fallback: use /usr/bin/env to find python3
        return .python(URL(fileURLWithPath: "/usr/bin/env"), ["python3", "-u"])
    }

    private func findProjectRoot() -> URL? {
        // 1. Environment variable override
        if let root = ProcessInfo.processInfo.environment["VOCAL_MORE_PROJECT_ROOT"] {
            return URL(fileURLWithPath: root)
        }

        // 2. Walk up from bundle (works when .app is inside project tree)
        var url = Bundle.main.bundleURL
        for _ in 0..<8 {
            url = url.deletingLastPathComponent()
            let pyproject = url.appendingPathComponent("pyproject.toml")
            if FileManager.default.fileExists(atPath: pyproject.path) {
                return url
            }
        }

        // 3. Walk up from source file path (works during Xcode development)
        //    #filePath = .../VocalMore/VocalMore/Backend/PythonBackend.swift
        //    Project root with pyproject.toml is 4 levels up
        var sourceURL = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 {
            sourceURL = sourceURL.deletingLastPathComponent()
            let pyproject = sourceURL.appendingPathComponent("pyproject.toml")
            if FileManager.default.fileExists(atPath: pyproject.path) {
                return sourceURL
            }
        }

        return nil
    }
}
