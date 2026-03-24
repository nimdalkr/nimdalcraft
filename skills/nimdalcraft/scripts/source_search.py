#!/usr/bin/env python3
"""Fetch, filter, score, and explain OSS candidates for Nimdalcraft's code retrieval engine."""

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

SCRIPT_DIR = Path(__file__).resolve().parent
ASSET_DIR = SCRIPT_DIR.parent / "assets"
USER_AGENT = "nimdalcraft/0.4"
GITHUB_API = "https://api.github.com/search/repositories"
GITHUB_SNAPSHOT_PATH = ASSET_DIR / "github-search-snapshots.json"
NPM_SEARCH_API = "https://registry.npmjs.org/-/v1/search"
PYPI_SEARCH_URL = "https://pypi.org/search/"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_RETRIES = 3
DEFAULT_SNAPSHOT_STALE_HOURS = 48
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
PRIMARY_FETCH_SOURCES = {"github", "npm", "pypi"}
CODE_EVIDENCE_SOURCES = {"sourcegraph", "grep_app", "searchcode", "code_rag"}
QUALITY_EVIDENCE_SOURCES = {"oss_insight", "deps_dev"}
ADAPTATION_EVIDENCE_SOURCES = {"continue", "codeium"}
SOURCEGRAPH_DEFAULT_ENDPOINT = "https://sourcegraph.com/.api/graphql"
SEARCHCODE_API_BASE = "https://searchcode.com/api"
OSS_INSIGHT_API_BASE = "https://api.ossinsight.io/v1"
DEPS_DEV_API_BASE = "https://api.deps.dev/v3"


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


def load_github_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"generated_at": "", "queries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"generated_at": "", "queries": {}}
    queries = data.get("queries")
    if not isinstance(queries, dict):
        data["queries"] = {}
    return data


def _snapshot_entry(snapshot: dict[str, Any], query: str) -> dict[str, Any] | None:
    queries = snapshot.get("queries") or {}
    if query in queries:
        return queries[query]
    lowered = query.casefold()
    for key, value in queries.items():
        if str(key).casefold() == lowered:
            return value
    return None


def _snapshot_generated_at(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("generated_at") or "")


def _snapshot_stale(snapshot: dict[str, Any], stale_after_hours: int = DEFAULT_SNAPSHOT_STALE_HOURS) -> bool:
    generated_at = _parse_date(_snapshot_generated_at(snapshot))
    if generated_at is None:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    age_seconds = (now - generated_at.astimezone(dt.timezone.utc)).total_seconds()
    return age_seconds > (stale_after_hours * 60 * 60)


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


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None,
    cache_dir: Path | None,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cache_key = f"POST::{url}::{hashlib.sha256(body).hexdigest()}"
    cache_path = _cache_file(cache_dir, cache_key) if cache_dir else None
    now = time.time()
    if cache_path and cache_path.exists():
        age = now - cache_path.stat().st_mtime
        if age <= cache_ttl_seconds:
            telemetry["fresh_cache_hits"] += 1
            cached = _read_cache(cache_path) or b"{}"
            return json.loads(cached.decode("utf-8"))
    if not allow_network:
        if cache_path and cache_path.exists():
            telemetry["stale_cache_hits"] += 1
            cached = _read_cache(cache_path) or b"{}"
            return json.loads(cached.decode("utf-8"))
        telemetry["request_failures"] += 1
        raise SearchError("offline mode has no cached response for this POST request")

    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                response_payload = response.read()
            telemetry["live_requests"] += 1
            if cache_path:
                _write_cache(cache_path, response_payload)
            return json.loads(response_payload.decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.2 * attempt)
                continue
            if cache_path and cache_path.exists():
                telemetry["stale_cache_hits"] += 1
                cached = _read_cache(cache_path) or b"{}"
                return json.loads(cached.decode("utf-8"))
    telemetry["request_failures"] += 1
    raise SearchError(f"request failed for {url}: {last_error}")


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


def _repo_slug_from_url(url: str) -> str:
    match = re.search(r"github\.com/([^/\s]+/[^/\s#?]+)", url or "", re.IGNORECASE)
    return match.group(1).rstrip(".git") if match else ""


