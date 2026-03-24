#!/usr/bin/env python3
"""Helpers for the Nimdalcraft retrieval pipeline state."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_STATE = {
    "input": {},
    "spec": {},
    "architecture": {},
    "feature_map": [],
    "search_map": [],
    "raw_candidates": [],
    "code_candidates": [],
    "curated_choices": [],
    "validation_result": {},
    "reconstruction_plan": {},
    "starter_plan": {},
    "execution": {},
    "reports": {},
}

DEFAULT_SOURCE_TYPES = [
    "github",
    "npm",
    "pypi",
    "sourcegraph",
    "grep_app",
    "searchcode",
    "code_rag",
    "oss_insight",
    "deps_dev",
    "continue",
    "codeium",
]


def ensure_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Return a state dict with all required top-level keys present."""
    merged = copy.deepcopy(DEFAULT_STATE)
    if isinstance(state, dict):
        for key, value in state.items():
            merged[key] = value
    return merged


def load_state(path: str | Path) -> dict[str, Any]:
    """Load a JSON state file or return an empty default state."""
    state_path = Path(path)
    if not state_path.exists():
        return ensure_state({})
    with state_path.open("r", encoding="utf-8-sig") as handle:
        return ensure_state(json.load(handle))


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    """Persist state as formatted JSON."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump(ensure_state(state), handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _coerce_target(target: Any) -> tuple[str, str]:
    if isinstance(target, dict):
        component = str(target.get("component") or target.get("name") or "").strip()
        purpose = str(target.get("purpose") or component).strip()
        return component, purpose
    text = str(target).strip()
    return text, text


def _query_variants(component: str) -> list[str]:
    base = component.strip()
    if not base:
        return []
    variants = [base]
    lower = base.lower()
    suffixes = []
    if "starter" not in lower:
        suffixes.append("starter")
    if "boilerplate" not in lower:
        suffixes.append("boilerplate")
    if "template" not in lower:
        suffixes.append("template")
    for suffix in suffixes:
        variants.append(f"{base} {suffix}")
    seen = set()
    deduped = []
    for item in variants:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def derive_search_map(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate a minimal search map from architecture targets."""
    state = ensure_state(state)
    existing = state.get("search_map") or []
    if existing:
        return existing

    architecture_payload = state.get("architecture") or {}
    targets = []
    if isinstance(architecture_payload, dict):
        targets = architecture_payload.get("component_search_targets") or []

    architecture = architecture_payload.get("architecture") if isinstance(architecture_payload, dict) else {}
    if not targets and isinstance(architecture, dict):
        inferred = []
        for key in ("frontend", "backend", "database", "auth", "storage", "deployment"):
            value = architecture.get(key)
            if isinstance(value, dict):
                stack = value.get("recommended_stack")
                if stack:
                    inferred.append(stack)
        worker = architecture.get("worker")
        if isinstance(worker, dict) and worker.get("needed") and worker.get("recommended_stack"):
            inferred.append(worker["recommended_stack"])
        targets = inferred

    search_map = []
    for target in targets:
        component, purpose = _coerce_target(target)
        if not component:
            continue
        search_map.append(
            {
                "component": component,
                "purpose": purpose,
                "source_types": DEFAULT_SOURCE_TYPES[:],
                "query_variants": _query_variants(component),
                "symbol_hints": [],
                "snippet_queries": [],
                "semantic_queries": [purpose],
                "adaptation_targets": [],
                "selection_criteria": [
                    "beginner-friendly setup",
                    "clear maintenance signals",
                    "MVP fit over platform breadth",
                    "prefer reusable implementation patterns over repo branding",
                ],
            }
        )
    return search_map


def merge_raw_candidates(
    state: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Append candidates without duplicating the same component/source/url."""
    merged = ensure_state(state)
    existing = merged.get("raw_candidates") or []
    seen = {
        (
            str(item.get("component", "")).casefold(),
            str(item.get("source_type", "")).casefold(),
            str(item.get("url") or item.get("name") or "").casefold(),
        )
        for item in existing
    }
    for candidate in candidates:
        key = (
            str(candidate.get("component", "")).casefold(),
            str(candidate.get("source_type", "")).casefold(),
            str(candidate.get("url") or candidate.get("name") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        existing.append(candidate)
    merged["raw_candidates"] = existing
    return merged
