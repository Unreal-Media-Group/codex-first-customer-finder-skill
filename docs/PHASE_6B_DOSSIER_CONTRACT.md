# Phase 6B dossier contract-first foundation

**Status:** Independently reviewed and finalized at commit `86f83f1a94862d573f30da70a47760556b045309`; synthetic contract work only

This foundation defines deterministic repository-local contracts for pre-search opportunity intent,
comprehensive synthetic customer dossiers, and graph-ready local lead-intelligence packages. It does
not perform customer research, read a live site, connect to a database, invoke another agent, or grant
authority to generate, contact, send, deploy, or write externally.

## Boundary

Phase 6B contract-first work is limited to three JSON schemas, one standard-library validator,
synthetic `.example` fixtures, tests, and documentation. The Phase 1 campaign and history formats and
their validators remain frozen. Phase 3–6A application code and SQLite state are unchanged.

The test fixture is not a production approval system. Its `approved_result` object represents an
exact synthetic result binding so the dossier/package contract can be validated in isolation. A
future runtime integration must use the independently finalized Phase 6A durable approval boundary
and requires separate authorization.

## Pre-search opportunity intent

`phase6b-search-request.schema.json` wraps a complete validated Phase 1 campaign. Its optional
`opportunity_filter` has two controlled arrays:

- `include_any`: `product_photography`, `product_video`, or `ugc_ad`; at least one evidenced match is
  required when present.
- `exclude`: the same vocabulary; any evidenced match is a hard veto and is evaluated first.

Unknown, duplicate, overlapping, malformed, and wholly empty filters fail closed. Absence preserves
unfiltered behavior. Filter values are user-supplied search intent, never proof that a company needs
the service.

Candidate evaluation is ordered deliberately:

1. validate the Phase 1 campaign and supplied history;
2. classify identity, relationships, duplicates, suppression, active relationships, cooldown, and
   re-engagement with the frozen `validate_history` and `classify_duplicate` functions;
3. evaluate the opportunity filter only for history-eligible candidates;
4. apply the campaign score threshold and deterministic target cap.

A filter cannot turn a known identity into a new prospect or bypass a protection.
Bundle validation is total: every candidate must be a valid object in a supported business unit with
at least one search-request route. Candidate/result IDs and dossier IDs are unique, and packages
built in one bundle share the same idempotency-conflict check used by direct replay.

## Customer dossier

`customer-dossier.schema.json` and the validator require the roadmap's exact canonical category order:

1. identity and relationships;
2. company and commercial context;
3. operations and digital footprint;
4. audiences, market, and reputation;
5. brand and messaging;
6. activity and signals;
7. opportunity and fit;
8. public people and contact paths;
9. governance and history;
10. evidence coverage;
11. additional material facts.

Each category is either `complete` with at least one validated claim, or has exactly one explicit
`not_found`, `not_applicable`, `conflicted`, `stale`, or `unknown` gap plus a bounded explanation and
no guessed claims. Every claim has a stable identifier, category, subject, typed value, observed or
labeled-inference basis, evidence references, confidence reason, uncertainty, source date,
observation time, and computed freshness state.

The validator binds a dossier to one business unit, selected synthetic result, global identity,
account, canonical `.example` domain, history fingerprint, and frozen duplicate classification.
Evidence and graph references must resolve and IDs must be unique. URLs are credential-free,
query-free HTTP(S) URLs on `.example`; percent-encoded material is not accepted. Values are bounded
for size and depth, non-finite JSON numbers are rejected, and private contact and credential patterns
are prohibited. Contact coverage is limited to synthetic business roles, public professional-profile
references, and official business routes—never email, telephone, named real people, personal accounts,
or guessed patterns. Every relationship has at least one claim or evidence reference. The executable
projection preflight enforces unique identifiers across authored and generated nodes and edges plus the
package's 300-claim, 300-evidence, 600-node, and 600-edge limits, so every accepted dossier can be
projected without an identifier collision or limit failure.

## Graph-ready local package

`lead-intelligence-package.schema.json` describes a deterministic projection of the validated dossier:

- exact business-unit and approved-result binding;
- dossier identity/version, UTC research cutoff, maximum evidence age, history fingerprint, and
  canonical SHA-256 hash;
- category coverage, sorted claims, bounded evidence inventory, stable typed nodes, and directed edges;
- resolvable claim/evidence/node/edge references and frozen duplicate/history classification;
- review and local-release state; and
- explicit authority flags allowing local data handoff only and denying generation, contact,
  outreach, external writes, agent invocation, and deployment.

Canonical JSON uses sorted object keys and compact separators. The package identity derives from the
business unit and idempotency identity. Replaying identical validated input returns byte-identical
output only after every prior package is validated and found non-conflicting; changed content under the
same idempotency identity raises a conflict regardless of prior-package order. Stable entity IDs are
preserved across dossier versions when the underlying entity is unchanged. Validation recomputes the
derived package ID, requires canonical claim/evidence/node/edge ordering, validates controlled
duplicate-history semantics, and reconstructs the exact evidence-node and `evidence_supports` edge
projection. Standalone validation also rejects evidence or claims after the cutoff, requires claim dates
to occur in referenced evidence, and recomputes conflicted-first, stale-second, otherwise-current
freshness from the carried maximum evidence age. Rehashing altered or incomplete package content does
not make it valid.

This is an integration-neutral data handoff. No graph database, graph engine, consumer, scheduler,
network client, service, or downstream invocation exists.

## Synthetic fixtures and validation

The fixtures cover UMG and Talent, product-photo/video inclusion with a UGC-ad veto, unfiltered
compatibility, exact duplicates, suppression, active relationships, cooldown, legitimate
re-engagement, a distinct subbrand, every dossier gap state, and deterministic package generation.
All companies and routes are invented and `.example` scoped.

Run the focused contract checks:

```bash
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/unreal-prospecting-skills-pycache" \
  python3 -m unittest discover -s tests/prospecting \
    -p 'test_phase6b_dossier_contract.py' -v
```

Validate the shipped fixture bundle directly:

```bash
python3 shared/prospecting-core/scripts/validate_phase6b_contract.py \
  fixtures/prospecting/phase6/dossier-fixtures.json \
  fixtures/prospecting/phase6/dossier-history.json
```

## Remaining authorization gates

The complete Phase 6 exit gate is not satisfied by this foundation. Live/public-source reads,
runtime integration with Phase 6A approvals, real customer research, storage, human release workflow,
downstream graph consumption, creative generation, likeness use, outreach, CRM/external writes,
deployment, and every later phase remain unauthorized.
