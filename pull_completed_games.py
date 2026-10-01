"""Pull completed MLB games from the MLB Stats API through 2026-09-30.

Appends one row per team to data/2026/teams. Scores, the sixth-inning
line, and starter names come from the schedule, linescore, and boxscore.
Games that are not final are skipped.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import statsapi

ROOT = Path(__file__).resolve().parent
YEAR = 2026
THROUGH = "2026-09-30"
START = "2026-03-25"
COMPLETED = {"Final", "Game Over", "Completed Early"}
GAME_TYPES = {"R", "F", "D", "L", "W"}
TEAM_DIR = ROOT / "data" / str(YEAR) / "teams"


def month_windows(start: str, end: str):
    cursor = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while cursor <= last:
        if cursor.month == 12:
            nxt = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            nxt = cursor.replace(month=cursor.month + 1, day=1)
        chunk_end = min(last, nxt - timedelta(days=1))
        yield cursor.isoformat(), chunk_end.isoformat()
        cursor = chunk_end + timedelta(days=1)


def fetch_range(start: str, end: str, depth: int = 0) -> list:
    try:
        games = statsapi.schedule(start_date=start, end_date=end, sportId=1)
        time.sleep(0.25)
        return games
    except requests.HTTPError as exc:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        if depth >= 6 or start_d >= end_d:
            raise
        mid = start_d + (end_d - start_d) // 2
        print(f"split {start}..{end} after HTTP {exc.response.status_code}")
        time.sleep(1.0)
        left = fetch_range(start, mid.isoformat(), depth + 1)
        right = fetch_range((mid + timedelta(days=1)).isoformat(), end, depth + 1)
        return left + right


def team_abbrevs() -> dict[int, str]:
    payload = statsapi.get("teams", {"sportId": 1, "season": YEAR})
    return {team["id"]: team["abbreviation"] for team in payload["teams"]}


def runs_through(linescore: dict, side: str, innings: int = 6):
    total = 0
    played = False
    for inning in linescore.get("innings", []):
        if inning.get("num", 99) > innings:
            continue
        cell = inning.get(side) or {}
        runs = cell.get("runs")
        if runs is None:
            continue
        played = True
        total += int(runs)
    if not played:
        return None
    return total


def starter_name(box: dict, side: str):
    players = box[side]["players"]
    for pitcher in box.get(f"{side}Pitchers", []):
        person_id = pitcher.get("personId") or 0
        if not person_id:
            continue
        person = players.get(f"ID{person_id}", {}).get("person", {})
        name = person.get("fullName")
        if name:
            return name
    return None


def load_existing() -> dict[str, set[tuple]]:
    existing: dict[str, set[tuple]] = {}
    for path in TEAM_DIR.glob("*.csv"):
        frame = pd.read_csv(path)
        keys = set()
        if not frame.empty and {"date", "opponent", "teamruns"}.issubset(frame.columns):
            for row in frame.itertuples(index=False):
                keys.add((str(row.date), str(row.opponent), int(row.teamruns)))
        existing[path.stem] = keys
    return existing


def append_row(team: str, row: list) -> None:
    path = TEAM_DIR / f"{team}.csv"
    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(row)


def main() -> None:
    abbrev = team_abbrevs()
    games = []
    for chunk_start, chunk_end in month_windows(START, THROUGH):
        games.extend(fetch_range(chunk_start, chunk_end))
    finals = []
    seen_ids = set()
    for game in games:
        game_id = game.get("game_id")
        if game_id in seen_ids:
            continue
        if game.get("game_date", "") > THROUGH:
            continue
        if game.get("status") not in COMPLETED:
            continue
        if game.get("game_type") not in GAME_TYPES:
            continue
        if game.get("away_score") is None or game.get("home_score") is None:
            continue
        seen_ids.add(game_id)
        finals.append(game)
    finals.sort(key=lambda game: (game["game_date"], game["game_id"]))

    schedule_rows = []
    for game in finals:
        schedule_rows.append(
            {
                "date": game["game_date"],
                "away": abbrev.get(game["away_id"], ""),
                "home": abbrev.get(game["home_id"], ""),
                "away_score": int(game["away_score"]),
                "home_score": int(game["home_score"]),
                "status": game["status"],
                "game_type": game["game_type"],
                "game_id": game["game_id"],
            }
        )
    schedule = pd.DataFrame(schedule_rows)
    out = ROOT / "data" / str(YEAR) / "completed_games.csv"
    schedule.to_csv(out, index=False)
    print(f"min date {schedule['date'].min()}")
    print(f"max date {schedule['date'].max()}")
    print(f"rows {len(schedule)}")

    existing = load_existing()
    added = 0
    skipped = 0
    for index, game in enumerate(finals, start=1):
        away = abbrev.get(game["away_id"])
        home = abbrev.get(game["home_id"])
        if away not in existing or home not in existing:
            skipped += 1
            continue
        away_runs = int(game["away_score"])
        home_runs = int(game["home_score"])
        date = game["game_date"]
        away_key = (date, home, away_runs)
        home_key = (date, away, home_runs)
        if away_key in existing[away] and home_key in existing[home]:
            continue
        try:
            linescore = statsapi.get("game_linescore", {"gamePk": game["game_id"]})
            box = statsapi.boxscore_data(game["game_id"])
        except requests.HTTPError as exc:
            print(f"skip {game['game_id']} HTTP {exc.response.status_code}")
            skipped += 1
            time.sleep(1.0)
            continue
        away_six = runs_through(linescore, "away")
        home_six = runs_through(linescore, "home")
        away_starter = starter_name(box, "away")
        home_starter = starter_name(box, "home")
        if None in (away_six, home_six, away_starter, home_starter):
            print(f"skip {game['game_id']} missing line or starter")
            skipped += 1
            continue
        if away_key not in existing[away]:
            append_row(
                away,
                [
                    date,
                    away,
                    home,
                    away_runs - home_runs,
                    away_runs,
                    away_six - home_six,
                    away_six,
                    away_starter,
                    home_starter,
                ],
            )
            existing[away].add(away_key)
            added += 1
        if home_key not in existing[home]:
            append_row(
                home,
                [
                    date,
                    home,
                    away,
                    home_runs - away_runs,
                    home_runs,
                    home_six - away_six,
                    home_six,
                    home_starter,
                    away_starter,
                ],
            )
            existing[home].add(home_key)
            added += 1
        if index % 100 == 0:
            print(f"processed {index} finals, added {added}")
        time.sleep(0.12)

    print(f"added team rows {added}")
    print(f"skipped games {skipped}")
    dates = []
    for path in TEAM_DIR.glob("*.csv"):
        frame = pd.read_csv(path)
        if not frame.empty:
            dates.extend(frame["date"].tolist())
    print(f"team file min {min(dates)}")
    print(f"team file max {max(dates)}")


if __name__ == "__main__":
    main()
