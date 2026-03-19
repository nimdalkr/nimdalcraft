# Nimdalcraft

`nimdalcraft` is a prompt-infrastructure skill for beginner-friendly vibe coding.

It turns:

`idea -> structured spec -> MVP architecture -> OSS candidates -> curated starter package -> runnable or handoff output`

The current implementation lives in [skills/saas-oss-accelerator](./skills/saas-oss-accelerator).

## What It Does

- Takes a vague product idea and normalizes it into a SaaS-friendly build plan
- Searches GitHub, npm, and PyPI for practical OSS building blocks
- Applies deterministic filters and scoring before any model judgment
- Produces human-readable outputs such as `STARTER_README.md`, `DECISION_LOG.md`, `NEXT_ACTION.md`
- Supports `runnable` mode with a validated starter set
- Tracks starter reliability with `verified`, `flaky`, `broken` states

## Repository Layout

```text
.github/workflows/validate-starters.yml   Daily starter validation
skills/saas-oss-accelerator/              Main skill package
  SKILL.md                                Skill instructions
  run.py                                  Main CLI entrypoint
  scripts/source_search.py                Deterministic search layer
  scripts/validate_starters.py            Validated starter checker
  assets/trusted-starters.json            Validated starter set
  references/                             Agent/reference docs
```

## Quick Start

From the repo root:

```powershell
python skills\saas-oss-accelerator\run.py --idea "Client portal SaaS with auth file uploads and email jobs" --search-mode degraded --result-mode safe
```

Output is created under:

```text
skills/saas-oss-accelerator/work/<idea-slug>-<timestamp>/
```

Main files to open first:

- `STARTER_README.md`
- `DECISION_LOG.md`
- `NEXT_ACTION.md`
- `prompts/codex.md`

## Recommended Usage Modes

### 1. Safe planning mode

Use this as the default path.

```powershell
python skills\saas-oss-accelerator\run.py --idea "Internal admin SaaS for sales ops" --search-mode degraded --result-mode safe
```

### 2. Explore mode

Use this when you want alternatives.

```powershell
python skills\saas-oss-accelerator\run.py --idea "Client portal SaaS" --search-mode degraded --result-mode explore
```

### 3. Runnable mode

Use this when you want a validated starter path.

```powershell
python skills\saas-oss-accelerator\run.py --idea "Starter-driven SaaS" --output-mode runnable --search-mode degraded
```

### 4. Strict mode

Use this only when `GITHUB_TOKEN` is available.

```powershell
$env:GITHUB_TOKEN="your-token"
python skills\saas-oss-accelerator\run.py --idea "Client portal SaaS" --search-mode strict
```

## Fast Test Guide

### Validate the code

```powershell
python -m py_compile skills\saas-oss-accelerator\run.py skills\saas-oss-accelerator\scripts\validate_starters.py
```

### Refresh the validated starter set

```powershell
python skills\saas-oss-accelerator\scripts\validate_starters.py --all --update-status --work-dir skills\saas-oss-accelerator\work\starter-validation-manual
```

### Test a successful runnable path

```powershell
python skills\saas-oss-accelerator\run.py --idea "Simple internal SaaS" --force-starter local-test-starter --output-mode runnable --output-dir skills\saas-oss-accelerator\work\manual-success
```

Expected:

- `OUTCOME_STATUS: success`
- `RUNNABLE_STATUS: pass`
- `FINAL_RESULT: usable`

### Test the flaky fallback path

```powershell
python skills\saas-oss-accelerator\run.py --idea "Simple internal SaaS" --force-starter local-flaky-starter --output-mode runnable --output-dir skills\saas-oss-accelerator\work\manual-flaky
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

- `skills/saas-oss-accelerator/assets/trusted-starters.json`

## Where To Read Next

- [skills/saas-oss-accelerator/SKILL.md](./skills/saas-oss-accelerator/SKILL.md)
- [skills/saas-oss-accelerator/run.py](./skills/saas-oss-accelerator/run.py)
- [skills/saas-oss-accelerator/scripts/validate_starters.py](./skills/saas-oss-accelerator/scripts/validate_starters.py)
