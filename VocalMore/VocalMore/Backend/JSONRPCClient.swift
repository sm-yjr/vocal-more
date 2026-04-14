import Foundation
import os

// MARK: - Errors

enum RPCClientError: Error, LocalizedError {
    case notConnected
    case timeout(method: String)
    case encodingFailed

    var errorDescription: String? {
        switch self {
        case .notConnected:
            return "Backend not connected"
        case .timeout(let method):
            return "Request timed out: \(method)"
        case .encodingFailed:
            return "Failed to encode request"
        }
    }
}

// MARK: - JSON-RPC Client Actor

actor JSONRPCClient {
    private var nextId: Int = 0
    private var pending: [Int: CheckedContinuation<JSONRPCResponse, Error>] = [:]
    private var writeHandler: ((Data) -> Void)?
    private var notificationHandler: ((String, [String: Any]) -> Void)?
    private let logger = Logger(subsystem: "com.vocal-more", category: "JSONRPCClient")

    func setWriteHandler(_ handler: @escaping (Data) -> Void) {
        self.writeHandler = handler
    }

    func setNotificationHandler(_ handler: @escaping (String, [String: Any]) -> Void) {
        self.notificationHandler = handler
    }

    /// Send a JSON-RPC request and await the response.
    func sendRequest(method: String, params: [String: Any] = [:], timeout: TimeInterval = 10.0) async throws -> JSONRPCResponse {
        guard let writeHandler else {
            throw RPCClientError.notConnected
        }

        let id = nextId
        nextId += 1

        let request = JSONRPCRequest(method: method, params: params, id: id)

        guard let data = try? JSONEncoder().encode(request),
              var line = String(data: data, encoding: .utf8) else {
            throw RPCClientError.encodingFailed
        }

        line += "\n"
        let lineData = line.data(using: .utf8)!

        // Store continuation and send data synchronously within the actor
        // Then race against a timeout
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            writeHandler(lineData)
            logger.debug("Sent request id=\(id) method=\(method)")

            // Schedule timeout
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self?.timeoutPending(id: id, method: method)
            }
        }
    }

    private func timeoutPending(id: Int, method: String) {
        if let continuation = pending.removeValue(forKey: id) {
            logger.warning("Request timed out: id=\(id) method=\(method)")
            continuation.resume(throwing: RPCClientError.timeout(method: method))
        }
    }

    /// Handle an incoming message from stdout.
    func handleIncomingMessage(_ data: Data) {
        do {
            let message = try IncomingMessage.parse(from: data)
            switch message {
            case .response(let response):
                if let id = response.id, let continuation = pending.removeValue(forKey: id) {
                    if let error = response.error {
                        logger.info("Response id=\(id): error \(error.code) \(error.message)")
                        continuation.resume(throwing: error)
                    } else {
                        logger.debug("Response id=\(id): success")
                        continuation.resume(returning: response)
                    }
                } else {
                    logger.warning("Received response with unknown or missing id: \(response.id ?? -1)")
                }

            case .notification(let method, let params):
                logger.debug("Notification: \(method)")
                notificationHandler?(method, params)
            }
        } catch {
            if let text = String(data: data, encoding: .utf8) {
                logger.error("Failed to parse message: \(error.localizedDescription), raw: \(text)")
            }
        }
    }

    /// Cancel all pending requests (e.g., on disconnect).
    func cancelAll(with error: Error) {
        let count = pending.count
        for (_, continuation) in pending {
            continuation.resume(throwing: error)
        }
        pending.removeAll()
        if count > 0 {
            logger.info("Cancelled \(count) pending requests")
        }
    }

    /// Reset state for a new connection.
    func reset() {
        // First cancel any pending continuations to avoid leaks/crashes
        for (_, continuation) in pending {
            continuation.resume(throwing: RPCClientError.notConnected)
        }
        pending.removeAll()
        nextId = 0
        logger.info("RPC client reset")
    }
}
