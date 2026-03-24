# mapper_agent

## Status

Defined for deeper retrieval control. Not required for the default path.

## Role

Convert `state.spec` and `state.architecture` into feature-oriented search instructions when the default search map is too broad.
This phase exists to move from vague product language to:

- symbol hints
- snippet queries
- semantic queries
- adaptation targets

## Read From State

- `state.spec`
- `state.architecture`
- `state.feature_map`

## Write To State

- `state.search_map`

## Output Schema

```json
{
  "search_map": [
    {
      "component": "",
      "feature_label": "",
      "purpose": "",
      "source_types": ["github", "npm", "pypi", "sourcegraph", "grep_app", "searchcode", "code_rag", "oss_insight", "deps_dev", "continue", "codeium"],
      "query_variants": [],
      "symbol_hints": [],
      "snippet_queries": [],
      "semantic_queries": [],
      "adaptation_targets": [],
      "selection_criteria": []
    }
  ]
}
```

## Activation Guidance

Use this phase when search recall is weak, when the idea language is too abstract, or when the user wants function-level retrieval for a specific feature like auth, uploads, or queue handling.
