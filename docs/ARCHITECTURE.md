# Prospecting repository architecture

## Boundary

This repository owns one upstream role: find, filter, qualify, and research potential customers, then
emit a reviewed lead-intelligence package for separately governed graph consumers. It does not
implement or modify Unreal OS, create the larger graph, invoke downstream agents, generate creative,
or contact anyone.

Phase 1 is the local discovery, history, and validation layer. It prepares stable JSON for later
research and integration but contains no database, network writer, scheduler, runner, creative
generator, likeness workflow, CRM, or outreach system.

```text
Campaign JSON + local history JSON
              |
              v
    validate configuration/history ---- fail closed
              |
              v
  public-source research by selected skill
              |
              v
 normalize identity -> classify duplicate/relationship/re-engagement
              |
              v
 business-specific evidence review + weighted scoring
              |
              v
 validated result + rejection/duplicate audit records
              |
              v
     JSON -> escaped Markdown / HTML
              |
              v
          human review only
```

Phase 3 adds a separate local review surface without changing that frozen Phase 1 pipeline:

```text
ordinary loopback POST form
           |
           v
bounded parser + CSRF + fixture actor/BU policy
           |
           v
existing Phase 1 campaign validator
           |
           v
MissionControl application service
           |
           v
FixtureRepository interface implementation
  | immutable campaign versions
  | append-only manual run facts
  | deterministic BU projections
  | append-only governed review events
           |
           v
escaped server-rendered HTML on 127.0.0.1
```

The UI depends on the repository boundary rather than fixture arrays. A later governed adapter can
implement the same boundary. Fixture actor selection is a policy simulation for local review, not
authentication.

Phase 4 places that same fixture adapter behind one locally registered, manually triggered worker
without changing the frozen Phase 1 pipeline or the Phase 3 review model:

```text
human loopback request (campaign detail form)
           |
           v
bounded server validation + CSRF + fixture actor/BU policy
           |
           v
registered agent lookup (brand-prospecting-agent, server-owned BU→skill binding)
           |
           v
durable logical run creation (SQLite, idempotency key + request fingerprint)
           |
           v
bounded one-shot cooperative worker attempt (timeout, zero cost cap, retry cap, cancellation)
           |
           v
non-visible prepared evaluation from the existing Phase 3 deterministic fixture adapter
           |
           v
post-executor cost, shape, and bounded-snapshot trust validation
           |
           v
atomic output + attempt + audit + human-review-task transaction
           |
           v
exactly-once same-process Phase 3 projection after durable success
           |
           v
human-visible run/attempt/recovery state on 127.0.0.1
```

Worker-run, attempt, output, review-task, and audit state is durable in a local, gitignored SQLite
file with additive schema versioning. Every attempt replays the run's durable validated input
(hash-checked configuration snapshot, frozen-validator revalidation, server-owned skill binding and
integrity manifest), so manual retries and recovery work after a real process restart, and the
durable result snapshot keeps outputs and pending review tasks inspectable when the process-local
Phase 3 queues are gone. Every durable output read verifies the stored snapshot bytes against its
byte-length and SHA-256 manifest before parsing or rendering. Prepared snapshots reuse existing
business-unit/prospect-scoped append-only review events, so their effective decision matches the
live projection without mutating review history. One repository-owned non-mutating validator checks
the concrete prepared-run/audit/prospect shape and executor consistency both before durable success
and before process-local commit, while accepting the no-duplicate `existing_run` form. Invalid cost
or executor output is converted into a completed durable failure attempt before it can reach SQLite
output persistence. Durable disposition rendering prioritizes suppression, then governed decision,
then protection/pending fallbacks. Fixture
evaluation stays non-visible until the durable success transaction
commits; only that winning success projects the Phase 3 run, prospects, idempotency entry, and
manual-run audit. Failed, cancelled, rejected, and rolled-back attempts leave those collections
unchanged. The loopback server handles requests on bounded, joined threads — no
scheduler, poller, or background worker — so a human can cancel a running cooperative attempt over
HTTP; the process-local fixture repository is serialized behind a reentrant lock. Phase 4 still
contains no database server, external migration, network client, credential, creative, likeness, or
outreach capability, and no fallback from fixtures to production.

Phase 5 adds one deliberately narrow scheduler around the finalized Phase 4 service. It is not a
parallel runner framework and does not change the Phase 1 schemas or Phase 2 contracts:

```text
human configures immutable campaign rotation (disabled)
           |
           v
explicit enable / pause / resume / disable (conditional SQLite transition + audit)
           |
           v
one joined non-daemon scheduler thread evaluates one weekly UTC boundary
           |
           v
atomic occurrence claim (stable schedule+occurrence idempotency; no catch-up storm)
           |
           v
load + validate synthetic seed history and durable same-BU Phase 4 output history
           |
           v
RegisteredAgentService (manual fingerprint preserved; scheduled run/link/audit atomic)
           |
           v
durable shadow occurrence + health + local alert + duplicate rate
           |
           v
pending human-review task only
```

