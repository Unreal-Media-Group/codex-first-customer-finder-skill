# Unreal Prospecting Skills Program Roadmap

**Program:** Unreal Media Group and Unreal Talent lead-intelligence system
**Repository strategy:** Fork of `Kappaemme-git/codex-first-customer-finder-skill`
**Downstream compatibility target:** A governed agent graph, including a possible future Unreal OS
consumer. This repository does not implement or modify Unreal OS.
**Current program state:** Phase 4 independently reviewed and finalized at current-lineage commit `b7892aed5ad9ab0dcb9d900d95d6971332582ef8`. Phase 5 independently reviewed and finalized at commit `1ddba9037f1db7e1b58098b83f7bf4caeafd7e8d`. Phase 6A's synthetic brief foundation is independently reviewed and finalized at commit `e8d2546647603905c0b97a21f335535b3a98cc17`. Phase 6B contract-first repository work is independently reviewed and finalized at commit `86f83f1a94862d573f30da70a47760556b045309`. The exact public-read proof's additive v2 contract, bounded reader, durable runtime, pre-live independent review, complete test suite, and loopback browser QA pass. The historical CELSIUS/Jazwares pair is terminal without a candidate and may not retry. The current exact 4ocean/Badia pair is separately authorized under the same bounded no-action controls and preserves historical request validation. No additional target, URL, retry, or substitution is authorized. Automated evidence QA and genuine exact-version human acceptance still require a current-pair candidate. Creative generation, outreach, CRM writes, orchestration, and outcome optimization are downstream responsibilities outside this repository.

**Operating principle:** The skills find, qualify, deduplicate, and comprehensively research potential
customers. Deterministic code validates the evidence and emits a versioned, graph-ready lead-
intelligence package. Separately authorized downstream agents may consume that package; this
repository does not invoke or implement them.

**Phase 1 checkpoint:** Completed and verified at commit `f77236a`. Phase 1 remains the frozen JSON and
local-skill compatibility baseline for Phase 2 design.

---

## 1. Program Goal

Build two separate but related prospecting skills:

1. `unreal-media-brand-prospector`
2. `unreal-talent-campaign-prospector`

The skills will share a deterministic prospecting core, but they will not share the same ideal-customer profile, scoring model, rights rules, or qualification thresholds.

This repository-owned lead-intelligence producer will:

1. Run manually or on an approved schedule.
2. Find brands or agencies that fit the selected business unit.
3. Optionally filter by vertical, industry, geography, buyer type, trigger, or talent category.
4. Support open discovery when no vertical is selected.
5. Load prior prospect history before evaluating candidates.
6. Avoid presenting previously discovered companies as new.
7. Detect legitimate re-engagement opportunities when a prior company has a new trigger.
8. Preserve source-backed evidence and uncertainty.
9. Research every human-approved prospect across the material public business-information
   categories defined in Phase 6.
10. Identify source-backed public business people, roles, and contact paths as one part of that
    broader research.
11. Record brand and creative evidence as factual observations and source references, without
    generating or copying campaign assets.
12. Emit one reviewed, versioned, machine-readable intelligence package with stable graph nodes,
    edges, evidence lineage, confidence, freshness, and explicit unknowns.
13. Stop at the handoff boundary so separately governed agents can perform creative, outreach,
    storage, or learning work.

---

## 2. Architectural Principle

This repository is the upstream intelligence node in a larger, separately governed graph:

```text
campaign + validated history
             |
             v
repo-owned finder and qualification skills
             |
             v
human-approved comprehensive public research
             |
             v
validated lead-intelligence dossier
             |
             v
versioned graph-ready handoff package
             |
             v
human release boundary
             |
             +--> downstream creative/brand agent       (outside this repo)
             +--> downstream contact/outreach agent     (outside this repo)
             +--> downstream CRM/orchestrator           (outside this repo)
             +--> downstream outcome-learning agent     (outside this repo)
```

Mission Control, the registered local worker, and the shadow loop are repository-local control and
review surfaces for the finder. They do not make this repository an autonomous sales system. The
handoff contract is integration-neutral: a future Unreal OS implementation may consume it, but this
repository neither writes to Unreal OS nor builds the downstream graph.

---

## 3. Shared Versus Separate Responsibilities

### Shared Prospecting Core

Both skills should share:

