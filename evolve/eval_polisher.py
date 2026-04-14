"""Evaluation script for text_polisher.py auto-evolve loop.

Runs test cases through the TextPolisher, measures pass rate and latency,
and saves outputs for LLM-as-Judge review.

Output format (stdout): key: value lines for metric extraction.
Output file: evolve/eval_output.json (for LLM-as-Judge).
"""

import json
import sys
import time
from pathlib import Path

# Add src to path so we can import vocal_more
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vocal_more.config import Config, LLMConfig, get_config, _config
import vocal_more.config as config_module


def override_llm_config(level: str, tone: str, persona: str) -> None:
    """Override the global config's LLM settings for a test case."""
    cfg = get_config()
    cfg.llm.level = level
    cfg.llm.tone = tone
    cfg.llm.persona = persona


def run_eval() -> None:
    cases_path = Path(__file__).parent / "test_cases.json"
    output_path = Path(__file__).parent / "eval_output.json"

    with open(cases_path) as f:
        cases = json.load(f)

    # Force-load config once (ensures api_key is set)
    get_config()

    # Import after path setup
    from vocal_more.core.text_polisher import TextPolisher

    results = []
    total_time = 0.0
    pass_count = 0
    error_count = 0

    for case in cases:
        case_id = case["id"]
        input_text = case["input"]
        level = case["level"]
        tone = case["tone"]
        persona = case["persona"]

        # Override config for this case
        override_llm_config(level, tone, persona)

        # Create a fresh polisher (picks up config changes)
        polisher = TextPolisher()

        start = time.time()
        try:
            result = polisher.polish(input_text)
            elapsed = time.time() - start
            total_time += elapsed

            output = result.polished_text
            used_llm = result.used_llm

            if output and len(output.strip()) > 0:
                pass_count += 1
                status = "pass"
            else:
                status = "empty"

            results.append({
                "id": case_id,
                "input": input_text,
                "output": output,
                "level": level,
                "tone": tone,
                "persona": persona,
                "description": case["description"],
                "used_llm": used_llm,
                "latency_s": round(elapsed, 3),
                "status": status,
            })

        except Exception as e:
            elapsed = time.time() - start
            total_time += elapsed
            error_count += 1
            results.append({
                "id": case_id,
                "input": input_text,
                "output": "",
                "level": level,
                "tone": tone,
                "persona": persona,
                "description": case["description"],
                "used_llm": False,
                "latency_s": round(elapsed, 3),
                "status": f"error: {e}",
            })

    # Save detailed output for LLM-as-Judge
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Print metrics (key: value format for extract)
    n = len(cases)
    pass_rate = pass_count / n if n > 0 else 0
    avg_latency = total_time / n if n > 0 else 0
    llm_used_count = sum(1 for r in results if r.get("used_llm"))

    print(f"pass_rate:          {pass_rate:.4f}")
    print(f"avg_latency:        {avg_latency:.3f}")
    print(f"error_count:        {error_count}")
    print(f"llm_used_count:     {llm_used_count}")
    print(f"total_cases:        {n}")


if __name__ == "__main__":
    run_eval()
