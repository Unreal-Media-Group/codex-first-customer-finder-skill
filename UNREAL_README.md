# Unreal Prospecting Skills Fork

The `unreal` branch preserves the upstream `first-customer-finder` skill and adds two separate local, dry-run Phase 1 skills:

- `unreal-media-brand-prospector` for Unreal Media Group creative-service prospects
- `unreal-talent-campaign-prospector` for rights-managed brand and agency campaign opportunities

They share deterministic identity, history, deduplication, validation, scoring, and report utilities. They do not connect to Unreal OS or Supabase, generate creative or likenesses, or send outreach.

Phase 2 contains independently reviewed and finalized design contracts for a future Unreal OS
prospecting data model. Phase 3 (finalized at `8ea7c16`) adds a synthetic, loopback-only Manual
Mission Control review app with deterministic fixtures; it is not production authentication.
Phase 4 (independently reviewed and finalized at `b340b2c013b2fd026cd0be3aff24429196bd9be8`) registers one fixture-only
`brand-prospecting-agent` worker with durable local gitignored SQLite run/attempt/output/audit and
human-review-task state, a zero cost cap, timeout, retry cap, cancellation, idempotency, and
startup recovery. Phase 5 adds a disabled-by-default, explicitly human-configured, weekly UTC local
shadow loop with immutable campaign rotation, durable occurrences, history-aware deduplication,
health and local alerts, pause/disable controls, and pending human review. Phase 5 is implemented
locally and pending independent review/finalization. No external-system connection or migration,
real-data API, production scheduler, generic cron, live research, creative, likeness use,
advertisement, or outreach has been built or authorized; Phase 6 remains unauthorized.

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
- [Architecture](docs/ARCHITECTURE.md)
- [Safe upstream synchronization](docs/UPSTREAM_SYNC.md)
- [Program roadmap](docs/specs/UNREAL_PROSPECTING_SKILLS_PROGRAM_ROADMAP.md)
- [Unreal OS prospecting data contract](docs/specs/UNREAL_OS_PROSPECTING_DATA_CONTRACT.md)
- [Prospecting RLS and service boundary](docs/specs/UNREAL_OS_PROSPECTING_RLS_AND_SERVICE_BOUNDARY.md)
- [Future prospecting migration plan](docs/specs/UNREAL_OS_PROSPECTING_MIGRATION_PLAN.md)

The root `README.md`, `package.json`, original skill directory, and original installer intentionally remain aligned with upstream to reduce future merge conflicts. Unreal-specific installation is local to this fork unless a separately named package is created later.
