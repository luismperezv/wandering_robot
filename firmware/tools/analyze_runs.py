#!/usr/bin/env python3
"""
Analyze repeatability of ultrasonic readings across multiple runs
of the same path.

Usage examples (from repo root):

    python tests/analyze_runs.py logs/runlog_20251126_133655.csv logs/another_run.csv
    python tests/analyze_runs.py run1.json run2.json run3.json

Supported formats:
  - JSON files with {"success": true, "log": [ { ... } ]} like your example
  - CSV logs with the header used by the existing log files

Assumptions:
  - All runs follow the same command sequence (same path)
  - Runs are aligned by row index and truncated to the shortest run
"""

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List

SENSOR_KEYS = ["front_distance_cm", "left_distance_cm", "right_distance_cm"]


def _to_float_or_none(x):
    """Convert value to float or None if not possible/empty."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_run(path: Path) -> List[Dict[str, Any]]:
    """Load a single run from JSON or CSV into a normalized list of dicts."""
    suffix = path.suffix.lower()

    if suffix == ".json":
        with path.open("r") as f:
            data = json.load(f)

        if isinstance(data, dict) and "log" in data:
            rows = data["log"]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError(f"Unrecognized JSON structure in {path}")

        norm_rows = []
        for r in rows:
            norm_rows.append(
                {
                    "timestamp_iso": r.get("timestamp") or r.get("timestamp_iso"),
                    "mode": r.get("mode", ""),
                    "front_distance_cm": _to_float_or_none(r.get("front_distance_cm")),
                    "left_distance_cm": _to_float_or_none(r.get("left_distance_cm")),
                    "right_distance_cm": _to_float_or_none(r.get("right_distance_cm")),
                    "executed_motion": r.get("executed_motion", ""),
                    "executed_speed": _to_float_or_none(r.get("executed_speed")),
                    "notes": r.get("notes", ""),
                }
            )
        return norm_rows

    # Assume CSV
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # Skip config rows; they don't have sensor data for the path
            if row.get("mode") == "CONFIG":
                continue
            rows.append(
                {
                    "timestamp_iso": row.get("timestamp_iso"),
                    "mode": row.get("mode", ""),
                    "front_distance_cm": _to_float_or_none(row.get("front_distance_cm")),
                    "left_distance_cm": _to_float_or_none(row.get("left_distance_cm")),
                    "right_distance_cm": _to_float_or_none(row.get("right_distance_cm")),
                    "executed_motion": row.get("executed_motion", ""),
                    "executed_speed": _to_float_or_none(row.get("executed_speed")),
                    "notes": row.get("notes", ""),
                }
            )
    return rows


def mean_std(values: List[float]):
    """Return (mean, sample_std_dev) for a list of floats."""
    n = len(values)
    if n == 0:
        return None, None
    m = sum(values) / n
    if n == 1:
        return m, 0.0
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return m, math.sqrt(var)


def most_common(items: List[str]) -> str:
    """Return the most common string in a list (empty string if list empty)."""
    counts: Dict[str, int] = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def analyze_runs(runs: List[List[Dict[str, Any]]], cv_threshold: float = 0.20) -> None:
    """Compute and print repeatability statistics across runs."""
    num_runs = len(runs)
    lengths = [len(r) for r in runs]
    if num_runs == 0 or min(lengths) == 0:
        print("No data to analyze.")
        return

    min_len = min(lengths)
    print(f"Loaded {num_runs} runs; lengths = {lengths}, analyzing first {min_len} steps (truncate to shortest).")
    print()

    # Overall per-sensor stats across all steps and runs
    all_sensor_values: Dict[str, List[float]] = {k: [] for k in SENSOR_KEYS}
    for run in runs:
        for row in run[:min_len]:
            for k in SENSOR_KEYS:
                v = row.get(k)
                if v is not None:
                    all_sensor_values[k].append(v)

    print("=== Overall sensor statistics across all runs & steps ===")
    for k in SENSOR_KEYS:
        m, s = mean_std(all_sensor_values[k])
        if m is None:
            continue
        cv = s / m if m else float("inf")
        print(f"{k:18s} mean={m:7.2f} cm, std={s:6.2f} cm, CV={cv*100:5.1f}%")
    print()

    # Step-by-step variance; flag high-variance locations
    print(f"=== High-variance positions (CV > {cv_threshold*100:.0f}% for any sensor) ===")
    print(
        "step_idx  motion   "
        + "  ".join(f"{k}_cv%" for k in SENSOR_KEYS)
        + "   notes_sample"
    )
    for i in range(min_len):
        step_vals: Dict[str, List[float]] = {k: [] for k in SENSOR_KEYS}
        motions: List[str] = []
        notes_samples: List[str] = []

        for run in runs:
            row = run[i]
            motions.append(row.get("executed_motion", ""))
            if row.get("notes"):
                notes_samples.append(row["notes"])
            for k in SENSOR_KEYS:
                v = row.get(k)
                if v is not None:
                    step_vals[k].append(v)

        cvs: Dict[str, float | None] = {}
        for k in SENSOR_KEYS:
            m, s = mean_std(step_vals[k])
            if m is None or m == 0:
                cvs[k] = None
            else:
                cvs[k] = (s / m) * 100.0

        if any((cvs[k] is not None and cvs[k] > cv_threshold * 100.0) for k in SENSOR_KEYS):
            mot = most_common(motions)
            note = notes_samples[0] if notes_samples else ""
            cvs_str = "  ".join(
                f"{(cvs[k] if cvs[k] is not None else 0):6.1f}"
                for k in SENSOR_KEYS
            )
            print(f"{i:7d}  {mot:7s}  {cvs_str}   {note}")
    print()

    print("Interpretation tips:")
    print("- CV (coefficient of variation) below ~10–15% is generally pretty stable.")
    print("- Repeatedly high CV at the same step means that pose is not reliably observed;")
    print("  this makes pure distance-based pseudo-SLAM harder or noisier there.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analyze distance-sensor repeatability across multiple runs."
    )
    ap.add_argument(
        "paths",
        nargs="+",
        help="Log files (.json or .csv) from runs of the same path.",
    )
    ap.add_argument(
        "--cv-threshold",
        type=float,
        default=0.20,
        help="Coefficient of variation threshold for flagging high-variance steps "
        "(default: 0.20 = 20%%).",
    )
    args = ap.parse_args()

    runs: List[List[Dict[str, Any]]] = []
    for p in args.paths:
        path = Path(p)
        if not path.is_file():
            raise SystemExit(f"Not a file: {p}")
        print(f"Loading {p} ...")
        runs.append(load_run(path))

    analyze_runs(runs, cv_threshold=args.cv_threshold)


if __name__ == "__main__":
    main()


