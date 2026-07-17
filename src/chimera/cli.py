from __future__ import annotations

import argparse
import json
from typing import Sequence

from chimera.demo import run_demo


def _demo() -> int:
    kernel = run_demo()
    for offset, event in enumerate(kernel.events):
        record = {
            "offset": offset,
            "type": event.event_type,
            "actor": event.actor_id,
            "correlation_id": str(event.correlation_id),
            "causation_id": str(event.causation_id) if event.causation_id else None,
            "payload": dict(event.payload),
        }
        print(json.dumps(record, sort_keys=True))
    print("\nPROCESS TABLE")
    for process in kernel.process_table():
        print(f"{process.process_id:<12} state={process.state:<10} steps={process.budget.steps_used:<3} tool_calls={process.budget.tool_calls_used}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo", help="run the deterministic kernel demo")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        return _demo()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
