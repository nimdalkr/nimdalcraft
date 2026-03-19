# Source Search

## Purpose

Use deterministic code to fetch fresh candidates from supported registries and repositories.
Do not replace this with model recall when current data matters.
Prefer the CLI entrypoint for normal use so users do not have to build state files manually.

## Primary Entry Point

```bash
python run.py --idea "B2B SaaS with auth, uploads, and email jobs" --search-mode degraded
```

This command bootstraps state, runs search, ranks and filters candidates, then writes:

- `state.json`
- `00-input.json` through `05-starter-plan.json`
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

## Supported Sources In V1

- `github`
- `npm`
- `pypi`

## Command

```bash
python scripts/source_search.py --state-in work/state.json --state-out work/state.json
```

Useful flags:

```bash
python run.py --idea "Client portal SaaS" --search-mode strict --result-mode safe
python run.py --idea "Client portal SaaS" --search-mode degraded --result-mode explore
python run.py --idea "Starter-driven SaaS" --search-mode strict --output-mode runnable
python scripts/source_search.py --state-in work/state.json --state-out work/state.json --search-mode offline
```

## Behavior

- Reads `state.search_map` if present.
- Derives `state.search_map` from `state.architecture.component_search_targets` if missing.
- Queries each enabled source per component query.
- Retries failed requests and falls back to cached responses when available.
- Normalizes results into `state.raw_candidates`.
- Applies hard filters before soft-score ranking.
- Scores candidates for recency, maintenance, popularity, beginner fit, and relevance.
- Filters out low-scoring, archived, stale, and demo-only results before curation.
- Adds maintenance and beginner-fit signals without making final recommendations.

## Search Contract

- `strict`
  - requires `GITHUB_TOKEN`
  - requires GitHub search to stay enabled
  - returns high-confidence candidates only
- `degraded`
  - tolerates weaker source coverage
  - marks result quality down
- `offline`
  - reads cache only
  - freshness will be `cached` or `stale`

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
    "beginner": 0.0
  },
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
- keywords suggesting starter-template suitability
- complexity keywords that should be penalized for beginners

It should not decide the winner. That belongs to `curation_agent`.
