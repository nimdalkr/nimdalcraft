#!/usr/bin/env python3
"""Validate trusted starters and optionally update the validated set."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SKILL_DIR))

from run import TRUSTED_STARTERS_PATH, starter_matches, validate_runnable_starter  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate trusted starters and update validated-set status.")
    parser.add_argument("--starter", help="Validate a single starter by id, label, or repo")
    parser.add_argument("--all", action="store_true", help="Validate all configured starters")
    parser.add_argument("--update-status", action="store_true", help="Persist status and validation history updates")
    parser.add_argument("--timeout-sec", type=int, default=120, help="Per-step validation timeout")
    parser.add_argument("--work-dir", default=str(SKILL_DIR / "work" / "starter-validation"), help="Directory for validation artifacts")
    return parser.parse_args()


def load_validated_set() -> list[dict[str, Any]]:
    if not TRUSTED_STARTERS_PATH.exists():
        return []
    return json.loads(TRUSTED_STARTERS_PATH.read_text(encoding="utf-8"))


def save_validated_set(starters: list[dict[str, Any]]) -> None:
    TRUSTED_STARTERS_PATH.write_text(json.dumps(starters, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    return dt.date.today().isoformat()


def command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    except Exception:  # noqa: BLE001
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or completed.stderr or "").strip()


def detect_verified_env(existing: dict[str, Any] | None = None) -> dict[str, str]:
    base = dict(existing or {})
    python_version = platform.python_version()
    node_version = command_output(["node", "--version"]) or str(base.get("node") or "")
    return {
        "node": node_version,
        "python": python_version,
        "os": platform.system().lower(),
    }


def append_validation_history(starter: dict[str, Any], passed: bool, final_result: str, validation: dict[str, Any]) -> list[dict[str, Any]]:
    history = list(starter.get("validation_history") or [])
    history.append(
        {
            "time": now_iso(),
            "status": "pass" if passed else "fail",
            "final_result": final_result,
            "runnable_status": validation.get("status") or "fail",
        }
    )
    return history


def trailing_count(history: list[dict[str, Any]], wanted: str) -> int:
    count = 0
    for item in reversed(history):
        if str(item.get("status") or "").casefold() != wanted:
            break
        count += 1
    return count


def transition_status(previous: str, history: list[dict[str, Any]]) -> str:
    current = previous.casefold()
    last = str((history[-1] if history else {}).get("status") or "").casefold()
    success_streak = trailing_count(history, "pass")
    fail_streak = trailing_count(history, "fail")

    if current == "verified" and last == "fail":
        return "flaky"
    if current == "flaky" and fail_streak >= 3:
        return "broken"
    if current == "broken" and success_streak >= 2:
        return "verified"
    return previous


def validate_one(starter: dict[str, Any], work_dir: Path, timeout_sec: int) -> tuple[dict[str, Any], dict[str, Any]]:
    target = work_dir / str(starter.get("id") or "starter")
    validation = validate_runnable_starter(copy.deepcopy(starter), target, timeout_sec)
    final_result = str(validation.get("final_result") or "unusable")
    passed = str(validation.get("status") or "").casefold() == "pass"

    updated = copy.deepcopy(starter)
    updated["verified_env"] = detect_verified_env(updated.get("verified_env"))
    updated["validation_history"] = append_validation_history(updated, passed, final_result, validation)
    if str(updated.get("status_policy") or "").casefold() == "keep_flaky":
        updated["status"] = "flaky"
    else:
        updated["status"] = transition_status(str(updated.get("status") or "flaky"), updated["validation_history"])
    outcome = "success" if final_result == "usable" else "partial_success"
    updated["last_validation"] = {
        "checked_at": today_iso(),
        "outcome": outcome,
        "runnable_status": validation.get("status") or "fail",
        "final_result": final_result,
        "detail": validation.get("details") or {},
    }
    if str(updated.get("status") or "").casefold() == "verified":
        updated["last_verified_at"] = today_iso()

    summary = {
        "id": updated.get("id"),
        "label": updated.get("label"),
        "previous_status": starter.get("status"),
        "status": updated.get("status"),
        "runnable_status": validation.get("status"),
        "final_result": final_result,
        "history_size": len(updated.get("validation_history") or []),
        "work_dir": str(target),
    }
    return updated, summary


def select_starters(starters: list[dict[str, Any]], starter_arg: str | None, all_flag: bool) -> list[dict[str, Any]]:
    if all_flag:
        return starters
    if starter_arg:
        selected = [item for item in starters if starter_matches(item, starter_arg)]
        if selected:
            return selected
    raise SystemExit("Use --starter <id> or --all.")


def main() -> int:
    args = parse_args()
    starters = load_validated_set()
    selected = select_starters(starters, args.starter, args.all)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    replacements: dict[str, dict[str, Any]] = {}
    results = []
    for starter in selected:
        updated, summary = validate_one(starter, work_dir, args.timeout_sec)
        replacements[str(starter.get("id") or "")] = updated
        results.append(summary)

    if args.update_status:
        merged = []
        for starter in starters:
            merged.append(replacements.get(str(starter.get("id") or ""), starter))
        save_validated_set(merged)

    print(
        json.dumps(
            {
                "updated": bool(args.update_status),
                "validated": len(results),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
