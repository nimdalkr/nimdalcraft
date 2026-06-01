# Security Policy

## Supported Versions

Nimdalcraft is currently in `0.x` active development. Security fixes are applied to the default branch first.

## Reporting a Vulnerability

Please report suspected security issues privately by emailing 0xnimdal@gmail.com. If the issue can be shared publicly without risk, you may also open a GitHub issue with only high-level details.

Useful reports include:

- The affected command or workflow
- Whether the issue requires untrusted input, a malicious repository, or a hostile package
- Steps to reproduce without exposing private credentials
- Any generated output that demonstrates the problem

## Security-Sensitive Areas

- Retrieval adapters that fetch remote code or package metadata
- Generated reconstruction plans that may include commands
- Starter validation scripts that execute checks in local work directories
- CI jobs that refresh snapshots or starter status files

Nimdalcraft should prefer deterministic degraded behavior and explicit user-controlled tokens for strict search modes. Do not commit tokens, private repository output, or generated work directories.
