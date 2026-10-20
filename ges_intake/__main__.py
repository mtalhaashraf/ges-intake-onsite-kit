"""Entry point: python -m ges_intake --library DIR --clients DIR run --client ID --inbox DIR [--inbox DIR ...] --as-of DATE --outbox FILE

The argument parsing and output format below are provided so you can spend your time on the
engine. Everything else (modules, classes, how the stages fit together) is your design.
Exit codes: 0 success, 2 bad input / bad library / unknown client.
"""
import argparse
import json
import sys
from datetime import date

from ges_intake import engine
from ges_intake.errors import InputError


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="ges_intake")
    p.add_argument("--library", required=True)
    p.add_argument("--clients", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--client", required=True)
    r.add_argument("--inbox", required=True, action="append", help="repeatable; processed in the given order")
    r.add_argument("--as-of", required=True, type=date.fromisoformat)
    r.add_argument("--outbox", required=True, help="file the advisory stub appends to")
    return p.parse_args(argv)


def run(args) -> dict:
    """Ingest every inbox in order, then evaluate readiness. Return the report described in TASK.md §3."""
    return engine.run(args.library, args.clients, args.client, args.inbox, args.as_of, args.outbox)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        report = run(args)
    except InputError as exc:
        sys.stderr.write(f"ges_intake: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
