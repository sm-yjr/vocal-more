import XCTest
@testable import VocalMore

final class BackendMessagesTests: XCTestCase {

    // MARK: - AnyCodable Tests

    func testAnyCodableEncodesString() throws {
        let value = AnyCodable("hello")
        let data = try JSONEncoder().encode(value)
        let json = String(data: data, encoding: .utf8)!
        XCTAssertEqual(json, "\"hello\"")
    }

    func testAnyCodableEncodesInt() throws {
        let value = AnyCodable(42)
        let data = try JSONEncoder().encode(value)
        let json = String(data: data, encoding: .utf8)!
        XCTAssertEqual(json, "42")
    }

    func testAnyCodableEncodesBool() throws {
        let value = AnyCodable(true)
        let data = try JSONEncoder().encode(value)
        let json = String(data: data, encoding: .utf8)!
        XCTAssertEqual(json, "true")
    }

    func testAnyCodableEncodesDict() throws {
        let value = AnyCodable(["key": "value"])
        let data = try JSONEncoder().encode(value)
        let json = String(data: data, encoding: .utf8)!
        XCTAssertTrue(json.contains("\"key\""))
        XCTAssertTrue(json.contains("\"value\""))
    }

    func testAnyCodableRoundtrip() throws {
        let original: [String: Any] = [
            "string": "hello",
            "number": 42,
            "bool": true,
            "array": [1, 2, 3],
        ]
        let encoded = try JSONEncoder().encode(AnyCodable(original))
        let decoded = try JSONDecoder().decode(AnyCodable.self, from: encoded)
        let dict = decoded.dictValue!
        XCTAssertEqual(dict["string"] as? String, "hello")
        XCTAssertEqual(dict["number"] as? Int, 42)
        XCTAssertEqual(dict["bool"] as? Bool, true)
    }

    // MARK: - JSONRPCRequest Tests

    func testJSONRPCRequestEncoding() throws {
        let request = JSONRPCRequest(method: "initialize", params: [:], id: 1)
        let data = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertEqual(json["jsonrpc"] as? String, "2.0")
        XCTAssertEqual(json["method"] as? String, "initialize")
        XCTAssertEqual(json["id"] as? Int, 1)
    }

    // MARK: - JSONRPCResponse Tests

    func testJSONRPCResponseDecoding() throws {
        let json = """
        {"jsonrpc": "2.0", "result": {"version": "0.1.0", "state": "idle"}, "id": 1}
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)

        XCTAssertEqual(response.jsonrpc, "2.0")
        XCTAssertEqual(response.id, 1)
        XCTAssertNil(response.error)
        XCTAssertNotNil(response.result)

        let result = response.result!.dictValue!
        XCTAssertEqual(result["version"] as? String, "0.1.0")
        XCTAssertEqual(result["state"] as? String, "idle")
    }

    func testJSONRPCErrorDecoding() throws {
        let json = """
        {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": 1}
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)

        XCTAssertNotNil(response.error)
        XCTAssertEqual(response.error?.code, -32601)
        XCTAssertEqual(response.error?.message, "Method not found")
    }

    // MARK: - IncomingMessage Tests

    func testIncomingMessageParsesResponse() throws {
        let json = """
        {"jsonrpc": "2.0", "result": {"ok": true}, "id": 5}
        """
        let data = json.data(using: .utf8)!
        let message = try IncomingMessage.parse(from: data)

        switch message {
        case .response(let resp):
            XCTAssertEqual(resp.id, 5)
        case .notification:
            XCTFail("Expected response, got notification")
        }
    }

    func testIncomingMessageParsesNotification() throws {
        let json = """
        {"jsonrpc": "2.0", "method": "state_changed", "params": {"state": "recording"}}
        """
        let data = json.data(using: .utf8)!
        let message = try IncomingMessage.parse(from: data)

        switch message {
        case .response:
            XCTFail("Expected notification, got response")
        case .notification(let method, let params):
            XCTAssertEqual(method, "state_changed")
            XCTAssertEqual(params["state"] as? String, "recording")
        }
    }
}