- Campaign configuration schema
- Domain and company-name normalization
- Social-handle normalization
- Parent-company and subbrand identity handling
- Local prospect-history contract
- Duplicate classification
- Re-engagement and cooldown logic
- Evidence quality rules
- Observed-versus-inferred labels
- Comprehensive research-category coverage and explicit gap states
- Public business-person, role, and contact-path provenance
- Brand-asset and campaign source references without asset generation
- Versioned graph-node, graph-edge, and evidence-lineage output
- Output schemas
- Report rendering
- Test fixtures
- Validation commands
- Upstream synchronization and repository policy

### Unreal Media Group-Specific Logic

The UMG skill should focus on:

- Repeatable creative need
- Product and visual fit
- Paid-social and short-form activity
- Product launches and seasonal drops
- Content-volume needs
- UGC and product-imagery opportunities
- Creative quality gaps
- Budget indicators
- Reachable marketing or founder path
- Public brand-system, campaign, content, and visual-asset evidence

### Unreal Talent-Specific Logic

The Unreal Talent skill should focus on:

- Brands and agencies capable of larger campaigns
- Existing endorsement, sponsorship, or talent usage
- Campaign and talent fit
- Launch, partnership, event, and sponsorship triggers
- Rights territory
- Roster compatibility
- Brand-safety and category conflicts
- Agency and brand relationship mapping
- Public business decision-maker and agency-role mapping
- Talent archetypes when no approved roster is loaded
- Strict prevention of unauthorized named-talent or likeness output

---

# Phase 0: Fork, Governance, and Safe Upstream Synchronization

## Objective

Create a maintainable fork that can absorb upstream improvements while preserving Unreal-specific work.

## Deliverables

- GitHub fork under Noah's account
- `origin` pointing to Noah's fork
- `upstream` pointing to the original repository
- `main` reserved as the upstream mirror
- `unreal` used for Unreal-specific development
- Root `AGENTS.md`
- `.agents/` compatibility and prompting structure modeled on Unreal OS
- `scripts/check-upstream.sh`
- `scripts/sync-upstream.sh`
- `docs/UPSTREAM_SYNC.md`
- Append-only local execution log
- Clear rule that upstream sync happens before repository-changing sessions

## Session-Start Behavior

Every Codex session should run:

```bash
bash scripts/check-upstream.sh
```

Before any repository edit, Codex should run:

```bash
bash scripts/sync-upstream.sh
```

The synchronization script must:

- Verify that `origin` and `upstream` exist
- Verify that `upstream` points to the expected original repository
- Refuse to run in a dirty worktree
- Fetch upstream changes
- Fast-forward local `main` to `upstream/main`
- Merge updated `main` into `unreal`
- Never force, reset, stash, push, or change remotes
- Abort and report any conflict
- Make no feature changes
- Log what happened

A read-only session may continue after the check script. A file-changing session must stop when safe synchronization cannot complete.

## Exit Gate

Phase 0 is complete when a clean test clone can:

1. Verify remotes.
2. Detect upstream changes.
3. Update `main` only by fast-forward.
4. Merge `main` into `unreal`.
5. Stop safely on a dirty worktree or conflict.
6. Preserve the original upstream skill.

---

# Phase 1: Local, Dry-Run Prospecting Skills

## Objective

Build and validate the two Unreal-specific skills without Supabase, scheduling, external writes, ad generation, or outreach.

## Phase 1A: Shared Prospecting Core

### Deliverables

- Campaign configuration schema
- Prospect-history schema
- Prospect result schema
- Run report schema
- Domain normalizer
- Company-name normalizer
- Social identity normalizer
- Duplicate classifier
- Cooldown and re-engagement evaluator
- Score validators
- JSON validator
- Markdown and HTML report support
- Synthetic history and candidate fixtures
- Deterministic tests

### Required Duplicate Statuses

- `new_prospect`
- `existing_no_new_trigger`
- `existing_new_trigger`
- `existing_active_outreach`
- `existing_client`
- `existing_partner`
- `suppressed`
- `possible_duplicate_needs_review`
- `distinct_subbrand`
- `parent_company_relationship`
- `agency_brand_overlap`

### Exit Gate

- A repeated run does not return prior brands as new.
- A valid new trigger can return an existing company as re-engagement.
- A missing or invalid history file fails closed.
- Duplicate and rejection decisions remain in the audit output.

## Phase 1B: Unreal Media Group Skill

### Deliverables

