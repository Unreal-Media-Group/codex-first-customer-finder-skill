# Phase 6A Synthetic Lead-Intelligence Brief Foundation

**Status:** Implemented locally from finalized Phase 5 commit
`1ddba9037f1db7e1b58098b83f7bf4caeafd7e8d`; pending independent review and finalization.

Phase 6A adds a manual, one-shot, zero-cost enrichment step for an exact result from a successful
fixture worker output. It uses only repository-owned synthetic metadata and `.example` source URLs.
Its source-linked UMG and Talent campaign briefs are a narrow governance, integrity, and evidence-
lineage foundation for the comprehensive customer dossier mapped in Phase 6B; Phase 6A does not
claim to research the whole customer. It performs no official-site read, live research, scraping,
external API call, external database or CRM write, generation, likeness use, advertisement,
outreach, deployment, downstream-agent invocation, or recurring work. Phase 6B remains unauthorized,
and downstream graph consumers are outside this repository.

## Relationship to the lead-intelligence program

This repository's final product boundary is discovery, history filtering, comprehensive research,
and a reviewed graph-ready data handoff. Contact identification is only one research category.
Subject to separate Phase 6B authorization, the full dossier will also cover identity and business
relationships, commercial context, products and services, audiences and markets, brand and campaign
evidence, public visual-asset references, activity and change signals, opportunities, competitors,
risks, rights, governance state, evidence coverage, and explicit knowledge gaps.

Phase 6A proves the exact-result approval and immutable reviewed-artifact path with a smaller
synthetic field set. It does not implement the comprehensive schema, perform public-source research,
emit the final graph package, modify Unreal OS, or invoke the future creative, contact/outreach, CRM,
or learning consumers.

## Manual workflow

1. A finalized Phase 4/5 fixture run succeeds and creates one durable pending review task.
2. A human fixture-policy reviewer other than the run proposer records an append-only decision for
   one exact task, run, output, result, global identity, business unit, protection snapshot, and
   scope `phase6a_fixture_enrichment`.
3. The approval becomes valid at `effective_at` and expires exactly seven days later. Validity is
   `effective_at <= now < expires_at`; the expiry instant is expired.
4. The bound run proposer manually starts one enrichment with that current approved leaf. The
   approval is single-use. An identical idempotent replay is read-only; conflicting reuse fails
   safely.
5. The service validates the repository fixture and atomically commits bounded source metadata,
   evidence links, and one immutable business-unit campaign brief.
6. A different allowed reviewer may append a research-quality decision. That decision grants only
   `research_quality_only`; it is not generation or execution authority.

Fixture actor selection simulates the policy contract for deterministic local testing. It is not
authentication, production authorization, identity proof, or a substitute for a production access
control system.

## Durable approval contract

Every event stores and rechecks:

- review task, worker run, output, exact result, output SHA-256 and byte length;
- canonical result SHA-256 and byte length;
- canonical worker-configuration SHA-256 and its business-unit binding;
- global identity and business unit;
- a hash of duplicate, suppression, rejection, cooldown, relationship, rights, brand-safety,
  named-person, queue, and governed-decision protections;
- scope, proposer, reviewer, bounded reason, effective time, recorded time, and expiry;
- the optional superseded leaf and a correlation-linked audit event.

