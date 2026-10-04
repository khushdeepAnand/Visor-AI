# Regulatory Review Required

## Current Status

**BLOCKED: no legal or regulatory approval is recorded.**

StockPilot AI is documented and tested as a local NSE/BSE research and paper-trading simulation. Technical controls and disclaimers do not determine whether a particular distribution, jurisdiction, data use, user population, or business model is regulated. No claim of SEBI registration, exchange authorization, broker certification, tax approval, privacy-law approval, or suitability approval is made.

## Required Human Review Before Distribution Or Expanded Use

- Determine whether the product, operator, content, forecasts, alerts, derivatives analytics, comparisons, and reports constitute regulated advice, research analysis, solicitation, or another regulated activity in each intended jurisdiction.
- Review all user-facing disclosures, marketing statements, performance presentations, forecast intervals, simulated results, and limitations. Technical test passes must not be represented as investment performance or model suitability.
- Confirm rights, licenses, attribution, retention, redistribution, and display conditions for exchange, broker, instrument-master, calendar, news, and fallback data.
- Review privacy notices, lawful basis, consent, data-subject rights, retention/deletion, incident response, subprocessors, OneDrive/cloud synchronization, telemetry, OAuth, SMTP, and Sentry usage.
- Review accessibility, consumer-protection, electronic-record, export-control, sanctions, age/eligibility, tax-report wording, and recordkeeping obligations as applicable.
- Review third-party software notices and the exact release inventory.
- Define approved jurisdictions, user categories, support model, complaint handling, evidence retention, and release sign-off owners.

## Review Tracker

No qualified reviewer has been assigned. `Unassigned` is intentional and is not an approval. A release owner must replace it with the reviewer's name and qualifications, add the completion date and evidence reference, and change the status only after receiving a written decision.

| Review item | Jurisdiction/scope | Reviewer | Status | Last updated | Completion evidence |
| --- | --- | --- | --- | --- | --- |
| Product classification: research, advice, solicitation, or algo service | India; intended private/local use | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Forecast, screener, alert, derivatives, and performance disclosures | India; all user-facing surfaces | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Exchange, broker, instrument, calendar, news, and fallback-data rights | NSE/BSE and configured providers | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Privacy, consent, retention, deletion, cloud sync, OAuth, SMTP, and telemetry | Intended user jurisdictions | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Accessibility, consumer protection, records, sanctions, eligibility, and tax wording | Intended user jurisdictions | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Third-party notices and exact release inventory | Private release archive | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |
| Approved users, jurisdictions, complaints, evidence retention, and sign-off authority | Proposed distribution model | Unassigned | BLOCKED / NOT REVIEWED | 2026-09-27 | None |

## Mandatory Boundaries Pending Review

- Keep the supported deployment on a single local workstation and loopback interfaces.
- Keep all orders simulated; do not add or enable broker order placement, modification, or cancellation.
- Do not market forecasts as guaranteed, personalized advice, or verified future performance.
- Treat realized-gain output as a personal-reference estimate, not a tax filing or tax opinion.
- Do not claim real-time, live, licensed, or provider-verified data without current retained evidence and applicable data rights.
- Do not distribute local credentials, databases, logs, caches, fitted model artifacts, or personal information.

## Sign-Off Record

The release owner must retain an external review record identifying reviewer qualifications, scope, jurisdiction, version, date, conditions, unresolved issues, and approval authority. Until that record exists, regulatory/legal status remains **BLOCKED / NOT REVIEWED**, regardless of passing engineering gates.

Live-provider verification is a separate technical evidence gate and cannot substitute for this review.

## Addendum — 2026-09-09: SEBI Retail Algorithmic Trading Framework (Effective 2026-04-01)

This addendum records a specific, dated regulatory development that a reviewer must consider. It is an engineering record of what was read and what the product does today. **It is not legal advice, not a compliance opinion, and it does not change the BLOCKED status above.**

### What changed

SEBI's framework for retail participation in algorithmic trading became mandatory for the industry from **1 April 2026**. Three elements of that framework bear directly on software of this shape:

