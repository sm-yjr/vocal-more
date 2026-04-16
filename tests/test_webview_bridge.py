"""Tests for Objective-C -> Python WebKit message conversion."""


def test_objc_to_python_accepts_objc_style_mapping():
    """NSDictionary-like objects should be converted into native dicts."""
    from vocal_more.ui.webview_bridge import objc_to_python

    class ObjCStyleDict:
        def __init__(self, data):
            self._data = data

        def items(self):
            return self._data.items()

    payload = ObjCStyleDict(
        {
            "action": "finish",
            "nested": ObjCStyleDict({"ok": True}),
        }
    )

    result = objc_to_python(payload)

    assert result == {"action": "finish", "nested": {"ok": True}}
