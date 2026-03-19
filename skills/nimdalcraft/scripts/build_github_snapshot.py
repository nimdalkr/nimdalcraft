#!/usr/bin/env python3
"""Build a GitHub repository-search snapshot for degraded nimdalcraft runs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ASSET_DIR = SKILL_DIR / "assets"

USER_AGENT = "nimdalcraft/0.4"
GITHUB_API = "https://api.github.com/search/repositories"
DEFAULT_QUERIES_PATH = ASSET_DIR / "github-snapshot-queries.json"
DEFAULT_OUTPUT_PATH = ASSET_DIR / "github-search-snapshots.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a GitHub search snapshot for degraded nimdalcraft runs.")
    parser.add_argument("--queries-file", default=str(DEFAULT_QUERIES_PATH), help="JSON file containing snapshot query strings")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Snapshot JSON output path")
    parser.add_argument("--per-query", type=int, default=8, help="Repositories to keep per query")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable name holding the GitHub token")
    return parser.parse_args()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_queries(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(values, list):
        raise SystemExit(f"Invalid query file: {path}")
    return [str(item).strip() for item in values if str(item).strip()]


def github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }


def request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def keep_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "full_name": item.get("full_name"),
        "html_url": item.get("html_url"),
        "description": item.get("description"),
        "archived": bool(item.get("archived")),
        "disabled": bool(item.get("disabled")),
        "topics": item.get("topics") or [],
        "pushed_at": item.get("pushed_at"),
        "updated_at": item.get("updated_at"),
        "stargazers_count": item.get("stargazers_count", 0),
        "forks_count": item.get("forks_count", 0),
        "open_issues_count": item.get("open_issues_count", 0),
        "license": {"spdx_id": ((item.get("license") or {}).get("spdx_id") or "")},
    }


def build_snapshot(queries: list[str], per_query: int, token: str) -> dict[str, Any]:
    headers = github_headers(token)
    payload: dict[str, Any] = {
        "generated_at": now_iso(),
        "source": "github-search-api",
        "query_count": len(queries),
        "item_count": 0,
        "queries": {},
    }
    total_items = 0
    for query in queries:
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": str(per_query)}
        data = request_json(f"{GITHUB_API}?{urllib.parse.urlencode(params)}", headers)
        items = [keep_item(item) for item in (data.get("items") or [])[:per_query] if isinstance(item, dict)]
        total_items += len(items)
        payload["queries"][query] = {
            "fetched_at": payload["generated_at"],
            "items": items,
        }
    payload["item_count"] = total_items
    return payload


def main() -> int:
    args = parse_args()
    token = os.getenv(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"{args.token_env} is required to build a GitHub snapshot.")
    queries = load_queries(Path(args.queries_file))
    snapshot = build_snapshot(queries, args.per_query, token)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "generated_at": snapshot["generated_at"],
                "queries": snapshot["query_count"],
                "items": snapshot["item_count"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
