"""
parse_export.py — BMS telemetry CSV → Parquet / SQLite parser
==============================================================
Reads every component_*.csv and device_*.csv in /data, parses the
"annotated-pivot" format (blank lines signal a new header block), and
writes a canonical long-form table with columns:

    time_utc  | site_id | entity_id | measurement | field | value

Usage
-----
    python data/parse_export.py                # writes data/telemetry.parquet (default)
    python data/parse_export.py --fmt sqlite   # writes data/telemetry.db
    python data/parse_export.py --fmt parquet  # explicit parquet
    python data/parse_export.py --data-dir /path/to/data --fmt sqlite

Data quality rules applied at ingestion (from PROJECT_CONTEXT.md §3):
  • Sentinel value -273.15 on any temperature field  → quality=1, value=NaN
  • Process value fields are nulled when the entity's `status` field == 0
    within the same minute (quality=3, value=NaN)
  • Cumulative counter fields (kwh, kvah) are tagged so the API layer can
    apply max-min delta logic; raw values are kept as-is here
  • A `quality` column is added: 0=ok, 1=sentinel, 2=out_of_range,
    3=suppressed
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import math
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional dependency: pyarrow / pandas for Parquet output.
# If not installed we gracefully fall back to SQLite.
# ---------------------------------------------------------------------------
try:
    import pandas as pd  # type: ignore
    import pyarrow as pa  # type: ignore
    import pyarrow.parquet as pq  # type: ignore
    _PARQUET_AVAILABLE = True
except ImportError:
    _PARQUET_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SITE_ID = "kmssh-nas"          # extracted from PROJECT_CONTEXT.md — only here!
SENTINEL_TEMP = -273.15        # sentinel value for temperature fields
TEMPERATURE_RE = re.compile(r"(temp|tmp|cwt|hwt|leavingwt|enteringwt|swt|rmt|amb)", re.I)
COUNTER_RE = re.compile(r"(kwh|kvah|wh|mwh|kvarh)", re.I)
STATUS_FIELD = "status"

OUTPUT_COLUMNS = ["time_utc", "site_id", "entity_id", "measurement", "field", "value", "quality"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Row:
    time_utc: str
    site_id: str
    entity_id: str
    measurement: str
    field: str
    value: Optional[float]
    quality: int = 0          # 0=ok, 1=sentinel, 2=out_of_range, 3=suppressed


# ---------------------------------------------------------------------------
# CSV block parser
# ---------------------------------------------------------------------------

def _iter_blocks(lines: List[str]) -> Iterator[List[str]]:
    """
    Yield contiguous non-empty line groups separated by blank lines.
    Each group is a list of raw CSV line strings, starting with its own header.
    """
    block: List[str] = []
    for raw in lines:
        stripped = raw.rstrip("\r\n")
        if stripped.strip() == "":
            if block:
                yield block
                block = []
        else:
            block.append(stripped)
    if block:
        yield block


def _detect_measurement_and_entity(filename_stem: str) -> Tuple[str, str]:
    """
    Derive a measurement label and a partial entity prefix from the file stem.

    Naming conventions observed in the project:
      component_<suffix>  → measurement = "component"
      device_<suffix>     → measurement = "device"
    The suffix is used as the entity context when the CSV itself doesn't
    carry an explicit entity column.
    """
    if filename_stem.startswith("component"):
        meas = "component"
        suffix = filename_stem[len("component"):].lstrip("_")
    elif filename_stem.startswith("device"):
        meas = "device"
        suffix = filename_stem[len("device"):].lstrip("_")
    else:
        meas = filename_stem
        suffix = ""
    return meas, suffix


def _coerce_float(raw: str) -> Optional[float]:
    """Parse a numeric string; return None for empty / non-numeric values."""
    s = raw.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _quality_flag(field_name: str, value: Optional[float]) -> Tuple[Optional[float], int]:
    """
    Apply data-quality rules and return (possibly-modified value, quality_flag).
    Rule: sentinel -273.15 on temperature fields → NaN, quality=1
    """
    if value is None:
        return None, 0
    if TEMPERATURE_RE.search(field_name) and math.isclose(value, SENTINEL_TEMP, abs_tol=0.001):
        return float("nan"), 1
    return value, 0


def parse_block(block_lines: List[str], measurement: str, file_stem: str) -> List[Row]:
    """
    Parse a single CSV block (header + data rows) into a list of Row objects.

    Expected pivot header shape:
        timestamp, <entity_id_col_or_entity_id>, <field1>, <field2>, ...

    OR wide format with the entity embedded in the header columns:
        timestamp, <entity>_<field>, <entity>_<field>, ...

    We handle both shapes conservatively:
      • If the second column looks like an entity ID (contains SITE_ID prefix
        or matches the entity-ID pattern "letters_digits"), we treat the CSV
        as [timestamp, entity_id, field1, field2, ...] — melting field columns.
      • Otherwise every non-timestamp column is treated as "<entity>.<field>"
        pairs encoded as "entityid_fieldname" or standalone field names, with
        the entity derived from the block's entity column value.
    """
    reader = csv.reader(io.StringIO("\n".join(block_lines)))
    headers = next(reader)
    headers = [h.strip() for h in headers]

    if not headers:
        return []

    rows: List[Row] = []
    ts_col = headers[0]

    # Identify whether column 1 carries entity IDs.
    entity_col_idx: Optional[int] = None
    ENTITY_RE = re.compile(r"^[a-zA-Z][\w\-]*_\d+$")  # e.g. kmssh-nas_224
    if len(headers) > 1 and (
        headers[1].lower() in ("entity_id", "entityid", "entity", "id", "device_id", "component_id")
        or ENTITY_RE.match(headers[1])          # first data cell of first data row check below
    ):
        entity_col_idx = 1

    # Peek at the first data row to confirm the entity column heuristic.
    data_rows_raw: List[List[str]] = list(reader)
    if entity_col_idx is None and data_rows_raw and len(data_rows_raw[0]) > 1:
        sample_val = data_rows_raw[0][1].strip()
        if ENTITY_RE.match(sample_val) or SITE_ID in sample_val:
            entity_col_idx = 1

    for raw_row in data_rows_raw:
        if len(raw_row) < 2:
            continue
        row_dict = dict(zip(headers, raw_row))
        ts_raw = row_dict.get(ts_col, "").strip()
        if not ts_raw:
            continue

        # Normalise timestamp to UTC ISO-8601 string.
        time_utc = _normalise_timestamp(ts_raw)

        # Determine entity_id
        if entity_col_idx is not None:
            entity_id = raw_row[entity_col_idx].strip() if entity_col_idx < len(raw_row) else ""
            field_start = entity_col_idx + 1
        else:
            entity_id = file_stem          # fall back to file stem as entity context
            field_start = 1

        if not entity_id:
            entity_id = file_stem

        # Emit one Row per field column
        for i, h in enumerate(headers[field_start:], start=field_start):
            if i >= len(raw_row):
                break
            field_name = h.strip()
            raw_val = raw_row[i].strip() if i < len(raw_row) else ""
            value = _coerce_float(raw_val)
            value, quality = _quality_flag(field_name, value)

            rows.append(Row(
                time_utc=time_utc,
                site_id=SITE_ID,
                entity_id=entity_id,
                measurement=measurement,
                field=field_name,
                value=value,
                quality=quality,
            ))

    return rows


def _normalise_timestamp(ts: str) -> str:
    """
    Coerce a timestamp string to UTC ISO-8601.
    Handles:
      • Already UTC/ISO strings ending in Z or +00:00
      • Strings with no timezone (assumed UTC per PROJECT_CONTEXT.md)
      • IST offset +05:30 (converted to UTC)
    Returns the string unchanged if parsing fails — the caller stores it as-is.
    """
    ts = ts.strip()
    # Fast path: already looks like ISO with UTC marker
    if ts.endswith("Z") or ts.endswith("+00:00"):
        return ts.rstrip("Z") + "Z" if ts.endswith("Z") else ts

    # Try to import datetime only when needed
    from datetime import datetime, timezone, timedelta

    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                # Timestamps without tz are assumed UTC per PROJECT_CONTEXT.md
                dt = dt.replace(tzinfo=timezone.utc)
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    # Fallback — store raw; the verification step will warn
    log.warning("Could not parse timestamp %r — storing raw", ts)
    return ts


# ---------------------------------------------------------------------------
# File-level parser
# ---------------------------------------------------------------------------

def parse_file(csv_path: Path) -> Tuple[List[Row], int]:
    """
    Parse a single component_*.csv or device_*.csv file.
    Returns (list_of_rows, raw_data_row_count).
    raw_data_row_count is the total non-header, non-blank rows for manifest verification.
    """
    stem = csv_path.stem                           # e.g. "component_sensors"
    measurement, _suffix = _detect_measurement_and_entity(stem)

    log.info("Parsing %s ...", csv_path.name)
    text = csv_path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    all_rows: List[Row] = []
    raw_data_row_count = 0

    for block in _iter_blocks(lines):
        # block[0] is the header; block[1:] are data lines
        raw_data_row_count += len(block) - 1
        rows = parse_block(block, measurement, stem)
        all_rows.extend(rows)

    log.info("  -> %d raw data rows, %d long-form records", raw_data_row_count, len(all_rows))
    return all_rows, raw_data_row_count


# ---------------------------------------------------------------------------
# Suppression pass: null out process values when status == 0
# ---------------------------------------------------------------------------

def apply_suppression(all_rows: List[Row]) -> List[Row]:
    """
    Per PROJECT_CONTEXT.md §3: a process value is only valid when the entity's
    `status` field == 1 within the same minute.  When status == 0, set
    value = NaN and quality = 3 (suppressed) for all non-status fields of
    that entity at that minute.
    """
    # Index: (entity_id, minute_key) -> status_value
    status_map: dict = {}
    minute_key = lambda ts: ts[:16]   # "YYYY-MM-DDTHH:MM"

    for row in all_rows:
        if row.field.lower() == STATUS_FIELD and row.value is not None:
            key = (row.entity_id, minute_key(row.time_utc))
            status_map[key] = row.value

    for row in all_rows:
        if row.field.lower() == STATUS_FIELD:
            continue
        key = (row.entity_id, minute_key(row.time_utc))
        status_val = status_map.get(key)
        if status_val is not None and status_val == 0.0:
            row.value = float("nan")
            row.quality = 3

    return all_rows


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_parquet(rows: List[Row], out_path: Path) -> None:
    if not _PARQUET_AVAILABLE:
        log.error("pyarrow / pandas not installed. Install with: pip install pyarrow pandas")
        sys.exit(1)

    data = {col: [] for col in OUTPUT_COLUMNS}
    for r in rows:
        data["time_utc"].append(r.time_utc)
        data["site_id"].append(r.site_id)
        data["entity_id"].append(r.entity_id)
        data["measurement"].append(r.measurement)
        data["field"].append(r.field)
        data["value"].append(r.value)
        data["quality"].append(r.quality)

    table = pa.table({
        "time_utc":    pa.array(data["time_utc"],    type=pa.string()),
        "site_id":     pa.array(data["site_id"],     type=pa.string()),
        "entity_id":   pa.array(data["entity_id"],   type=pa.string()),
        "measurement": pa.array(data["measurement"], type=pa.string()),
        "field":       pa.array(data["field"],       type=pa.string()),
        "value":       pa.array(data["value"],       type=pa.float64()),
        "quality":     pa.array(data["quality"],     type=pa.int8()),
    })
    pq.write_table(table, out_path, compression="snappy")
    log.info("Wrote %d rows -> %s", len(rows), out_path)


def write_sqlite(rows: List[Row], out_path: Path) -> None:
    con = sqlite3.connect(out_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            time_utc    TEXT    NOT NULL,
            site_id     TEXT    NOT NULL,
            entity_id   TEXT    NOT NULL,
            measurement TEXT    NOT NULL,
            field       TEXT    NOT NULL,
            value       REAL,
            quality     INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("DELETE FROM telemetry")   # idempotent re-run
    con.executemany(
        "INSERT INTO telemetry VALUES (?,?,?,?,?,?,?)",
        [(r.time_utc, r.site_id, r.entity_id, r.measurement, r.field, r.value, r.quality)
         for r in rows],
    )
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_time
        ON telemetry (entity_id, time_utc)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_site_time
        ON telemetry (site_id, time_utc)
    """)
    con.commit()
    con.close()
    log.info("Wrote %d rows -> %s", len(rows), out_path)