def _owner_repo(slug: str) -> tuple[str, str]:
    if "/" not in slug:
        return "", ""
    owner, repo = slug.split("/", 1)
    return owner, repo


def _normalize_snippet(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


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


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _semantic_tokens(values: list[str]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        tokens.extend(token for token in _tokenize(value) if len(token) > 2 and token not in RELEVANCE_STOPWORDS)
    return sorted(set(tokens))


def build_retrieval_context(candidate: dict[str, Any], entry: dict[str, Any], enabled_sources: set[str]) -> dict[str, Any]:
    symbol_hints = _dedupe_texts([str(item) for item in (entry.get("symbol_hints") or [])])
    snippet_queries = _dedupe_texts([str(item) for item in (entry.get("snippet_queries") or [])])
    semantic_queries = _dedupe_texts(
        [str(item) for item in (entry.get("semantic_queries") or [])]
        + [str(item) for item in (entry.get("query_variants") or [])]
        + [str(entry.get("purpose") or "")]
    )
    adaptation_targets = _dedupe_texts([str(item) for item in (entry.get("adaptation_targets") or [])])
    searchable = " ".join(
        [
            str(candidate.get("name") or ""),
            str(candidate.get("description") or ""),
            str(candidate.get("query") or ""),
            " ".join(candidate.get("selection_hints") or []),
        ]
    ).casefold()
    symbol_matches = sorted({hint for hint in symbol_hints if hint.casefold() in searchable})
    snippet_matches = sorted({hint for hint in snippet_queries if hint.casefold() in searchable})
    semantic_hits = sorted({token for token in _semantic_tokens(semantic_queries) if token in searchable})
    feature_label = str(entry.get("feature_label") or entry.get("component") or "").strip()
    retrieval_sources = sorted(
        source
        for source in enabled_sources
        if source in (CODE_EVIDENCE_SOURCES | QUALITY_EVIDENCE_SOURCES | ADAPTATION_EVIDENCE_SOURCES)
    )
    return {
        "feature_label": feature_label,
        "symbol_hints": symbol_hints,
        "symbol_matches": symbol_matches,
        "snippet_queries": snippet_queries,
        "snippet_matches": snippet_matches,
        "semantic_queries": semantic_queries,
        "semantic_hits": semantic_hits,
        "adaptation_targets": adaptation_targets,
        "retrieval_sources": retrieval_sources,
    }


def _searchcode_hits(
    candidate: dict[str, Any],
    retrieval_context: dict[str, Any],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    queries = _dedupe_texts(
        list(retrieval_context.get("symbol_hints") or [])[:2]
        + list(retrieval_context.get("snippet_queries") or [])[:2]
    )
    repo_slug = _repo_slug_from_url(str(candidate.get("url") or ""))
    repo_filter = f" repo:{repo_slug}" if repo_slug else ""
    hits: list[dict[str, Any]] = []
    for query in queries[:3]:
        params = {"q": f"{query}{repo_filter}"}
        try:
            data = _get_json(
                f"{SEARCHCODE_API_BASE}/codesearch_I/?{urllib.parse.urlencode(params)}",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                cache_dir=cache_dir / "searchcode",
                cache_ttl_seconds=cache_ttl_seconds,
                retries=retries,
                allow_network=allow_network,
                telemetry=telemetry,
            )
        except SearchError:
            continue
        for item in (data.get("results") or [])[:2]:
            preview = item.get("lines") or item.get("line") or item.get("filename") or ""
            hits.append(
                {
                    "query": query,
                    "repo": item.get("repo") or "",
                    "path": item.get("filename") or "",
                    "preview": _normalize_snippet(preview),
                }
            )
    return hits[:5]


def _sourcegraph_hits(
    candidate: dict[str, Any],
    retrieval_context: dict[str, Any],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    endpoint = os.getenv("SOURCEGRAPH_ENDPOINT", SOURCEGRAPH_DEFAULT_ENDPOINT).strip()
    token = os.getenv("SOURCEGRAPH_TOKEN", "").strip()
    if not endpoint or not token:
        return []
    repo_slug = _repo_slug_from_url(str(candidate.get("url") or ""))
    headers = {"User-Agent": USER_AGENT, "Authorization": f"token {token}"}
    hits: list[dict[str, Any]] = []
    symbol_queries = retrieval_context.get("symbol_hints") or []
    snippet_queries = retrieval_context.get("snippet_queries") or []
    search_terms = _dedupe_texts(list(symbol_queries)[:2] + list(snippet_queries)[:1])
    graphql = """
    query CodeSearch($query: String!) {
      search(query: $query, version: V3) {
        results {
          results {
            __typename
            ... on FileMatch {
              repository { name }
              file { path url }
              lineMatches {
                preview
                lineNumber
              }
            }
            ... on SymbolMatch {
              symbol {
                name
                kind
                location {
                  resource {
                    path
                    url
                    repository { name }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    for term in search_terms:
        repo_scope = f" repo:{repo_slug}" if repo_slug else ""
        query = f"{term}{repo_scope} count:5"
        if term in symbol_queries:
            query = f"type:symbol {query}"
        elif any(ch in term for ch in "()[]{}.*+?|\\"):
            query = f"patternType:regexp {query}"
        try:
            data = _post_json(
                endpoint,
                {"query": graphql, "variables": {"query": query}},
                headers=headers,
                cache_dir=cache_dir / "sourcegraph",
                cache_ttl_seconds=cache_ttl_seconds,
                retries=retries,
                allow_network=allow_network,
                telemetry=telemetry,
            )
        except SearchError:
            continue
        results = (((data.get("data") or {}).get("search") or {}).get("results") or {}).get("results") or []
        for item in results[:3]:
            typename = str(item.get("__typename") or "")
            if typename == "FileMatch":
                preview = ""
                line_matches = item.get("lineMatches") or []
                if line_matches:
                    preview = line_matches[0].get("preview") or ""
                hits.append(
                    {
                        "query": term,
                        "repo": ((item.get("repository") or {}).get("name") or ""),
                        "path": ((item.get("file") or {}).get("path") or ""),
                        "preview": _normalize_snippet(preview),
                    }
                )
            elif typename == "SymbolMatch":
                symbol = item.get("symbol") or {}
                resource = ((symbol.get("location") or {}).get("resource") or {})
                hits.append(
                    {
                        "query": term,
                        "repo": ((resource.get("repository") or {}).get("name") or ""),
                        "path": resource.get("path") or "",
                        "preview": _normalize_snippet(f"{symbol.get('kind') or 'symbol'} {symbol.get('name') or ''}"),
                    }
                )
    return hits[:5]


def _grep_app_hits(
    candidate: dict[str, Any],
    retrieval_context: dict[str, Any],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    endpoint = os.getenv("GREP_APP_ENDPOINT", "").strip()
    if not endpoint:
        return []
    repo_slug = _repo_slug_from_url(str(candidate.get("url") or ""))
    queries = _dedupe_texts(list(retrieval_context.get("snippet_queries") or [])[:2] + list(retrieval_context.get("symbol_hints") or [])[:1])
    hits: list[dict[str, Any]] = []
    for query in queries[:3]:
        params = {"q": query}
        if repo_slug:
            params["filter[repo][0]"] = repo_slug
        try:
            data = _get_json(
                f"{endpoint}?{urllib.parse.urlencode(params)}",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                cache_dir=cache_dir / "grep_app",
                cache_ttl_seconds=cache_ttl_seconds,
                retries=retries,
                allow_network=allow_network,
                telemetry=telemetry,
            )
        except SearchError:
            continue
        for item in (data.get("hits") or data.get("results") or [])[:2]:
            preview = item.get("content") or item.get("snippet") or ""
            hits.append(
                {
                    "query": query,
                    "repo": item.get("repo") or repo_slug,
                    "path": item.get("path") or "",
                    "preview": _normalize_snippet(preview),
                }
            )
    return hits[:5]


def _oss_insight_metrics(
    candidate: dict[str, Any],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> dict[str, Any]:
    if str(candidate.get("source_type") or "") != "github":
        return {}
    owner, repo = _owner_repo(_repo_slug_from_url(str(candidate.get("url") or "")))
    if not owner or not repo:
        return {}
    try:
        creators = _get_json(
            f"{OSS_INSIGHT_API_BASE}/repos/{owner}/{repo}/pull-request-creators?page_size=100",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            cache_dir=cache_dir / "oss_insight",
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    except SearchError:
        return {}
    rows = creators.get("rows") or creators.get("data") or []
    contributor_count = len(rows)
    pr_total = 0.0
    for row in rows:
        for key in ("pull_requests", "prs", "count"):
            if key in row:
                pr_total += float(row.get(key) or 0.0)
                break
    return {
        "contributors_count": contributor_count,
        "commit_frequency_proxy": round(pr_total, 2),
        "activity_source": "oss_insight",
    }


def _deps_dev_metrics(
    candidate: dict[str, Any],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> dict[str, Any]:
    source_type = str(candidate.get("source_type") or "")
    if source_type not in {"npm", "pypi"}:
        return {}
    system = "npm" if source_type == "npm" else "pypi"
    name = urllib.parse.quote(str(candidate.get("name") or ""), safe="")
    version = urllib.parse.quote(str(candidate.get("latest_version") or ""), safe="")
    if not name:
        return {}
    path = f"/systems/{system}/packages/{name}"
    if version:
        path += f"/versions/{version}"
    try:
        data = _get_json(
            f"{DEPS_DEV_API_BASE}{path}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            cache_dir=cache_dir / "deps_dev",
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    except SearchError:
        return {}
    licenses = data.get("licenses") or []
    links = data.get("links") or []
    related = data.get("relatedProjects") or []
    score = min(1.0, ((1 if licenses else 0) + min(3, len(links)) + min(3, len(related))) / 7.0)
    return {
        "dependency_usage_proxy": round(score, 3),
        "license_present": bool(licenses),
        "related_project_count": len(related),
        "credibility_source": "deps_dev",
    }


def _external_retrieval_evidence(
    candidate: dict[str, Any],
    retrieval_context: dict[str, Any],
    enabled_sources: set[str],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"sourcegraph": [], "grep_app": [], "searchcode": []}
    if "sourcegraph" in enabled_sources:
        evidence["sourcegraph"] = _sourcegraph_hits(
            candidate,
            retrieval_context,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    if "grep_app" in enabled_sources:
        evidence["grep_app"] = _grep_app_hits(
            candidate,
            retrieval_context,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    if "searchcode" in enabled_sources:
        evidence["searchcode"] = _searchcode_hits(
            candidate,
            retrieval_context,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
    return evidence


def _score_code_search(candidate: dict[str, Any], retrieval_context: dict[str, Any]) -> float:
    symbol_total = max(1, len(retrieval_context.get("symbol_hints") or []))
    snippet_total = max(1, len(retrieval_context.get("snippet_queries") or []))
    semantic_total = max(1, len(_semantic_tokens(retrieval_context.get("semantic_queries") or [])))
    symbol_score = len(retrieval_context.get("symbol_matches") or []) / symbol_total
    snippet_score = len(retrieval_context.get("snippet_matches") or []) / snippet_total
    semantic_score = len(retrieval_context.get("semantic_hits") or []) / semantic_total
    external_hits = candidate.get("external_code_evidence") or {}
    external_score = min(
        1.0,
        (
            min(2, len(external_hits.get("sourcegraph") or []))
            + min(2, len(external_hits.get("grep_app") or []))
            + min(2, len(external_hits.get("searchcode") or []))
        )
        / 6.0,
    )
    if candidate.get("source_type") == "github":
        semantic_score += 0.08
    return _clamp((symbol_score * 0.28) + (snippet_score * 0.22) + (semantic_score * 0.20) + (external_score * 0.30))


def _score_activity(candidate: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    raw = candidate.get("raw_signals") or {}
    source_type = str(candidate.get("source_type") or "")
    recency = _score_recency(candidate.get("last_update"))
    external = candidate.get("external_activity_signals") or {}
    if external:
        contributors = float(external.get("contributors_count") or 0.0)
        commit_frequency = float(external.get("commit_frequency_proxy") or 0.0)
        score = _clamp((recency * 0.35) + min(1.0, contributors / 40.0) * 0.30 + min(1.0, commit_frequency / 100.0) * 0.35)
        return score, {
            "contributors_estimate": contributors,
            "commit_frequency_estimate": commit_frequency,
            "growth_signal": round(score, 3),
            "activity_source": external.get("activity_source") or "external",
        }
    if source_type == "github":
        stars = float(raw.get("stars") or 0.0)
        forks = float(raw.get("forks") or 0.0)
        contributors = max(1.0, min(25.0, round(math.sqrt(stars / 20.0), 1))) if stars > 0 else 1.0
        commit_frequency = round((recency * 12.0) + min(8.0, math.log10(forks + 1.0) * 4.0), 2)
        growth = round(min(1.0, math.log10(stars + forks + 1.0) / 4.0), 3)
        score = _clamp((recency * 0.45) + (growth * 0.35) + min(1.0, contributors / 12.0) * 0.20)
        return score, {
            "contributors_estimate": contributors,
            "commit_frequency_estimate": commit_frequency,
            "growth_signal": growth,
            "activity_source": "oss_insight_proxy",
        }
    registry_popularity = float(raw.get("popularity") or raw.get("quality") or 0.35)
    score = _clamp((recency * 0.55) + (registry_popularity * 0.45))
    return score, {
        "contributors_estimate": 0,
        "commit_frequency_estimate": round(recency * 10.0, 2),
        "growth_signal": round(registry_popularity, 3),
        "activity_source": "registry_proxy",
    }


def _score_credibility(candidate: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    raw = candidate.get("raw_signals") or {}
    source_type = str(candidate.get("source_type") or "")
    external = candidate.get("external_credibility_signals") or {}
    if external:
        score = _clamp(
            (float(external.get("dependency_usage_proxy") or 0.0) * 0.75)
            + ((1.0 if external.get("license_present") else 0.0) * 0.25)
        )
        return score, {
            "dependency_usage_proxy": round(float(external.get("dependency_usage_proxy") or 0.0), 3),
            "license_present": bool(external.get("license_present")),
            "credibility_source": external.get("credibility_source") or "external",
        }
    has_license = 1.0 if candidate.get("license") else 0.0
    if source_type == "github":
        usage = _clamp(math.log10(float(raw.get("stars") or 0.0) + float(raw.get("forks") or 0.0) + 1.0) / 5.0)
    else:
        usage = _clamp(float(raw.get("popularity") or raw.get("maintenance") or raw.get("quality") or 0.35))
    score = _clamp((usage * 0.75) + (has_license * 0.25))
    return score, {
        "dependency_usage_proxy": round(usage, 3),
        "license_present": bool(candidate.get("license")),
        "credibility_source": "deps_dev_proxy",
    }


def _adaptation_hints(candidate: dict[str, Any], retrieval_context: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for target in retrieval_context.get("adaptation_targets") or []:
        hints.append(f"adapt for {target}")
    if candidate.get("source_type") == "github":
        hints.append("extract function-level implementation before copying project structure")
    if candidate.get("beginner_fit_signals"):
        hints.append("prefer narrow transplant over full repo adoption")
    if candidate.get("complexity_signals"):
        hints.append("strip infra-heavy layers during reconstruction")
    return _dedupe_texts(hints)


def _score_adaptation(candidate: dict[str, Any], retrieval_context: dict[str, Any]) -> tuple[float, list[str]]:
    hints = _adaptation_hints(candidate, retrieval_context)
    starter_bonus = min(0.2, len(candidate.get("beginner_fit_signals") or []) * 0.06)
    complexity_penalty = min(0.35, len(candidate.get("complexity_signals") or []) * 0.1)
    target_bonus = min(0.2, len(retrieval_context.get("adaptation_targets") or []) * 0.05)
    score = _clamp(0.52 + starter_bonus + target_bonus - complexity_penalty)
    return score, hints


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


def enrich_candidate(
    candidate: dict[str, Any],
    entry: dict[str, Any],
    enabled_sources: set[str],
    *,
    cache_dir: Path,
    cache_ttl_seconds: int,
    retries: int,
    allow_network: bool,
    telemetry: dict[str, int],
) -> dict[str, Any]:
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
    retrieval_context = build_retrieval_context(enriched, entry, enabled_sources)
    enriched["external_code_evidence"] = _external_retrieval_evidence(
        enriched,
        retrieval_context,
        enabled_sources,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        retries=retries,
        allow_network=allow_network,
        telemetry=telemetry,
    )
    enriched["external_activity_signals"] = (
        _oss_insight_metrics(
            enriched,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
        if "oss_insight" in enabled_sources
        else {}
    )
    enriched["external_credibility_signals"] = (
        _deps_dev_metrics(
            enriched,
            cache_dir=cache_dir,
            cache_ttl_seconds=cache_ttl_seconds,
            retries=retries,
            allow_network=allow_network,
            telemetry=telemetry,
        )
        if "deps_dev" in enabled_sources
        else {}
    )
    code_search_score = _score_code_search(enriched, retrieval_context)
    activity_score, activity_signals = _score_activity(enriched)
    credibility_score, credibility_signals = _score_credibility(enriched)
    adaptation_score, adaptation_hints = _score_adaptation(enriched, retrieval_context)
    beginner_score, setup_difficulty = _score_beginner(enriched)
    overall_score = (
        recency_score * 0.10
        + maintenance_score * 0.14
        + popularity_score * 0.08
        + beginner_score * 0.10
        + relevance_score * 0.18
        + code_search_score * 0.20
        + activity_score * 0.10
        + credibility_score * 0.06
        + adaptation_score * 0.04
    ) * 100.0
    enriched["scores"] = {
        "recency": round(recency_score, 3),
        "maintenance": round(maintenance_score, 3),
        "popularity": round(popularity_score, 3),
        "beginner": round(beginner_score, 3),
        "relevance": round(relevance_score, 3),
        "code_search": round(code_search_score, 3),
        "activity": round(activity_score, 3),
        "credibility": round(credibility_score, 3),
        "adaptation": round(adaptation_score, 3),
    }
    enriched["overall_score"] = round(overall_score, 2)
    enriched["confidence"] = _candidate_confidence(overall_score)
    enriched["setup_difficulty"] = setup_difficulty
    enriched["relevance_hits"] = relevance_hits
    enriched["retrieval_sources"] = retrieval_context["retrieval_sources"]
    enriched["code_evidence"] = {
        "feature_label": retrieval_context["feature_label"],
        "symbol_matches": retrieval_context["symbol_matches"],
        "snippet_matches": retrieval_context["snippet_matches"],
        "semantic_hits": retrieval_context["semantic_hits"],
        "external_hits": {
            key: value
            for key, value in (enriched.get("external_code_evidence") or {}).items()
            if value
        },
        "queries": {
            "symbols": retrieval_context["symbol_hints"],
            "snippets": retrieval_context["snippet_queries"],
            "semantic": retrieval_context["semantic_queries"],
        },
    }
    enriched["activity_signals"] = activity_signals
    enriched["credibility_signals"] = credibility_signals
    enriched["adaptation_hints"] = adaptation_hints
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
    if candidate.get("retrieval_sources") and float((candidate.get("scores") or {}).get("code_search", 0.0)) < max(0.2, float(settings["min_relevance"]) - 0.05):
        failures.append(_filter_reason("code_search", "code-level retrieval evidence is too weak"))
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


def search_github_snapshot(
    query: str,
    component: str,
    purpose: str,
    limit: int,
    *,
    snapshot: dict[str, Any],
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    entry = _snapshot_entry(snapshot, query)
    if not entry:
        telemetry["github_snapshot_misses"] += 1
        return []
    telemetry["github_snapshot_hits"] += 1
    items = entry.get("items") or []
    return [
        _normalize_github_item(component, purpose, query, item)
        for item in items[:limit]
        if isinstance(item, dict)
    ]


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
    search_mode: str,
    snapshot: dict[str, Any],
    telemetry: dict[str, int],
) -> list[dict[str, Any]]:
    if search_mode in {"degraded", "offline"}:
        snapshot_results = search_github_snapshot(
            query,
            component,
            purpose,
            limit,
            snapshot=snapshot,
            telemetry=telemetry,
        )
        if snapshot_results:
            return snapshot_results
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


def _data_freshness(telemetry: dict[str, int], snapshot: dict[str, Any]) -> str:
    if telemetry["stale_cache_hits"] > 0:
        return "stale"
    if telemetry["live_requests"] > 0:
        return "live"
    if telemetry["github_snapshot_hits"] > 0:
        return "stale" if _snapshot_stale(snapshot) else "snapshot"
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
    if freshness == "snapshot":
        return "medium"
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
    snapshot = load_github_snapshot(GITHUB_SNAPSHOT_PATH)
    enabled_sources = {item.strip() for item in sources if item.strip()}
    telemetry = {
        "live_requests": 0,
        "fresh_cache_hits": 0,
        "stale_cache_hits": 0,
        "request_failures": 0,
        "github_snapshot_hits": 0,
        "github_snapshot_misses": 0,
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
            search_mode=search_mode,
            snapshot=snapshot,
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

    primary_sources = sorted(source for source in enabled_sources if source in PRIMARY_FETCH_SOURCES)
    evidence_sources = sorted(source for source in enabled_sources if source not in PRIMARY_FETCH_SOURCES)

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []
    fetched_count = 0
    github_enabled = "github" in enabled_sources
    if not primary_sources:
        warnings.append("No primary fetch source is enabled. At least one of github, npm, or pypi is required to collect candidates.")

    for entry in search_map:
        component = str(entry.get("component") or "").strip()
        purpose = str(entry.get("purpose") or component).strip()
        queries = entry.get("query_variants") or [component]
        entry_sources = {str(source).strip() for source in (entry.get("source_types") or []) if str(source).strip()}
        source_types = [source for source in primary_sources if source in entry_sources and source in handlers]
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
                    candidate = enrich_candidate(
                        item,
                        entry,
                        enabled_sources & entry_sources,
                        cache_dir=cache_dir,
                        cache_ttl_seconds=cache_ttl_seconds,
                        retries=retries,
                        allow_network=allow_network,
                        telemetry=telemetry,
                    )
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
    if github_enabled and search_mode in {"degraded", "offline"}:
        if not (snapshot.get("queries") or {}):
            warnings.append("GitHub snapshot is unavailable. Falling back to live GitHub or cache where the current mode allows it.")
        elif telemetry["github_snapshot_hits"] == 0:
            warnings.append("GitHub snapshot had no exact query hit. Falling back to live GitHub or cache for some queries.")
        if telemetry["github_snapshot_hits"] > 0 and _snapshot_stale(snapshot):
            warnings.append("GitHub snapshot is older than the freshness target and may be stale.")
    freshness = _data_freshness(telemetry, snapshot)
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
            "primary_sources": primary_sources,
            "evidence_sources": evidence_sources,
        },
        "engine": {
            "mode": "code_retrieval_reconstruction",
            "pipeline": [
                "feature_extraction",
                "code_retrieval",
                "semantic_rerank",
                "activity_filter",
                "credibility_filter",
                "project_adaptation",
            ],
            "adapter_support": {
                "sourcegraph": bool(os.getenv("SOURCEGRAPH_TOKEN", "").strip()),
                "grep_app": bool(os.getenv("GREP_APP_ENDPOINT", "").strip()),
                "searchcode": True,
                "oss_insight": True,
                "deps_dev": True,
            },
        },
        "snapshot": {
            "path": str(GITHUB_SNAPSHOT_PATH),
            "generated_at": _snapshot_generated_at(snapshot),
            "query_count": len(snapshot.get("queries") or {}),
            "hits": telemetry["github_snapshot_hits"],
            "misses": telemetry["github_snapshot_misses"],
            "stale": _snapshot_stale(snapshot) if (snapshot.get("queries") or {}) else True,
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
    parser.add_argument(
        "--sources",
        default="github,npm,pypi,sourcegraph,grep_app,searchcode,code_rag,oss_insight,deps_dev,continue,codeium",
        help="Comma-separated enabled sources",
    )
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
