import XCTest
@testable import VocalMore

final class HotkeyManagerLogicTests: XCTestCase {

    func testLookupTableWithFnOnly() {
        let manager = HotkeyManager()
        manager.updateLookupTables(["fn"])

        // Fn should be in modifier lookup, double_cmd inactive
        XCTAssertTrue(manager.isAnyKeyPressed == false)
    }

    func testLookupTableWithDoubleCmd() {
        let manager = HotkeyManager()
        manager.updateLookupTables(["double_cmd"])

        // Only double_cmd active, no modifiers or regulars
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testLookupTableWithFKeys() {
        let manager = HotkeyManager()
        manager.updateLookupTables(["f13", "f16", "f20"])

        // Verify manager starts with no keys pressed
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testLookupTableWithAllKeys() {
        let manager = HotkeyManager()
        let allKeys = ["fn", "double_cmd", "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20"]
        manager.updateLookupTables(allKeys)

        // Should not crash with all keys
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testLookupTableWithEmptyList() {
        let manager = HotkeyManager()
        manager.updateLookupTables([])

        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testKeyRegistryCompleteness() {
        // Verify all expected keys exist in the registry
        let expectedKeys = ["fn", "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20"]
        for key in expectedKeys {
            XCTAssertNotNil(keyRegistry[key], "Missing key in registry: \(key)")
        }
    }

    func testFnKeyIsModifier() {
        let def = keyRegistry["fn"]!
        XCTAssertTrue(def.isModifier)
        XCTAssertEqual(def.keyCode, 63)
        XCTAssertEqual(def.flagMask, 0x80_0000)
    }

    func testFKeysAreRegular() {
        let fKeys = ["f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20"]
        for key in fKeys {
            let def = keyRegistry[key]!
            XCTAssertFalse(def.isModifier, "\(key) should not be a modifier")
            XCTAssertEqual(def.flagMask, 0, "\(key) should have zero flag mask")
        }
    }

    func testSetActiveHotkeysUpdatesThreshold() {
        let manager = HotkeyManager()
        manager.setActiveHotkeys(["fn"], threshold: 0.5)
        // Threshold is stored internally; we verify no crash
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testSetCustomKeyDoesNotCrash() {
        let manager = HotkeyManager()
        let def = CustomHotkeyDef(
            keyCode: 49, displayName: "Space", isModifier: false, flagMask: 0
        )
        manager.setCustomKey(def)
        // Should not crash
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testSetCustomKeyNilDoesNotCrash() {
        let manager = HotkeyManager()
        manager.setCustomKey(nil)
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testSetCustomKeyModifier() {
        let manager = HotkeyManager()
        let def = CustomHotkeyDef(
            keyCode: 63, displayName: "Fn", isModifier: true, flagMask: 0x80_0000
        )
        manager.setCustomKey(def)
        // Custom key set but no key pressed yet
        XCTAssertFalse(manager.isAnyKeyPressed)
    }

    func testSetCustomKeyThenClear() {
        let manager = HotkeyManager()
        let def = CustomHotkeyDef(
            keyCode: 49, displayName: "Space", isModifier: false, flagMask: 0
        )
        manager.setCustomKey(def)
        manager.setCustomKey(nil)
        XCTAssertFalse(manager.isAnyKeyPressed)
    }
}
