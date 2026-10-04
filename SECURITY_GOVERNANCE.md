# Security Governance

Version: 1.0, effective 2026-09-27 for the private/local release.

## Ownership And Cadence

| Area | Accountable role | Required evidence | Cadence |
| --- | --- | --- | --- |
| Authentication and secrets | Release security owner | Auth/security tests, secret scan, threat-model update | Every release and auth change |
| Dependencies | Backend/frontend maintainers | `pip-audit`, Bandit, npm production audit, Dependabot alerts | Every push/PR and weekly |
| Retention and deletion | Deployment owner | Scheduled-job result counts and erasure regression tests | Every release; policy review annually |
| Forecast/model claims | Model owner | Model card, calibration/drift evidence, disclaimer version | Every material language/model change |
| Release archive | Release owner | Allowlist inventory, clean extraction, archive hash and scan | Every archive |
| Regulatory status | Qualified external reviewer | Named, dated jurisdiction/scope decision | Before any distribution or expanded use |

No role may waive a failed secret scan, Medium-or-higher Bandit finding, dependency vulnerability gate, IDOR regression, or broker-order boundary test without a documented risk acceptance naming a human owner, expiry date, affected version, and compensating control.

## Automated Enforcement

- `.github/workflows/ci.yml` runs secret/release checks, mypy, `pip-audit`, Bandit, pytest, frontend tests/typecheck/build, and npm production audit on pushes and pull requests, plus a weekly scheduled run.
- `.github/dependabot.yml` opens reviewed update proposals for Python, npm, and GitHub Actions dependencies. Repository notification delivery is configured by the GitHub organization/repository owner; source code cannot guarantee an external notification recipient.
- The release builder fails closed on forbidden paths and secret findings.
- Retention enforcement runs daily in supported non-test local execution and logs aggregate counts only.

## Change Gates

1. Update `THREAT_MODEL.md` when a trust boundary, sensitive data class, network integration, or authorization model changes.
2. Bump `RESEARCH_ACKNOWLEDGMENT_VERSION` and re-prompt users when forecast, screener, alert, derivatives, or performance language changes materially.
3. Add API-contract and negative authorization tests before changing user-owned routes.
4. Re-run the full CI-equivalent workflow and rebuild from the explicit release allowlist.
5. Record unresolved external/legal gates honestly; fixture tests are not live-provider or regulatory evidence.

## Incident And Rotation Rule

Revoke an exposed provider credential first, preserve sanitized evidence second, close the exposure path third, then generate and store a replacement. JWT rotation invalidates sessions; MFA encryption material is independent and must not be casually rotated because enrolled TOTP secrets depend on it. See `SECURITY.md` for commands.
