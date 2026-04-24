"""Meeting notes generation with initial dual-speaker diarization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import dashscope
from dashscope import Generation, MultiModalConversation

from ..config import get_llm_model_info
from ..infrastructure.pricing import (
    build_polish_billing,
    extract_usage_from_response,
)
from ..infrastructure.pricing import merge_billing


DEFAULT_MEETING_NOTES_MODEL = "qwen3.5-omni-plus"

MEETING_TRANSCRIPT_SYSTEM_PROMPT = """你是会议逐字稿助手。请直接从音频中识别最多两位发言人，并输出按时间排列的双人逐字稿。

要求：
- 初期仅支持双人对话；如果只有一位发言人，也保留 speaker_1。
- 通过声音、说话顺序和上下文区分发言人，不要编造真实姓名；不知道姓名时使用 Speaker 1 / Speaker 2。
- 按音频时间顺序输出一条对话时间线，保持你一句我一句的 turn 顺序，不要按发言人分轨道聚合。
- 每一行是一段发言，尽量给出开始和结束时间；无法确定结束时间时可以只给开始时间。
- 保留关键原话，不要把不同发言人的内容合并到同一段。
- 只输出双人逐字稿，不要输出概述、关键点、待办或会议纪要。
- 不要输出代码块、表格或额外说明。

每行格式：
[00:00.00 - 00:03.20] Speaker 1: 发言内容
[00:03.20 - 00:06.40] Speaker 2: 发言内容
"""

MEETING_MINUTES_SYSTEM_PROMPT = """你是会议纪要助手。你会收到已经分好 Speaker 1 / Speaker 2 的逐字稿。

要求：
- 只基于逐字稿生成纪要，不要补充逐字稿里没有的信息。
- summary 用 1-2 句话概括。
- key_points 保留关键讨论点。
- action_items 只提取明确的待办；没有待办时返回空数组。
- 只输出 JSON，不要输出 Markdown。

