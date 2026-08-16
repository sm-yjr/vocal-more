import json

import pytest


class FakeResponse:
    def __init__(self, *, payload=None, lines=None, status_code=200, headers=None):
        self._payload = payload
        self._lines = lines or []
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(payload or {})
        self.closed = False

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_compatible_client_sends_json_completion_without_sdk():
    from vocal_more.infrastructure.openai_compatible import OpenAICompatibleClient

    response = FakeResponse(
        payload={"choices": [{"message": {"content": "ok"}}]},
        headers={"x-request-id": "req-1"},
    )
    session = FakeSession(response)
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://example.test/v1",
        session=session,
    )

    result = client.chat.completions.create(
        model="model",
        messages=[{"role": "user", "content": "hello"}],
        extra_body={"enable_thinking": False},
    )

    assert result.choices[0].message.content == "ok"
    assert result._request_id == "req-1"
    url, kwargs = session.calls[0]
    assert url == "https://example.test/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"]["enable_thinking"] is False
    assert response.closed is True


def test_compatible_client_parses_sse_and_closes_response():
    from vocal_more.infrastructure.openai_compatible import OpenAICompatibleClient

    response = FakeResponse(
        lines=[
            'data: {"id":"one","choices":[{"delta":{"content":"你"}}]}',
            'data: {"id":"one","choices":[{"delta":{"content":"好"}}]}',
            "data: [DONE]",
        ],
        headers={"x-dashscope-request-id": "req-stream"},
    )
    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://example.test/v1",
        session=FakeSession(response),
    )

    stream = client.chat.completions.create(model="model", messages=[], stream=True)
    chunks = list(stream)

    assert "".join(chunk.choices[0].delta.content for chunk in chunks) == "你好"
    assert all(chunk._request_id == "req-stream" for chunk in chunks)
    assert response.closed is True


def test_compatible_client_classifies_http_status():
    from vocal_more.infrastructure.openai_compatible import (
        CompatibleStatusError,
        OpenAICompatibleClient,
    )

    client = OpenAICompatibleClient(
        api_key="secret",
        base_url="https://example.test/v1",
        session=FakeSession(FakeResponse(status_code=429)),
    )

    with pytest.raises(CompatibleStatusError) as exc_info:
        client.chat.completions.create(model="model", messages=[])

    assert exc_info.value.status_code == 429
