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
reviewed and finalized at `1ddba9037f1db7e1b58098b83f7bf4caeafd7e8d`. No external-system connection or migration,
real-data API, production scheduler, generic cron, live research, creative, likeness use,
advertisement, or outreach has been built or authorized. Phase 6A adds a pending-review, manually
approved, one-shot, zero-cost synthetic `.example` fixture enrichment and source-linked campaign
brief as the narrow foundation for the customer dossier. Phase 6B will, if separately authorized,
add comprehensive public-source research and a graph-ready handoff package. It is not implemented
or authorized. Creative generation, outreach, external persistence, orchestration, and outcome
optimization are downstream responsibilities outside this repository rather than later phases here.

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
- [Architecture](docs/ARCHITECTURE.md)
- [Safe upstream synchronization](docs/UPSTREAM_SYNC.md)
- [Program roadmap](docs/specs/UNREAL_PROSPECTING_SKILLS_PROGRAM_ROADMAP.md)
- [Unreal OS prospecting data contract](docs/specs/UNREAL_OS_PROSPECTING_DATA_CONTRACT.md)
- [Prospecting RLS and service boundary](docs/specs/UNREAL_OS_PROSPECTING_RLS_AND_SERVICE_BOUNDARY.md)
- [Future prospecting migration plan](docs/specs/UNREAL_OS_PROSPECTING_MIGRATION_PLAN.md)

The root `README.md`, `package.json`, original skill directory, and original installer intentionally remain aligned with upstream to reduce future merge conflicts. Unreal-specific installation is local to this fork unless a separately named package is created later.
