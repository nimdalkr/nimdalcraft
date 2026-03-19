# Nimdalcraft

`nimdalcraft` is a prompt-infrastructure skill for beginner-friendly vibe coding.

It turns:

`idea -> structured spec -> MVP architecture -> OSS candidates -> curated starter package -> runnable or handoff output`

The current implementation lives in [skills/nimdalcraft](./skills/nimdalcraft).

## What It Does

- Takes a vague product idea and normalizes it into a SaaS-friendly build plan
- Searches GitHub, npm, and PyPI for practical OSS building blocks
- Applies deterministic filters and scoring before any model judgment
- Produces human-readable outputs such as `STARTER_README.md`, `DECISION_LOG.md`, `NEXT_ACTION.md`
- Supports `runnable` mode with a validated starter set
- Tracks starter reliability with `verified`, `flaky`, `broken` states
- Uses a CI-built GitHub search snapshot so `degraded` mode is not dependent on unauthenticated live GitHub search

## Repository Layout

```text
.github/workflows/validate-starters.yml   Daily starter validation
.github/workflows/build-github-snapshot.yml Daily GitHub snapshot refresh
skills/nimdalcraft/                       Main skill package
  SKILL.md                                Skill instructions
  run.py                                  Main CLI entrypoint
  scripts/build_github_snapshot.py        GitHub snapshot builder
  scripts/source_search.py                Deterministic search layer
  scripts/validate_starters.py            Validated starter checker
  assets/github-search-snapshots.json     Daily GitHub query snapshot
  assets/github-snapshot-queries.json     Snapshot query set
  assets/trusted-starters.json            Validated starter set
  references/                             Agent/reference docs
```

## Recommended Flow

Keep the normal vibe-coding flow:

1. Open Codex or Claude Code first
2. Use `$nimdalcraft` in Codex or `/nimdalcraft` in Claude Code
3. Use the CLI only for one-time setup or maintenance

Inside Codex:

```text
$nimdalcraft B2B client portal SaaS with auth and uploads
```

Inside Claude Code:

```text
/nimdalcraft B2B client portal SaaS with auth and uploads
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

## Install And Use

### 1. npm / npx

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

### 2. Codex skill install

`init` installs the bundled Codex skill and Claude Code slash command into your home directories.

Direct command:

```powershell
npx nimdalcraft install codex
npx nimdalcraft install claude
npx nimdalcraft install all
```

Then inside Codex you can use:

```text
$nimdalcraft your idea
```

Inside Claude Code you can use:

```text
/nimdalcraft your idea
```

## Recommended Usage Modes

### 1. Safe planning mode

Use this as the default path.

```powershell
npx nimdalcraft run "Internal admin SaaS for sales ops" --search-mode degraded --result-mode safe
```

### 2. Explore mode

Use this when you want alternatives.

```powershell
npx nimdalcraft run "Client portal SaaS" --search-mode degraded --result-mode explore
```

### 3. Runnable mode

Use this when you want a validated starter path.

```powershell
npx nimdalcraft run "Starter-driven SaaS" --output-mode runnable --search-mode degraded
```

### 4. Strict mode

Use this only when `GITHUB_TOKEN` is available.

```powershell
$env:GITHUB_TOKEN="your-token"
npx nimdalcraft run "Client portal SaaS" --search-mode strict
```

### 5. Degraded snapshot-first mode

This is the default no-token path. It uses the daily GitHub snapshot first, then falls back to live GitHub or cache only when needed.

```powershell
npx nimdalcraft run "Client portal SaaS" --search-mode degraded
```

## Fast Test Guide

### Validate the code

```powershell
python -m py_compile skills\nimdalcraft\run.py skills\nimdalcraft\scripts\validate_starters.py
```

### Refresh the validated starter set

```powershell
npx nimdalcraft validate --all --update-status
```

### Test a successful runnable path

```powershell
npx nimdalcraft run "Simple internal SaaS" --force-starter local-test-starter --output-mode runnable
```

Expected:

- `OUTCOME_STATUS: success`
- `RUNNABLE_STATUS: pass`
- `FINAL_RESULT: usable`

### Test the flaky fallback path

```powershell
npx nimdalcraft run "Simple internal SaaS" --force-starter local-flaky-starter --output-mode runnable
```

Expected:

- `OUTCOME_STATUS: partial_success`
- `RUNNABLE_STATUS: pass`
- `FINAL_RESULT: unstable`

## Validated Starter Policy

Trusted starters are not a static whitelist. They are a validated set.

Status transitions:

- `verified -> flaky` after 1 failed validation
- `flaky -> broken` after 3 consecutive failures
- `broken -> verified` after 2 consecutive successes

Minimum coverage target:

- `verified >= 3`
- `flaky >= 1`

If usable starter coverage drops too low, the tool emits:

- `FAILURE_MODE=low_coverage`

## Output Contract

Each run reports:

- `SEARCH_MODE`
- `SEARCH_QUALITY`
- `DATA_FRESHNESS`
- `OUTCOME_STATUS`
- `FAILURE_MODE`
- `RUNNABLE_STATUS`
- `FINAL_RESULT`
- validated set summary counts

This makes debugging and trust analysis explicit instead of hidden.

## Automation

Daily starter validation runs through:

- [.github/workflows/validate-starters.yml](./.github/workflows/validate-starters.yml)

It refreshes:

- `skills/nimdalcraft/assets/trusted-starters.json`

Daily GitHub snapshot refresh runs through:

- [.github/workflows/build-github-snapshot.yml](./.github/workflows/build-github-snapshot.yml)

It refreshes:

- `skills/nimdalcraft/assets/github-search-snapshots.json`

## Where To Read Next

- [skills/nimdalcraft/SKILL.md](./skills/nimdalcraft/SKILL.md)
- [skills/nimdalcraft/run.py](./skills/nimdalcraft/run.py)
- [skills/nimdalcraft/scripts/validate_starters.py](./skills/nimdalcraft/scripts/validate_starters.py)
