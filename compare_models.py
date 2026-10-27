"""Compare Nemotron models on the sample incidents: same prompt, same logs, side by side.

Usage:
    python compare_models.py                    # Nano vs Super on every file in samples/
    python compare_models.py --models nvidia/nemotron-3-super-120b-a12b nvidia/Nemotron-3-Ultra-550b-a55b

Prints one row per (sample, model) with severity, calls, time, tokens and cost, and saves the
full answers to comparison.json so you can read them side by side.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import openai
from dotenv import load_dotenv

from opspilot.diagnose import DiagnosisError, diagnose, get_client

SAMPLES_DIR = Path(__file__).parent / "samples"
OUTPUT_FILE = Path(__file__).parent / "comparison.json"

# USD per 1M tokens (input, output) on Nebius Token Factory public endpoints, Oct 2026.
PRICES = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
}


class UsageMeter:
    """Counts model calls and tokens by wrapping the client, so diagnose() stays unchanged."""

    def __init__(self, client: openai.OpenAI):
        self._create = client.chat.completions.create
        client.chat.completions.create = self._counting_create
        self.reset()

    def reset(self) -> None:
        self.calls = self.tokens_in = self.tokens_out = 0

    def _counting_create(self, **kwargs):
        response = self._create(**kwargs)
        self.calls += 1
        if response.usage:
            self.tokens_in += response.usage.prompt_tokens
            self.tokens_out += response.usage.completion_tokens
        return response

    def cost(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        price_in, price_out = PRICES[model]
        return (self.tokens_in * price_in + self.tokens_out * price_out) / 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Nemotron models on the sample logs.")
    parser.add_argument("--models", nargs="+", default=list(PRICES),
                        help="model IDs to compare (default: Nano and Super)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    meter = UsageMeter(get_client())
    results = {}

    print(f"{'sample':<22} {'model':<34} {'severity':<9} {'conf':>5} {'calls':>5} "
          f"{'time':>6} {'tokens in/out':>14} {'cost':>9}")
    for sample in sorted(SAMPLES_DIR.glob("*.log")):
        logs = sample.read_text(encoding="utf-8")
        for model in args.models:
            meter.reset()
            started = time.perf_counter()
            try:
                diagnosis = diagnose(logs, model=model)
            except (DiagnosisError, openai.APIError) as err:
                diagnosis = {"error": str(err)}
            seconds = time.perf_counter() - started
            cost = meter.cost(model)

            results.setdefault(sample.name, {})[model] = {
                "seconds": round(seconds, 1), "calls": meter.calls,
                "tokens_in": meter.tokens_in, "tokens_out": meter.tokens_out,
                "cost_usd": cost, "diagnosis": diagnosis,
            }
            severity = diagnosis.get("severity", "FAILED")
            confidence = f"{diagnosis['confidence']:.0%}" if "confidence" in diagnosis else "-"
            cost_text = f"${cost:.5f}" if cost is not None else "?"
            print(f"{sample.name:<22} {model.split('/')[-1]:<34} {severity:<9} {confidence:>5} "
                  f"{meter.calls:>5} {seconds:>5.1f}s "
                  f"{f'{meter.tokens_in}/{meter.tokens_out}':>14} {cost_text:>9}")
            print(f"{'':<22} {diagnosis.get('summary', diagnosis.get('error', ''))[:120]}")

    OUTPUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(run["cost_usd"] or 0 for runs in results.values() for run in runs.values())
    print(f"\nTotal cost: ${total:.4f}. Full answers saved to {OUTPUT_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
