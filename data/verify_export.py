"""
verify_export.py — Manifest reconciliation for parsed BMS CSV exports
=====================================================================
Reads manifest.json and parsed_counts.json (produced by parse_export.py),
sums the raw data rows per source file, and asserts that every file's count
matches the manifest exactly.

Usage
-----
    python data/verify_export.py                       # looks in same dir as this script
    python data/verify_export.py --data-dir /path/to/data

Exit codes
----------
    0  all files PASS
    1  one or more files FAIL (mismatch or missing)

Output format (per file)
------------------------
    [PASS]  component_sensors.csv  expected=14400  parsed=14400
    [FAIL]  device_meters.csv      expected=7200   parsed=7156   delta=-44
    [MISS]  device_unknown.csv     expected=5000   (not found in parsed_counts)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ANSI colours — disabled automatically on non-TTY (CI logs)
_IS_TTY = sys.stdout.isatty()
_GREEN  = "\033[32m" if _IS_TTY else ""
_RED    = "\033[31m" if _IS_TTY else ""
_YELLOW = "\033[33m" if _IS_TTY else ""
_BOLD   = "\033[1m"  if _IS_TTY else ""
_RESET  = "\033[0m"  if _IS_TTY else ""


def _load_json(path: Path) -> dict:
    if not path.exists():
        print(f"{_RED}[ERROR]{_RESET} File not found: {path}")
        sys.exit(1)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _print_separator(char: str = "-", width: int = 72) -> None:
    print(char * width)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Verify parsed row counts against manifest.json."
    )
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory containing manifest.json and parsed_counts.json (default: same dir as this script)",
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Explicit path to manifest.json (overrides --data-dir lookup)",
    )
    ap.add_argument(
        "--counts",
        type=Path,
        default=None,
        help="Explicit path to parsed_counts.json (overrides --data-dir lookup)",
    )
    args = ap.parse_args()

    data_dir: Path = args.data_dir.resolve()
    manifest_path = (args.manifest or data_dir / "manifest.json").resolve()
    counts_path   = (args.counts   or data_dir / "parsed_counts.json").resolve()

    # ------------------------------------------------------------------
    # Load both JSON files
    # ------------------------------------------------------------------
    manifest   = _load_json(manifest_path)
    counts_doc = _load_json(counts_path)

    # manifest.json schema:
    #   { "files": ["name.csv", ...] }              -- list only (row count unknown)
    # OR
    #   { "files": { "name.csv": 14400, ... } }     -- dict with expected counts
    # OR
    #   { "files": [{"name": "name.csv", "rows": 14400}, ...] }  -- list of objects
    #
    # We support all three shapes.
    raw_files = manifest.get("files", {})
    expected: dict[str, int | None] = {}

    if isinstance(raw_files, dict):
        expected = {k: v for k, v in raw_files.items()}
    elif isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, str):
                expected[item] = None          # count unknown — existence-only check
            elif isinstance(item, dict):
                name = item.get("name") or item.get("file") or item.get("filename", "")
                rows = item.get("rows") or item.get("row_count") or item.get("count")
                expected[name] = int(rows) if rows is not None else None
    else:
        print(f"{_RED}[ERROR]{_RESET} Unrecognised manifest.files structure.")
        sys.exit(1)

    # parsed_counts.json schema:
    #   { "parsed_counts": { "name.csv": 14400, ... } }
    parsed: dict[str, int] = counts_doc.get("parsed_counts", {})

    # ------------------------------------------------------------------
    # Reconcile
    # ------------------------------------------------------------------
    all_file_names = sorted(set(expected) | set(parsed))

    pass_count  = 0
    fail_count  = 0
    miss_count  = 0
    extra_count = 0

    _print_separator("=")
    print(f"{_BOLD}BMS Export Verification — {manifest_path.name} vs {counts_path.name}{_RESET}")
    _print_separator("=")

    for filename in all_file_names:
        in_manifest = filename in expected
        in_parsed   = filename in parsed

        if in_manifest and not in_parsed:
            # File listed in manifest but parser never saw it
            exp_val = expected[filename]
            exp_str = f"expected={exp_val}" if exp_val is not None else "expected=?"
            print(f"{_YELLOW}[MISS]{_RESET}  {filename:<45}  {exp_str}  (not found in parsed_counts)")
            miss_count += 1

        elif not in_manifest and in_parsed:
            # Parser found a file that manifest does not list — informational
            parsed_val = parsed[filename]
            print(f"{_YELLOW}[XTRA]{_RESET}  {filename:<45}  parsed={parsed_val}  (not in manifest)")
            extra_count += 1

        else:
            # Both sides have an entry
            exp_val    = expected[filename]
            parsed_val = parsed[filename]

            if exp_val is None:
                # Manifest only lists the filename without a row count.
                # A non-zero parse count is considered a pass.
                if parsed_val > 0:
                    print(f"{_GREEN}[PASS]{_RESET}  {filename:<45}  parsed={parsed_val}  (no expected count in manifest)")
                    pass_count += 1
                else:
                    print(f"{_RED}[FAIL]{_RESET}  {filename:<45}  parsed={parsed_val}  (empty — 0 rows)")
                    fail_count += 1
            else:
                exp_val = int(exp_val)
                delta = parsed_val - exp_val
                if delta == 0:
                    print(f"{_GREEN}[PASS]{_RESET}  {filename:<45}  expected={exp_val:<8}  parsed={parsed_val}")
                    pass_count += 1
                else:
                    sign = "+" if delta > 0 else ""
                    print(
                        f"{_RED}[FAIL]{_RESET}  {filename:<45}  expected={exp_val:<8}  "
                        f"parsed={parsed_val:<8}  delta={sign}{delta}"
                    )
                    fail_count += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _print_separator("-")
    total = pass_count + fail_count + miss_count
    print(
        f"\n{_BOLD}Summary{_RESET}: "
        f"{_GREEN}{pass_count} PASS{_RESET}  "
        f"{_RED}{fail_count} FAIL{_RESET}  "
        f"{_YELLOW}{miss_count} MISS{_RESET}  "
        f"{_YELLOW}{extra_count} XTRA{_RESET}  "
        f"(of {total} manifest entries)"
    )

    if fail_count == 0 and miss_count == 0:
        print(f"\n{_GREEN}{_BOLD}ALL CHECKS PASSED{_RESET}")
    else:
        problems = []
        if fail_count:
            problems.append(f"{fail_count} file(s) have row-count mismatches")
        if miss_count:
            problems.append(f"{miss_count} manifest file(s) were not parsed")
        print(f"\n{_RED}{_BOLD}VERIFICATION FAILED:{_RESET} {'; '.join(problems)}")

    _print_separator("=")

    # Exit 1 if any failure or missing file; extras are warnings only
    sys.exit(0 if (fail_count == 0 and miss_count == 0) else 1)


if __name__ == "__main__":
    main()
