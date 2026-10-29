import argparse
import json
from pathlib import Path

from .core import FRAMEWORKS, load_cases, load_config, safe_error


def main():
    parser = argparse.ArgumentParser(description="OpenAI evaluation matrix and Databricks reporting")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("matrix")
    gen = sub.add_parser("generate")
    gen.add_argument("--model", required=True)
    gen.add_argument("--output", type=Path, required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--framework", choices=FRAMEWORKS, required=True)
    ev.add_argument("--model", required=True)
    ev.add_argument("--responses", type=Path, required=True)
    ev.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        load_cases(config)
        if args.command == "matrix":
            print(json.dumps({"model": config["models"], "framework": list(FRAMEWORKS)}))
            return 0
        if args.command == "generate":
            from .generation import generate
            report = generate(config, args.model, args.output)
            errors = sum(r["status"] != "ok" for r in report["responses"].values())
            print(json.dumps({"model": args.model, "cases": len(report["responses"]), "errors": errors}))
            return 1 if errors else 0
        from .runner import evaluate
        report = evaluate(config, args.framework, args.model, args.responses, args.out)
        print(json.dumps(report["summary"]))
        return 0 if report["summary"]["gate_passed"] else 1
    except Exception as exc:
        print("Runner error: " + safe_error(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
