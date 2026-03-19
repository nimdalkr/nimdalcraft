#!/usr/bin/env python3
"""User-facing CLI entrypoint for the SaaS OSS Accelerator skill."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "scripts"))

from pipeline_state import ensure_state, save_state  # noqa: E402
from source_search import run_search  # noqa: E402


FEATURE_KEYWORDS = {
    "Authentication": {"auth", "login", "signup", "user", "users", "account", "accounts", "team", "member"},
    "File Uploads": {"file", "files", "upload", "uploads", "image", "images", "document", "documents"},
    "Background Jobs": {"queue", "queues", "job", "jobs", "email", "emails", "notification", "notifications", "import", "export", "webhook", "report", "reports", "async"},
    "AI Features": {"ai", "llm", "chatbot", "assistant", "embedding", "rag", "agent", "agents"},
    "Admin Dashboard": {"admin", "dashboard", "internal", "ops"},
}
FAILURE_ORDER = ["no_candidates", "low_coverage", "runnable_failed", "low_confidence", "degraded_search"]
TRUSTED_STARTERS_PATH = SCRIPT_DIR / "assets" / "trusted-starters.json"
SUCCESS_CONFIDENCE_LEVELS = {"high"}
MIN_USABLE_STARTERS = 3


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "saas-idea"


def build_output_dir(base_dir: Path, idea: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return base_dir / f"{slugify(idea)}-{stamp}"


def idea_keywords(idea: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", idea.lower()))


def product_name_from_idea(idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", idea)
    if not words:
        return "New SaaS Product"
    return " ".join(words[:4]).title()


def infer_target_user(idea: str) -> str:
    match = re.search(r"\bfor\s+([a-z0-9 ,/-]+)", idea, re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(".")
    return "general business users"


def infer_features(tokens: set[str]) -> list[str]:
    features = [name for name, keywords in FEATURE_KEYWORDS.items() if tokens & keywords]
    if "Admin Dashboard" not in features:
        features.insert(0, "Admin Dashboard")
    return features


def build_search_map(idea: str, beginner: bool) -> list[dict[str, Any]]:
    tokens = idea_keywords(idea)
    search_map = [
        {
            "component": "Next.js SaaS starter",
            "purpose": "frontend application shell",
            "source_types": ["github", "npm"],
            "query_variants": ["Next.js SaaS starter", "Next.js starter template auth dashboard", "React admin dashboard starter"],
            "selection_criteria": ["fast beginner setup", "good auth integration surface", "strong starter-template fit"],
        },
        {
            "component": "FastAPI backend starter",
            "purpose": "backend API foundation",
            "source_types": ["github", "pypi"],
            "query_variants": ["FastAPI starter", "FastAPI boilerplate", "FastAPI SaaS template"],
            "selection_criteria": ["clear docs", "lightweight MVP structure", "beginner-friendly Python backend"],
        },
        {
            "component": "PostgreSQL ORM toolkit",
            "purpose": "database access layer",
            "source_types": ["github", "npm", "pypi"],
            "query_variants": ["PostgreSQL ORM starter", "Prisma starter", "SQLModel starter"],
            "selection_criteria": ["migrations support", "simple local setup", "works well for CRUD-heavy SaaS"],
        },
    ]
    if tokens & FEATURE_KEYWORDS["Authentication"]:
        search_map.append(
            {
                "component": "Authentication solution",
                "purpose": "sign-in, session, and user management",
                "source_types": ["github", "npm", "pypi"],
                "query_variants": ["NextAuth starter", "better-auth starter", "FastAPI auth starter"],
                "selection_criteria": ["simple local auth", "clear session model", "beginner-friendly docs"],
            }
        )
    if tokens & FEATURE_KEYWORDS["File Uploads"]:
        search_map.append(
            {
                "component": "File storage adapter",
                "purpose": "file upload and object storage integration",
                "source_types": ["github", "npm", "pypi"],
                "query_variants": ["S3 upload starter", "uploadthing starter", "FastAPI file upload storage"],
                "selection_criteria": ["simple upload flow", "good starter examples", "minimal cloud lock-in for MVP"],
            }
        )
    if tokens & FEATURE_KEYWORDS["Background Jobs"]:
        search_map.append(
            {
                "component": "Background job worker",
                "purpose": "async tasks and queue processing",
                "source_types": ["github", "npm", "pypi"],
                "query_variants": ["Celery starter", "RQ starter", "BullMQ starter"],
                "selection_criteria": ["easy local development", "clear queue semantics", "not too heavy for MVP"],
            }
        )
    if tokens & FEATURE_KEYWORDS["AI Features"]:
        search_map.append(
            {
                "component": "LLM application toolkit",
                "purpose": "AI or agent integration layer",
                "source_types": ["github", "npm", "pypi"],
                "query_variants": ["OpenAI SDK starter", "LangChain starter", "FastAPI OpenAI template"],
                "selection_criteria": ["simple request flow", "good quickstart docs", "minimal framework lock-in"],
            }
        )
    if beginner:
        for entry in search_map:
            entry["selection_criteria"].append("prefer lower setup complexity")
    return search_map


def build_spec(idea: str, beginner: bool) -> dict[str, Any]:
    tokens = idea_keywords(idea)
    assumptions = ["Default architecture is web-based SaaS unless later refined."]
    if beginner:
        assumptions.append("Complexity is penalized because the target user is a beginner.")
    features = infer_features(tokens)
    return {
        "product_name": product_name_from_idea(idea),
        "summary": idea,
        "target_user": infer_target_user(idea),
        "core_jobs": ["Capture the main workflow described by the idea.", "Deliver the first usable web-based MVP flow."],
        "core_features": features,
        "must_have_constraints": ["Use OSS components that are practical for a first MVP."],
        "nice_to_have_constraints": ["Keep architecture explainable for a beginner builder."],
        "input_assumptions": assumptions,
        "success_criteria": ["A builder can choose one stack and begin implementation immediately."],
    }


def build_architecture(spec: dict[str, Any], search_map: list[dict[str, Any]]) -> dict[str, Any]:
    feature_names = set(spec.get("core_features") or [])
    return {
        "architecture": {
            "app_type": "web SaaS",
            "frontend": {"role": "web app shell and dashboard", "recommended_stack": "Next.js"},
            "backend": {"role": "API and business logic", "recommended_stack": "FastAPI"},
            "worker": {"needed": "Background Jobs" in feature_names, "recommended_stack": "Celery or RQ"},
            "database": {"recommended_stack": "PostgreSQL"},
            "auth": {"needed": "Authentication" in feature_names, "recommended_stack": "NextAuth or FastAPI auth starter"},
            "storage": {"needed": "File Uploads" in feature_names, "recommended_stack": "S3-compatible adapter"},
            "deployment": {"recommended_stack": "simple app hosting with managed Postgres"},
        },
        "mvp_boundaries": ["Prefer one web app, one API, and one database for the first release."],
        "tradeoffs": ["Favor simpler local setup over maximum extensibility."],
        "component_search_targets": [entry["component"] for entry in search_map],
    }


def build_initial_state(idea: str, beginner: bool, search_map: list[dict[str, Any]], search_mode: str, result_mode: str, output_mode: str) -> dict[str, Any]:
    spec = build_spec(idea, beginner)
    architecture = build_architecture(spec, search_map)
    return ensure_state(
        {
            "input": {"idea": idea, "beginner_mode": beginner},
            "spec": spec,
            "architecture": architecture,
            "search_map": search_map,
            "execution": {
                "search_mode": search_mode,
                "result_mode": result_mode,
                "output_mode": output_mode,
                "forced_mode": False,
                "forced_starter": "",
                "failure_mode": "",
                "failure_modes": [],
                "outcome_status": "",
                "search_quality": "",
                "data_freshness": "",
                "runnable_status": "",
                "final_result": "",
            },
            "reports": {},
        }
    )


def group_by_component(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(str(item.get("component") or "Unknown"), []).append(item)
    for items in grouped.values():
        items.sort(key=lambda candidate: (-float(candidate.get("overall_score") or 0.0), str(candidate.get("name") or "")))
    return grouped


def candidate_lookup(candidates: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(item.get("component") or ""), str(item.get("name") or "")): item for item in candidates}


def short_reason(candidate: dict[str, Any]) -> str:
    days = candidate.get("last_update")
    if candidate.get("scores", {}).get("maintenance", 0) >= 0.7 and days:
        age = candidate.get("last_update") or ""
        return f"maintained ({age[:10] or 'recent'})"
    if candidate.get("setup_difficulty") == "low":
        return "beginner-friendly (low setup)"
    hits = candidate.get("relevance_hits") or []
    if hits:
        return f"query match ({', '.join(hits[:2])})"
    return f"deterministic score {candidate.get('overall_score')}"


def curate_candidates(candidates: list[dict[str, Any]], result_mode: str) -> list[dict[str, Any]]:
    grouped = group_by_component(candidates)
    curated = []
    for component, items in grouped.items():
        selected = items[0]
        entry = {
            "component": component,
            "selected": {
                "name": selected.get("name"),
                "source_type": selected.get("source_type"),
                "url": selected.get("url"),
                "confidence": selected.get("confidence"),
                "score": selected.get("overall_score"),
                "why_selected": [short_reason(selected), f"overall score {selected.get('overall_score')}"],
                "risks": (selected.get("complexity_signals") or []) + (selected.get("maintenance_flags") or []),
            },
            "alternatives": [],
            "rejected_patterns": [
                "archived repository",
                "demo-only project",
                "weak maintenance signal",
                "too much setup complexity for beginner MVP",
            ],
        }
        if result_mode == "explore":
            entry["alternatives"] = [
                {
                    "name": alt.get("name"),
                    "source_type": alt.get("source_type"),
                    "url": alt.get("url"),
                    "why_not_selected": [f"ranked below the primary choice ({alt.get('overall_score')})"],
                }
                for alt in items[1:3]
            ]
        curated.append(entry)
    return curated


def load_trusted_starters() -> list[dict[str, Any]]:
    if not TRUSTED_STARTERS_PATH.exists():
        return []
    return json.loads(TRUSTED_STARTERS_PATH.read_text(encoding="utf-8"))


def validation_set_summary(trusted_starters: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"verified": 0, "flaky": 0, "broken": 0}
    last_run = ""
    for starter in trusted_starters:
        status = str(starter.get("status") or "").casefold()
        if status in counts:
            counts[status] += 1
        checked = str((starter.get("last_validation") or {}).get("checked_at") or "")
        if checked and checked > last_run:
            last_run = checked
    usable = counts["verified"] + counts["flaky"]
    return {
        "total": len(trusted_starters),
        "usable": usable,
        "verified": counts["verified"],
        "flaky": counts["flaky"],
        "broken": counts["broken"],
        "last_validation_run": last_run,
        "minimum_usable_target": MIN_USABLE_STARTERS,
    }


def starter_matches(starter: dict[str, Any], value: str) -> bool:
    needle = value.strip().casefold()
    candidates = [
        str(starter.get("id") or ""),
        str(starter.get("label") or ""),
        str(starter.get("repo") or ""),
    ]
    return any(needle == item.casefold() for item in candidates if item)


def resolve_starter_repo(repo: str) -> Path | str:
    if repo.startswith("./") or repo.startswith(".\\"):
        return (SCRIPT_DIR / repo[2:]).resolve()
    if repo.startswith("../") or repo.startswith("..\\"):
        return (SCRIPT_DIR / repo).resolve()
    path = Path(repo)
    if path.exists():
        return path.resolve()
    return repo


def trusted_candidate_urls(trusted_starters: list[dict[str, Any]], allowed_statuses: set[str]) -> set[str]:
    return {
        str(item.get("repo") or "")
        for item in trusted_starters
        if str(item.get("status") or "").casefold() in allowed_statuses
    }


def filter_candidates_for_runnable(candidates: list[dict[str, Any]], trusted_starters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    verified = trusted_candidate_urls(trusted_starters, {"verified"})
    verified_candidates = [item for item in candidates if str(item.get("url") or "") in verified]
    if verified_candidates:
        return verified_candidates, "verified"
    flaky = trusted_candidate_urls(trusted_starters, {"flaky"})
    flaky_candidates = [item for item in candidates if str(item.get("url") or "") in flaky]
    if flaky_candidates:
        return flaky_candidates, "flaky"
    return [], "none"


def build_forced_candidate(starter: dict[str, Any]) -> dict[str, Any]:
    return {
        "component": "Trusted runnable starter",
        "purpose": "validated runnable starter",
        "query": "forced-starter",
        "source_type": "github" if str(starter.get("repo") or "").startswith("http") else "local",
        "name": starter.get("label") or starter.get("id") or "forced-starter",
        "url": starter.get("repo") or "",
        "description": "Forced trusted starter selection.",
        "latest_version": "",
        "license": "",
        "last_update": starter.get("last_verified_at") or "",
        "maintenance_flags": [],
        "beginner_fit_signals": ["trusted-whitelist"],
        "selection_hints": ["forced starter path"],
        "raw_signals": {},
        "complexity_signals": [],
        "demo_flags": [],
        "scores": {
            "recency": 1.0,
            "maintenance": 1.0,
            "popularity": 1.0,
            "beginner": 1.0,
            "relevance": 1.0,
        },
        "overall_score": 100.0,
        "confidence": "high",
        "setup_difficulty": "low",
        "relevance_hits": ["trusted"],
    }


def find_trusted_starter(candidates: list[dict[str, Any]], trusted_starters: list[dict[str, Any]]) -> dict[str, Any] | None:
    for allowed_statuses in ({"verified"}, {"flaky"}):
        by_url = {
            str(item.get("repo") or ""): item
            for item in trusted_starters
            if str(item.get("status") or "").casefold() in allowed_statuses
        }
        trusted_candidates = [item for item in candidates if str(item.get("url") or "") in by_url]
        if not trusted_candidates:
            continue
        trusted_candidates.sort(key=lambda item: (-float(item.get("overall_score") or 0.0), str(item.get("name") or "")))
        chosen = trusted_candidates[0]
        starter = dict(by_url[str(chosen.get("url") or "")])
        starter["candidate"] = chosen
        return starter
    return None


def build_starter_plan(state: dict[str, Any], trusted_starter: dict[str, Any] | None) -> dict[str, Any]:
    curated = state.get("curated_choices") or []
    has_worker = any("worker" in str(item.get("component") or "").lower() for item in curated)
    has_storage = any("storage" in str(item.get("component") or "").lower() for item in curated)
    project_structure = ["app/web", "app/api", "app/db", "docs", "prompts"]
    if has_worker:
        project_structure.append("app/worker")
    if has_storage:
        project_structure.append("app/storage")
    setup_steps = []
    for item in curated:
        selected = item.get("selected") or {}
        source_type = selected.get("source_type")
        if source_type == "npm":
            setup_steps.append(f"npm install {selected.get('name')}")
        elif source_type == "pypi":
            setup_steps.append(f"pip install {selected.get('name')}")
        elif source_type == "github":
            setup_steps.append(f"git clone {selected.get('url')}.git")
    integration_order = [
        "Set up the frontend shell and routing.",
        "Create the backend API skeleton and health endpoint.",
        "Add the database layer and first migration.",
    ]
    if any("Authentication" in str(item.get("component") or "") for item in curated):
        integration_order.append("Integrate auth before protected CRUD screens.")
    if has_storage:
        integration_order.append("Add upload/storage flows after core CRUD works.")
    if has_worker:
        integration_order.append("Introduce the background worker after synchronous flows are stable.")
    summary_lines = [
        f"- {item.get('component')}: {(item.get('selected') or {}).get('name')} ({(item.get('selected') or {}).get('source_type')})"
        for item in curated
    ]
    if trusted_starter:
        summary_lines.append(f"- Trusted runnable starter: {trusted_starter.get('label')} ({trusted_starter.get('repo')})")
    joined_summary = "\n".join(summary_lines)
    idea = state.get("input", {}).get("idea", "")
    return {
        "project_structure": project_structure,
        "files_to_create_first": ["README.md", "app/web/package.json", "app/api/requirements.txt", "docs/architecture.md"],
        "setup_steps": setup_steps,
        "integration_order": integration_order,
        "prompt_handoff": {
            "for_codex": f"Build the first working MVP for this idea: {idea}\nUse this chosen stack:\n{joined_summary}\nImplement the first vertical slice only.",
            "for_claude_code": f"Implement a beginner-friendly SaaS MVP for: {idea}\nUse this chosen stack:\n{joined_summary}\nKeep the first slice small and runnable.",
            "for_cursor": f"Generate the initial runnable project for: {idea}\nUse this chosen stack:\n{joined_summary}\nFocus on local setup first.",
        },
    }


def failure_modes_for_state(state: dict[str, Any], trusted_starter: dict[str, Any] | None) -> list[str]:
    modes = []
    execution = state.get("execution") or {}
    report = (state.get("reports") or {}).get("search") or {}
    validation_summary = (state.get("reports") or {}).get("validation_set") or {}
    if not (state.get("curated_choices") or []):
        modes.append("no_candidates")
    if (execution.get("output_mode") == "runnable" or execution.get("forced_mode")) and int(validation_summary.get("usable") or 0) < int(validation_summary.get("minimum_usable_target") or MIN_USABLE_STARTERS):
        modes.append("low_coverage")
    if execution.get("search_mode") != "strict" or execution.get("search_quality") != "high":
        modes.append("degraded_search")
    selected = [(item.get("selected") or {}) for item in (state.get("curated_choices") or [])]
    if selected and any(str(item.get("confidence") or "") != "high" for item in selected):
        modes.append("low_confidence")
    runnable_report = (state.get("reports") or {}).get("runnable") or {}
    if str(runnable_report.get("selected_status") or "").casefold() == "flaky" and "low_confidence" not in modes:
        modes.append("low_confidence")
    if (execution.get("output_mode") == "runnable" or execution.get("forced_mode")) and execution.get("runnable_status") == "fail":
        modes.append("runnable_failed")
    if report.get("stats", {}).get("kept", 0) == 0 and "no_candidates" not in modes:
        modes.append("no_candidates")
    ordered = [mode for mode in FAILURE_ORDER if mode in modes]
    return ordered


def primary_failure_mode(modes: list[str]) -> str:
    return modes[0] if modes else ""


def outcome_status_for_state(state: dict[str, Any]) -> str:
    execution = state.get("execution") or {}
    curated = state.get("curated_choices") or []
    selected = [(item.get("selected") or {}) for item in curated]
    hard_fail = bool(execution.get("hard_fail"))
    if hard_fail or not selected or "no_candidates" in (execution.get("failure_modes") or []):
        return "failed"
    low_confidence = any(str(item.get("confidence") or "").casefold() not in SUCCESS_CONFIDENCE_LEVELS for item in selected)
    final_result = str(execution.get("final_result") or "").casefold()
    runnable_not_usable = (execution.get("output_mode") == "runnable" or execution.get("forced_mode")) and final_result not in {"", "usable"}
    if low_confidence or runnable_not_usable or "degraded_search" in (execution.get("failure_modes") or []):
        return "partial_success"
    return "success"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_decision_log(state: dict[str, Any], result_mode: str) -> str:
    curated = state.get("curated_choices") or []
    raw_lookup = candidate_lookup(state.get("raw_candidates") or [])
    rejected = ((state.get("reports") or {}).get("search") or {}).get("rejected_candidates") or []
    lines = ["# Decision Log", ""]
    for item in curated:
        component = str(item.get("component") or "")
        selected = item.get("selected") or {}
        candidate = raw_lookup.get((component, str(selected.get("name") or "")), {})
        lines.append(f"[Component] {component}")
        lines.append(f"Selected: {selected.get('name')} ({selected.get('source_type')})")
        lines.append("Why:")
        lines.append(f"- {short_reason(candidate) if candidate else 'best surviving candidate'}")
        lines.append(f"- confidence {str(selected.get('confidence') or '').title()}")
        lines.append("Rejected:")
        rejected_lines = []
        if result_mode == "explore":
            for alt in item.get("alternatives") or []:
                reason = ((alt.get("why_not_selected") or []) or ["ranked lower"])[0]
                rejected_lines.append(f"- {alt.get('name')} -> {reason}")
        for rejected_item in rejected:
            if len(rejected_lines) >= 2:
                break
            if str(rejected_item.get("component") or "") != component:
                continue
            rejected_lines.append(f"- {rejected_item.get('name')} -> {rejected_item.get('rejection_reason')}")
        if not rejected_lines:
            rejected_lines.append("- none recorded")
        lines.extend(rejected_lines[:2])
        lines.append(f"Confidence: {str(selected.get('confidence') or 'unknown').title()}")
        lines.append("")
    runnable = (state.get("reports") or {}).get("runnable") or {}
    if runnable.get("selected_trusted_starter"):
        lines.append("[Component] Runnable Starter")
        lines.append(f"Selected: {runnable.get('label')} ({runnable.get('selected_status') or 'unknown'})")
        lines.append("Why:")
        lines.append(f"- validated set status {runnable.get('selected_status') or 'unknown'}")
        lines.append(f"- final result {runnable.get('final_result') or 'unknown'}")
        lines.append("Rejected:")
        lines.append(f"- other starters -> {runnable.get('selection_policy') or 'not selected'}")
        lines.append(f"Confidence: {str(runnable.get('confidence') or 'medium').title()}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_recovery_action(state: dict[str, Any]) -> str:
    modes = (state.get("execution") or {}).get("failure_modes") or []
    lines = ["# Recovery Action", ""]
    if not modes:
        lines.append("No recovery is required.")
        return "\n".join(lines) + "\n"
    for mode in modes:
        if mode == "degraded_search":
            lines.extend(["## degraded_search", "", "- Add `GITHUB_TOKEN` and rerun in `strict` mode.", "- If live search is not possible, rerun with `--search-mode offline` and expect stale data.", ""])
        elif mode == "no_candidates":
            lines.extend(["## no_candidates", "", "- Simplify the idea wording and rerun.", "- Switch to `--result-mode explore` to widen the search surface.", ""])
        elif mode == "low_confidence":
            lines.extend(["## low_confidence", "", "- Use `--result-mode explore` to inspect alternatives.", "- Tighten the idea or add explicit constraints before rerunning.", ""])
        elif mode == "low_coverage":
            lines.extend(["## low_coverage", "", "- The validated starter pool is too small for a reliable runnable recommendation.", "- Try `--result-mode explore` or simplify the idea and rerun.", ""])
        elif mode == "runnable_failed":
            lines.extend(["## runnable_failed", "", "- Rerun with `--output-mode plan`.", "- Or use a query that matches a trusted starter in the whitelist.", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_next_action(state: dict[str, Any]) -> str:
    execution = state.get("execution") or {}
    runnable_report = (state.get("reports") or {}).get("runnable") or {}
    failure_modes = execution.get("failure_modes") or []
    expected_output = str(runnable_report.get("expected_output") or "service starts successfully")
    lines = ["# Next Action", ""]
    outcome = execution.get("outcome_status")
    if outcome == "success" and execution.get("output_mode") == "runnable":
        lines.extend(["1. Run `project\\setup.ps1`.", "2. Open `project\\RUNBOOK.md`.", f"3. Expect: {expected_output}."])
    elif outcome == "success":
        lines.extend(["1. Open `STARTER_README.md`.", "2. Paste `prompts\\codex.md` into your coding agent.", "3. Done."])
    elif "low_coverage" in failure_modes:
        lines.extend(["1. Try `--result-mode explore`.", "2. Use a broader or simpler idea.", "3. Rerun the CLI."])
    elif outcome == "partial_success":
        lines.extend(["1. Open `RECOVERY_ACTION.md`.", "2. Fix the listed issue and rerun the CLI.", "3. Then use `prompts\\codex.md`."])
    else:
        lines.extend(["1. Open `RECOVERY_ACTION.md`.", "2. Apply the recovery step.", "3. Rerun the CLI."])
    return "\n".join(lines) + "\n"


def build_readme(state: dict[str, Any], result_mode: str, trusted_starter: dict[str, Any] | None) -> str:
    execution = state.get("execution") or {}
    validation_summary = (state.get("reports") or {}).get("validation_set") or {}
    lines = [
        "# SaaS OSS Accelerator Output",
        "",
        f"- SEARCH_MODE: `{execution.get('search_mode')}`",
        f"- SEARCH_QUALITY: `{execution.get('search_quality')}`",
        f"- DATA_FRESHNESS: `{execution.get('data_freshness')}`",
        f"- OUTCOME_STATUS: `{execution.get('outcome_status') or 'unknown'}`",
        f"- FAILURE_MODE: `{execution.get('failure_mode') or 'none'}`",
        f"- RUNNABLE_STATUS: `{execution.get('runnable_status') or 'n/a'}`",
        f"- FINAL_RESULT: `{execution.get('final_result') or 'n/a'}`",
        f"- LAST_VALIDATION_RUN: `{validation_summary.get('last_validation_run') or 'unknown'}`",
        f"- VALIDATED_STARTERS: `{validation_summary.get('total') or 0}`",
        f"- VERIFIED: `{validation_summary.get('verified') or 0}`",
        f"- FLAKY: `{validation_summary.get('flaky') or 0}`",
        f"- BROKEN: `{validation_summary.get('broken') or 0}`",
        "",
        "## Idea",
        "",
        str(state.get("input", {}).get("idea", "")),
        "",
        "## Chosen Stack",
        "",
        "| Component | Choice | Source | Score | Confidence | URL |",
        "|---|---|---|---:|---|---|",
    ]
    raw_lookup = candidate_lookup(state.get("raw_candidates") or [])
    for item in state.get("curated_choices") or []:
        component = str(item.get("component") or "")
        selected = item.get("selected") or {}
        chosen = raw_lookup.get((component, str(selected.get("name") or "")), {})
        lines.append(
            f"| {component} | {selected.get('name','')} | {selected.get('source_type','')} | {chosen.get('overall_score','')} | {selected.get('confidence','')} | {selected.get('url','')} |"
        )
    if trusted_starter:
        lines.extend(
            [
                "",
                "## Trusted Runnable Starter",
                "",
                f"- {trusted_starter.get('label')} -> {trusted_starter.get('repo')}",
                f"- Status: {trusted_starter.get('status')}",
            ]
        )
    if result_mode == "explore":
        lines.extend(["", "## Explore Alternatives", ""])
        for item in state.get("curated_choices") or []:
            alternatives = item.get("alternatives") or []
            if not alternatives:
                continue
            lines.append(f"### {item.get('component')}")
            for alt in alternatives:
                lines.append(f"- {alt.get('name')} -> {((alt.get('why_not_selected') or []) or ['ranked lower'])[0]}")
            lines.append("")
    lines.extend(["## Setup Commands", ""])
    for step in (state.get("starter_plan") or {}).get("setup_steps", []):
        lines.append(f"- `{step}`")
    return "\n".join(lines).rstrip() + "\n"


def write_stage_files(output_dir: Path, state: dict[str, Any]) -> None:
    write_json(output_dir / "00-input.json", state.get("input") or {})
    write_json(output_dir / "01-spec.json", state.get("spec") or {})
    write_json(output_dir / "02-architecture.json", state.get("architecture") or {})
    write_json(output_dir / "03-search-report.json", ((state.get("reports") or {}).get("search") or {}))
    write_json(output_dir / "04-curation.json", state.get("curated_choices") or [])
    write_json(output_dir / "05-starter-plan.json", state.get("starter_plan") or {})


def write_prompt_files(output_dir: Path, starter_plan: dict[str, Any]) -> None:
    prompt_dir = output_dir / "prompts"
    handoff = starter_plan.get("prompt_handoff") or {}
    write_text(prompt_dir / "codex.md", str(handoff.get("for_codex", "")) + "\n")
    write_text(prompt_dir / "claude-code.md", str(handoff.get("for_claude_code", "")) + "\n")
    write_text(prompt_dir / "cursor.md", str(handoff.get("for_cursor", "")) + "\n")


def _run_process(command: list[str], cwd: Path, timeout_sec: int) -> tuple[str, str, int]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError as exc:
        return ("fail", str(exc), -1)
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(part for part in [str(exc.stdout or "").strip(), str(exc.stderr or "").strip()] if part).strip()
        return ("fail", output or f"Timed out after {timeout_sec}s", -1)
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    return ("pass" if completed.returncode == 0 else "fail", output, completed.returncode)


def _split_command(command: str) -> list[str]:
    return shlex.split(command, posix=False)


def validate_runnable_starter(starter: dict[str, Any], output_dir: Path, timeout_sec: int) -> dict[str, Any]:
    project_dir = output_dir.resolve() / "project"
    app_dir = project_dir / "app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    repo_ref = resolve_starter_repo(str(starter.get("repo") or ""))
    details = {
        "clone": {"status": "fail", "command": "", "fallback_used": False, "output": "", "return_code": -1},
        "install": {"status": "fail", "command": "", "fallback_used": False, "output": "", "return_code": -1},
        "env": {"status": "fail", "command": "env-check", "fallback_used": False, "output": "", "return_code": -1},
        "run": {"status": "fail", "command": "", "fallback_used": False, "output": "", "return_code": -1},
    }

    if isinstance(repo_ref, Path):
        try:
            shutil.copytree(repo_ref, app_dir, dirs_exist_ok=True)
            details["clone"] = {"status": "pass", "command": f"copy {repo_ref}", "fallback_used": False, "output": "", "return_code": 0}
        except Exception as exc:  # noqa: BLE001
            details["clone"]["output"] = str(exc)
            return {"status": "fail", "final_result": "unusable", "details": details}
    else:
        command = ["git", "clone", str(repo_ref), str(app_dir)]
        status, output, return_code = _run_process(command, project_dir, timeout_sec)
        details["clone"] = {"status": status, "command": " ".join(command), "fallback_used": False, "output": output, "return_code": return_code}
        if status != "pass":
            return {"status": "fail", "final_result": "unusable", "details": details}

    install_attempts: list[list[str]] = []
    custom_validation = [str(item) for item in (starter.get("validation_install_commands") or []) if str(item).strip()]
    if custom_validation:
        install_attempts = [_split_command(item) for item in custom_validation]
    elif (app_dir / "pnpm-lock.yaml").exists():
        install_attempts = [["pnpm", "install"]]
    elif (app_dir / "yarn.lock").exists():
        install_attempts = [["yarn", "install"]]
    elif (app_dir / "package.json").exists():
        install_attempts = [["npm", "install"], ["npm", "install", "--ignore-scripts"]]
    elif (app_dir / "requirements.txt").exists():
        install_attempts = [["python", "-m", "pip", "install", "--dry-run", "-r", "requirements.txt"]]

    if install_attempts:
        primary = install_attempts[0]
        status, output, return_code = _run_process(primary, app_dir, timeout_sec)
        details["install"] = {"status": status, "command": " ".join(primary), "fallback_used": False, "output": output, "return_code": return_code}
        if status != "pass" and len(install_attempts) > 1:
            fallback = install_attempts[1]
            status, output, return_code = _run_process(fallback, app_dir, timeout_sec)
            details["install"] = {"status": status, "command": " ".join(fallback), "fallback_used": True, "output": output, "return_code": return_code}
    else:
        details["install"] = {"status": "fail", "command": "install-check", "fallback_used": False, "output": "No supported install command detected.", "return_code": -1}

    env_output = []
    env_status = "fail"
    env_example = app_dir / ".env.example"
    if env_example.exists():
        env_output.append(".env.example exists")
        env_status = "pass"
    elif starter.get("env_template"):
        env_lines = [f"{key}={value}" for key, value in (starter.get("env_template") or {}).items()]
        write_text(app_dir / ".env.example", "\n".join(env_lines) + "\n")
        env_output.append(".env.example generated from whitelist template")
        env_status = "pass"
    else:
        env_output.append("No env template available.")
    details["env"] = {"status": env_status, "command": "env-check", "fallback_used": False, "output": "; ".join(env_output), "return_code": 0 if env_status == "pass" else -1}
    run_command = str(starter.get("run_command") or "").strip()
    expected_output = str(starter.get("expected_output") or "").strip()
    if run_command:
        command = _split_command(run_command)
        status, output, return_code = _run_process(command, app_dir, timeout_sec)
        if status == "pass" and expected_output and expected_output not in output:
            status = "fail"
            output = (output + "\n" if output else "") + f"Expected output not found: {expected_output}"
            return_code = -1
        details["run"] = {
            "status": status,
            "command": run_command,
            "fallback_used": False,
            "output": output,
            "return_code": return_code,
        }
    else:
        details["run"] = {
            "status": "fail",
            "command": "run-check",
            "fallback_used": False,
            "output": "No run_command configured.",
            "return_code": -1,
        }

    overall = "pass" if all(step["status"] == "pass" for step in details.values()) else "fail"
    if details["run"]["status"] == "pass" and all(details[key]["status"] == "pass" for key in ("clone", "install", "env")):
        final_result = "usable"
    elif details["run"]["status"] == "pass":
        final_result = "unstable"
    else:
        final_result = "unusable"
    if str(starter.get("validation_mode") or "").casefold() == "keep_flaky" and overall == "pass":
        final_result = "unstable"
    return {"status": overall, "final_result": final_result, "details": details}


def write_runnable_package(output_dir: Path, trusted_starter: dict[str, Any]) -> None:
    project_dir = output_dir.resolve() / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    env_lines = [f"{key}={value}" for key, value in (trusted_starter.get("env_template") or {}).items()]
    write_text(project_dir / ".env.example", "\n".join(env_lines) + ("\n" if env_lines else ""))
    setup_lines = ["$ErrorActionPreference = 'Stop'"]
    for command in trusted_starter.get("tested_commands") or []:
        setup_lines.append(command)
    write_text(project_dir / "setup.ps1", "\n".join(setup_lines) + "\n")
    runbook = [
        "# RUNBOOK",
        "",
        f"Starter: {trusted_starter.get('label')}",
        f"Status: {trusted_starter.get('status')}",
        f"Expected Output: {trusted_starter.get('expected_output') or 'see starter docs'}",
        "",
        "Steps:",
    ]
    for index, command in enumerate(trusted_starter.get("tested_commands") or [], start=1):
        runbook.append(f"{index}. `{command}`")
    write_text(project_dir / "RUNBOOK.md", "\n".join(runbook) + "\n")
    known_issues = ["# KNOWN ISSUES", ""] + [f"- {issue}" for issue in (trusted_starter.get("known_issues") or [])]
    write_text(project_dir / "KNOWN_ISSUES.md", "\n".join(known_issues) + "\n")
    write_json(project_dir / "TRUSTED_STARTER.json", trusted_starter)


def preflight_failure(search_mode: str, sources: list[str]) -> str:
    if search_mode == "strict" and "github" not in sources:
        return "strict mode requires GitHub search to stay enabled"
    if search_mode == "strict" and "github" in sources and not os.getenv("GITHUB_TOKEN"):
        return "strict mode requires GITHUB_TOKEN for GitHub search"
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SaaS OSS Accelerator from a single idea string.")
    parser.add_argument("--idea", required=True, help="Product idea in plain language")
    parser.add_argument("--output-dir", help="Directory for generated outputs")
    parser.add_argument("--force-starter", help="Force a trusted starter by id, label, or repo")
    parser.add_argument("--sources", default="npm,pypi,github", help="Comma-separated search sources")
    parser.add_argument("--limit-per-source", type=int, default=4, help="Results per source query")
    parser.add_argument("--cache-ttl-seconds", type=int, default=6 * 60 * 60, help="Fresh cache TTL")
    parser.add_argument("--retries", type=int, default=3, help="HTTP retries per request")
    parser.add_argument("--validation-timeout-sec", type=int, default=120, help="Per-step runnable validation timeout")
    parser.add_argument("--search-mode", choices=["strict", "degraded", "offline"], default="strict")
    parser.add_argument("--result-mode", choices=["safe", "explore"], default="safe")
    parser.add_argument("--output-mode", choices=["plan", "runnable"], default="plan")
    parser.add_argument("--min-score", type=float, help="Optional override for the mode score threshold")
    parser.add_argument("--beginner", dest="beginner", action="store_true", default=True)
    parser.add_argument("--no-beginner", dest="beginner", action="store_false")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else build_output_dir(SCRIPT_DIR / "work", args.idea)
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = [item.strip() for item in args.sources.split(",") if item.strip()]
    search_map = build_search_map(args.idea, args.beginner)
    state = build_initial_state(args.idea, args.beginner, search_map, args.search_mode, args.result_mode, args.output_mode)
    trusted_starters = load_trusted_starters()
    state["reports"]["validation_set"] = validation_set_summary(trusted_starters)
    forced_starter = None
    if args.force_starter:
        forced_starter = next((item for item in trusted_starters if starter_matches(item, args.force_starter)), None)
        if not forced_starter:
            state["execution"]["hard_fail"] = True
            state["execution"]["failure_modes"] = ["runnable_failed"]
            state["execution"]["failure_mode"] = "runnable_failed"
            state["execution"]["outcome_status"] = "failed"
            state["execution"]["forced_mode"] = True
            state["execution"]["forced_starter"] = args.force_starter
            state["reports"]["runnable"] = {"forced_starter": args.force_starter, "error": "trusted starter not found"}
            save_state(output_dir / "state.json", state)
            write_stage_files(output_dir, state)
            write_text(output_dir / "RECOVERY_ACTION.md", build_recovery_action(state))
            write_text(output_dir / "NEXT_ACTION.md", build_next_action(state))
            write_text(output_dir / "DECISION_LOG.md", "# Decision Log\n\nNo component was selected.\n")
            write_text(output_dir / "STARTER_README.md", build_readme(state, args.result_mode, None))
            print(json.dumps({"output_dir": str(output_dir), "failure_mode": "runnable_failed", "reason": "trusted starter not found"}, indent=2))
            return 2
        state["execution"]["forced_mode"] = True
        state["execution"]["forced_starter"] = str(forced_starter.get("id") or forced_starter.get("label") or forced_starter.get("repo"))

    preflight = "" if forced_starter else preflight_failure(args.search_mode, sources)
    if preflight:
        state["execution"]["search_quality"] = "low"
        state["execution"]["data_freshness"] = "stale"
        state["execution"]["hard_fail"] = True
        state["execution"]["failure_modes"] = ["degraded_search"]
        state["execution"]["failure_mode"] = "degraded_search"
        state["execution"]["outcome_status"] = "failed"
        state["reports"]["search"] = {"search_mode": args.search_mode, "search_quality": "low", "data_freshness": "stale", "warnings": [preflight], "stats": {"fetched": 0, "kept": 0, "rejected": 0, "warnings": 1}}
        save_state(output_dir / "state.json", state)
        write_stage_files(output_dir, state)
        write_text(output_dir / "RECOVERY_ACTION.md", build_recovery_action(state))
        write_text(output_dir / "NEXT_ACTION.md", build_next_action(state))
        write_text(output_dir / "DECISION_LOG.md", "# Decision Log\n\nNo component was selected.\n")
        write_text(output_dir / "STARTER_README.md", build_readme(state, args.result_mode, None))
        print(json.dumps({"output_dir": str(output_dir), "failure_mode": "degraded_search", "reason": preflight}, indent=2))
        return 2

    if forced_starter:
        forced_candidate = build_forced_candidate(forced_starter)
        candidates = [forced_candidate]
        state["reports"]["search"] = {
            "search_mode": args.search_mode,
            "search_quality": "high",
            "data_freshness": "live",
            "contract": {"forced_mode": True},
            "stats": {"fetched": 0, "kept": 1, "rejected": 0, "warnings": 0, "live_requests": 0, "fresh_cache_hits": 0, "stale_cache_hits": 0, "request_failures": 0},
            "warnings": [],
            "rejected_candidates": [],
        }
        state["reports"]["runnable"] = {
            "forced_mode": True,
            "selection_policy": "forced starter override",
            "pool_status": str(forced_starter.get("status") or "").casefold() or "unknown",
        }
    else:
        candidates, search_report = run_search(
            search_map,
            sources,
            args.limit_per_source,
            cache_dir=output_dir / ".cache",
            cache_ttl_seconds=args.cache_ttl_seconds,
            retries=args.retries,
            search_mode=args.search_mode,
            min_score=args.min_score,
        )
        state["reports"]["search"] = search_report
        if args.output_mode == "runnable":
            candidates, trusted_pool_status = filter_candidates_for_runnable(candidates, trusted_starters)
            warnings = list(search_report.get("warnings") or [])
            if trusted_pool_status == "flaky":
                warnings.append("No verified runnable starter matched. Falling back to flaky validated starters.")
                state["reports"]["search"]["warnings"] = warnings
            state["reports"]["runnable"] = {
                "trusted_candidate_pool": len(candidates),
                "trusted_repos": len(trusted_starters),
                "forced_mode": False,
                "selection_policy": (
                    "verified starters only"
                    if trusted_pool_status == "verified"
                    else "flaky starter fallback"
                    if trusted_pool_status == "flaky"
                    else "no validated starter matched"
                ),
                "pool_status": trusted_pool_status,
            }

    state["raw_candidates"] = candidates
    search_report = state["reports"]["search"]
    state["execution"]["search_quality"] = search_report["search_quality"]
    state["execution"]["data_freshness"] = search_report["data_freshness"]
    state["curated_choices"] = curate_candidates(candidates, args.result_mode)

    trusted_starter = forced_starter or find_trusted_starter(candidates, trusted_starters)
    runnable_report = state["reports"].get("runnable") or {}
    if args.output_mode == "runnable" or forced_starter:
        if trusted_starter:
            validation = validate_runnable_starter(trusted_starter, output_dir, args.validation_timeout_sec)
            state["execution"]["runnable_status"] = validation["status"]
            state["execution"]["final_result"] = validation["final_result"]
            runnable_report.update(
                {
                    "forced_mode": bool(forced_starter),
                    "selected_trusted_starter": trusted_starter.get("repo"),
                    "label": trusted_starter.get("label"),
                    "status": trusted_starter.get("status"),
                    "selected_status": trusted_starter.get("status"),
                    "verified_env": trusted_starter.get("verified_env"),
                    "expected_output": trusted_starter.get("expected_output"),
                    "runnable_status": validation["status"],
                    "final_result": validation["final_result"],
                    "runnable_status_detail": validation["details"],
                    "confidence": "high" if str(trusted_starter.get("status") or "").casefold() == "verified" else "medium",
                }
            )
        else:
            state["execution"]["runnable_status"] = "fail"
            state["execution"]["final_result"] = "unusable"
            runnable_report.update(
                {
                    "forced_mode": False,
                    "selected_trusted_starter": "",
                    "selected_status": "",
                    "runnable_status": "fail",
                    "final_result": "unusable",
                    "runnable_status_detail": {},
                }
            )
        state["reports"]["runnable"] = runnable_report
    else:
        state["execution"]["runnable_status"] = "n/a"
        state["execution"]["final_result"] = "n/a"

    state["starter_plan"] = build_starter_plan(state, trusted_starter)
    failure_modes = failure_modes_for_state(state, trusted_starter)
    state["execution"]["failure_modes"] = failure_modes
    state["execution"]["failure_mode"] = primary_failure_mode(failure_modes)
    state["execution"]["outcome_status"] = outcome_status_for_state(state)
    state["reports"]["curation"] = {"result_mode": args.result_mode, "selected_components": len(state["curated_choices"])}

    save_state(output_dir / "state.json", state)
    write_stage_files(output_dir, state)
    write_text(output_dir / "STARTER_README.md", build_readme(state, args.result_mode, trusted_starter))
    write_text(output_dir / "DECISION_LOG.md", build_decision_log(state, args.result_mode))
    write_text(output_dir / "RECOVERY_ACTION.md", build_recovery_action(state))
    write_text(output_dir / "NEXT_ACTION.md", build_next_action(state))
    write_prompt_files(output_dir, state["starter_plan"])
    if (args.output_mode == "runnable" or forced_starter) and trusted_starter:
        write_runnable_package(output_dir, trusted_starter)
        if (state.get("reports") or {}).get("runnable"):
            write_json(output_dir / "project" / "RUNNABLE_STATUS.json", (state.get("reports") or {}).get("runnable"))

    summary = {
        "output_dir": str(output_dir),
        "idea": args.idea,
        "search_mode": args.search_mode,
        "result_mode": args.result_mode,
        "output_mode": args.output_mode,
        "forced_mode": state["execution"]["forced_mode"],
        "search_quality": state["execution"]["search_quality"],
        "data_freshness": state["execution"]["data_freshness"],
        "outcome_status": state["execution"]["outcome_status"],
        "runnable_status": state["execution"]["runnable_status"],
        "final_result": state["execution"]["final_result"],
        "failure_mode": state["execution"]["failure_mode"] or "none",
        "failure_modes": state["execution"]["failure_modes"],
        "retained_candidates": len(state["raw_candidates"]),
        "curated_components": len(state["curated_choices"]),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 2 if state["execution"]["outcome_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