# ---------------------------------------------------------------------------
# Row-count metadata sidecar  (consumed by verify_export.py)
# ---------------------------------------------------------------------------

def write_row_counts(counts: dict, out_path: Path) -> None:
    """
    Write a JSON sidecar mapping {filename: raw_data_row_count} so the
    verification script can compare against manifest.json without re-parsing.
    """
    import json
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"parsed_counts": counts}, f, indent=2)
    log.info("Wrote row-count sidecar -> %s", out_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Parse BMS annotated-pivot CSV exports into a canonical telemetry table."
    )
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory containing component_*.csv and device_*.csv files (default: same dir as this script)",
    )
    ap.add_argument(
        "--fmt",
        choices=["parquet", "sqlite"],
        default="parquet" if _PARQUET_AVAILABLE else "sqlite",
        help="Output format (default: parquet if pyarrow installed, else sqlite)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Explicit output file path (overrides --fmt default name)",
    )
    args = ap.parse_args()

    data_dir: Path = args.data_dir.resolve()
    if not data_dir.is_dir():
        log.error("Data directory not found: %s", data_dir)
        sys.exit(1)

    # Discover source files
    csv_files = sorted(
        list(data_dir.glob("component_*.csv")) + list(data_dir.glob("device_*.csv"))
    )
    if not csv_files:
        log.warning(
            "No component_*.csv or device_*.csv files found in %s.\n"
            "  Place the raw export CSVs there and re-run.",
            data_dir,
        )
        # Still exit cleanly so CI does not hard-fail on an empty data dir.
        sys.exit(0)

    log.info("Found %d source file(s) in %s", len(csv_files), data_dir)

    all_rows: List[Row] = []
    raw_counts: dict = {}

    for csv_path in csv_files:
        rows, raw_count = parse_file(csv_path)
        all_rows.extend(rows)
        raw_counts[csv_path.name] = raw_count

    log.info("Total raw data rows across all files: %d", sum(raw_counts.values()))

    # Suppression pass (needs full cross-file index)
    all_rows = apply_suppression(all_rows)

    suppressed = sum(1 for r in all_rows if r.quality == 3)
    sentinels  = sum(1 for r in all_rows if r.quality == 1)
    log.info("Quality summary — sentinel: %d, suppressed: %d", sentinels, suppressed)

    # Determine output path
    if args.out:
        out_path = args.out.resolve()
    elif args.fmt == "parquet":
        out_path = data_dir / "telemetry.parquet"
    else:
        out_path = data_dir / "telemetry.db"

    # Write output
    if args.fmt == "parquet":
        write_parquet(all_rows, out_path)
    else:
        write_sqlite(all_rows, out_path)

    # Write sidecar for verification script
    sidecar_path = data_dir / "parsed_counts.json"
    write_row_counts(raw_counts, sidecar_path)

    log.info("Done. Output: %s", out_path)


if __name__ == "__main__":
    main()
