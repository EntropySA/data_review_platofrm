#!/usr/bin/env python3
"""Review an upload document against deterministic rules and report failures.

    python auto_review.py --type arabizi data.json -o failures.xlsx

Reads nothing but the file named on the command line and writes nothing but the
workbook: no network, no credentials, no database. Feed the workbook it produces
to apply_review.py to record those failures in the platform.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from autoreview import DATA_TYPES, NOTES, failure_rows, load_records, review_records
from reporting import create_failure_export


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="upload JSON, in the format the platform imports")
    parser.add_argument("--type", required=True, choices=DATA_TYPES, dest="data_type",
                        help="which set of rules to apply")
    parser.add_argument("-o", "--output", type=Path,
                        help="workbook to write (default: <source>.failures.xlsx)")
    return parser.parse_args(argv)


def summarise(records, findings, unchecked, rows, out) -> None:
    print(f"read      {len(records)} records", file=out)
    print(f"failed    {len(rows)} records", file=out)
    for check, count in Counter(item.check for item in findings).most_common():
        print(f"  {count:>6}  {check:<18} {NOTES[check]}", file=out)
    if unchecked:
        print(f"unchecked {len(unchecked)} records", file=out)
        for reason, count in Counter(item.reason for item in unchecked).most_common():
            print(f"  {count:>6}  {reason}", file=out)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = json.loads(args.source.read_text(encoding="utf-8"))
        records, malformed = load_records(document)
    except (OSError, ValueError) as exc:
        print(f"Could not read {args.source}: {exc}", file=sys.stderr)
        return 2

    findings, unchecked = review_records(records, args.data_type)
    unchecked = malformed + unchecked
    rows = failure_rows(records, findings)

    destination = args.output or args.source.with_suffix(".failures.xlsx")
    destination.write_bytes(create_failure_export(rows, [item._asdict() for item in unchecked]))

    summarise(records, findings, unchecked, rows, sys.stdout)
    print(f"\nwrote     {destination}", file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
