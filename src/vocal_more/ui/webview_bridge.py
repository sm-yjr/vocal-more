"""Helpers for bridging WKWebView / Objective-C payloads into Python."""


def objc_to_python(obj):
    """Recursively rebuild WebKit message payloads as native Python types."""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str, bytes)):
        return obj
    if hasattr(obj, "items"):
        try:
            return {str(k): objc_to_python(v) for k, v in obj.items()}
        except Exception:
            pass
    try:
        return [objc_to_python(v) for v in obj]
    except TypeError:
        return obj
