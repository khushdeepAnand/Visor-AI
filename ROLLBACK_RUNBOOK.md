# Release gate rollback runbook

1. Stop promotion immediately when `promotion-gate` or
   `challenger-reconciliation` fails. Never override a missing artifact,
   inverted quantile interval, or coverage-divergence alert.
2. Keep the last known-good release deployed; do not retrain or change the
   forecast core while investigating.
3. Download the failed workflow artifacts and compare `fresh-gate.json` and
   `cqr-challenger.json` (dataset timestamp, symbol set, provider, and sample
   count). A credential/network error is an **unavailable gate**, not a pass.
4. Restore the last known-good artifact/configuration, rerun the offline tests,
   then rerun the live gate with valid provider credentials. Record the run URL.
5. Only a clean replay and reconciliation may unblock a new tag. Escalate
   repeated divergence to model/data owners; do not disable the alert.
