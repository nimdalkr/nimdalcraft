---
name: nimdalcraft
description: Turn a vague SaaS or app idea into a structured, beginner-friendly open-source implementation package by extracting a product spec, proposing an MVP architecture, searching GitHub, npm, and PyPI for realistic building blocks, curating compatible options, and producing runnable or handoff outputs. Use when Codex needs to convert an idea, feature brief, or product note into a startable stack and implementation kickoff plan rather than generic brainstorming.
---

# Nimdalcraft

## Overview

Use this skill to transform a rough product idea into a startable SaaS build package.
Favor practical MVP combinations for beginners over fashionable stacks or maximal flexibility.
Keep the normal coding flow intact: the user should already be inside Codex or another coding agent session when this skill is used.

## Quick Start

Preferred usage is inside Codex:

```text
Use $nimdalcraft to turn this idea into a runnable starter package: client portal SaaS with auth and uploads
```

Use the CLI for one-time setup, validation, or standalone runs instead of hand-authoring `state.json`.

```bash
python run.py --idea "AI-assisted invoicing SaaS for freelancers" --search-mode degraded
```

Recommended production-style usage with GitHub token:

```bash
$env:GITHUB_TOKEN="your-token"
python run.py --idea "Client portal SaaS with auth and file uploads" --search-mode strict
```

The CLI creates a timestamped folder under `work/` containing:

- `state.json`
- `00-input.json` through `05-starter-plan.json`
- `STARTER_README.md`
- `DECISION_LOG.md`
- `RECOVERY_ACTION.md`
- `NEXT_ACTION.md`
- `prompts/codex.md`
- `prompts/claude-code.md`
- `prompts/cursor.md`

Key modes:

- `--search-mode strict|degraded|offline`
- `--result-mode safe|explore`
- `--output-mode plan|runnable`
- `--force-starter <trusted-id>`

Trusted starters are a validated set, not a raw whitelist.
Refresh them with:

```bash
python scripts/validate_starters.py --starter local-test-starter
python scripts/validate_starters.py --all --update-status
```

Validated starters keep `validation_history` and state transitions:

- `verified -> flaky` after 1 failed validation
- `flaky -> broken` after 3 consecutive failures
- `broken -> verified` after 2 consecutive successes

Keep minimum coverage in the validated set:

- `verified >= 3`
- `flaky >= 1`
- keep broken entries visible for exclusion and debugging

## Workflow

Run the phases in this order:

1. Build `state.input` from the user's idea, explicit constraints, and any conservative assumptions that must be carried forward.
2. Run `spec_agent` using [references/spec-agent.md](./references/spec-agent.md). Write JSON output to `state.spec`.
3. Run `architect_agent` using [references/architect-agent.md](./references/architect-agent.md). Write JSON output to `state.architecture`.
4. Run the deterministic source search layer to fill `state.search_map` and `state.raw_candidates`.
5. Run `curation_agent` using [references/curation-agent.md](./references/curation-agent.md). Write JSON output to `state.curated_choices`.
6. Run `scaffold_agent` using [references/scaffold-agent.md](./references/scaffold-agent.md). Write JSON output to `state.starter_plan`.
7. Use `mapper_agent` and `validator_agent` only when the user asks for deeper search coverage or stricter compatibility review.

## State Contract

Read [references/state-schema.json](./references/state-schema.json) before mutating state.
Always preserve these top-level keys:

- `input`
- `spec`
- `architecture`
- `search_map`
- `raw_candidates`
- `curated_choices`
- `validation_result`
- `starter_plan`

Unknowns belong in explicit assumption fields, not hidden model guesses.

## Deterministic Search Layer

Use [references/source-search.md](./references/source-search.md), `run.py`, and `scripts/source_search.py`.
Do not let the model invent latest package or repository data from memory when the search script can fetch it.

Recommended commands:

```bash
python run.py --idea "Internal admin SaaS for sales ops" --search-mode degraded --result-mode safe
python run.py --idea "Client portal SaaS" --search-mode strict --result-mode explore
python run.py --idea "Starter-driven SaaS" --search-mode strict --output-mode runnable
```

The search layer is responsible for:

- GitHub repository search
- npm package search
- PyPI package search
- freshness and maintenance signals
- retry and cache fallback
- deterministic hard filters and soft scoring
- search quality and data freshness contracts
- normalized candidate payloads for LLM review

The model is responsible for:

- product specification
- architecture framing
- selecting among candidates
- explaining tradeoffs
- producing the final handoff package

## Agent References

Load only the phase reference you need:

- [references/spec-agent.md](./references/spec-agent.md)
- [references/architect-agent.md](./references/architect-agent.md)
- [references/curation-agent.md](./references/curation-agent.md)
- [references/scaffold-agent.md](./references/scaffold-agent.md)
- [references/mapper-agent.md](./references/mapper-agent.md)
- [references/validator-agent.md](./references/validator-agent.md)

`mapper_agent` and `validator_agent` are defined now but are not part of the default path.

## Guardrails

- Output JSON for each phase.
- Keep phase boundaries strict. `spec_agent` must not choose stacks. `curation_agent` must not perform search.
- Penalize complexity when the user is a beginner, solo builder, or asking for the fastest path to first working code.
- Penalize archived, stale, demo-only, unmaintained, or overly opinionated starters.
- Prefer a startable combination over an abstractly "best" stack.
- Keep assumptions visible and conservative.
- In `safe` mode, show one chosen stack only.
- In `explore` mode, expose alternatives and comparisons.
- In `runnable` mode, prefer `status=verified`; fall back to `status=flaky` only when no verified starter matches.
- If usable starter coverage drops below the minimum threshold, emit `FAILURE_MODE=low_coverage`.
- Emit `OUTCOME_STATUS`, `FAILURE_MODE`, `RUNNABLE_STATUS`, and `FINAL_RESULT` in the final output package.

## Final Deliverable

End with a package the user can immediately use:

- structured product spec
- MVP SaaS architecture
- curated OSS choices with alternatives and rejection reasons
- starter project structure and setup order
- handoff prompts for Codex, Claude Code, and Cursor
