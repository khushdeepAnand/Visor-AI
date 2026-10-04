# No Live Order Execution Policy

Status: enforced technical policy. Deployment-owner and regulatory signatures are required before any exception can be considered.

## Boundary

StockPilot is a research and paper-simulation application. Shipped application code must not place, modify, cancel, route, or schedule a real broker or exchange order. Market-data, option-chain, and margin endpoints are read-only inputs and do not authorize execution.

## Change Control

Any proposal that could transmit an executable order must be implemented only in a separate, explicitly named module that is disabled by default and excluded from ordinary releases. Before merge or deployment it requires:

1. a documented threat model and independent security review;
2. qualified Indian regulatory review and written sign-off;
3. broker approval and least-privilege credential design;
4. dedicated audit, consent, kill-switch, reconciliation, and incident controls;
5. removal or deliberate revision of the static no-live-order CI gate.

Silently extending a market-data adapter, paper-trading service, webhook, or alert handler to place orders violates this policy.

## Evidence

`tests/test_broker_order_mutation_unreachable.py` statically blocks known broker order actions and non-paper order routes. `ARCHITECTURE.md`, `SECURITY.md`, and `REGULATORY_REVIEW_REQUIRED.md` document the same boundary.

## Approval Record

| Role | Name | Signature | Date |
|---|---|---|---|
| Deployment owner | Pending | Pending | Pending |
| Security reviewer | Pending | Pending | Pending |
| Qualified regulatory reviewer | Pending | Pending | Pending |

No pending row is an approval. Until all required reviews are signed, the no-live-order boundary remains absolute.