- UMG-specific `SKILL.md`
- UMG positioning reference
- UMG ideal-customer profile
- Discovery signals
- UMG scoring model
- Disqualifiers
- Evidence rules
- Campaign examples
- UMG sample report

### Exit Gate

The skill can:

- Run without a vertical
- Run with one vertical
- Run with several verticals
- Return only evidence-backed qualified prospects
- Distinguish current triggers from general fit
- Separate observed facts from model inference
- Stop before creative generation or outreach

## Phase 1C: Unreal Talent Skill

### Deliverables

- Unreal Talent-specific `SKILL.md`
- Brand ICP
- Agency ICP
- Campaign trigger framework
- Rights and roster rules
- Brand-safety rules
- Talent scoring model
- Relationship mapping
- Talent sample report

### Exit Gate

The skill can:

- Find brands, agencies, or both
- Run without a vertical
- Filter by optional vertical or talent category
- Recommend a talent archetype without naming a real person
- Reject unauthorized named-talent output
- Record likely rights territory
- Flag conflict and brand-safety issues
- Stop before likeness use, creative generation, or outreach

## Phase 1D: Evaluation and Regression Protection

### Deliverables

- Synthetic evaluation dataset
- Expected outcomes
- Triggering tests for both skill descriptions
- False-positive and false-negative cases
- Duplicate regression cases
- Unauthorized-talent regression case
- Upstream original-skill regression check
- Installation tests

### Exit Gate

- Existing upstream skill still installs and works.
- Both new skills trigger correctly.
- Reports render safely.
- All deterministic tests pass.
- Any remaining uncertainty is documented rather than hidden.

---

# Phase 2: Unreal OS Data Contracts

**Current status:** Phase 2 contract design is complete and independently finalized on 2026-07-17. This
closes design only. No SQL, migration, Supabase connection, Unreal OS implementation, service, UI,
runner, schedule, creative, advertisement, likeness use, or outreach is authorized by finalization.

## Objective

Design compatibility contracts for durable prospecting state that a future Unreal OS consumer could
implement. The contracts do not make this repository an Unreal OS worktree or authorize OS changes.

## Deliverables

- Account model
- Account identity model
- Parent and subbrand relationships
- Brand and agency relationships
- Prospecting campaign model
- Discovery event model
- Signal model
- Score snapshot model
- Contact model
- Outreach event model
- Suppression and cooldown model
- Run and evidence references
- Migration plan
- RLS and authorization design
- Service-boundary specification

## Important Rule

A company account, a contact, a discovery event, a public signal, and an outreach event are different records. Do not collapse them into one flat `leads` table.

## Exit Gate

- Schemas are reviewed.
- RLS is reviewed.
- No live production migration occurs without the correct phase approval.
- Phase 1 JSON maps cleanly to the proposed data model.
- Independent review findings are resolved and the three Phase 2 contract documents are explicitly
  finalized; authored drafts alone do not close the phase.

---

# Phase 3: Manual Mission Control Feature

**Status:** Independently reviewed and finalized at commit `8ea7c16` with deterministic synthetic
fixtures. This status does not authorize external connections, scheduling, creative or likeness
generation, outreach, or deployment.

## Objective

Create the human-facing sales feature before enabling autonomous schedules.

## Deliverables

Inside Sales/Growth:

- Prospecting Campaigns page
- New campaign form
- Open or vertical-focused configuration
- Run summary
- New prospects queue
- Duplicate and re-engagement queue
- Rejection view
- Prospect detail
- Evidence viewer
- Merge-identity action
- Suppression action
- Approve-for-deeper-research action
- Assignment and notes
- Run cost and error display

Inside Agent Registry and Run History:

- Prospecting worker record
- Manual run records
- Input version
- Skill version
- Sources
- Outputs
- Costs
- Errors
- Stop reason

## Exit Gate

A human can create a campaign, inspect dry-run results, resolve duplicates, and approve or reject prospects without any outbound action.

---

# Phase 4: Registered Prospecting Agent

**Status:** Independently reviewed and finalized at commit
`b7892aed5ad9ab0dcb9d900d95d6971332582ef8` (see `docs/PHASE_4_REGISTERED_AGENT.md`). The local
worker remains synthetic, fixture-only, loopback-only, zero-cost, and durable. Phase 5 extends its
control plane without changing this historical acceptance gate.

## Objective

Move skill execution behind a governed repository-local worker whose contract can later be consumed
by a separately implemented orchestrator.

