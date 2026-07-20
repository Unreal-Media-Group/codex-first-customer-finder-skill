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
implement the same boundary, but Phase 3 contains no database client, migration, network client,
worker, scheduler, or fallback from fixtures to production. Fixture actor selection is a policy
simulation for local review, not authentication.

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
| Original upstream skill | `first-customer-finder/` |

Skill report scripts are thin delegates. The installer places the shared core once at `unreal-prospecting-core`, preventing two business skills from drifting.

## Data contracts

Campaign, controlled roster, history, prospect result, and run report schemas separate company identity, discovery event, public signal, score snapshot, duplicate decision, rejection decision, qualified/re-engagement result, and summary. Runtime validators enforce score/freshness, history, source lineage, shortlist eligibility, handoff readiness, roster compatibility, rights-review, privacy, and summary invariants without adding a dependency. That separation maps cleanly to later durable storage without designing or implementing Phase 2.

## Identity limits

The standard library has no public suffix list. Domain normalization handles common service subdomains and a bounded set of common multi-part suffixes. Unknown suffixes, shared storefronts, franchises, and ambiguous company relationships must be reviewed rather than silently merged.

## Claude compatibility

`.agents/skills/` is canonical. Phase 1 does not create manually duplicated `.claude/skills` copies because that would create drift. A deterministic compatibility generator can be evaluated later if a real Claude installation requirement appears.
