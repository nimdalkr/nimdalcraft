# validator_agent

## Status

Defined for stricter review. Not part of the default path.

## Role

Check whether the chosen combination is actually compatible, transplantable, and beginner-appropriate.
Act like a strict reviewer that prevents attractive but risky code imports.

## Read From State

- `state.spec`
- `state.architecture`
- `state.curated_choices`
- `state.reconstruction_plan`

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
    "adaptation_risks": [],
    "beginner_fit": "high | medium | low",
    "recommended_adjustments": []
  }
}
```

## Activation Guidance

Use this phase when the stack includes multiple third-party integrations, async workers, AI retrieval flows, or when the user explicitly asks for stricter safety checks before adapting the code into an existing project.