JSON 结构：
{
  "summary": "会议概述",
  "key_points": ["关键点"],
  "action_items": ["待办"]
}
"""


@dataclass
class MeetingNotesResult:
    """Structured meeting notes plus model billing metadata."""

    notes: dict[str, Any]
    billing: dict | None = None


@dataclass
class MeetingMinutesResult:
    """Second-stage meeting minutes plus billing metadata."""

    minutes: dict[str, Any]
    billing: dict | None = None


def _merge_meeting_billing(metering: dict | None) -> dict | None:
    if not metering:
        return None
    if metering.get("stage") == "asr":
        return merge_billing(metering)
    asr_metering = dict(metering)
    cost = float(asr_metering.get("cost_cny") or 0.0)
    merged = merge_billing({"stage": "asr", "cost_cny": cost})
    if merged is None:
        return None
    merged["asr"] = asr_metering
    return merged


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    if "{" in stripped and "}" in stripped:
        return stripped[stripped.find("{"):stripped.rfind("}") + 1]
    return stripped


def _repair_common_json_issues(text: str) -> str:
    repaired = re.sub(
        r'(,\s*)("(?:(?:\\.)|[^"\\])*")(\s*})',
        r'\1"text": \2\3',
        text,
    )
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


def _load_json_object(response_text: str) -> dict[str, Any]:
    stripped = _strip_json_fence(response_text)
    candidates = [stripped, _repair_common_json_issues(stripped)]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, str):
            nested = _load_json_object(payload)
            if nested:
                return nested
        if isinstance(payload, dict):
            return payload
    return {}


def _speaker_id(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    if text in {"speaker_2", "speaker2", "2", "b", "speaker_b", "发言人2", "说话人2"}:
        return "speaker_2"
    return "speaker_1"


def _seconds_from_timestamp(value: str) -> float | None:
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        return max(0.0, minutes * 60 + seconds)
    hours, minutes, seconds = numbers
    return max(0.0, hours * 3600 + minutes * 60 + seconds)


def _coerce_seconds(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip().lower()
    if not text:
        return None
    if ":" in text:
        return _seconds_from_timestamp(text)
    text = text.removesuffix("seconds").removesuffix("second").removesuffix("secs").removesuffix("sec").removesuffix("s")
    try:
        return max(0.0, float(text.strip()))
    except ValueError:
        return None


def _format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _normalize_speakers(raw_speakers: object) -> list[dict[str, str]]:
    labels: dict[str, str] = {}
    if isinstance(raw_speakers, list):
        for index, entry in enumerate(raw_speakers[:2], start=1):
            if not isinstance(entry, dict):
                continue
            speaker_id = _speaker_id(entry.get("id") or entry.get("speaker") or index)
            label = str(entry.get("label") or entry.get("name") or "").strip()
            labels[speaker_id] = label or f"Speaker {1 if speaker_id == 'speaker_1' else 2}"

    return [
        {"id": "speaker_1", "label": labels.get("speaker_1", "Speaker 1")},
        {"id": "speaker_2", "label": labels.get("speaker_2", "Speaker 2")},
    ]


def _normalize_segments(raw_segments: object, speakers: list[dict[str, str]]) -> list[dict[str, Any]]:
    speaker_labels = {speaker["id"]: speaker["label"] for speaker in speakers}
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for entry in raw_segments:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or entry.get("content") or "").strip()
            if not text:
                continue
            speaker_id = _speaker_id(entry.get("speaker") or entry.get("speaker_id"))
            segment = {
                "speaker": speaker_id,
                "speaker_label": speaker_labels.get(speaker_id, "Speaker 1"),
                "text": text,
            }
            start_seconds = _coerce_seconds(
                entry.get("start_seconds")
                if "start_seconds" in entry
                else entry.get("start")
            )
            end_seconds = _coerce_seconds(
                entry.get("end_seconds")
                if "end_seconds" in entry
                else entry.get("end")
            )
            if start_seconds is not None:
                segment["start_seconds"] = round(start_seconds, 3)
                segment["timestamp"] = _format_timestamp(start_seconds)
            if end_seconds is not None:
                segment["end_seconds"] = round(end_seconds, 3)
            segments.append(segment)
    if segments and all("start_seconds" in segment for segment in segments):
        segments.sort(key=lambda segment: segment["start_seconds"])
    return segments


def _strip_text_fence(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:text|txt)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return stripped


def _split_time_range(value: str) -> tuple[float | None, float | None]:
    parts = re.split(r"\s*(?:->|[-–—~]|至|到)\s*", value.strip(), maxsplit=1)
    start_seconds = _coerce_seconds(parts[0]) if parts else None
    end_seconds = _coerce_seconds(parts[1]) if len(parts) > 1 else None
    return start_seconds, end_seconds


_TIMELINE_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:[-*]\s*)?
    (?:\[(?P<time>[^\]]+)\]\s*)?
    (?P<speaker>speaker\s*[_-]?\s*[12]|speaker[12]|发言人\s*[12]|说话人\s*[12])
    \s*[：:]\s*
    (?P<text>.+?)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_timeline_segments(response_text: str, speakers: list[dict[str, str]]) -> list[dict[str, Any]]:
    speaker_labels = {speaker["id"]: speaker["label"] for speaker in speakers}
    segments: list[dict[str, Any]] = []
    for raw_line in _strip_text_fence(response_text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _TIMELINE_LINE_RE.match(line)
        if not match:
            continue

        speaker_id = _speaker_id(match.group("speaker"))
        text = match.group("text").strip()
        if not text:
            continue

        segment = {
            "speaker": speaker_id,
            "speaker_label": speaker_labels.get(speaker_id, "Speaker 1"),
            "text": text,
        }
        time_range = match.group("time")
        if time_range:
            start_seconds, end_seconds = _split_time_range(time_range)
            if start_seconds is not None:
                segment["start_seconds"] = round(start_seconds, 3)
                segment["timestamp"] = _format_timestamp(start_seconds)
            if end_seconds is not None:
                segment["end_seconds"] = round(end_seconds, 3)
        segments.append(segment)

    if segments and all("start_seconds" in segment for segment in segments):
        segments.sort(key=lambda segment: segment["start_seconds"])
    return segments


def _format_meeting_transcript(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        prefix = f"{segment['speaker_label']}:"
        if segment.get("timestamp"):
            prefix = f"[{segment['timestamp']}] {prefix}"
        lines.append(f"{prefix} {segment['text']}")
    return "\n".join(lines)


def parse_meeting_transcript_response(
    response_text: str,
    *,
    fallback_transcript: str = "",
) -> dict[str, Any]:
    """Parse and normalize the model's dual-speaker transcript."""
    payload = _load_json_object(response_text)

    speakers = _normalize_speakers(payload.get("speakers"))
    segments = _normalize_segments(payload.get("segments"), speakers)
    if not segments:
        segments = _parse_timeline_segments(response_text, speakers)
    if not segments and fallback_transcript.strip():
        segments = [
            {
                "speaker": "speaker_1",
                "speaker_label": speakers[0]["label"],
                "text": fallback_transcript.strip(),
            }
        ]

    transcript = _format_meeting_transcript(segments)
    return {
        "speaker_count": len({segment["speaker"] for segment in segments}) if segments else 0,
        "speakers": speakers,
        "segments": segments,
        "transcript": transcript,
    }


def parse_meeting_minutes_response(response_text: str) -> dict[str, Any]:
    """Parse and normalize the model's text-only meeting minutes JSON."""
    payload = _load_json_object(response_text)

    def _list_field(name: str) -> list[str]:
        raw = payload.get(name)
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    return {
        "status": "success",
        "summary": str(payload.get("summary") or "").strip(),
        "key_points": _list_field("key_points"),
        "action_items": _list_field("action_items"),
        "error": None,
    }


