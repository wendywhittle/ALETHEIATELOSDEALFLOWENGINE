ALETHEIATELOS DEALFLOW ENGINE — README

Repository:

wendywhittle/ALETHEIATELOSDEALFLOWENGINE

REPOSITORY BOUNDARY

This repository is the standalone source of truth for the AletheiaTelos DealFlow Engine.

Work ONLY in:

wendywhittle/ALETHEIATELOSDEALFLOWENGINE

Do NOT:

* access or modify wendywhittle/upgraded-octo-tribble
* access or modify any other repository
* import, copy, or depend on code from another repository
* recreate the previous AletheiaTelos dashboard architecture
* introduce unnecessary architecture, abstractions, agent hierarchies, or decorative intelligence layers

This is a new product with a deliberately lean architecture.

⸻

What This Is

AletheiaTelos DealFlow Engine is a real-estate deal acquisition and underwriting workflow.

The core loop is:

PUBLIC LANDING PAGE → DEAL INTAKE → CANONICAL DEAL RECORD → QUICK UNDERWRITING → EXCEL MODEL → INTERNAL DEAL PIPELINE → DEAL REVIEW

The system should allow a person with a real deal to enter the basic numbers, submit the deal, immediately see useful analytical results, and create a real structured deal record for internal review.

The product should be:

* simple
* fast
* useful
* credible
* deterministic
* understandable
* extensible without over-engineering

Sophistication should come from what the system actually does, not from terminology or visual complexity.

⸻

Public Experience

The public-facing proposition is straightforward:

Have a deal? Enter the basics and see what the numbers say.

The primary workflow is:

ENTER DEAL → SUBMIT → SEE RESULTS

The public interface should be responsive across:

* iPhone
* iPad
* tablet
* desktop

It should use large, touch-friendly controls and avoid a terminal-like interface.

No fake AI activity.

No decorative intelligence panels.

No fabricated performance claims.

No autonomous-investing claims.

⸻

Deal Intake

Initial deal information should support:

Required / Core

* Deal or property name
* Asset type
* Location
* Purchase price
* Annual NOI

Optional Financial Information

* Annual EGI
* Annual operating expenses
* Occupancy
* LTV
* Interest rate
* Amortization period
* Hold period
* Exit cap rate
* Closing costs

Contact

* Name
* Email
* Phone

Not every field should be mandatory.

The system must distinguish between:

* supplied
* calculated
* unavailable
* unknown

Missing information must never be silently fabricated.

⸻

Canonical Deal Record

Every submitted deal receives one unique Deal ID.

Example:

AT-2026-000001

The Deal Record is the canonical source of truth.

It preserves:

* original submitted inputs
* derived calculations
* missing information
* contact information
* timestamps
* status
* generated artifact references

Original inputs must never be overwritten by calculations.

There should not be competing versions of the same deal.

Initial status:

NEW

⸻

Underwriting

The underwriting engine must be deterministic and independently testable.

Where sufficient information exists, calculate:

* NOI
* Cap Rate
* Loan Amount
* Initial Equity
* Annual Debt Service
* DSCR
* Annual Cash Flow
* Cash-on-Cash Return
* Exit Value
* IRR
* Equity Multiple

Calculation rules:

1. Explicit NOI takes precedence.
2. If NOI is absent but EGI and operating expenses are supplied, calculate NOI as EGI minus operating expenses.
3. Purchase price comes from the submitted deal.
4. Never invent missing assumptions.
5. Missing values must not silently become zero.
6. An uncomputable metric must explicitly report as unavailable or unknown.
7. Repeated calculations using identical inputs must produce identical outputs.
8. Preserve the inputs used to produce each derived result.

Every displayed number must have a real source.

⸻

User Result

After submission, the user should receive a concise analytical result based only on the information supplied.

Example:

* Purchase Price
* NOI
* Cap Rate
* Loan Amount, if computable
* Equity, if computable
* DSCR, if computable
* Cash Flow, if computable
* Other available return metrics

The result should clearly communicate:

Based on the information you provided.

The output is analytical decision support.

It is not:

* an investment recommendation
* authorization
* a guarantee
* a prediction
* financial advice
* an autonomous investment decision

⸻

Excel Underwriting Model

Each submitted deal should be capable of producing a standardized Excel underwriting workbook.

The application remains authoritative.

The Excel workbook is an artifact generated from the canonical Deal Record.

Minimum workbook structure:

Deal Summary

* Deal ID
* Deal name
* Asset type
* Location
* Purchase price
* NOI
* Contact information where appropriate

Operating Inputs

* EGI
* Operating expenses
* Occupancy

Financing

* LTV
* Interest rate
* Amortization
* Loan amount
* Initial equity
* Debt service

Returns

* Cap rate
* DSCR
* Cash flow
* Cash-on-cash return
* Exit value
* IRR
* Equity multiple

Assumptions / Missing Data

Clearly identify what was:

* supplied
* calculated
* unavailable
* unknown

The Excel workbook must not become a second source of truth.

⸻

Internal Deal Pipeline

The internal pipeline should remain intentionally simple.

Each submitted deal is a real record.

Display:

* Deal ID
* Deal name
* Asset type
* Location
* Purchase price
* NOI
* Cap rate when available
* Submission date
* Status

Initial statuses:

NEW → REVIEWING → PURSUE / HOLD / PASS

Clicking a deal opens the actual Deal Record.

The detail view should expose:

* original inputs
* calculated outputs
* missing information
* contact information
* generated Excel artifact
* timestamps
* current status

The deal itself is the workspace.

Do not build a complicated CRM.

⸻

Automation

The system should be designed around:

SUBMISSION → VALIDATION → DEAL RECORD → CALCULATION → EXCEL GENERATION → PIPELINE → NOTIFICATION

Only implement automation that is actually required.

Do not introduce speculative:

* microservices
* event buses
* queues
* workers
* external integrations
* unnecessary infrastructure

unless a real requirement makes them necessary.

Prefer the simplest reliable architecture.

⸻

Security and Authority Boundary

The DealFlow Engine is analytical and administrative.

It must never:

* execute trades
* execute purchases
* move capital
* transfer funds
* access brokerage accounts
* mutate portfolios
* autonomously authorize investments
* represent analytical output as human authorization

A calculated result is not an investment decision.

⸻

Testing

The system must test at minimum:

Deal Record

* unique Deal ID
* original inputs preserved
* missing information explicit

Underwriting

* direct NOI
* derived NOI
* missing NOI
* missing financing
* invalid inputs
* zero values
* negative values
* deterministic repeated calculations

Submission

* valid submission
* incomplete optional information
* invalid required information
* persistence

Excel

* workbook generated
* values match canonical Deal Record
* calculations match application calculations

Pipeline

* submitted deal appears
* deal detail opens
* status persists

Frontend

* mobile
* tablet
* desktop
* validation
* submission
* results

Run the full test suite before committing.

⸻

Definition of Done

The first complete milestone is:

VISITOR → LANDING PAGE → DEAL INPUT → SUBMIT → CANONICAL DEAL RECORD → UNDERWRITING → USEFUL RESULT → EXCEL MODEL → INTERNAL DEAL PIPELINE → DEAL DETAIL

A real submitted deal must successfully traverse the entire loop.

Do not expand scope until this loop is reliable.

Lean. Real. Useful.
