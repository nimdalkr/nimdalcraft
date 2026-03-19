#!/usr/bin/env python3
"""Fetch, filter, score, and explain OSS candidates for the SaaS OSS Accelerator."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pipeline_state import derive_search_map, load_state, merge_raw_candidates, save_state

USER_AGENT = "saas-oss-accelerator/0.3"
GITHUB_API = "https://api.github.com/search/repositories"
NPM_SEARCH_API = "https://registry.npmjs.org/-/v1/search"
PYPI_SEARCH_URL = "https://pypi.org/search/"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_RETRIES = 3
STARTER_HINTS = ("starter", "boilerplate", "template", "saas", "example")
RELEVANCE_STOPWORDS = {
    "starter",
    "boilerplate",
    "template",
    "toolkit",
    "solution",
    "application",
    "shell",
    "foundation",
    "app",
    "saas",
    "backend",
    "frontend",
    "api",
}
COMPLEXITY_HINTS = (
    "kubernetes",
    "terraform",
    "helm",
    "microservice",
    "microservices",
    "distributed",
    "enterprise",
    "event-driven",
    "service mesh",
    "monorepo",
    "plugin system",
    "highly extensible",
)
DEMO_HINTS = ("demo", "proof of concept", "poc", "sample app", "showcase")
SEARCH_MODE_SETTINGS = {
    "strict": {
        "require_github_token": True,
        "allow_network": True,
        "hard_stale_days": 730,
        "min_relevance": 0.45,
        "min_score": 62.0,
        "allowed_confidence": {"high"},
    },
    "degraded": {
        "require_github_token": False,
        "allow_network": True,
        "hard_stale_days": 1095,
        "min_relevance": 0.34,
        "min_score": 50.0,
        "allowed_confidence": {"high", "medium", "low"},
    },
    "offline": {
        "require_github_token": False,
        "allow_network": False,
        "hard_stale_days": 1095,
        "min_relevance": 0.34,
        "min_score": 50.0,
        "allowed_confidence": {"high", "medium", "low"},
    },
}


class SearchError(RuntimeError):
    """Raised when a search provider cannot complete its work."""


class PyPISearchParser(HTMLParser):
    """Extract package snippets from the public PyPI search page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_anchor = False
        self._current: dict[str, str] | None = None
        self._capture_name = False
        self._capture_desc = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = attr_map.get("class") or ""
        if tag == "a" and "package-snippet" in classes:
            self._in_anchor = True
            self._current = {"href": attr_map.get("href") or "", "name": "", "description": ""}
            return
        if not self._in_anchor:
            return
        if tag == "span" and "package-snippet__name" in classes:
            self._capture_name = True
        elif tag == "p" and "package-snippet__description" in classes:
            self._capture_desc = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            self._in_anchor = False
            self._capture_name = False
            self._capture_desc = False
            if self._current and self._current.get("name"):
                self.results.append(self._current)
            self._current = None
            return
        if tag == "span":
            self._capture_name = False
        elif tag == "p":
            self._capture_desc = False

    def handle_data(self, data: str) -> None:
        if not self._current:
            return
        text = data.strip()
        if not text:
            return
        if self._capture_name:
            self._current["name"] += text
        elif self._capture_desc:
            current = self._current.get("description", "")
            self._current["description"] = (current + " " + text).strip()


