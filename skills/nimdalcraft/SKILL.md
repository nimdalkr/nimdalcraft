---
name: nimdalcraft
description: Turn a vague product or feature idea into a feature-level open-source code retrieval and reconstruction plan by extracting concrete features, searching for reusable code patterns, filtering by activity and credibility, and producing an adaptation plan for the user's project. Use when Codex needs to find real OSS implementations and reshape them into the user's codebase rather than just recommending repos.
---

# Nimdalcraft

## Overview

Use this skill to transform a rough product idea into a code retrieval and reconstruction package.
Default behavior is not `query -> repo recommendation`.
Default behavior is:

`query -> feature extraction -> code retrieval -> semantic rerank -> activity and credibility filtering -> project-aware reconstruction`

Favor practical, transplantable implementation patterns over repo branding.
Keep the normal coding flow intact: the user should already be inside Codex or another coding agent session when this skill is used.

## Quick Start

Preferred usage is inside Codex:

```text
Use $nimdalcraft to find OSS code patterns for JWT auth and adapt them to this app
```

Use the CLI for one-time setup, validation, or standalone runs instead of hand-authoring `state.json`.

```bash
python run.py --idea "Client portal SaaS with auth and file uploads" --search-mode degraded
```

Recommended production-style usage with GitHub token:

```bash
$env:GITHUB_TOKEN="your-token"
python run.py --idea "Client portal SaaS with auth and file uploads" --search-mode strict
```

The CLI creates a timestamped folder under `work/` containing:

- `state.json`
- `00-input.json` through `05-reconstruction-plan.json`
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
- `--target-project <path>`
- `--apply-adaptations`

Runtime adapter configuration:

- `GITHUB_TOKEN` for strict GitHub search
- `SOURCEGRAPH_TOKEN` for Sourcegraph GraphQL search
- `SOURCEGRAPH_ENDPOINT` to override the Sourcegraph URL
- `GREP_APP_ENDPOINT` to enable the best-effort grep.app adapter

## Workflow

Run the phases in this order:

1. Build `state.input` from the user's idea, explicit constraints, and any conservative assumptions that must be carried forward.
2. Extract concrete feature units into `state.feature_map`.
3. Run `spec_agent` using [references/spec-agent.md](./references/spec-agent.md). Write JSON output to `state.spec`.
4. Run `architect_agent` using [references/architect-agent.md](./references/architect-agent.md). Write JSON output to `state.architecture`.
5. Build `state.search_map` so each feature has symbol hints, snippet queries, semantic queries, and adaptation targets.
6. Run the deterministic source search layer to fill `state.raw_candidates` and `state.code_candidates`.
7. Run `curation_agent` using [references/curation-agent.md](./references/curation-agent.md). Write JSON output to `state.curated_choices`.
8. Run `scaffold_agent` using [references/scaffold-agent.md](./references/scaffold-agent.md). Write JSON output to `state.reconstruction_plan`.
9. Use `mapper_agent` and `validator_agent` when the user asks for deeper coverage, stricter compatibility review, or tighter reconstruction guidance.

## State Contract

Read [references/state-schema.json](./references/state-schema.json) before mutating state.
Always preserve these top-level keys:

- `input`
- `spec`
- `architecture`
- `feature_map`
- `search_map`
- `raw_candidates`
- `code_candidates`
- `curated_choices`
- `validation_result`
- `reconstruction_plan`
- `starter_plan`

Unknowns belong in explicit assumption fields, not hidden model guesses.

## Retrieval Layer

Use [references/source-search.md](./references/source-search.md), `run.py`, and `scripts/source_search.py`.
Do not let the model invent latest repository or package data from memory when the search script can fetch it.

The retrieval layer is responsible for:

- repository and package discovery from `github`, `npm`, and `pypi`
- code-search evidence slots for `sourcegraph`, `grep_app`, and `searchcode`
- semantic rerank and chunk-style evidence scoring via `code_rag`
- activity scoring inspired by `OSS Insight`
- credibility scoring inspired by `deps.dev`
- project-aware adaptation hints inspired by `Continue` and `Codeium`
- retry and cache fallback
- deterministic hard filters and soft scoring
- normalized candidate payloads for LLM review

The model is responsible for:

- product specification
- architecture framing
- selecting among candidates
- explaining tradeoffs
- producing the final adaptation or runnable handoff package

## Agent References

Load only the phase reference you need:

- [references/spec-agent.md](./references/spec-agent.md)
- [references/architect-agent.md](./references/architect-agent.md)
- [references/curation-agent.md](./references/curation-agent.md)
- [references/scaffold-agent.md](./references/scaffold-agent.md)
- [references/mapper-agent.md](./references/mapper-agent.md)
- [references/validator-agent.md](./references/validator-agent.md)

## Guardrails

- Output JSON for each phase.
- Keep phase boundaries strict. `spec_agent` must not choose stacks. `curation_agent` must not perform search.
- Penalize archived, stale, demo-only, unmaintained, or overly opinionated sources.
- Penalize code that is hard to transplant into an existing project.
- Prefer function, symbol, and module-level reuse over full repo adoption.
- Prefer an implementation pattern that can be adapted quickly over an abstractly "best" stack.
- Keep assumptions visible and conservative.
- In `safe` mode, show one chosen path only.
- In `explore` mode, expose alternatives and comparisons.
- In `runnable` mode, prefer `status=verified`; fall back to `status=flaky` only when no verified starter matches.
- Emit `OUTCOME_STATUS`, `FAILURE_MODE`, `RUNNABLE_STATUS`, and `FINAL_RESULT` in the final output package.

## Final Deliverable

End with a package the user can immediately use:

- structured product spec
- MVP architecture
- extracted feature map
- curated OSS choices with code evidence, alternatives, and rejection reasons
- reconstruction plan and setup order
- handoff prompts for Codex, Claude Code, and Cursor
