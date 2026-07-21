# Prospecting repository architecture

## Boundary

Phase 1 is a local research and validation layer. It prepares stable JSON for later integration but contains no database, network writer, scheduler, runner, creative generator, likeness workflow, CRM, or outreach system.

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
| Original upstream skill | `first-customer-finder/` |

Skill report scripts are thin delegates. The installer places the shared core once at `unreal-prospecting-core`, preventing two business skills from drifting.

## Data contracts

Campaign, controlled roster, history, prospect result, and run report schemas separate company identity, discovery event, public signal, score snapshot, duplicate decision, rejection decision, qualified/re-engagement result, and summary. Runtime validators enforce score/freshness, history, source lineage, shortlist eligibility, handoff readiness, roster compatibility, rights-review, privacy, and summary invariants without adding a dependency. That separation maps cleanly to later durable storage without designing or implementing Phase 2.

## Identity limits

The standard library has no public suffix list. Domain normalization handles common service subdomains and a bounded set of common multi-part suffixes. Unknown suffixes, shared storefronts, franchises, and ambiguous company relationships must be reviewed rather than silently merged.

## Claude compatibility

`.agents/skills/` is canonical. Phase 1 does not create manually duplicated `.claude/skills` copies because that would create drift. A deterministic compatibility generator can be evaluated later if a real Claude installation requirement appears.
