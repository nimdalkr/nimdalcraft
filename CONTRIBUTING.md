# Contributing

Thanks for taking a look at Nimdalcraft. The project is early, but the contribution boundary is intentionally narrow: improve how the tool finds, scores, validates, and explains reusable OSS implementation patterns.

## Good First Contribution Areas

- Retrieval adapters for code search, package metadata, or ecosystem signals
- Credibility scoring signals for repo activity, release health, and dependency risk
- Starter validation checks that make `runnable` mode more reliable
- Documentation that makes generated plans easier for Codex, Claude Code, Cursor, and human maintainers to use
- Small bug fixes in CLI argument handling, output generation, or degraded search behavior

## Local Setup

```powershell
git clone https://github.com/nimdalkr/nimdalcraft.git
cd nimdalcraft
npm install
node bin\nimdalcraft.js doctor
```

## Validation

Before opening a pull request, run the focused checks that match your change:

```powershell
python -m py_compile skills\nimdalcraft\run.py skills\nimdalcraft\scripts\source_search.py skills\nimdalcraft\scripts\validate_starters.py
node bin\nimdalcraft.js doctor
node bin\nimdalcraft.js validate --all
```

For a runnable smoke test:

```powershell
node bin\nimdalcraft.js run "Simple internal SaaS" --force-starter local-test-starter --output-mode runnable --search-mode degraded
```

## Pull Request Guidelines

- Keep PRs focused on one adapter, scoring signal, validation rule, or documentation improvement.
- Include the command output or a short validation note in the PR body.
- Explain how the change affects degraded mode, strict mode, or runnable output.
- Avoid adding network-dependent behavior to degraded mode unless there is a deterministic fallback.

## Maintainer Notes

Nimdalcraft uses daily GitHub Actions jobs to refresh the GitHub search snapshot and starter validation state. Changes that touch those generated assets should explain whether the update came from a manual validation run or a CI refresh.
