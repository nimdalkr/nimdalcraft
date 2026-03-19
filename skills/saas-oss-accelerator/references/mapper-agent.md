# mapper_agent

## Status

Defined for future use. Not part of the default V1 path.

## Role

Convert `state.architecture` into search-oriented OSS categories and query variants when `architect_agent` output is too abstract or too sparse for deterministic search.

## Read From State

- `state.spec`
- `state.architecture`

## Write To State

- `state.search_map`

## Output Schema

```json
{
  "search_map": [
    {
      "component": "",
      "purpose": "",
      "source_types": ["github", "npm", "pypi"],
      "query_variants": [],
      "selection_criteria": []
    }
  ]
}
```

## Activation Guidance

Use this phase only when `component_search_targets` is not sufficient to generate strong search queries or when search recall is weak.
