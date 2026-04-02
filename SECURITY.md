# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please email **surya96t@gmail.com** with:

1. A description of the vulnerability and its potential impact
2. Steps to reproduce (version, environment, inputs)
3. Any suggested remediation if you have one

You will receive an acknowledgment within **48 hours** and a status update within **7 days**.

---

## Scope

This is an MCP server that wraps the [FastF1](https://github.com/theOehrly/Fast-F1) library. It:

- Reads publicly available Formula 1 timing data
- Does **not** handle authentication, user credentials, or private data
- Communicates only via local stdio transport — no network listeners are opened

If you find an issue in FastF1 itself, please report it to the [FastF1 project](https://github.com/theOehrly/Fast-F1/security).
