"""Diagnose a log file with OpsPilot and pretty-print the result.

Usage:
    python run_local.py                                  # uses samples/ecs_oom.log
    python run_local.py samples/alb_health_check.log
    python run_local.py samples/missing_env_var.log --json
    python run_local.py --list-models                    # find the Nemotron model ID for .env
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

import openai
from dotenv import load_dotenv

from opspilot.diagnose import DiagnosisError, diagnose, get_client

DEFAULT_SAMPLE = Path(__file__).parent / "samples" / "ecs_oom.log"
WIDTH = 88


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OpsPilot on a local log file.")
    parser.add_argument("log_file", nargs="?", type=Path, default=DEFAULT_SAMPLE,
                        help="log file to diagnose (default: samples/ecs_oom.log)")
    parser.add_argument("--json", action="store_true",
                        help="print the raw JSON instead of the formatted report")
    parser.add_argument("--list-models", action="store_true",
                        help="list the Nemotron model IDs available on your Nebius account")
    args = parser.parse_args()

    # Model output can contain characters such as non-breaking hyphens, which Windows' default
    # cp1252 encoding can't print when output is piped or redirected. UTF-8 handles them all.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    # Local only: copies .env into os.environ. In Lambda the same variables come from the
    # function configuration, which is why diagnose.py never loads .env itself.
    load_dotenv()

    try:
        if args.list_models:
            list_nemotron_models()
            return 0
        result = diagnose(args.log_file.read_text(encoding="utf-8"))
    except (DiagnosisError, RuntimeError, OSError, openai.APIError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(args.log_file, result)
    return 0


def print_report(path: Path, result: dict) -> None:
    def field(label: str, text: str) -> None:
        print(textwrap.fill(text, WIDTH, initial_indent=f"{label:<12}",
                            subsequent_indent=" " * 12))

    print(f"\nOpsPilot diagnosis: {path.name}")
    print("=" * WIDTH)
    field("Summary", result["summary"])
    field("Service", result["affected_service"])
    field("Severity", result["severity"].upper())
    field("Confidence", f"{result['confidence']:.0%}")
    print()
    field("Root cause", result["root_cause"])
    print("\nFix steps")
    for number, step in enumerate(result["fix_steps"], start=1):
        print(textwrap.fill(step, WIDTH, initial_indent=f"  {number}. ",
                            subsequent_indent="     "))
    print()


def list_nemotron_models() -> None:
    model_ids = sorted(model.id for model in get_client().models.list())
    nemotron = [model_id for model_id in model_ids if "nemotron" in model_id.lower()]
    if not nemotron:
        print("No model with 'nemotron' in its ID was found. All available models:")
    print("\n".join(nemotron or model_ids))


if __name__ == "__main__":
    sys.exit(main())
