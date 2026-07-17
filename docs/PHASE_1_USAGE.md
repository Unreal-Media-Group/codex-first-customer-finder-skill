# Phase 1 local usage

## Safety boundary

These workflows use local JSON and public-source research only. They do not connect to Unreal OS, Supabase, OpenClaw, an external CRM, a scheduler, or an outreach channel. They do not generate ads or real-person likenesses.

## Validate campaigns

```bash
python3 shared/prospecting-core/scripts/validate_campaign.py fixtures/prospecting/campaigns/umg-open-discovery.json
python3 shared/prospecting-core/scripts/validate_campaign.py fixtures/prospecting/campaigns/talent-open-discovery.json
```

No vertical is required for open discovery. One or several verticals may be supplied for filtered campaigns. The examples cover UMG open, fitness/activewear, apparel; Talent open, sports brands, agencies; and re-engagement.

Named-talent mode is fail-closed. When enabled, the campaign must provide explicit verticals, talent categories, campaign channels, and an `approved_roster_path` resolving to a version-1 controlled roster. A result may name only one approved, reviewable entry whose category, approved brand categories, restrictions, territory, channels, exclusivity state, and data freshness pass deterministic checks. `rights_status` must still require internal review. Without that controlled roster, use `none` or `archetype_only`.

## History and deduplication

History must load before qualification. It records canonical identity, domains, aliases, handles, parents/subbrands, agency relationships, discovery/campaign/score/decision history, outreach, client/partner/suppression state, cooldown, prior signals, and rejection reasons.

```bash
python3 shared/prospecting-core/scripts/classify_duplicate.py \
  fixtures/prospecting/candidates/reengagement.json \
  fixtures/prospecting/history/prospect-history.json \
  fixtures/prospecting/campaigns/reengagement.json
```

Existing companies are never new. Re-engagement requires an enabled campaign, new dated evidence and reason, an expired cooldown, and a source not already in history. Test-only cooldown overrides are accepted only through an explicit unit-test function argument and are prohibited in run reports.

## Render validated reports

```bash
python3 shared/prospecting-core/scripts/render_report.py fixtures/prospecting/expected/umg-sample-run.json /tmp/umg-report.md
python3 shared/prospecting-core/scripts/render_report.py fixtures/prospecting/expected/umg-sample-run.json /tmp/umg-report.html
python3 shared/prospecting-core/scripts/render_report.py fixtures/prospecting/expected/talent-sample-run.json /tmp/talent-report.md
python3 shared/prospecting-core/scripts/render_report.py fixtures/prospecting/expected/talent-sample-run.json /tmp/talent-report.html
```

Reports preserve duplicate/rejected outcomes, evidence links and dates, observed/inferred labels, score breakdowns, uncertainty, next actions, limitations, validation errors, and future handoff readiness. Text is escaped and links must be credential-free HTTP(S).

Every raw candidate must have a preserved result decision. Summary counts are recomputed from those decisions, and qualified results must meet the configured score and evidence-age thresholds.

## Test

```bash
bash tests/prospecting/run_all.sh
```

Tests use only synthetic fixtures and temporary directories/repositories.

## Install

See `UNREAL_README.md` for original-only, UMG-only, Talent-only, both-Unreal, and all-skills commands. No global install is performed by the Phase 1 build.

## Limitations and deferred phases

- Public research quality still depends on an agent following the skill instructions; Phase 1 tests deterministic contracts, not live web results.
- Registrable-domain normalization is conservative and lacks a complete public suffix list.
- JSON is local test state, not a concurrent database.
- Rights and brand-safety review is preliminary and never a legal conclusion.
- Unreal OS/Supabase data contracts, manual Mission Control, runner, schedule, enrichment, creative/likeness generation, and outreach belong to later phases.
