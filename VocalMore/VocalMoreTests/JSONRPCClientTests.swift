import XCTest
@testable import VocalMore

final class JSONRPCClientTests: XCTestCase {

    func testRequestEncoding() throws {
        let request = JSONRPCRequest(
            method: "set_config",
            params: ["key": "enable_polish", "value": false],
            id: 42
        )
        let data = try JSONEncoder().encode(request)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertEqual(json["jsonrpc"] as? String, "2.0")
        XCTAssertEqual(json["method"] as? String, "set_config")
        XCTAssertEqual(json["id"] as? Int, 42)

        let params = json["params"] as! [String: Any]
        XCTAssertEqual(params["key"] as? String, "enable_polish")
        XCTAssertEqual(params["value"] as? Bool, false)
    }

    func testResponseWithResult() throws {
        let json = """
        {
            "jsonrpc": "2.0",
            "result": {
                "version": "0.1.0",
                "state": "idle",
                "current_mode": "walkie_talkie",
                "config": {
                    "enable_polish": true,
                    "auto_paste": true,
                    "default_mode": "walkie_talkie"
                }
            },
            "id": 1
        }
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)

        XCTAssertEqual(response.id, 1)
        XCTAssertNil(response.error)

        let result = response.result!.dictValue!
        XCTAssertEqual(result["version"] as? String, "0.1.0")
        XCTAssertEqual(result["state"] as? String, "idle")

        let config = result["config"] as! [String: Any]
        XCTAssertEqual(config["enable_polish"] as? Bool, true)
    }

    func testResponseWithError() throws {
        let json = """
        {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Unknown config key: bogus"}, "id": 3}
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)

        XCTAssertEqual(response.id, 3)
        XCTAssertNil(response.result)
        XCTAssertNotNil(response.error)
        XCTAssertEqual(response.error?.code, -32602)
        XCTAssertTrue(response.error?.message.contains("bogus") ?? false)
    }

    func testNotificationParsing() throws {
        let json = """
        {"jsonrpc": "2.0", "method": "audio_level", "params": {"rms": 0.42}}
        """
        let data = json.data(using: .utf8)!
        let message = try IncomingMessage.parse(from: data)

        switch message {
        case .notification(let method, let params):
            XCTAssertEqual(method, "audio_level")
            XCTAssertEqual(params["rms"] as? Double, 0.42)
        case .response:
            XCTFail("Expected notification")
        }
    }

    func testAnyCodableWithNull() throws {
        let json = """
        {"jsonrpc": "2.0", "result": null, "id": 1}
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)
        XCTAssertEqual(response.id, 1)
        // result is decoded as AnyCodable wrapping NSNull
        XCTAssertNotNil(response.result)
    }

    func testAnyCodableWithArray() throws {
        let json = """
        {"jsonrpc": "2.0", "result": [{"index": 0, "name": "Built-in", "is_default": true}], "id": 1}
        """
        let data = json.data(using: .utf8)!
        let response = try JSONDecoder().decode(JSONRPCResponse.self, from: data)

        let result = response.result!.arrayValue!
        XCTAssertEqual(result.count, 1)

        let device = result[0] as! [String: Any]
        XCTAssertEqual(device["name"] as? String, "Built-in")
        XCTAssertEqual(device["is_default"] as? Bool, true)
    }
}