The scheduler starts with the loopback process but schedules default to `disabled`; only Noah's
explicit local fixture action can enable one. Cadence is fixed to one weekday/hour/minute in UTC,
not generic cron. Each evaluation claims at most one due occurrence and advances directly to a
future weekly boundary, preventing catch-up storms. Campaign configurations are copied into an
immutable schedule-version snapshot and hash-checked again by the registered agent before execution.
History is validated before each attempt and then applied by the existing Phase 1 duplicate
classifier so a durable prior identity cannot return to the new queue. Missing, corrupt, ambiguous,
directory-valued, unreadable, or inconsistent history blocks before worker execution and settles
the claim with a local alert without terminating the scheduler. Durable history outputs share one
aggregate snapshot-byte budget, and transient settlement/output-read failures are reconciled before
new work. The local prospect cap is at most
15 and the inherited cost cap remains exactly zero. Duplicate rate counts only rows carrying a
governed identity/history classification over all output rows; ordinary qualification, protection,
and cap rejections are not duplicates merely because they are not new.

SQLite schema version 2 adds schedule versions, campaign rotation snapshots, schedule state,
occurrence claims, schedule audits, and local alerts without altering or deleting a Phase 4 table or
row. Run creation and occurrence linking share one transaction. Startup recovery closes an
interrupted occurrence: a previously committed successful output is preserved only after the normal
length, SHA-256, JSON, result-shape, and duplicate-metric validation; otherwise the occurrence fails
closed with an audit and local alert, the linked run becomes terminally failed, and its pending review
task is invalidated. Scheduled linking is Noah-only and requires an exact business-unit, campaign,
and idempotency match. Every linked scheduled run rejects manual retry before another attempt claim,
including while its occurrence is still claimed; only unlinked Phase 4 manual runs retain manual
retry. Noah alone sees
schedule mutation controls; Rob and Dan receive truthful business-unit-scoped read-only pages. The one
scheduler thread uses an injected clock and stop event, and is stopped and joined before server
shutdown completes. It performs no live research, network request, approval, enrichment, creative,
likeness, advertising, outreach, or Phase 6 action.

Phase 6A adds a separate manual one-shot service; it does not extend the scheduler or worker:

```text
pending durable review task + exact fixture result
           |
           v
separate human records append-only, seven-day, result-bound approval
           |
           v
atomic current-leaf + task/run/output/configuration/current-protection recheck
           |
           v
single-use enrichment claim (identical replay is read-only)
           |
           v
validate repository-owned .example metadata (no network fallback)
           |
           v
atomic immutable sources + evidence links + exact-set manifest + BU brief + audit
           |
           v
separate human research-quality review (no generation authority)
```

`EnrichmentService` is the schema-v3 activation boundary. The finalized Phase 4/5 store initializes
to version 2 when those services are used alone; every Phase 6A construction path calls the additive
v3 initializer before its first read or write, and normal `WebApplication` construction always
activates it. Version 3 adds append-only approval/review events, a one-run-per-approval claim,
immutable integrity-bound source metadata, immutable brief versions, field-level evidence links,
an exact ordered-set integrity manifest, complete canonical snapshots/hashes/lengths for every
authority-bearing approval and review field, and immutability triggers. Earlier tables, rows,
identifiers, snapshots, and hashes are not rewritten. A pre-correction local v3 fixture file without
the final authority-event integrity columns is rejected unchanged and must be archived and recreated;
unverifiable authority history is never silently trusted or backfilled.
Reopening v3 is idempotent.

The local SQLite file and the OS account that owns it are trusted. These colocated snapshots, hashes,
and lengths, together with the immutability triggers, detect accidental corruption and inconsistent or
partial mutation and fail closed for ordinary application access; they are not independent
authentication of authority. A trusted database owner who disables the triggers can rewrite an event
(or source, link, brief, or manifest) and recompute its colocated digest, and that self-consistent
rewrite is accepted by design. Phase 6A does not defend against a malicious local database owner and
adds no HMAC, signature, secret, key management, second ledger, or external trust service; the
lead-finder product does not require that resistance. See the trusted local-state boundary in
`docs/PHASE_6_ENRICHMENT_BRIEF.md`.

The approval is bound to the task, logically linked worker run and output, verified configuration
hash, exact result bytes/hash, global identity, business unit, current governed protection hash,
reviewer separation, scope, effective time, and exact seven-day expiry. A stale or unavailable
process-local governed projection is non-actionable and requires a new worker result. `BEGIN
IMMEDIATE` serializes approval supersession and enrichment claiming; a process-local active-owner
registry distinguishes a live synchronous claim from crash recovery only inside the supported single
Python process and threaded loopback server. It is not an independent-process coordination mechanism;
multiple processes sharing one state file are unsupported. Rejected or unknown governed decisions
fail closed, while a valid process-local approval only removes that block and never replaces the
durable Phase 6A approval. Every synthetic fixture profile is bound to the approved result's exact
business unit, result ID, global identity, account name, and `.example` domain. Research-field values
have exact list/text/null rules, and bounded unique evidence identifiers must resolve to declared
sources before persistence. Brief families are business-unit qualified. `accepted` requires the
latest ready, conflict-free family version; historical/conflicted versions remain readable and may
receive non-accepting review events. Approval and review event bindings and same-brief supersession
chains are revalidated before detail, history, claim, or rendering. Claim-first
permits only the claimed attempt to finish; revoke-first blocks a claim. There is no automatic retry,
recurring work, external research client, credential, real data, page-body storage, creative,
likeness, advertisement, outreach, deployment, or downstream-agent capability. Brief acceptance is
explicitly research-quality status only. The full contract and independent-review checks are in
`docs/PHASE_6_ENRICHMENT_BRIEF.md`.