## Deliverables

- `brand-prospecting-agent`
- UMG and Talent business-unit selection
- Manual run endpoint
- Bounded run state
- Timeout
- Cost cap
- Retry cap
- Cancellation
- Durable output persistence
- Idempotency
- Failure recovery
- Versioned skill reference
- Audit log
- Human review task creation

## Exit Gate

- Manual runs work end to end.
- A retry does not duplicate discovery records.
- The agent cannot send messages or generate creative.
- Database and runner failures are visible and resumable.

---

# Phase 5: Scheduled Loop in Shadow Mode

**Status:** Independently reviewed and finalized at commit
`1ddba9037f1db7e1b58098b83f7bf4caeafd7e8d` (see `docs/PHASE_5_SHADOW_LOOP.md`). The implementation is synthetic, fixture-only, loopback-only,
zero-cost, shadow-only, and disabled by default. It grants no Phase 6 authority; Phase 6A has a
separate exact-result durable approval gate.

## Objective

Run prospecting on an approved schedule while preserving human control.

## Deliverables

- Weekly loop configuration
- Campaign rotation
- Optional vertical selection
- Open-discovery weeks
- Budget cap
- Prospect cap
- Stop rule
- Health status
- Failure alert
- Duplicate-rate reporting
- Shadow-mode review
- Pause and disable controls

## Initial Funnel

```text
100 raw discoveries
    ↓
30 normalized and plausible candidates
    ↓
10 to 15 candidates approved for deep research
    ↓
5 to 10 human-approved prospects
```

These are starting caps, not permanent performance targets.

## Exit Gate

- The loop runs on schedule.
- It never sends outreach.
- It never generates creative automatically.
- It stops when history cannot be loaded.
- It does not repeatedly rediscover the same brands as new.
- Noah can pause or disable it.

---

# Phase 6: Comprehensive Customer Intelligence and Graph Handoff

**Status:** Phase 6A's synthetic fixture brief foundation is independently reviewed and finalized at
commit `e8d2546647603905c0b97a21f335535b3a98cc17`. Phase 6B contract-first schemas,
documentation, synthetic fixtures, deterministic validation, and tests are independently reviewed
and finalized at commit `86f83f1a94862d573f30da70a47760556b045309`. The governed synthetic
dossier runtime now proves additive SQLite v4 activation, durable history-first filtering, derived
account identity, exact source-plan approval, single-use claims, immutable candidates, terminal
review, and inert graph-package release. The additive real-source contract, versioned exact
two-target manifest, bounded standard-library reader, and result-bound runtime are implemented and
independently reviewed under separate authorization. The historical CELSIUS/Jazwares route is
terminal without a candidate and may not retry. The current exact 4ocean/Badia route is authorized
with the same product-photography or product-video filter excluding UGC ads, existing budgets,
read-only public HTTPS boundary, durable local recording, and no-action controls. No additional
target, URL, retry, or substitution is authorized. The full Phase 6 exit gate requires a
current-route candidate to pass automated review, receive genuine exact-version human acceptance,
and release its local package atomically.

## Objective

For each exact human-approved prospect, research the potential customer as completely as material,
lawful, public business evidence permits and emit one reviewed, versioned lead-intelligence package.
Finding a person or route to contact is one research category, not the whole product.

## Meaning of Comprehensive

“Comprehensive” means pursuing every applicable category below within a declared source, time, and
cost boundary. It does not mean claiming omniscience, collecting private or sensitive information,
or filling gaps with guesses. Every category must be populated or explicitly marked `not_found`,
`not_applicable`, `conflicted`, `stale`, or `unknown`. Every material statement must distinguish
observation from inference and carry evidence, confidence, uncertainty, and freshness.

Research may use official sites and other intentionally public business sources only. It must
respect access controls and source terms. It may not use authenticated scraping, private profiles,
data brokers, paid enrichment, guessed contact patterns, credentials, sensitive traits, or copied
page bodies.

All retrieved content is untrusted evidence, never an instruction to an agent or permission to use a
tool. A Phase 6B implementation must enforce public HTTP(S) source policy, reject credential-bearing
or private/internal destinations, revalidate redirects, bound time, bytes, content types, and
extraction depth, and refuse executable downloads. It stores bounded extracted claims and source
references rather than raw page bodies. Source text cannot alter system policy, tool permissions,
the research plan, review state, or downstream authority.