class MeetingMinutesGenerator:
    """Generate text-only meeting minutes from a speaker-attributed transcript."""

    def __init__(self, *, config) -> None:
        self._config = config

    def _call_generation(self, transcript: str):
        dashscope.api_key = self._config.api_key or None
        info = get_llm_model_info(self._config.llm.model)
        if info and info.get("api") == "multimodal_conversation":
            return MultiModalConversation.call(
                model=self._config.llm.model,
                messages=[
                    {
                        "role": "system",
                        "content": [{"text": MEETING_MINUTES_SYSTEM_PROMPT}],
                    },
                    {"role": "user", "content": [{"text": transcript}]},
                ],
                enable_thinking=self._config.llm.enable_thinking,
                temperature=self._config.llm.temperature,
                max_tokens=self._config.llm.max_tokens,
            )

        return Generation.call(
            model=self._config.llm.model,
            messages=[
                {"role": "system", "content": MEETING_MINUTES_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=self._config.llm.temperature,
            max_tokens=self._config.llm.max_tokens,
            enable_thinking=self._config.llm.enable_thinking,
            result_format="message",
        )

    def _extract_response_text(self, response) -> str:
        choice = response.output.choices[0].message.content
        if isinstance(choice, str):
            return choice

        chunks = []
        for item in choice or []:
            if isinstance(item, dict) and "text" in item:
                chunks.append(str(item["text"]))
            elif isinstance(item, str):
                chunks.append(item)
        return "".join(chunks)

    def generate(self, transcript: str) -> MeetingMinutesResult:
        response = self._call_generation(transcript)
        if response.status_code != 200:
            raise RuntimeError(f"API error: {response.code} - {response.message}")
        minutes = parse_meeting_minutes_response(self._extract_response_text(response))
        billing = build_polish_billing(
            model=self._config.llm.model,
            enable_thinking=self._config.llm.enable_thinking,
            usage=extract_usage_from_response(response),
        )
        return MeetingMinutesResult(minutes=minutes, billing=billing)


class MeetingNotesService:
    """Generate structured meeting notes from a saved recording."""

    def __init__(
        self,
        *,
        config,
        engine_factory: Callable[[], object] | None = None,
        minutes_generator_factory: Callable[[], object] | None = None,
        model: str = DEFAULT_MEETING_NOTES_MODEL,
    ) -> None:
        self._config = config
        self._engine_factory = engine_factory
        self._minutes_generator_factory = minutes_generator_factory
        self._model = model

    def _build_engine(self):
        if self._engine_factory is None:
            from ..core.asr_engine import BatchASREngine

            return BatchASREngine()
        return self._engine_factory()

    def _build_minutes_generator(self):
        if self._minutes_generator_factory is None:
            return MeetingMinutesGenerator(config=self._config)
        return self._minutes_generator_factory()

    def generate(
        self,
        pcm_data: bytes,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> MeetingNotesResult:
        engine = self._build_engine()
        if on_stage is not None:
            on_stage("meeting_transcribing")
        response_text = engine.transcribe_with_system_prompt(
            pcm_data,
            system_prompt=MEETING_TRANSCRIPT_SYSTEM_PROMPT,
            model_override=self._model,
            language_override=getattr(self._config.asr, "language", "auto"),
        )
        transcript_notes = parse_meeting_transcript_response(
            response_text,
            fallback_transcript=response_text,
        )
        if not transcript_notes["transcript"].strip():
            raise RuntimeError("Empty meeting transcript result")

        transcript_metering = (
            engine.get_last_metering()
            if hasattr(engine, "get_last_metering")
            else None
        )
        transcript_billing = _merge_meeting_billing(transcript_metering)

        notes = dict(transcript_notes)
        notes["status"] = "summarizing"
        notes["minutes"] = {
            "status": "pending",
            "summary": "",
            "key_points": [],
            "action_items": [],
            "error": None,
        }
        notes["billing"] = {"transcript": transcript_billing, "minutes": None}

        try:
            if on_stage is not None:
                on_stage("meeting_summarizing")
            minutes_result = self._build_minutes_generator().generate(notes["transcript"])
            notes["status"] = "success"
            notes["minutes"] = minutes_result.minutes
            notes["billing"]["minutes"] = merge_billing(minutes_result.billing)
        except Exception as exc:
            notes["status"] = "partial"
            notes["minutes"] = {
                "status": "failed",
                "summary": "",
                "key_points": [],
                "action_items": [],
                "error": str(exc),
            }

        return MeetingNotesResult(
            notes=notes,
            billing=merge_billing(transcript_metering, minutes_result.billing)
            if "minutes_result" in locals()
            else merge_billing(transcript_metering),
        )


parse_meeting_notes_response = parse_meeting_transcript_response
