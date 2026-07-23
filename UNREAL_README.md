# Unreal Prospecting Skills Fork

The `unreal` branch preserves the upstream `first-customer-finder` skill and adds two separate local, dry-run Phase 1 skills:

- `unreal-media-brand-prospector` for Unreal Media Group creative-service prospects
- `unreal-talent-campaign-prospector` for rights-managed brand and agency campaign opportunities

They share deterministic identity, history, deduplication, validation, scoring, and report utilities. They do not connect to Unreal OS or Supabase, generate creative or likenesses, or send outreach.

This repository owns the upstream lead-intelligence node: find and filter potential customers,
research approved leads across all material public business-information categories, and emit a
reviewed machine-readable dossier for a larger governed agent graph. Public business people and
contact paths are one category within that dossier. Brand and campaign images are recorded as
source references and factual observations, not generated assets. Downstream agents may later use
the dossier for creative, campaign, outreach, CRM, or learning work, but those agents are neither
implemented nor invoked here. This repository does not implement or modify Unreal OS.

Phase 2 contains independently reviewed and finalized design contracts for a future Unreal OS
prospecting data model. Phase 3 (finalized at `8ea7c16`) adds a synthetic, loopback-only Manual
Mission Control review app with deterministic fixtures; it is not production authentication.
Phase 4 (independently reviewed and finalized at current-lineage commit `b7892aed5ad9ab0dcb9d900d95d6971332582ef8`) registers one fixture-only
`brand-prospecting-agent` worker with durable local gitignored SQLite run/attempt/output/audit and
human-review-task state, a zero cost cap, timeout, retry cap, cancellation, idempotency, and
startup recovery. Phase 5 adds a disabled-by-default, explicitly human-configured, weekly UTC local
shadow loop with immutable campaign rotation, durable occurrences, history-aware deduplication,
health and local alerts, pause/disable controls, and pending human review. Phase 5 is independently
reviewed and finalized at `1ddba9037f1db7e1b58098b83f7bf4caeafd7e8d`. Except for the exact
read-only Phase 6 proof described below, no external-system connection or migration, real-data API,
production scheduler, generic cron, broader live research, creative, likeness use, advertisement,
or outreach has been built or authorized. Phase 6A is independently reviewed and
finalized at `e8d2546647603905c0b97a21f335535b3a98cc17`; it adds a manually approved, one-shot,
zero-cost synthetic `.example` fixture enrichment and source-linked campaign brief as the narrow
foundation for the customer dossier. Phase 6B's contract-first repository-local schemas,
documentation, synthetic fixtures, deterministic validation, and tests now define comprehensive
dossiers, pre-search filters, durable history, and a graph-ready local handoff; this contract-first
work is independently reviewed and finalized at `86f83f1a94862d573f30da70a47760556b045309`
and now has a governed synthetic runtime that activates additive SQLite v4, runs durable history-first
filters, binds exact source-plan approval, produces immutable eleven-category candidates, and releases
one inert local package only after terminal exact-version review. This runtime still does not satisfy
the full Phase 6 exit gate. A separately authorized additive v2 real-source path uses a bounded
standard-library public reader, produces a scrubbed pending dossier, and keeps human acceptance as
the final release gate. Its historical CELSIUS/Jazwares and 4ocean/Badia routes are exhausted without
a candidate and cannot retry. The TUUCI/Miansai v3 route is also spent: TUUCI ended without a
candidate, and Miansai's exact candidate received a human `changes_requested` decision because
navigation-heavy summaries did not substantiate broader dossier categories. No package was released.
The reader now accepts bounded semantic main/article paragraph prose, suppresses site chrome, and
fails closed on weak or unsafe retained evidence. Source summaries remain evidence inventory and do
not populate broader category claims without claim-level verification. The additive real v3
contract now requires a configured skill/tool claim projector before search or any public read,
binds each observed claim to an exact hashed summary substring, derives category gaps and bounded
product-photo/video inferences, and embeds an automated evidence-review attestation for exact
candidate and package revalidation. Frozen v2 proof artifacts remain read-compatible. The default
runtime has no executable real-proof route. Coolibar's separately authorized v4 route consumed its
one exact attempt and failed closed as `source_read_failed_no_retry` when the reviewed claim
projector could not establish an exact retained product-fit premise; it produced no candidate and
cannot retry. The full Phase 6 gate therefore requires a newly authorized exact target/source plan
and versioned route, followed by a v3 candidate that passes automated evidence QA, genuine
exact-version human acceptance, and atomic local package release.
Creative generation, outreach, external persistence,
orchestration, and outcome optimization are downstream responsibilities outside this repository.

## Local installation

From the `unreal` branch checkout:

```bash
node scripts/install-unreal.js --skill original
node scripts/install-unreal.js --skill umg
node scripts/install-unreal.js --skill talent
node scripts/install-unreal.js --skill unreal
node scripts/install-unreal.js --skill all
```

Use `--skills-dir PATH` for an isolated location. The original `scripts/install.js` flow remains unchanged.

## Documentation

- [Phase 1 usage](docs/PHASE_1_USAGE.md)
- [Phase 3 Manual Mission Control](docs/PHASE_3_MISSION_CONTROL.md)
- [Phase 4 Registered Agent](docs/PHASE_4_REGISTERED_AGENT.md)
- [Phase 5 Shadow Loop](docs/PHASE_5_SHADOW_LOOP.md)
- [Phase 6A Synthetic Lead-Intelligence Brief Foundation](docs/PHASE_6_ENRICHMENT_BRIEF.md)
- [Phase 6B Dossier Contract-First Foundation](docs/PHASE_6B_DOSSIER_CONTRACT.md)
- [Phase 6 Governed Dossier Runtime](docs/PHASE_6_DOSSIER_RUNTIME.md)
- [Phase 6 Exact Public-Business Proof](docs/PHASE_6_REAL_PUBLIC_PROOF.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Safe upstream synchronization](docs/UPSTREAM_SYNC.md)
- [Program roadmap](docs/specs/UNREAL_PROSPECTING_SKILLS_PROGRAM_ROADMAP.md)
- [Unreal OS prospecting data contract](docs/specs/UNREAL_OS_PROSPECTING_DATA_CONTRACT.md)
- [Prospecting RLS and service boundary](docs/specs/UNREAL_OS_PROSPECTING_RLS_AND_SERVICE_BOUNDARY.md)
- [Future prospecting migration plan](docs/specs/UNREAL_OS_PROSPECTING_MIGRATION_PLAN.md)

The root `README.md`, `package.json`, original skill directory, and original installer intentionally remain aligned with upstream to reduce future merge conflicts. Unreal-specific installation is local to this fork unless a separately named package is created later.