## Phase 6A: Synthetic Brief Foundation

Phase 6A proves the durable, exact-result human-approval boundary, fixture-only evidence lineage,
immutable business-unit brief, and separate research-quality review described in
`docs/PHASE_6_ENRICHMENT_BRIEF.md`. Its existing campaign-brief fields are a deliberately narrow
foundation for the broader dossier. It performs no live research and does not yet satisfy the full
Phase 6 completion gate.

## Phase 6B: Comprehensive Public-Source Dossier

Phase 6B begins contract-first with synthetic coverage tests, then may add live public reads only
after separate authorization. For each approved lead, the dossier covers all applicable categories:

- **Identity and relationships:** canonical business identity, domains, aliases, public handles,
  founding and ownership facts when public, parent company, subbrands, subsidiaries, agencies,
  partners, locations, operating markets, and public leadership or team structure.
- **Company and commercial context:** business model, maturity, headcount/size, funding, revenue, or
  other financial indicators when reliably public; products, services, pricing or offer structure,
  distribution, sales channels, customer types, procurement or agency signals, and market position.
- **Operations and digital footprint:** official sites, public social and marketplace presence,
  apps, ecommerce and physical channels, geographic reach, public technology/vendor indicators,
  publishing cadence, and material customer-experience or operational signals.
- **Audiences, market, and reputation:** target segments, customer use cases, audience behavior,
  category trends, competitors and comparables, public reviews or sentiment, awards, material
  controversies, and other source-backed reputation or market context.
- **Brand and messaging:** stated value proposition, voice, themes, claims, restrictions, visual
  system, content patterns, and credential-free references to relevant public logos, brand images,
  campaigns, and other creative assets. The repository records observations and source references;
  it does not copy or generate assets.
- **Activity and signals:** launches, campaigns, ads, promotions, partnerships, sponsorships,
  events, hiring, expansion, public business news, regulatory or litigation facts material to the
  opportunity, and dated change signals.
- **Opportunity and fit:** observed needs, service fit, likely objectives, gaps, buying or budget
  indicators, competitors or comparables, risks, and recommended angles. Inferences remain labeled
  and cannot be promoted to fact.
- **Public people and contact paths:** intentionally public business people or decision-making
  roles, title/function, account relationship, responsibility, public professional profile, and
  provenance-backed business contact point or official route. Private/personal details, guessed
  emails, private phone numbers, personal accounts, sensitive traits, and paid enrichment are
  prohibited.
- **Governance and history:** prior discoveries, duplicate and relationship state, re-engagement
  basis, suppressions, cooldowns, rights, talent and brand-safety constraints, conflicts,
  limitations, and unresolved questions.
- **Evidence coverage:** source inventory, dates, observation times, original-source status,
  corroboration, conflicts, freshness, research cutoff, and explicit coverage gaps.
- **Additional material facts:** any other public business fact that could materially change
  qualification, strategy, safety, or a downstream agent's work, represented as a typed claim under
  the same provenance, privacy, freshness, and uncertainty rules rather than forced into prose.

## Graph-Ready Lead-Intelligence Package

The reviewed dossier is emitted as stable, versioned, machine-readable local data. It contains
enough structure for downstream agents to consume facts without scraping prose:

- nodes for organizations, brands, public business people or roles, public business contact points,
  products or services, audiences or markets, campaign or asset references, signals, opportunities,
  restrictions, and evidence;
- edges for parent/subbrand, agency/client, partner, affiliated-with, responsible-for,
  contact-path-for, offers, targets, campaign-for, evidence-supports, duplicate, prior-discovery,
  and re-engagement relationships;
- stable identities and idempotency keys, business-unit scope, dossier version, review state, and
  source lineage; and
- claim-level basis, confidence, uncertainty, source date, observation time, and freshness.

The broadly consumable package contains public business identities, roles, and contact references,
not unrestricted contact values. Any separately authorized business email, business phone, or
equivalent point remains in a restricted contact projection with its own provenance, review,
visibility, suppression, retention, and audit rules. Creative or analytics consumers do not receive
that restricted projection. Public-person research is purpose-limited to business identity,
responsibility, account relationship, and an approved contact route; unrelated personal biography
is not part of the dossier.

This repository emits and validates the package locally. It does not create the larger graph,
invoke downstream agents, write to Unreal OS or a CRM, or grant any downstream action authority.

## Phase 6 Exit Gate