The planned Phase 6B extends that governed research boundary; it does not add a downstream worker:

```text
exact human-approved lead + validated identity/history/protection state
           |
           v
bounded reads from authorized public business source classes
           |
           v
URL/content boundary + untrusted-source extraction
           |
           v
claim-level observations, inferences, dates, freshness, conflicts, and gaps
           |
           v
comprehensive customer dossier across the roadmap research categories
           |
           v
stable graph nodes + relationships + evidence lineage
           |
           v
exact-version human research-quality review
           |
           v
local machine-readable lead-intelligence package; stop
```

The dossier treats public contact research as one category alongside company identity and
relationships, commercial context, products and services, audiences and markets, brand and campaign
evidence, public asset references, activity and change signals, opportunities, competitors, risks,
rights, and history. Each applicable category is populated or carries an explicit gap state. Every
material value distinguishes observation from inference and carries source lineage, confidence,
uncertainty, and freshness.

All Phase 6B source material is untrusted data. The future reader must enforce public HTTP(S)
destinations, redirect revalidation, private/internal-address denial, and bounded time, size, content
type, and extraction depth. It retains bounded claims and references, not raw page bodies or
executable content. Source text cannot become a system instruction, tool call, policy override,
review event, or downstream action.

The graph-ready package is integration-neutral. It can represent organizations, brands, public
business people or roles, public business contact points, products or services, audiences, campaign
or asset references, signals, opportunities, restrictions, and evidence, plus their supported
relationships. The general package carries public contact identities and references, not unrestricted
contact values; any separately authorized business email, business phone, or equivalent point stays
in a restricted projection that creative and analytics consumers cannot read. The package contains
stable identities and versioning, not executable downstream instructions. Phase 6B is not
implemented or authorized. A future consumer may store or route the released package, but that is
separate work in its own repository and authority boundary.

## Ownership

| Concern | Canonical location |
| --- | --- |
| UMG research judgment | `.agents/skills/unreal-media-brand-prospector/` |
| Talent/agency/rights judgment | `.agents/skills/unreal-talent-campaign-prospector/` |
| Deterministic contracts and code | `shared/prospecting-core/` |
| Synthetic evaluation data | `fixtures/prospecting/` |
| Regression and safety checks | `tests/prospecting/` |
| Phase 3 local application | `apps/prospecting-mission-control/` |
| Phase 3 synthetic review catalog | `fixtures/prospecting/phase3/` |
| Phase 4 registered worker + durable store | `apps/prospecting-mission-control/mission_control/agent.py`, `store.py` |
| Phase 4 synthetic worker scenarios | `fixtures/prospecting/phase4/` |
| Phase 4 local runtime state (gitignored) | `apps/prospecting-mission-control/local_state/` |
| Phase 5 weekly shadow control plane | `apps/prospecting-mission-control/mission_control/shadow.py`, `store.py` |
| Phase 5 synthetic history seeds | `fixtures/prospecting/phase5/` |
| Phase 6A approval, enrichment, brief, and review service | `apps/prospecting-mission-control/mission_control/enrichment.py`, `store.py` |
| Phase 6A synthetic brief metadata | `fixtures/prospecting/phase6/` |
| Original upstream skill | `first-customer-finder/` |

Skill report scripts are thin delegates. The installer places the shared core once at `unreal-prospecting-core`, preventing two business skills from drifting.

## Data contracts

Campaign, controlled roster, history, prospect result, and run report schemas separate company identity, discovery event, public signal, score snapshot, duplicate decision, rejection decision, qualified/re-engagement result, and summary. Runtime validators enforce score/freshness, history, source lineage, shortlist eligibility, handoff readiness, roster compatibility, rights-review, privacy, and summary invariants without adding a dependency. That separation maps cleanly to later durable storage without designing or implementing Phase 2.

## Identity limits

The standard library has no public suffix list. Domain normalization handles common service subdomains and a bounded set of common multi-part suffixes. Unknown suffixes, shared storefronts, franchises, and ambiguous company relationships must be reviewed rather than silently merged.

## Claude compatibility

`.agents/skills/` is canonical. Phase 1 does not create manually duplicated `.claude/skills` copies because that would create drift. A deterministic compatibility generator can be evaluated later if a real Claude installation requirement appears.
