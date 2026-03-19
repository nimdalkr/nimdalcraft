# validator_agent

## Status

Defined for future use. Not part of the default V1 path.

## Role

Check whether the chosen combination is actually compatible and beginner-appropriate.
Act like a strict reviewer that prevents attractive but risky combinations.

## Read From State

- `state.spec`
- `state.architecture`
- `state.curated_choices`

## Write To State

- `state.validation_result`

## Output Schema

```json
{
  "validation_result": {
    "status": "pass | caution | fail",
    "compatibility_issues": [],
    "complexity_risks": [],
    "maintenance_risks": [],
    "beginner_fit": "high | medium | low",
    "recommended_adjustments": []
  }
}
```

## Activation Guidance

Use this phase when the stack includes multiple third-party integrations, async workers, multiple deployment surfaces, or when the user explicitly asks for stricter safety checks.
