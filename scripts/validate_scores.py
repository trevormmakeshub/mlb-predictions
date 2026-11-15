"""Check the 2026 completed-game file. This does not download."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data" / "2026" / "completed_games.csv"


def main() -> int:
    with PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    dates = [row["date"] for row in rows]
    ids = [row["game_id"] for row in rows]
    problems = 0
    if len(ids) != len(set(ids)):
        print("fail duplicate game_id")
        problems += 1
    if not dates or max(dates) > date.today().isoformat():
        print("fail date range")
        problems += 1
    accepted = {"Final", "Completed Early", "Game Over"}
    missing = sum(
        1 for row in rows
        if row["status"] not in accepted or row["away_score"] == "" or row["home_score"] == ""
    )
    if missing:
        print(f"fail {missing} rows are not final scores")
        problems += 1
    print(f"rows {len(rows)} min {min(dates)} max {max(dates)}")
    if problems:
        return 1
    print("validation pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
