# Security Policy

## Reporting a Vulnerability

NemotronFlow takes security reports seriously. We appreciate responsible
disclosure and will work with you to investigate and remediate reported issues.

**Do NOT open a public GitHub issue for a security vulnerability.**

Instead, please report vulnerabilities privately using **GitHub Security
Advisories**:

1. Go to the repository on GitHub.
2. Click the **Security** tab.
3. Click **Report a vulnerability** and follow the prompts.

This creates a private advisory visible only to the maintainers. Please include:

- A description of the issue and its potential impact.
- Steps to reproduce (proof of concept, logs, screenshots as relevant).
- Affected versions, if known.
- Any suggested remediation.

## Response expectations

- **Acknowledgement:** within 72 hours of the initial report.
- **Initial assessment:** within 7 days, including a severity rating and a
  tentative remediation plan.
- **Status updates:** at least every 7 days until the issue is resolved or
  closed.
- **Coordinated disclosure:** we will credit reporters (with permission) in the
  advisory and release notes, and time public disclosure to land alongside a
  fix.

## Supported versions

NemotronFlow is in alpha. Only the following versions receive security updates:

| Version | Supported |
|---------|-----------|
| 0.1.x (alpha, latest) | ✅ |
| < 0.1.0 | ❌ |

As the project moves toward stable releases, this table will be updated with a
formal support window per major version.

## Scope

In scope:

- The NemotronFlow desktop application (Tauri shell + Python sidecar).
- The IPC bridge between the two processes.
- Local data handling (settings, history database, logs).
- The packaging pipeline as it affects end-user machines.

Out of scope:

- Vulnerabilities in third-party dependencies (report upstream; we will track
  and update affected versions promptly).
- Self-compiled builds from non-release commits.
- Issues requiring physical access to an unlocked machine, beyond standard
  local-privilege boundaries.

## Local-first design

NemotronFlow is local-first: it makes no outbound network calls in its default
configuration. The only optional network features are the LLM cleanup
providers (v2, off by default and requiring explicit configuration) and the
updater (which checks a GitHub Releases endpoint). Model downloads for the
Nemotron engine are user-initiated. This design is a deliberate security and
privacy choice.