The service also stores a canonical snapshot, SHA-256, and byte length over every authority-bearing
approval column. It validates that material before approval detail or history is returned and again
before a claim can consume the approval. Brief-review events use the same complete canonical-event
binding, including proposer/reviewer separation, exact brief and approval provenance, decision,
reason, times, authority, and supersession. Their same-brief supersession chain is validated before
listing, rendering, or accepting another review. Immutability triggers are the enforced defense for
ordinary application access, and the canonical snapshot, hash, and length detect an inconsistent or
partial row change — a column that no longer agrees with its colocated snapshot — as accidental
corruption. They do not independently authenticate authority against a trusted database owner who
disables the triggers and rewrites both an event and its colocated digest; see
[Trusted local-state boundary](#trusted-local-state-boundary).

Process-local `approve_deeper_research` state is not authority for Phase 6A. Missing durable
approval, self-approval, ambiguous identity, suppression, rejection, named talent, unresolved
rights or brand-safety conflict, wrong business unit, stale/forked decision, bytes inconsistent with
their colocated snapshot, a non-current leaf, future-effective approval, or expiration fails closed.

The effective governed decision must be explicitly `pending` or a valid process-local
`approved_for_deeper_research` supersession. A rejection or unknown decision blocks durable approval
and claim. The process-local approval removes that protection block only; it never supplies Phase 6A
authority, which still requires the separate exact durable approval above.

At approval and claim, the service also compares the worker output's governed protection projection
with the current process-local prospect projection. A changed suppression, rejection, identity,
decision, or other protection leaf invalidates the earlier output. If a fresh process cannot
truthfully reconstruct that process-local projection, durable approval history remains visible but
is not actionable; a new worker result is required. A process-local decision therefore can block a
stale result but can never supply Phase 6A authority.

The current process-local projection lock is held through approval or claim commit, establishing a
deterministic order with concurrent suppression or decision changes. Protection-first blocks the
approval or claim; claim-first permits only that already-bound synchronous attempt to finish.

Approval creation, supersession, revocation, invalidation, enrichment claim, completion, failure,
brief review, and audit writes use serialized `BEGIN IMMEDIATE` transactions. If revocation commits
first, no run can claim. If the run claim commits first, that one attempt may finish under its
immutable binding; later revocation is non-retroactive and blocks every new start. Concurrent
supersessions have one winner and a `409` loser. Concurrent identical starts create one logical run;
an audit failure rolls back the associated state transition. SQLite immutability triggers reject
updates or deletes of approval and brief-review events, while legitimate supersession remains a new
insert. A process-local active-claim registry prevents another service instance in the supported
single Python process and threaded loopback server from misclassifying a live synchronous attempt as
crashed; a genuinely unowned running attempt is terminalized on restart. The registry does not
coordinate independent processes sharing one SQLite file. Multiple Mission Control processes against
the same state file are unsupported.

## Trusted local-state boundary

Phase 6A trusts the local SQLite file and the operating-system account that owns it. It is a
repository-local, single-operator lead-intelligence tool, not a multi-tenant service defending stored
state from its own database owner.

Within that boundary Phase 6A provides normal, enforced application protections: endpoint
authorization and business-unit isolation; exact result-bound, seven-day human approval with
proposer/reviewer separation; serialized current-leaf transitions and single-use claims; append-only
and immutability triggers that reject ordinary updates and deletes; foreign keys, CHECK constraints,
idempotency, `BEGIN IMMEDIATE` transactions, and rollback; and canonical snapshots, SHA-256 hashes,
and byte lengths over every authority-bearing field and over the exact source/evidence set.

Those colocated snapshots, hashes, and lengths exist to detect **accidental corruption and
inconsistent or partial mutation** — a column, row, or set that no longer agrees with its recorded
snapshot — and to fail closed before that state is treated as authority, displayed, claimed, or
reviewed. Because a snapshot and its hash are stored next to the row they describe, they are **not**
independent authentication of authority. A trusted operator who can disable the SQLite triggers can
edit an authority event (or a source, link, brief, or manifest) and recompute its colocated digest,
and that self-consistent rewrite is, by design, accepted. Independent review reproduced exactly this:
a self-consistent authority-event edit with a recomputed colocated digest is accepted.

Phase 6A deliberately does not defend against that database owner, and does not add HMACs, digital
signatures, secrets, key management, a second ledger, or an external trust service to try to. The
lead-finder product does not require resistance to a malicious local database owner; treating the
local file and its owner as trusted is the correct, sufficient contract for this tool. Cryptographic
authentication of authority across an untrusted boundary would belong to a separately owned system,
not to this repository-local state.

## Synthetic source and brief contract

The fixture at `fixtures/prospecting/phase6/brief-fixtures.json` is size bounded and must explicitly
declare its synthetic-only boundary. Each source must:

- use `http` or `https` with a hostname ending in `.example`;
- contain no user information, password, query string, or fragment;
- be marked non-official;
- provide only a bounded title, summary, short quote of at most 25 words, observation time, and
  optional source date; and
- contain no personal contact data or credential-like material.

The service has no network client or fallback. It stores no page body. Fixture values and mapping
keys are checked recursively for bounded shape and contact or credential patterns before persistence.
The root, profile, source, research-field, UMG, and Talent shapes use exact key sets. UMG and Talent
sections enforce required scalar, list, false-only, and null-only types; cross-format and unknown keys
fail closed. Credential/contact key normalization covers snake case, kebab case, spaces, and camel
case. Non-finite JSON constants and percent-encoded fixture URLs are prohibited.

Each business-unit fixture profile names one exact synthetic target: business unit, result ID,
global identity, account name, and `.example` domain must all match the approved result before any
source, evidence, or brief row is written. Product extraction, current campaigns, existing ad angles,
and claims/restrictions are lists; brand voice, visual direction, target audience, hero product,
campaign objective, and creative opportunity are scalar text. `explicit_unknown` alone permits a
null value. Source and evidence identifiers are bounded canonical identifiers; every evidence list is
unique and refers only to a declared source. Type, identifier, and lineage failures occur before any
Phase 6A brief data is persisted.

Sources and evidence links each store a canonical snapshot, SHA-256, and byte length covering every
displayed denormalized field. One shared validator checks those snapshots, denormalized values, and
cross-record source lineage before listing, rendering, or recording a research-quality review. An
immutable integrity manifest binds the exact ordered source and evidence-link sets, their row
identities and hashes, and the brief hash. Immutability triggers reject ordinary deletes and updates
of these rows; the manifest's exact-set binding additionally detects an inconsistent or partial set —
an appended, missing, reordered, rebound, or altered row that no longer matches the recorded set —
and fails closed before listing, rendering, or review. As with the approval events, this detects
accidental corruption and partial mutation; it does not resist a trusted database owner who disables
the triggers and rewrites the manifest to agree with the changed rows; see
[Trusted local-state boundary](#trusted-local-state-boundary).

Both brief formats contain the complete Phase 6A foundation field set: product extraction, current
campaigns, brand voice, visual direction, existing ad angles, claims and restrictions, target
audience, hero product, campaign objective, and creative opportunity. They do not yet represent the
complete Phase 6B customer dossier. Every material Phase 6A research and business-unit value carries
basis, evidence references, confidence reason, uncertainty, freshness/currentness, and source
lineage. Current-campaign evidence is date checked. Missing dates remain explicit; stale, future-
dated, conflicting, and unknown values are never silently replaced by a newer claim.

The UMG section records product truth, packaging boundaries, hero rationale, UMG relevance,
format/channel metadata, and product-accuracy review needs. The Talent section is archetype-only and
records territory, channel, duration, usage, rights, roster, exclusivity/conflict, brand-safety, and
human-review requirements. It cannot name talent or claim availability, endorsement, clearance, or
likeness permission. Brief-family identifiers and version sequences are business-unit qualified, so
the same global identity has independent UMG and Talent version 1 families.

An `accepted` research-quality decision is valid only for the latest business-unit-qualified family
version when its state is `ready_for_research_quality_review` and every conflict entry is resolved.
Historical or conflicted versions remain readable. Reviewers may append `changes_requested` or
`rejected` to those versions because those decisions grant no positive authority; they cannot append
`accepted`. Every decision remains `research_quality_only` and grants no graph release, generation,
outreach, external-write, or other downstream authority.

## Additive SQLite v3 activation

The finalized Phase 4/5 store remains schema v2 when used by those services alone. Constructing
`EnrichmentService` is the Phase 6A activation boundary and deterministically calls the additive v3
initializer before any Phase 6A read or write. The normal `WebApplication` always constructs that
service, including when reopening an existing state file. Reopening a v3 file is idempotent.

Version 3 adds only:

- append-only `prospect_approval_events`;
- single-use `enrichment_runs`;
- immutable, integrity-bound `enrichment_sources`;
- immutable `campaign_brief_versions`;
- immutable, integrity-bound per-field `brief_evidence_links`;
- immutable exact-set `brief_integrity_manifests`; and
- append-only `brief_review_events`.

Approval and brief-review rows include the complete canonical event snapshot, hash, and byte length
described above. A local schema-v3 fixture file created by the earlier pending-review layout without
those columns is rejected without changing its version or rows. Because Phase 6A has not been
finalized or deployed, the supported recovery is to archive that local synthetic file and create a
fresh fixture state; the service does not silently trust, backfill, or destructively rewrite
authority history it cannot verify.

The initializer uses only `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS` and a schema metadata update
inside one transaction. It does not drop, truncate, rewrite, delete, or recreate an earlier table or row. Tests
activate v3 from a fresh v2 store, upgrade a populated actual Phase 4/5 state byte-for-byte, reopen
v3, and run SQLite integrity and foreign-key checks. This opt-in boundary preserves the frozen
isolated Phase 5 schema-version contract while ensuring no Phase 6A entry point can operate on v2.

## Loopback UI and shutdown

The existing loopback-only server adds business-unit-scoped approval, enrichment, source, brief,
lineage, limitation, and research-quality review views. Existing Host, CSRF, bounded form, escaping,
security-header, responsive table, focus, status, and error patterns remain in use. Loading and
terminal states are explicit. No Phase 6A scheduler, retry loop, watcher, background thread, or
worker exists; execution is synchronous and one-shot. Existing Phase 5 scheduler and request
threads retain their joined shutdown contract.

The proposing actor sees the brief and append-only history read-only with an explicit
separation-of-duty notice. Only a different allowed reviewer sees the research-quality review form.
Fixture/schema failures and local persistence/audit/internal failures receive distinct bounded
terminal metadata. No raw exception text is stored or rendered, and a failed single-use claim is
never retried automatically.

## Independent-review focus

- Recompute every hash and protection binding at the approval and claim boundaries.
- Test both revocation/claim commit orders, concurrent starts, concurrent supersessions, expiry at
  the exact instant, audit rollback, restart, and cross-business-unit non-disclosure.
- Confirm the immutability triggers reject ordinary updates and deletes, that the pure integrity
  validators reject inconsistent copied records, and that the manifest completeness check rejects an
  appended row — within the [Trusted local-state boundary](#trusted-local-state-boundary), which does
  not claim resistance to a trusted database owner who disables the triggers and recomputes digests.
- Confirm every Phase 6A construction path activates v3 before access and that finalized Phase 3–5
  tests remain unchanged and green.
- Confirm Phase 6A is presented as the synthetic brief foundation, not as comprehensive customer
  research or a graph handoff implementation.
- Inspect the complete diff for any real data, contact data, credential, external client, dynamic
  execution, scheduling, generation, likeness, advertising, outreach, deployment, or downstream-
  agent capability.
