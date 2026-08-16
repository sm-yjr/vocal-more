"""Small OpenAI-compatible chat client for the DashScope endpoints we use."""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import requests


class CompatibleAPIError(RuntimeError):
    """Base error raised by the narrow compatible API client."""


class CompatibleConnectionError(CompatibleAPIError):
    """The request could not reach the provider."""


class CompatibleTimeoutError(CompatibleAPIError):
    """The provider did not respond before the configured deadline."""


class CompatibleStatusError(CompatibleAPIError):
    """The provider returned a non-success HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


def _as_object(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _as_object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_as_object(item) for item in value]
    return value


class _ChatCompletionStream:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.request_id = (
            response.headers.get("x-request-id")
            or response.headers.get("x-dashscope-request-id")
            or response.headers.get("x-acs-request-id")
        )

    def __iter__(self) -> Iterator[object]:
        try:
            for raw_line in self.response.iter_lines(decode_unicode=True):
                line = str(raw_line or "").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    item = _as_object(json.loads(payload))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise CompatibleAPIError("Provider returned invalid SSE JSON") from exc
                item._request_id = self.request_id
                yield item
        finally:
            self.response.close()


class _ChatCompletions:
    def __init__(self, owner: OpenAICompatibleClient) -> None:
        self._owner = owner

    def create(self, **kwargs):
        timeout = float(kwargs.pop("timeout", self._owner.timeout))
        extra_body = kwargs.pop("extra_body", None)
        if isinstance(extra_body, dict):
            kwargs.update(extra_body)
        stream = bool(kwargs.get("stream", False))
        response = self._owner._post(
            "/chat/completions",
            payload=kwargs,
            timeout=timeout,
            stream=stream,
        )
        if stream:
            return _ChatCompletionStream(response)
        try:
            result = _as_object(response.json())
            result._request_id = response.headers.get("x-request-id")
            return result
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CompatibleAPIError("Provider returned invalid JSON") from exc
        finally:
            response.close()


class OpenAICompatibleClient:
    """Implement only ``chat.completions.create`` without the OpenAI SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = str(api_key)
        self.base_url = str(base_url).rstrip("/")
        self.timeout = float(timeout)
        self._session = session or requests.Session()
        self.chat = SimpleNamespace(completions=_ChatCompletions(self))

    def _post(
        self,
        path: str,
        *,
        payload: dict,
        timeout: float,
        stream: bool,
    ) -> requests.Response:
        try:
            response = self._session.post(
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
                stream=stream,
            )
        except requests.Timeout as exc:
            raise CompatibleTimeoutError(str(exc)) from exc
        except requests.ConnectionError as exc:
            raise CompatibleConnectionError(str(exc)) from exc
        except requests.RequestException as exc:
            raise CompatibleAPIError(str(exc)) from exc

        if not 200 <= response.status_code < 300:
            details = response.text[:500]
            response.close()
            raise CompatibleStatusError(
                f"HTTP {response.status_code}: {details}",
                status_code=response.status_code,
            )
        return response


__all__ = [
    "CompatibleAPIError",
    "CompatibleConnectionError",
    "CompatibleStatusError",
    "CompatibleTimeoutError",
    "OpenAICompatibleClient",
]