1. **Broker as principal for algo access.** Retail algo order flow is intermediated by the registered stock broker. The broker is accountable for registering/tagging algos with the exchange, for unique algo identifiers on orders, for API-based order controls, and for the two-tier white-box/black-box treatment. An independent application cannot self-authorize retail algo order routing.
2. **White box versus black box.** A *white box* (execution-logic-disclosed, replicable) algo is treated differently from a *black box* algo, whose provider is expected to hold **Research Analyst** registration and to maintain a research report per algo strategy.
3. **Research analyst registration for recommendation-like output.** Distributing buy/sell/target-price recommendations, or a strategy presented as producing them, is regulated activity irrespective of whether the software is free.

### Where StockPilot AI stands against each element (verified in this repository)

| Element | Current state in the code | Evidence |
| --- | --- | --- |
| Broker order routing | **Not present.** Only simulated order routes exist (`/api/v1/paper/orders`, `/api/v1/paper/orders/process`, `/api/v1/paper/orders/{order_id}`). No broker order placement, modification, or cancellation call exists anywhere in the shipped source. | `tests/test_broker_order_mutation_unreachable.py` scans the whole source tree for vendor order endpoints and client order calls and fails if any appear |
| Algo registration / exchange algo ID | **Not applicable today**, because nothing is routed to an exchange. It becomes a hard prerequisite the moment any order path is added. | Same test; `REGULATORY_REVIEW_REQUIRED.md` boundary "Keep all orders simulated" |
| White box vs black box | The forecast path is **disclosed-method**: documented model family, feature schema version, model version, training window, evidence tier, and abstention codes are returned with every range. The pooled low-history bridge is likewise labelled with its model version and peer cohort. This makes the output *describable*, which is a prerequisite for white-box treatment — **it is not a determination that a reviewer would classify it as white box.** | `FORECAST_CONTRACT.md`, `MODEL_CARD.md`, `forecasting/pooled_cross_section.py` (`POOLED_FEATURE_SCHEMA_VERSION`, `POOLED_MODEL_VERSION`), `services/low_history_forecast.py` |
| Research Analyst registration | **Not held and not claimed.** The product publishes uncertainty ranges with explicit evidence grades and abstains rather than issuing targets; it does not publish buy/sell calls, target prices, or personalized suitability. A reviewer must still decide whether range output plus screeners plus a morning brief amounts to research in the regulatory sense. | `README.md` disclaimers, forecast `support_state` / `abstained` contract, no recommendation field exists in any response model |
| Recommendation-shaped surfaces added in v8 | The morning brief, screener, and options payoff surfaces are **descriptive**: realized price/volume statistics, user-defined filters over computed indicators, and expiry-payoff arithmetic. None ranks instruments as buys, and none returns a target price. | `services/morning_brief.py`, `services/screener.py`, `derivatives/payoff.py` |

### Reviewer questions this addendum does not answer

- Does publishing a probabilistic price range, even with abstention and evidence grades, constitute research or a recommendation for the intended audience and distribution model?
- Does a saved technical screener that alerts on matches constitute an algorithmic strategy or a recommendation when it is user-defined rather than vendor-defined?
- If any hosted, multi-user, or paid distribution is contemplated, which registration (Research Analyst, Investment Adviser, or none) applies to the operator, and which disclosures and record-retention obligations follow?
- What algo registration, broker agreement, unique-identifier, and audit-trail obligations would be triggered by adding *any* live order capability, including a "one-click send to broker" convenience?

### Standing engineering boundary as a result

Until an external review record exists:

- No broker order placement, modification, or cancellation may be added. The structural test above must keep passing.
- No output may be reshaped into a buy/sell call, a target price, a ranked "top picks" list, or a personalized suitability statement.
- The disclosed-method properties of the forecast path (model version, feature schema version, evidence tier, abstention reason) must not be removed, because they are the only thing that makes the output describable to a reviewer.

Regulatory status after this addendum: **BLOCKED / NOT REVIEWED.**
