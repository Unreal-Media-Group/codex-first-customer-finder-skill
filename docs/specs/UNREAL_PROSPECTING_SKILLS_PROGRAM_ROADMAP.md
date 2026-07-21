# Unreal Prospecting Skills Program Roadmap

**Program:** Unreal Media Group and Unreal Talent prospecting system  
**Repository strategy:** Fork of `Kappaemme-git/codex-first-customer-finder-skill`  
**Integration target:** Unreal OS  
**Current program state:** Phase 3 independently reviewed and finalized at commit `8ea7c16`. Phase 4 repository-local implementation is explicitly authorized and implemented locally, pending independent review/finalization. Phase 5 is not authorized.

**Operating principle:** Skills define repeatable research behavior; deterministic scripts validate and normalize data; Unreal OS later stores, schedules, observes, and approves the work.

**Phase 1 checkpoint:** Completed and verified at commit `f77236a`. Phase 1 remains the frozen JSON and
local-skill compatibility baseline for Phase 2 design.

---

## 1. Program Goal

Build two separate but related prospecting skills:

1. `unreal-media-brand-prospector`
2. `unreal-talent-campaign-prospector`

The skills will share a deterministic prospecting core, but they will not share the same ideal-customer profile, scoring model, rights rules, or qualification thresholds.

The complete system will eventually:

1. Run manually or on an approved schedule.
2. Find brands or agencies that fit the selected business unit.
3. Optionally filter by vertical, industry, geography, buyer type, trigger, or talent category.
4. Support open discovery when no vertical is selected.
5. Load prior prospect history before evaluating candidates.
6. Avoid presenting previously discovered companies as new.
7. Detect legitimate re-engagement opportunities when a prior company has a new trigger.
8. Preserve source-backed evidence and uncertainty.
9. Save results into Unreal OS.
10. Route qualified prospects to deeper brand research.
11. Prepare custom campaign concepts and preview assets.
12. Require human approval before asset generation and again before outreach.
13. Track results so the qualification system can improve over time.

---

## 2. Architectural Principle

The final system is a hybrid:

```text
Unreal OS / Mission Control
    campaign configuration, review, approval, reporting

Supabase
    durable account identities, history, signals, scores, outreach state

Registered runner agent
    executes an approved prospecting campaign

Repo-owned Codex skills
    define UMG and Unreal Talent research behavior

Deterministic scripts
    normalize identities, validate inputs, classify duplicates, render reports

Human approver
    decides whether deeper research, preview generation, or outreach may proceed
```

The skills are not standalone autonomous sales bots. They are governed worker playbooks that later plug into Unreal OS.

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
- Future speculative product-ad previews

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

Design, but do not yet schedule, durable prospecting state inside Unreal OS.

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

**Status:** Repository-local implementation explicitly authorized and implemented locally, pending
independent review/finalization (see `docs/PHASE_4_REGISTERED_AGENT.md`). The local worker is
manual, synthetic, fixture-only, and loopback-only with durable local SQLite state. External
systems, real data, schedules, recurring loops, creative, likeness, advertisements, outreach, and
Phase 5 remain prohibited.

## Objective

Move skill execution behind a governed Unreal OS worker.

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

**Status:** Not authorized.

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
10 to 15 deep research records
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

# Phase 6: Brand Enrichment and Campaign Brief Agent

## Objective

Research only prospects that a human approved.

## Deliverables

- Official-site research
- Product extraction
- Current campaign extraction
- Brand voice
- Visual direction
- Existing ad angles
- Claims and restrictions
- Target audience
- Hero product
- Campaign objective
- Source-backed creative opportunity
- UMG brief format
- Unreal Talent brief format
- Rights and conflict review status
- Human approval before generation

## Exit Gate

The agent produces a defensible brief with source lineage and does not generate assets until approval.

---

# Phase 7: Speculative Preview Generation

## Objective

Create a controlled preview for approved prospects.

## UMG Flow

```text
Approved brief
    ↓
Still-frame concepts
    ↓
Product-accuracy QA
    ↓
Human concept approval
    ↓
10-second preview
    ↓
Proof and compliance QA
```

## Unreal Talent Flow

```text
Approved campaign brief
    ↓
Generic or authorized talent concept
    ↓
Rights and conflict check
    ↓
Storyboard approval
    ↓
Preview generation
    ↓
Frame-by-frame human review
```

## Required Controls

- Product accuracy
- Logo accuracy
- Packaging accuracy
- No invented claims
- No false endorsement
- No unauthorized likeness
- No implication that the brand commissioned the work
- Concept-preview disclosure
- Asset lineage
- Generation cost
- Human approval

## Exit Gate

No preview may enter outreach until it passes human review.

---

# Phase 8: Outreach Drafting and Human Send

## Objective

Prepare personalized outreach without autonomous sending.

## Deliverables

- Email draft
- DM draft
- Brand-direct version
- Agency version
- Source-based opener
- Approved preview link
- Human approval screen
- Manual send status
- Reply tracking
- Follow-up proposal
- Suppression updates

## Exit Gate

- The exact recipient, message, and asset package are approved by a human.
- Instagram cold DMs remain manual unless a permitted official mechanism is later verified.
- Every send and reply is logged.

---

# Phase 9: Outcome Learning and Optimization

## Objective

Improve targeting using real outcomes instead of model confidence alone.

## Metrics

- Qualified rate
- Duplicate rate
- Re-engagement rate
- Approval rate
- Preview-generation rate
- Positive reply rate
- Meeting rate
- Proposal rate
- Win rate
- Cost per qualified prospect
- Cost per meeting
- Performance by vertical
- Performance by trigger
- Performance by score band
- Performance by source
- Performance by offer
- Performance by business unit

## Rule

The system may recommend scoring changes, but it may not automatically rewrite qualification, legal, rights, or brand-safety policies.

---

## 4. Recommended Development Order

1. Complete Phase 0.
2. One-shot Phase 1 in a dedicated Codex build thread.
3. Review the completed repository manually and with a separate read-only reviewer.
4. Run a Phase 1.1 correction pass.
5. Freeze the Phase 1 output contracts.
6. Move the approved skills into or reference them from Unreal OS.
7. Design Phase 2 schemas.
8. Build the manual Mission Control workflow.
9. Build the registered agent.
10. Enable a bounded manual run.
11. Enable the weekly shadow loop.
12. Add enrichment.
13. Add preview generation.
14. Add human-approved outreach.
15. Optimize from measured results.

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

The program is complete when Unreal can:

1. Configure an open or focused prospecting campaign.
2. Find unique evidence-backed brands or agencies.
3. Reliably recognize prior discoveries.
4. Reconsider old prospects only for legitimate new triggers.
5. Review every candidate in Mission Control.
6. Approve deeper research.
7. Generate controlled campaign previews.
8. Approve the exact outreach package.
9. Send manually or through a specifically approved permitted channel.
10. Measure outcomes and improve the system without weakening safety or rights controls.
