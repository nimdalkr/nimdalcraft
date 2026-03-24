# Source Search

## Purpose

Use deterministic code to fetch fresh OSS candidates and attach code-level evidence for each feature.
Do not replace this with model recall when current data matters.
Prefer the CLI entrypoint for normal use so users do not have to build state files manually.

## Primary Entry Point

```bash
python run.py --idea "B2B SaaS with auth, uploads, and email jobs" --search-mode degraded
```

This command bootstraps state, runs feature extraction, ranks and filters candidates, then writes:

- `state.json`
- `00-input.json` through `05-reconstruction-plan.json`
- `STARTER_README.md`
- `DECISION_LOG.md`
- `RECOVERY_ACTION.md`
- `NEXT_ACTION.md`
- `prompts/codex.md`
- `prompts/claude-code.md`
- `prompts/cursor.md`

Trusted starters are maintained as a validated set. Refresh them with:

```bash
python scripts/validate_starters.py --starter local-test-starter
python scripts/validate_starters.py --all --update-status
```

GitHub search snapshots are built separately for degraded runs:

```bash
python scripts/build_github_snapshot.py
```

## Supported Sources

Primary fetch sources:

- `github`
- `npm`
- `pypi`

Evidence and rerank sources:

- `sourcegraph`
- `grep_app`
- `searchcode`
- `code_rag`
- `oss_insight`
- `deps_dev`
- `continue`
- `codeium`

## Command

```bash
python scripts/source_search.py --state-in work/state.json --state-out work/state.json
```

Useful flags:

```bash
python run.py --idea "Client portal SaaS" --search-mode strict --result-mode safe
python run.py --idea "Client portal SaaS" --search-mode degraded --result-mode explore
python run.py --idea "Starter-driven SaaS" --search-mode strict --output-mode runnable
python run.py --idea "JWT auth with uploads" --target-project ../my-app --apply-adaptations
python scripts/source_search.py --state-in work/state.json --state-out work/state.json --search-mode offline
```

## Adapter Configuration

Environment variables:

- `GITHUB_TOKEN` for strict GitHub search
- `SOURCEGRAPH_TOKEN` for Sourcegraph GraphQL search
- `SOURCEGRAPH_ENDPOINT` to override the GraphQL endpoint
- `GREP_APP_ENDPOINT` to enable the best-effort grep.app adapter

Public APIs used directly by the pipeline:

- `searchcode` via `https://searchcode.com/api`
- `OSS Insight` via `https://api.ossinsight.io/v1`
- `deps.dev` via `https://api.deps.dev/v3`

## Behavior

- Reads `state.search_map` if present.
- Derives `state.search_map` from `state.architecture.component_search_targets` if missing.
- Uses `state.feature_map` to build symbol hints, snippet queries, semantic queries, and adaptation targets.
- Queries each enabled primary source per component query.
- Retries failed requests and falls back to cached responses when available.
- Uses the daily GitHub snapshot first in `degraded` and `offline` modes.
- Normalizes results into `state.raw_candidates`.
- Builds `code_evidence`, `activity_signals`, `credibility_signals`, and `adaptation_hints` on each candidate.
- Applies hard filters before soft-score ranking.
- Scores candidates for recency, maintenance, popularity, beginner fit, relevance, code search, activity, credibility, and adaptation fit.
- Filters out low-scoring, archived, stale, demo-only, and weak code-match results before curation.

## Search Contract

- `strict`
  - requires `GITHUB_TOKEN`
  - requires GitHub search to stay enabled
  - returns high-confidence candidates only
- `degraded`
  - uses the daily GitHub snapshot first
  - may fall back to live GitHub or cache when a query is missing from the snapshot
  - marks result quality down
- `offline`
  - reads GitHub snapshot and cache only
  - freshness will be `snapshot`, `cached`, or `stale`

## Runnable Contract

- `runnable` mode prefers starters with `status=verified`
- if no verified starter matches, it may fall back to `status=flaky`
- `--force-starter` may override normal selection for validation and debugging
- runnable validation checks `clone`, `install`, `env`, and `run`

## Candidate Shape

Each raw candidate should stay compact and comparable. The script emits objects shaped like:

```json
{
  "component": "",
  "purpose": "",
  "query": "",
  "source_type": "github | npm | pypi",
  "name": "",
  "url": "",
  "description": "",
  "latest_version": "",
  "license": "",
  "last_update": "",
  "maintenance_flags": [],
  "beginner_fit_signals": [],
  "complexity_signals": [],
  "scores": {
    "recency": 0.0,
    "maintenance": 0.0,
    "popularity": 0.0,
    "beginner": 0.0,
    "relevance": 0.0,
    "code_search": 0.0,
    "activity": 0.0,
    "credibility": 0.0,
    "adaptation": 0.0
  },
  "code_evidence": {
    "feature_label": "",
    "symbol_matches": [],
    "snippet_matches": [],
    "semantic_hits": []
  },
  "activity_signals": {},
  "credibility_signals": {},
  "adaptation_hints": [],
  "overall_score": 0.0,
  "setup_difficulty": "low | medium | high",
  "selection_hints": [],
  "raw_signals": {}
}
```

## Signal Policy

The deterministic layer should gather signals such as:

- archival status
- last update date
- stars or registry quality scores when available
- package version recency
- symbol or snippet hits that indicate feature-level match
- semantic tokens that indicate function-level relevance
- activity proxies and dependency-usage proxies
- complexity keywords that should be penalized for beginners

It should not decide the winner. That belongs to `curation_agent`.
