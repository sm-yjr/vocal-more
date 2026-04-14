class InputStream:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None

    def stop(self):
        return None

    def close(self):
        return None


class PortAudioError(Exception):
    pass


class CallbackFlags:
    pass


def query_devices(*args, **kwargs):
    return []
