# Security Policy

## Scope & threat model

Unreal AI Connection is a UE editor-automation plugin plus a Python stdio↔TCP
bridge. The plugin binds **`127.0.0.1:18888` only** (localhost) — there is no
remote listener by design, so the primary threat surface is a local user who can
already reach the loopback interface. The most security-relevant class of bug is
therefore **a crafted MCP `tools/call` that escalates into arbitrary code
execution, file write outside the project, or editor/host compromise** (e.g. via
`execute_unreal_python`, path traversal in a `bulk_*`/asset path, or an SSRF in a
marketplace download).

## Supported versions

Security fixes land on `main` and ship in the next tagged release. Only the
latest release line is supported.

| Component | Supported |
| --- | --- |
| Latest release (`v0.9.1` and newer) | ✅ |
| Older tagged releases | ❌ — update to latest |
| UE 5.7 (official target) | ✅ |
| UE 5.6 (host-certified, prebuilt binaries) | ✅ best-effort |
| UE 4.27 / 5.0–5.5 / 5.8 (uncertified compat scaffold) | ❌ — community build-from-source, unsupported |

## Reporting a vulnerability

**Do NOT open a public issue for a security concern.** Report privately via
GitHub's **["Report a vulnerability"](https://github.com/NAJEMWEHBE/unreal-ai-connection/security/advisories/new)**
flow (Security tab → Advisories → Report a vulnerability) on this repository.

Please include:

- The affected tool/handler or bridge path, and the plugin / engine version.
- A minimal `tools/call` (or repro steps) that triggers the issue.
- The impact you observed (code execution, out-of-project file write, crash, etc.).

### What to expect

- **Acknowledgement** within a few days of the report.
- An initial assessment (accepted / needs-info / declined, with reasoning) after
  triage.
- For accepted issues: a fix on a private branch, then a coordinated release; you
  will be credited in the release notes unless you prefer to stay anonymous.
- For declined issues: a clear explanation of why it falls outside the threat
  model above (e.g. requires an attacker who already has local shell as the same
  user the editor runs as).

Because the bridge is localhost-only, please state explicitly if your finding
assumes a non-local attacker or a cross-user boundary — that materially changes
severity.
