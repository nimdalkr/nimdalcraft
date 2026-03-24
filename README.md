# Nimdalcraft

`nimdalcraft` is a prompt-infrastructure skill and CLI for vibe coding with real OSS code.

It turns:

`idea -> feature extraction -> code retrieval -> semantic rerank -> activity and credibility filtering -> reconstruction plan -> runnable or handoff output`

The current implementation lives in [skills/nimdalcraft](./skills/nimdalcraft).

## What It Does

- Takes a vague product or feature idea and normalizes it into concrete feature units
- Searches GitHub, npm, and PyPI for reusable OSS implementation surfaces
- Scores candidates with code-match, activity, credibility, and adaptation-fit signals
- Produces human-readable outputs such as `STARTER_README.md`, `DECISION_LOG.md`, and `NEXT_ACTION.md`
- Supports `runnable` mode with a validated starter set
- Tracks starter reliability with `verified`, `flaky`, and `broken` states
- Uses a CI-built GitHub search snapshot so `degraded` mode is not dependent on unauthenticated live GitHub search

## Core Upgrade Direction

Nimdalcraft is no longer framed as a repo recommender.
It is framed as a code retrieval and reconstruction engine.

The target path is:

1. Extract the feature from the user's request
2. Find symbol, snippet, and semantic matches
3. Filter dead or weak sources using activity and credibility signals
4. Adapt the retrieved code to the user's project structure

## Retrieval Model

Primary fetch sources:

- `github`
- `npm`
- `pypi`

Evidence and rerank sources modeled by the pipeline:

- `sourcegraph`
- `grep_app`
- `searchcode`
- `code_rag`
- `oss_insight`
- `deps_dev`
- `continue`
- `codeium`

## Repository Layout

```text
.github/workflows/validate-starters.yml      Daily starter validation
.github/workflows/build-github-snapshot.yml  Daily GitHub snapshot refresh
skills/nimdalcraft/                          Main skill package
  SKILL.md                                   Skill instructions
  run.py                                     Main CLI entrypoint
  scripts/build_github_snapshot.py           GitHub snapshot builder
  scripts/source_search.py                   Deterministic retrieval layer
  scripts/validate_starters.py               Validated starter checker
  references/                                Agent and state docs
  assets/                                    Snapshots and starter metadata
```

## Quick Start

From the repo root:

```powershell
npx nimdalcraft setup
npx nimdalcraft "Client portal SaaS with auth file uploads and email jobs" --search-mode degraded --result-mode safe
```

Output is created under:

```text
nimdalcraft-output/<idea-slug>-<timestamp>/
```

Main files to open first:

- `STARTER_README.md`
- `DECISION_LOG.md`
- `NEXT_ACTION.md`
- `prompts/codex.md`

If you want Nimdalcraft to generate adaptation modules for an existing project:

```powershell
npx nimdalcraft run "JWT auth with uploads" --target-project C:\path\to\your-app --apply-adaptations
```

## Install And Use

After publishing the package, the intended UX is:

```powershell
npx nimdalcraft setup
npx nimdalcraft "B2B client portal SaaS with auth and uploads"
npx nimdalcraft doctor
```

Before publish, use local development commands:

```powershell
node bin\nimdalcraft.js init
node bin\nimdalcraft.js "B2B client portal SaaS"
node bin\nimdalcraft.js validate --all --update-status
```

Or link it locally:

```powershell
npm link
nimdalcraft doctor
nimdalcraft "B2B client portal SaaS"
```

## Recommended Usage Modes

Safe planning mode:

```powershell
npx nimdalcraft run "Internal admin SaaS for sales ops" --search-mode degraded --result-mode safe
```

Explore mode:

```powershell
npx nimdalcraft run "Client portal SaaS" --search-mode degraded --result-mode explore
```

Runnable mode:

```powershell
npx nimdalcraft run "Starter-driven SaaS" --output-mode runnable --search-mode degraded
```

Strict mode:

```powershell
$env:GITHUB_TOKEN="your-token"
npx nimdalcraft run "Client portal SaaS" --search-mode strict
```

## External Adapter Configuration

Environment variables:

- `GITHUB_TOKEN` for strict GitHub search
- `SOURCEGRAPH_TOKEN` for Sourcegraph GraphQL search
- `SOURCEGRAPH_ENDPOINT` to override the Sourcegraph GraphQL URL
- `GREP_APP_ENDPOINT` to enable the best-effort grep.app adapter

Built-in public adapters:

- `searchcode` via `https://searchcode.com/api`
- `OSS Insight` via `https://api.ossinsight.io/v1`
- `deps.dev` via `https://api.deps.dev/v3`

## Fast Test Guide

Validate the code:

```powershell
python -m py_compile skills\nimdalcraft\run.py skills\nimdalcraft\scripts\source_search.py skills\nimdalcraft\scripts\validate_starters.py
```

Refresh the validated starter set:

```powershell
npx nimdalcraft validate --all --update-status
```

Test a successful runnable path:

```powershell
npx nimdalcraft run "Simple internal SaaS" --force-starter local-test-starter --output-mode runnable
```

Expected:

- `OUTCOME_STATUS: success`
- `RUNNABLE_STATUS: pass`
- `FINAL_RESULT: usable`

## Where To Read Next

- [skills/nimdalcraft/SKILL.md](./skills/nimdalcraft/SKILL.md)
- [skills/nimdalcraft/run.py](./skills/nimdalcraft/run.py)
- [skills/nimdalcraft/scripts/source_search.py](./skills/nimdalcraft/scripts/source_search.py)
- [skills/nimdalcraft/scripts/validate_starters.py](./skills/nimdalcraft/scripts/validate_starters.py)
