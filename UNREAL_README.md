# Unreal Prospecting Skills Fork

The `unreal` branch preserves the upstream `first-customer-finder` skill and adds two separate local, dry-run Phase 1 skills:

- `unreal-media-brand-prospector` for Unreal Media Group creative-service prospects
- `unreal-talent-campaign-prospector` for rights-managed brand and agency campaign opportunities

They share deterministic identity, history, deduplication, validation, scoring, and report utilities. They do not connect to Unreal OS or Supabase, schedule work, generate creative or likenesses, or send outreach.

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
- [Architecture](docs/ARCHITECTURE.md)
- [Safe upstream synchronization](docs/UPSTREAM_SYNC.md)
- [Program roadmap](docs/specs/UNREAL_PROSPECTING_SKILLS_PROGRAM_ROADMAP.md)

The root `README.md`, `package.json`, original skill directory, and original installer intentionally remain aligned with upstream to reduce future merge conflicts. Unreal-specific installation is local to this fork unless a separately named package is created later.