def _cache_file(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.bin"


def _read_cache(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return path.read_bytes()


def _write_cache(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _http_request(
    url: str,
    *,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> bytes:
    cache_path = _cache_file(cache_dir, url) if cache_dir else None
    now = time.time()
    if cache_path and cache_path.exists():
        age = now - cache_path.stat().st_mtime
        if age <= cache_ttl_seconds:
            telemetry["fresh_cache_hits"] += 1
            return _read_cache(cache_path) or b""
    if not allow_network:
        if cache_path and cache_path.exists():
            telemetry["stale_cache_hits"] += 1
            return _read_cache(cache_path) or b""
        telemetry["request_failures"] += 1
        raise SearchError("offline mode has no cached response for this query")

    request = urllib.request.Request(url, headers=headers or {})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read()
            telemetry["live_requests"] += 1
            if cache_path:
                _write_cache(cache_path, payload)
            return payload
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.2 * attempt)
                continue
            if cache_path and cache_path.exists():
                telemetry["stale_cache_hits"] += 1
                return _read_cache(cache_path) or b""
    telemetry["request_failures"] += 1
    raise SearchError(f"request failed for {url}: {last_error}")


def _get_json(
    url: str,
    *,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> Any:
    payload = _http_request(
        url,
        headers=headers,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        retries=retries,
        allow_network=allow_network,
        telemetry=telemetry,
    )
    return json.loads(payload.decode("utf-8"))


def _get_text(
    url: str,
    *,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> str:
    payload = _http_request(
        url,
        headers=headers,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        retries=retries,
        allow_network=allow_network,
        telemetry=telemetry,
    )
    return payload.decode("utf-8", errors="replace")


def _parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(value: str | None) -> int | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    return (now - parsed.astimezone(dt.timezone.utc)).days


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _clamp(score: float) -> float:
    return max(0.0, min(1.0, score))


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    haystack = text.casefold()
    return any(hint in haystack for hint in hints)


def _starter_signals(name: str, description: str) -> list[str]:
    text = f"{name} {description}".lower()
    signals = []
    if _contains_any(text, STARTER_HINTS):
        signals.append("starter-oriented")
    if "auth" in text:
        signals.append("auth-surface")
    if "admin" in text or "dashboard" in text:
        signals.append("admin-surface")
    return signals


def _complexity_signals(name: str, description: str) -> list[str]:
    text = f"{name} {description}".lower()
    return [hint for hint in COMPLEXITY_HINTS if hint in text]


def _demo_flags(name: str, description: str) -> list[str]:
    text = f"{name} {description}".lower()
    return [hint for hint in DEMO_HINTS if hint in text]


def _score_recency(last_update: str | None) -> float:
    days = _days_since(last_update)
    if days is None:
        return 0.45
    if days <= 30:
        return 1.0
    if days <= 90:
        return 0.9
    if days <= 180:
        return 0.75
    if days <= 365:
        return 0.55
    if days <= 730:
        return 0.25
    return 0.08


def _score_maintenance(candidate: dict[str, Any]) -> float:
    source_type = candidate.get("source_type")
    raw = candidate.get("raw_signals") or {}
    score = 0.7
    if source_type == "github":
        stars = float(raw.get("stars") or 0)
        issues = float(raw.get("open_issues") or 0)
        score = 0.85 - (min(0.25, issues / max(stars, 1.0)) if stars else 0.05)
    elif source_type == "npm":
        score = 0.25 + (0.75 * float(raw.get("maintenance") or 0.0))
    elif source_type == "pypi":
        classifiers = raw.get("classifiers") or []
        score = 0.65 + (0.1 if classifiers else 0.0)
    return _clamp(score)


def _score_popularity(candidate: dict[str, Any]) -> float:
    source_type = candidate.get("source_type")
    raw = candidate.get("raw_signals") or {}
    if source_type == "github":
        return _clamp(math.log10(float(raw.get("stars") or 0) + 1.0) / 5.0)
    if source_type == "npm":
        return _clamp(float(raw.get("popularity") or 0.4))
    return 0.45


def _score_relevance(candidate: dict[str, Any]) -> tuple[float, list[str]]:
    basis = " ".join([str(candidate.get("component") or ""), str(candidate.get("query") or "")])
    target_tokens = [
        token
        for token in _tokenize(basis)
        if len(token) > 2 and token not in RELEVANCE_STOPWORDS
    ]
    if not target_tokens:
        return 0.5, []
    searchable = " ".join(
        [
            str(candidate.get("name") or ""),
            str(candidate.get("description") or ""),
            " ".join(candidate.get("selection_hints") or []),
        ]
    ).lower()
    hits = sorted({token for token in target_tokens if token in searchable})
    return _clamp(len(hits) / len(set(target_tokens))), hits


def _score_beginner(candidate: dict[str, Any]) -> tuple[float, str]:
    starter = len(candidate.get("beginner_fit_signals") or [])
    complexity = len(candidate.get("complexity_signals") or [])
    demo = len(candidate.get("demo_flags") or [])
    score = 0.55 + (0.12 * starter) - (0.12 * complexity) - (0.15 * demo)
    if candidate.get("source_type") == "npm":
        score += 0.05
    if candidate.get("license"):
        score += 0.03
    score = _clamp(score)
    if score >= 0.75:
        return score, "low"
    if score >= 0.5:
        return score, "medium"
    return score, "high"


def _candidate_confidence(overall_score: float) -> str:
    if overall_score >= 80:
        return "high"
    if overall_score >= 65:
        return "medium"
    return "low"


def enrich_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Attach filterable and explainable fields to a raw candidate."""
    enriched = dict(candidate)
    enriched["complexity_signals"] = _complexity_signals(
        str(candidate.get("name") or ""), str(candidate.get("description") or "")
    )
    enriched["demo_flags"] = _demo_flags(
        str(candidate.get("name") or ""), str(candidate.get("description") or "")
    )
    recency_score = _score_recency(candidate.get("last_update"))
    maintenance_score = _score_maintenance(enriched)
    popularity_score = _score_popularity(enriched)
    relevance_score, relevance_hits = _score_relevance(enriched)
    beginner_score, setup_difficulty = _score_beginner(enriched)
    overall_score = (
        recency_score * 0.20
        + maintenance_score * 0.20
        + popularity_score * 0.15
        + beginner_score * 0.15
        + relevance_score * 0.30
    ) * 100.0
    enriched["scores"] = {
        "recency": round(recency_score, 3),
        "maintenance": round(maintenance_score, 3),
        "popularity": round(popularity_score, 3),
        "beginner": round(beginner_score, 3),
        "relevance": round(relevance_score, 3),
    }
    enriched["overall_score"] = round(overall_score, 2)
    enriched["confidence"] = _candidate_confidence(overall_score)
    enriched["setup_difficulty"] = setup_difficulty
    enriched["relevance_hits"] = relevance_hits
    return enriched


def _filter_reason(label: str, detail: str) -> dict[str, str]:
    return {"filter": label, "reason": detail}


def apply_hard_filters(candidate: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Return a filter verdict before soft-score ranking is applied."""
    failures: list[dict[str, str]] = []
    maintenance_flags = candidate.get("maintenance_flags") or []
    if "archived repository" in maintenance_flags:
        failures.append(_filter_reason("archived", "repository is archived"))
    if "disabled repository" in maintenance_flags:
        failures.append(_filter_reason("disabled", "repository is disabled"))
    days = _days_since(candidate.get("last_update"))
    if days is not None and days > int(settings["hard_stale_days"]):
        failures.append(_filter_reason("stale", f"last update is {days}d old"))
    if candidate.get("demo_flags"):
        failures.append(_filter_reason("demo_only", "description indicates a demo or showcase project"))
    if float((candidate.get("scores") or {}).get("relevance", 0.0)) < float(settings["min_relevance"]):
        failures.append(_filter_reason("relevance", "query match is too weak"))
    if str(candidate.get("confidence") or "low") not in set(settings["allowed_confidence"]):
        failures.append(_filter_reason("confidence", "candidate confidence is below this mode contract"))
    return {
        "passed": not failures,
        "failed_filters": failures,
        "rejection_reason": "; ".join(item["reason"] for item in failures) if failures else "",
    }


def apply_soft_score(candidate: dict[str, Any], min_score: float) -> dict[str, Any]:
    """Rank surviving candidates with a deterministic threshold."""
    passed = float(candidate.get("overall_score") or 0.0) >= min_score
    return {
        "passed": passed,
        "rejection_reason": "" if passed else f"overall score {candidate.get('overall_score')} is below threshold {min_score}",
    }


def _normalize_github_item(component: str, purpose: str, query: str, item: dict[str, Any]) -> dict[str, Any]:
    pushed_at = item.get("pushed_at") or item.get("updated_at") or ""
    flags = []
    if item.get("archived"):
        flags.append("archived repository")
    if item.get("disabled"):
        flags.append("disabled repository")
    if (days := _days_since(pushed_at)) is not None and days > 365:
        flags.append("stale recent activity signal")
    topics = item.get("topics") or []
    return {
        "component": component,
        "purpose": purpose,
        "query": query,
        "source_type": "github",
        "name": item.get("full_name") or item.get("name") or "",
        "url": item.get("html_url") or "",
        "description": item.get("description") or "",
        "latest_version": "",
        "license": (item.get("license") or {}).get("spdx_id") or "",
        "last_update": pushed_at,
        "maintenance_flags": flags,
        "beginner_fit_signals": _starter_signals(item.get("name") or "", item.get("description") or ""),
        "selection_hints": [f"topics: {', '.join(topics[:5])}"] if topics else [],
        "raw_signals": {
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "open_issues": item.get("open_issues_count", 0),
        },
    }


def search_github(
    query: str,
    component: str,
    purpose: str,
    limit: int,
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    require_token: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    token = os.getenv("GITHUB_TOKEN")
    if require_token and not token:
        raise SearchError("GITHUB_TOKEN is required for strict GitHub search")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": str(limit)}
    data = _get_json(
        f"{GITHUB_API}?{urllib.parse.urlencode(params)}",
        headers=headers,
        cache_dir=cache_dir / "github",
        cache_ttl_seconds=cache_ttl_seconds,
        retries=retries,
        allow_network=allow_network,
        telemetry=telemetry,
    )
    return [_normalize_github_item(component, purpose, query, item) for item in data.get("items", [])]


def _normalize_npm_item(component: str, purpose: str, query: str, item: dict[str, Any]) -> dict[str, Any]:
    package = item.get("package") or {}
    detail = (item.get("score") or {}).get("detail") or {}
    date = package.get("date") or ""
    flags = []
    if (days := _days_since(date)) is not None and days > 365:
        flags.append("stale package release signal")
    if detail.get("maintenance", 0.0) < 0.2:
        flags.append("weak maintenance score")
    return {
        "component": component,
        "purpose": purpose,
        "query": query,
        "source_type": "npm",
        "name": package.get("name") or "",
        "url": (package.get("links") or {}).get("npm") or "",
        "description": package.get("description") or "",
        "latest_version": package.get("version") or "",
        "license": package.get("license") or "",
        "last_update": date,
        "maintenance_flags": flags,
        "beginner_fit_signals": _starter_signals(package.get("name") or "", package.get("description") or ""),
        "selection_hints": [
            "npm scores q={:.2f} p={:.2f} m={:.2f}".format(
                detail.get("quality", 0.0),
                detail.get("popularity", 0.0),
                detail.get("maintenance", 0.0),
            )
        ],
        "raw_signals": {
            "quality": detail.get("quality"),
            "popularity": detail.get("popularity"),
            "maintenance": detail.get("maintenance"),
        },
    }


def search_npm(
    query: str,
    component: str,
    purpose: str,
    limit: int,
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    data = _get_json(
        f"{NPM_SEARCH_API}?{urllib.parse.urlencode({'text': query, 'size': str(limit)})}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        cache_dir=cache_dir / "npm",
        cache_ttl_seconds=cache_ttl_seconds,
        retries=retries,
        allow_network=allow_network,
        telemetry=telemetry,
    )
    return [_normalize_npm_item(component, purpose, query, item) for item in data.get("objects", [])]


def _normalize_pypi_item(
    component: str,
    purpose: str,
    query: str,
    summary_item: dict[str, str],
    details: dict[str, Any],
) -> dict[str, Any]:
    info = details.get("info") or {}
    releases = details.get("releases") or {}
    version = info.get("version") or ""
    files = releases.get(version) or []
    last_update = ""
    if files:
        last_update = files[-1].get("upload_time_iso_8601") or files[-1].get("upload_time") or ""
    flags = []
    if (days := _days_since(last_update)) is not None and days > 365:
        flags.append("stale package release signal")
    hints = []
    if info.get("requires_python"):
        hints.append(f"requires-python: {info.get('requires_python')}")
    return {
        "component": component,
        "purpose": purpose,
        "query": query,
        "source_type": "pypi",
        "name": info.get("name") or summary_item.get("name") or "",
        "url": info.get("package_url") or f"https://pypi.org/project/{summary_item.get('name', '')}/",
        "description": info.get("summary") or summary_item.get("description") or "",
        "latest_version": version,
        "license": info.get("license") or "",
        "last_update": last_update,
        "maintenance_flags": flags,
        "beginner_fit_signals": _starter_signals(info.get("name") or "", info.get("summary") or ""),
        "selection_hints": hints,
        "raw_signals": {"classifiers": (info.get("classifiers") or [])[:6]},
    }


def search_pypi(
    query: str,
    component: str,
    purpose: str,
    limit: int,
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    parser = PyPISearchParser()
    parser.feed(
        _get_text(
            f"{PYPI_SEARCH_URL}?{urllib.parse.urlencode({'q': query})}",
            headers={"User-Agent": USER_AGENT},
            cache_dir=cache_dir / "pypi-search",
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    )
    results = []
    for item in parser.results[:limit]:
        name = item.get("name") or ""
        if not name:
            continue
        details = _get_json(
            PYPI_JSON_URL.format(name=urllib.parse.quote(name)),
            headers={"User-Agent": USER_AGENT},
            cache_dir=cache_dir / "pypi-detail",
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
        results.append(_normalize_pypi_item(component, purpose, query, item, details))
    return results


def _contract_for_mode(search_mode: str, min_score_override: float | None) -> dict[str, Any]:
    base = dict(SEARCH_MODE_SETTINGS[search_mode])
    if min_score_override is not None:
        base["min_score"] = min_score_override
    return base


def _data_freshness(telemetry: dict[str, int]) -> str:
    if telemetry["stale_cache_hits"] > 0:
        return "stale"
    if telemetry["live_requests"] > 0:
        return "live"
    if telemetry["fresh_cache_hits"] > 0:
        return "cached"
    return "stale"


def _search_quality(search_mode: str, freshness: str, kept: int, warnings: list[str]) -> str:
    if kept == 0:
        return "low"
    if search_mode == "strict" and freshness == "live" and not warnings:
        return "high"
    if search_mode == "offline" or freshness == "stale":
        return "low"
    if search_mode == "degraded" or warnings:
        return "medium"
    return "medium"


def run_search(
    search_map: list[dict[str, Any]],
    sources: list[str],
    limit: int,
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    search_mode: str,
    min_score: float | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch from all enabled providers and return kept candidates plus a detailed report."""
    settings = _contract_for_mode(search_mode, min_score)
    allow_network = bool(settings["allow_network"])
    telemetry = {
        "live_requests": 0,
        "fresh_cache_hits": 0,
        "stale_cache_hits": 0,
        "request_failures": 0,
    }
    handlers = {
        "github": lambda q, c, p, l: search_github(
            q,
            c,
            p,
            l,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            require_token=bool(settings["require_github_token"]),
            telemetry=telemetry,
        ),
        "npm": lambda q, c, p, l: search_npm(
            q,
            c,
            p,
            l,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        ),
        "pypi": lambda q, c, p, l: search_pypi(
            q,
            c,
            p,
            l,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        ),
    }

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []
    fetched_count = 0

    for entry in search_map:
        component = str(entry.get("component") or "").strip()
        purpose = str(entry.get("purpose") or component).strip()
        queries = entry.get("query_variants") or [component]
        source_types = [source for source in entry.get("source_types", []) if source in handlers and source in sources]
        for query in queries:
            for source_type in source_types:
                try:
                    fetched = handlers[source_type](query, component, purpose, limit)
                except SearchError as exc:
                    message = f"{source_type}:{query}:{exc}"
                    warnings.append(message)
                    print(f"[WARN] {source_type} query failed for '{query}': {exc}", file=sys.stderr)
                    continue
                for item in fetched:
                    fetched_count += 1
                    candidate = enrich_candidate(item)
                    hard_filter = apply_hard_filters(candidate, settings)
                    candidate["filter_result"] = hard_filter
                    if not hard_filter["passed"]:
                        rejected.append(
                            {
                                "component": component,
                                "name": candidate.get("name"),
                                "source_type": source_type,
                                "query": query,
                                "overall_score": candidate.get("overall_score"),
                                "confidence": candidate.get("confidence"),
                                "rejection_reason": hard_filter["rejection_reason"],
                                "failed_filters": hard_filter["failed_filters"],
                            }
                        )
                        continue
                    soft_score = apply_soft_score(candidate, float(settings["min_score"]))
                    candidate["soft_score_result"] = soft_score
                    if not soft_score["passed"]:
                        rejected.append(
                            {
                                "component": component,
                                "name": candidate.get("name"),
                                "source_type": source_type,
                                "query": query,
                                "overall_score": candidate.get("overall_score"),
                                "confidence": candidate.get("confidence"),
                                "rejection_reason": soft_score["rejection_reason"],
                                "failed_filters": [],
                            }
                        )
                        continue
                    kept.append(candidate)

    kept.sort(
        key=lambda item: (
            -float(item.get("overall_score") or 0.0),
            str(item.get("component") or ""),
            str(item.get("name") or ""),
        )
    )
    freshness = _data_freshness(telemetry)
    quality = _search_quality(search_mode, freshness, len(kept), warnings)
    report = {
        "search_mode": search_mode,
        "search_quality": quality,
        "data_freshness": freshness,
        "contract": {
            "require_github_token": settings["require_github_token"],
            "hard_stale_days": settings["hard_stale_days"],
            "min_relevance": settings["min_relevance"],
            "min_score": settings["min_score"],
            "allowed_confidence": sorted(settings["allowed_confidence"]),
        },
        "stats": {
            "fetched": fetched_count,
            "kept": len(kept),
            "rejected": len(rejected),
            "warnings": len(warnings),
            **telemetry,
        },
        "warnings": warnings,
        "rejected_candidates": rejected[:50],
    }
    return kept, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch OSS candidates into pipeline state.")
    parser.add_argument("--state-in", required=True, help="Input state JSON path")
    parser.add_argument("--state-out", required=True, help="Output state JSON path")
    parser.add_argument("--sources", default="npm,pypi,github", help="Comma-separated enabled sources")
    parser.add_argument("--limit-per-source", type=int, default=5, help="Maximum results per query per source")
    parser.add_argument("--dry-run", action="store_true", help="Only derive and print search_map")
    parser.add_argument("--cache-dir", default=".cache", help="HTTP cache directory")
    parser.add_argument("--cache-ttl-seconds", type=int, default=DEFAULT_CACHE_TTL_SECONDS, help="Fresh cache TTL")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="HTTP retries per request")
    parser.add_argument("--search-mode", choices=sorted(SEARCH_MODE_SETTINGS), default="strict")
    parser.add_argument("--min-score", type=float, help="Optional override for the mode's score threshold")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state(args.state_in)
    state["search_map"] = state.get("search_map") or derive_search_map(state)

    if args.dry_run:
        save_state(args.state_out, state)
        print(json.dumps({"search_map": state["search_map"]}, indent=2, ensure_ascii=False))
        return 0

    candidates, report = run_search(
        state["search_map"],
        [item.strip() for item in args.sources.split(",") if item.strip()],
        args.limit_per_source,
        cache_dir=Path(args.cache_dir),
        cache_ttl_seconds=args.cache_ttl_seconds,
        retries=args.retries,
        search_mode=args.search_mode,
        min_score=args.min_score,
    )
    merged = merge_raw_candidates(state, candidates)
    merged["execution"] = {
        **(merged.get("execution") or {}),
        "search_mode": report["search_mode"],
        "search_quality": report["search_quality"],
        "data_freshness": report["data_freshness"],
    }
    merged["reports"] = {**(merged.get("reports") or {}), "search": report}
    save_state(args.state_out, merged)
    print(
        json.dumps(
            {
                "search_mode": report["search_mode"],
                "search_quality": report["search_quality"],
                "data_freshness": report["data_freshness"],
                "kept": report["stats"]["kept"],
                "rejected": report["stats"]["rejected"],
                "warnings": report["stats"]["warnings"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
