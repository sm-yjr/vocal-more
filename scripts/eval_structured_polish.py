#!/usr/bin/env python3
"""Run a live Omni eval for structured polish list line breaks."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more import config as config_module
from vocal_more.config import Config, reload_config
from vocal_more.core.asr_engine import BatchASREngine
from vocal_more.dictionary import reload_dictionary


DEFAULT_MODEL = "qwen3.5-omni-flash-realtime"
DEFAULT_VOICE = "Tingting"

LIST_LINE_RE = re.compile(
    r"^\s*(?:\d{1,2}[.、．]|[一二三四五六七八九十][.、．]|[•·*-])\s*\S+"
)

CASES = [
    {
        "id": "gallery_morning",
        "title": "画廊晨会",
        "text": (
            "请把今天画廊开场前的安排整理成三点。第一，九点以前把青瓷展柜的灯调暗一点，"
            "不要让玻璃反光压过器物本身。第二，把入口处那束白色马蹄莲换成银莲花，"
            "让观众一进门就看到冷一点的春天。第三，十点半之前确认志愿者讲解词，"
            "每个人只讲一个故事，不要把说明牌念一遍。"
        ),
        "expected_items": 3,
        "required_terms": ["青瓷", "马蹄莲", "银莲花"],
    },
    {
        "id": "bookshop_salon",
        "title": "书店沙龙",
        "text": (
            "我想把周六书店沙龙的流程拆成四个部分。第一，先用十分钟播放海边火车的环境声，"
            "让大家从手机里出来。第二，请作者读一段关于雨夜码头的文字，语速要慢。"
            "第三，开放提问，但只收三个真正和文本有关的问题。第四，结束以后把没聊完的句子贴在留言墙上。"
        ),
        "expected_items": 4,
        "required_terms": ["书店", "雨夜码头", "留言墙"],
    },
    {
        "id": "poetry_workshop",
        "title": "诗歌工作坊",
        "text": (
            "这次诗歌工作坊我只想留下三个练习。第一，写一行完全没有形容词的月亮。"
            "第二，写一个只出现声音、不出现人物的房间。第三，把一句抱怨改成一句可以寄出去的明信片。"
        ),
        "expected_items": 3,
        "required_terms": ["月亮", "房间", "明信片"],
    },
]


def read_pcm_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1:
            raise ValueError(f"{path} must be mono WAV")
        if wav_file.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16-bit PCM WAV")
        return wav_file.readframes(wav_file.getnframes())


def synthesize_case(text: str, wav_path: Path, *, voice: str, rate: int) -> None:
    for command in ("say", "afconvert"):
        if not shutil.which(command):
            raise SystemExit(f"{command} is required to synthesize eval audio on macOS.")

    aiff_path = wav_path.with_suffix(".aiff")
    subprocess.run(
        ["say", "-v", voice, "-r", str(rate), "-o", str(aiff_path), text],
        check=True,
    )
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff_path), str(wav_path)],
        check=True,
    )


def apply_eval_config(base_config: Config, *, model: str) -> Config:
    config = copy.deepcopy(base_config)
    config.enable_polish = True
    config.asr.model = model
    config.asr.backend = "realtime_ws"
    config.asr.language = "zh"
    config.asr.use_dictionary_corpus = True
    config.llm.structured = True
    config.llm.level = "balanced"
    config.llm.tone = "neutral"
    config.llm.persona = "professional"
    config.llm.temperature = 0.0
    config.llm.enable_thinking = False
    config_module._config = config
    reload_dictionary()
    return config


def list_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if LIST_LINE_RE.match(line)]


def evaluate_output(case: dict, output: str) -> dict:
    lines = list_lines(output)
    missing_terms = [term for term in case["required_terms"] if term not in output]
    passed = (
        "\n" in output
        and len(lines) >= case["expected_items"]
        and not missing_terms
    )
    return {
        "id": case["id"],
        "title": case["title"],
        "passed": passed,
        "expected_items": case["expected_items"],
        "list_line_count": len(lines),
        "missing_terms": missing_terms,
        "output": output,
    }


def run_eval(model: str, voice: str, rate: int) -> dict:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("DASHSCOPE_API_KEY is required for live Omni eval.")

    base_config = reload_config()
    apply_eval_config(base_config, model=model)
    engine = BatchASREngine()

    case_results = []
    with tempfile.TemporaryDirectory(prefix="vocal-more-structured-eval-") as temp_dir:
        audio_dir = Path(temp_dir)
        for case in CASES:
            wav_path = audio_dir / f"{case['id']}.wav"
            synthesize_case(case["text"], wav_path, voice=voice, rate=rate)

            start = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                output = engine.transcribe(read_pcm_from_wav(wav_path))
            latency_ms = round((time.perf_counter() - start) * 1000, 2)

            result = evaluate_output(case, output)
            result["latency_ms"] = latency_ms
            case_results.append(result)

    return {
        "model": model,
        "voice": voice,
        "rate": rate,
        "passed": all(result["passed"] for result in case_results),
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", type=int, default=175)
    args = parser.parse_args()

    report = run_eval(model=args.model, voice=args.voice, rate=args.rate)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
