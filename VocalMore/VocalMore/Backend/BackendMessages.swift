import Foundation

// MARK: - JSON-RPC Base Types

struct JSONRPCRequest: Codable {
    let jsonrpc: String
    let method: String
    let params: AnyCodable
    let id: Int

    init(method: String, params: [String: Any], id: Int) {
        self.jsonrpc = "2.0"
        self.method = method
        self.params = AnyCodable(params)
        self.id = id
    }
}

struct JSONRPCResponse: Codable {
    let jsonrpc: String
    let result: AnyCodable?
    let error: JSONRPCError?
    let id: Int?
}

struct JSONRPCError: Codable, Error {
    let code: Int
    let message: String
}

struct JSONRPCNotification: Codable {
    let jsonrpc: String
    let method: String
    let params: AnyCodable?
}

// MARK: - Generic JSON Wrapper

/// A type-erased Codable wrapper for arbitrary JSON values.
struct AnyCodable: Codable {
    let value: Any

    init(_ value: Any) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            self.value = NSNull()
        } else if let bool = try? container.decode(Bool.self) {
            self.value = bool
        } else if let int = try? container.decode(Int.self) {
            self.value = int
        } else if let double = try? container.decode(Double.self) {
            self.value = double
        } else if let string = try? container.decode(String.self) {
            self.value = string
        } else if let array = try? container.decode([AnyCodable].self) {
            self.value = array.map { $0.value }
        } else if let dict = try? container.decode([String: AnyCodable].self) {
            self.value = dict.mapValues { $0.value }
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        switch value {
        case is NSNull:
            try container.encodeNil()
        case let bool as Bool:
            try container.encode(bool)
        case let int as Int:
            try container.encode(int)
        case let double as Double:
            try container.encode(double)
        case let string as String:
            try container.encode(string)
        case let array as [Any]:
            try container.encode(array.map { AnyCodable($0) })
        case let dict as [String: Any]:
            try container.encode(dict.mapValues { AnyCodable($0) })
        default:
            throw EncodingError.invalidValue(
                value,
                EncodingError.Context(
                    codingPath: encoder.codingPath,
                    debugDescription: "Unsupported type: \(type(of: value))"
                )
            )
        }
    }

    // MARK: - Convenience Accessors

    var dictValue: [String: Any]? {
        value as? [String: Any]
    }

    var arrayValue: [Any]? {
        value as? [Any]
    }

    var stringValue: String? {
        value as? String
    }

    var doubleValue: Double? {
        value as? Double
    }

    var intValue: Int? {
        value as? Int
    }

    var boolValue: Bool? {
        value as? Bool
    }
}

// MARK: - Incoming Message (can be response or notification)

enum IncomingMessage {
    case response(JSONRPCResponse)
    case notification(method: String, params: [String: Any])

    static func parse(from data: Data) throws -> IncomingMessage {
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw JSONRPCError(code: -32700, message: "Invalid JSON")
        }

        // If it has an "id" field, it's a response
        if json["id"] != nil {
            let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)
            return .response(response)
        }

        // Otherwise it's a notification
        let method = json["method"] as? String ?? ""
        let params = json["params"] as? [String: Any] ?? [:]
        return .notification(method: method, params: params)
    }
}
