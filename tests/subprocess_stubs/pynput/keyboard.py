class _DummyPressed:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class Controller:
    def type(self, text):
        return None

    def pressed(self, key):
        return _DummyPressed()

    def press(self, key):
        return None

    def release(self, key):
        return None


class Key:
    cmd = "cmd"
    backspace = "backspace"
    shift = "shift"
    left = "left"