- One approved lead can produce a comprehensive dossier in which every applicable category is
  populated or has an explicit gap state.
- Every material fact, inference, person, contact path, asset reference, opportunity, and
  relationship is provenance-linked and freshness-aware.
- Identity, duplicate, history, suppression, rights, and business-unit protections remain intact.
- The exact dossier version and graph package receive human research-quality review before release.
- A downstream consumer can parse the package without relying on rendered prose.
- No creative generation, likeness use, outreach, CRM/external write, deployment, or downstream
  agent invocation occurs.

---

# Downstream Graph Consumers — Outside This Repository

The former preview-generation, outreach, and outcome-optimization phases are not remaining phases
of this lead-intelligence repository. They are separate graph consumers with separate owners,
repositories, permissions, tests, and human approval boundaries:

- a brand or creative agent may use the dossier's brand evidence and asset references to propose or
  generate concepts, images, or video;
- a campaign or talent agent may use the opportunity, audience, rights, and conflict research;
- a contact or outreach agent may use approved public business people and contact paths to verify a
  recipient, draft a message, or request a send;
- a CRM or orchestration layer may persist and route the released package; and
- an outcome-learning agent may return measured results for future qualification analysis.

None of those consumers is implemented, invoked, or authorized here. A released research package
is information, not permission to generate, contact, send, store externally, or act.

---

## 4. Recommended Development Order

1. Preserve the finalized Phase 0–5 foundations and frozen Phase 1/2 contracts.
2. Preserve the independently finalized Phase 6A synthetic brief foundation at
   `e8d2546647603905c0b97a21f335535b3a98cc17`.
3. Under the authorized contract-first Phase 6B boundary, define the comprehensive dossier and graph-handoff schema, including category gap states,
   claim-level provenance, stable identities, and public-business-contact restrictions.
4. Prove the expanded contract with synthetic fixtures, deterministic validation, and both business
   units before enabling any live source.
5. Integrate the frozen contract with the durable local approval, history, candidate-review, and
   exact-version release boundary without weakening v1 compatibility.
6. Obtain separate authorization for the exact Phase 6B source classes, network-read boundary,
   budgets, retention rules, and stop conditions.
7. Implement bounded public-source research by extending the existing skill and evidence patterns.
8. Validate the complete path from discovery and history filtering through reviewed dossier and
   local graph-package emission.
9. Independently review privacy, evidence quality, identity, business-unit isolation, failure
   behavior, and the no-action handoff boundary.
10. Stop. Any creative, outreach, external persistence, orchestration, or outcome-learning work moves
   to a separately authorized downstream project.

---

## 5. Parallel Codex Thread Rules

Use two Codex threads with different authority.

### Build Thread

- May edit the fork.
- Must run upstream check and sync before changes.
- Must follow the repository `AGENTS.md`.
- Implements only the authorized phase.
- Runs tests and reports the diff.
- Does not push unless Noah separately approves it.

### Prompt Architect and Reviewer Thread

- Read-only.
- Must not edit the same worktree.
- Reviews specifications, builder summaries, diffs, and test results.
- Produces the next prompt or correction prompt.
- Identifies missing requirements and unsafe scope expansion.
- Never claims an implementation check passed unless evidence is provided.
- Does not authorize future phases.

When using parallel work in the Codex app, isolate the threads with worktrees when both need repository access. If the second thread is only writing prompts, keep it read-only and do not let it touch the build worktree.

---

## 6. Program Completion Definition

This repository's program is complete when the lead-intelligence workflow can:

1. Configure an open or focused prospecting campaign.
2. Find unique evidence-backed brands or agencies.
3. Reliably recognize prior discoveries.
4. Reconsider old prospects only for legitimate new triggers.
5. Review every candidate in Mission Control.
6. Approve the exact prospect and result for deeper research.
7. Produce a comprehensive, source-backed dossier with explicit unknown, stale, conflicting, and
   not-applicable states.
8. Identify public business people, decision roles, and provenance-backed contact paths without
   private or guessed data.
9. Represent the customer, related entities, products, audiences, campaigns, evidence, people,
   opportunities, restrictions, and history as stable graph nodes and edges.
10. Human-review and release one exact, versioned, machine-readable lead-intelligence package.
11. Make that package available at a local handoff boundary for a separately governed consumer,
    without this repository generating assets, contacting anyone, writing to an external system, or
    invoking a downstream agent.
