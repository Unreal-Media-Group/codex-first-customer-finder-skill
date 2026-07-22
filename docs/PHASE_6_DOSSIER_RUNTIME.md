# Phase 6 governed dossier runtime

This runtime connects the finalized synthetic Phase 6B contracts to the existing local Mission
Control approval and review surface. It remains standard-library-only, local, synthetic, and
gitignored. It does not read a live site, store a real prospect, invoke another agent, generate an
asset, contact anyone, write externally, deploy, or modify Unreal OS.

## Search and durable history

`DossierService` runs the Phase 6B history classifier before opportunity filtering, qualification,
and target capping. The optional controlled filters support the documented product-photography and
product-video inclusion with a UGC-ad exclusion. Every immutable search records its request, the
exact history snapshot, the complete candidate evaluation, canonical hashes, byte lengths, and all
selected and non-selected outcomes.

Later searches project prior durable outcomes into their next history snapshot. A previously seen
identity therefore cannot become new because a filter changes. The same re-engagement trigger is
recorded and cannot be reused. Search, run, review, record, and audit identifiers use separate
business-unit counters so one unit does not expose another unit's activity count. Dossier-family
versions also advance independently inside each business unit, even when both units resolve the same
global account identity.

The authoritative account identity is always recomputed as:

```text
"account-v1-" + first 24 lowercase hexadecimal characters of SHA-256(
  UTF-8 compact, sorted-key JSON {
    "canonical_domain": <exact canonical domain>,
    "global_identity_id": <exact global identity>
  }
)
```

A fixture-supplied account value is never authority.

## SQLite activation and recovery

The finalized service boundaries remain additive:

- constructing `SqliteStore` alone activates no later than Phase 4/5 schema v2;
- constructing `EnrichmentService` activates and validates Phase 6A schema v3;
- only `DossierService` activates and validates dossier schema v4; and
- `WebApplication` composes the services in v2, v3, then v4 order.

The v3-to-v4 migration is one `BEGIN IMMEDIATE` transaction. Existing Phase 6A rows and bytes are
preserved. A failed migration rolls back. Reopening a valid v4 file validates its exact columns and
required indexes/triggers without rewriting it. An incomplete v4 layout fails closed with an
archive-and-recreate instruction.

One process-local active-run registry distinguishes a live synchronous claim from crash recovery in
the supported threaded loopback server. A second service in that process cannot falsely recover a
live claim. A committed running claim with no process-local owner is terminalized on startup and
requires a new approval. Multiple independent processes sharing one state file remain unsupported.

## Exact approval, claim, and cancellation

One append-only authority event binds the complete canonical selected-result projection and its hash
and length, search and history hashes, derived account, business unit, proposer, separate reviewer,
exact synthetic source plan, canonical source-plan hash and length, scope, reason, effective time,
recorded time, and exact seven-day expiry.
The stored immutable event remains readable if a repository fixture later evolves; execution compares
the current fixture evidence to the approved stored plan and requires a new approval if they differ.

Current-leaf checks, expected-leaf supersession, revocation, expiry, and single-use claiming are
serialized. Revoke-first denies the claim. Claim-first permits only that committed synchronous run to
finish; later revocation is non-retroactive and does not restore the approval. Search, claim, and
review idempotency keys are scoped to one business unit. Identical replay is read-only and changed
input conflicts.

Cancellation and completion serialize against the same durable run. Cancellation-first creates no
candidate. Completion-first prevents cancellation. Candidate-creation failure terminalizes the
authorized claim without leaving a partial artifact. An unauthorized completion attempt cannot
unregister or fail another actor's live claim.

## Candidate review and local release

Every candidate uses the exact eleven Phase 6B categories. A category is populated with typed,
evidence-linked claims or carries an explicit gap. Runtime cutoff replaces fixture time, and claim
freshness is recomputed as conflicted first, stale second, otherwise current.

The candidate is immutable and starts in `pending_research_quality_review`. A separate allowed
reviewer may record exactly one terminal decision for that exact candidate version:

- `accepted` atomically creates the terminal review, immutable final dossier, one deterministic
  graph-ready package, and audit event;
- `changes_requested` or `rejected` records the terminal decision and creates no released artifact;
  and
- identical review replay is read-only, while changed content or a concurrent loser receives a safe
  conflict.

Every read projection recomputes stored idempotency fingerprints, requires exact event field sets,
and follows the run-to-approval-to-result-to-search-to-candidate relationship before trusting a
snapshot. The release projection then revalidates the review, candidate, final dossier, package,
source-plan hash, canonical hashes and lengths, account identity, version, and business-unit
bindings. Every package authority flag remains false except `local_data_handoff_only`. A released
package is information, not permission to generate, contact, send, write externally, deploy, or
invoke another agent.

## Verification

Focused coverage lives in `tests/prospecting/test_phase6_dossier_runtime.py`. It covers isolated
activation, populated migration, rollback, reopen, incomplete layouts, integrity and foreign keys,
history projection, filtering, account derivation, complete-result and source-plan authority,
business-unit isolation including shared-account version allocation, recomputed idempotency,
self-consistent snapshot and relationship rewrites, revoke/claim and cancel/complete ordering,
process-local recovery, failure cleanup, freshness, exact terminal-review concurrency, atomic fault
rollback, both business units, all eleven categories, and the loopback web workflow.

The real public-source contract and reader are separate subsequent Phase 6 work. No live proof may
run until their focused network-control tests, complete repository suite, independent security
review, exact approved source-plan verification, and durable result-bound authority all pass.
