# Phase 6 exact public-business proof

Phase 6 adds explicitly versioned real-source paths beside the frozen synthetic contracts. Each
route is limited to two user-authorized public-business alternatives, one business unit, one
opportunity filter, and the exact URLs in the repository manifest. It is not a crawler, a general
web research tool, an outreach system, or authority for any downstream action.

## Exact authorization

The canonical manifest is
`shared/prospecting-core/manifests/phase6-live-proof-source-plans.json`. Its plan hashes exclude only
the `source_plan_hash` field itself and use compact sorted-key UTF-8 JSON.

The historical `search-real-live-proof-v1` route remains in the manifest only so its durable records
can be validated. It is exhausted and cannot be selected by a new search.

| Historical order | Organization | Plan | Plan hash | Result | Account |
| --- | --- | --- | --- | --- | --- |
| 1 | Celsius Holdings, Inc. / CELSIUS | `phase6-live-proof-celsius-v1` | `d80f8ad433ad3d44f726b566539b1c1f59890087ca224d2ceed5fff636a318bf` | `result-real-v1-569b9ffde583d4bfc01e77ec` | `account-v1-13c98ba8965a8319d0cf7b3a` |
| 2 | Jazwares, LLC | `phase6-live-proof-jazwares-v1` | `4ff2de65b2ffda9d2e0e75ae8821c98eeadbe5a0b46b562dd3c022098d54aaeb` | `result-real-v1-92c0f82008a2bce7a52fbf8c` | `account-v1-f865f1fbf50fc7239a69cf17` |

The exhausted `search-real-live-proof-v2` route contains exactly these formerly authorized alternatives:

| Current order | Organization | Plan | Plan hash | Result | Account |
| --- | --- | --- | --- | --- | --- |
| 1 | 4ocean PBC | `phase6-live-proof-4ocean-v1` | `a5f8e5419da79dad34c9686a0bee4588e6c44d1b2bcebe764e0d13f7dbe67b06` | `result-real-v1-18bb9c083e574dfe499ea6e9` | `account-v1-7e159cfaee4fd296693c39ee` |
| 2 | Badia Spices | `phase6-live-proof-badia-v1` | `97657c3b4899af8a906314a734d8a89b389ed0367c9845b987713ec8c0ffa533` | `result-real-v1-24943333c909566d80517671` | `account-v1-b8c95659f44173d6390e3acb` |

The spent `search-real-live-proof-v3` route contains exactly these historical alternatives:

| Historical order | Organization | Plan | Plan hash | Canonical bytes | Result | Account |
| --- | --- | --- | --- | ---: | --- | --- |
| 1 | The Ultimate Umbrella Company, Inc. / Tuuci | `phase6-live-proof-tuuci-v1` | `bebc6febbce3d5d8b8c74afb5d23142b6aa820dbc287c526af106b20b646ad51` | 1474 | `result-real-v1-f2fd1af5859f5cbec1ef87f5` | `account-v1-319e190f1842af5a8c7c580c` |
| 2 | Miansai | `phase6-live-proof-miansai-v1` | `3cfcbdf8df08e9aadae4eb9a4575cd941b31d927a93f0f09cf2882efc63d7cf5` | 1382 | `result-real-v1-a70c37b08ed9c9ad23ff2b4a` | `account-v1-b368242de54e01b48b0c0ad3` |

All plans use `include_any=[product_photography, product_video]` and
`exclude=[ugc_ad]`. That filter is search intent, not evidence of demand, budget, buying intent, or
UGC aversion. Before a public read, both targets pass through the durable identity, suppression,
relationship, cooldown, duplicate, and re-engagement classifier. The v3 route permitted TUUCI first
and Miansai only after TUUCI became terminal without a candidate. A TUUCI candidate would have
stopped the pair.
No additional target, URL, retry, or substitution is authorized.

## Live proof outcome

The historical v1 alternatives are exhausted without a candidate. Jazwares failed closed at
robots-policy availability; its six failed source records contain zero source bytes, hashes, or
summaries. The one
permitted CELSIUS infrastructure recovery then ended as `source_read_failed_no_retry`; all six
approved source records failed with zero body and extracted-text bytes and no hashes or summaries.
The failed CELSIUS records intentionally retain no transport detail, so no narrower cause is claimed.

The v2 alternatives are also exhausted without a candidate. 4ocean ended as
`source_read_failed_no_retry` because none of its exact product sources produced successful official
product evidence. Badia reached local bundle validation once, then failed closed because a successful
source summary did not satisfy the prohibited-material scrub contract. Its immutable live row records
the already-terminal `candidate_creation_failed` class exposed by that run; the runtime already treats
that class as no-retry, and the post-proof correction maps an equivalent future post-read contract
failure to `source_read_failed_no_retry` without rewriting historical audit state.

The v3 route is spent. TUUCI's sole attempt ended as `interrupted_execution_recovered` without a
candidate and is not authorized to retry. Miansai's five exact sources succeeded and produced
immutable candidate `dcandidate-umg-0001` with content hash
`f3c197c28aa4c65fd829cea7112d7b5143a5e10487908abe9df877a74d279441` and byte length `37054`.
Mechanical integrity and security review passed, but the retained 1,000-character summaries were
navigation-heavy and did not substantiate the broader dossier categories. The user recorded the
exact terminal `changes_requested` decision for that candidate. No final dossier or package was
created.

The preserved gitignored SQLite state now contains three searches, six selected input result records,
eight approval events, eight runs, one immutable candidate, and one terminal review event. It contains
zero final dossier versions or packages. `PRAGMA integrity_check` returns `ok`, and
`PRAGMA foreign_key_check` returns no rows. No proof worker, server, or listener remains running.

None of the six attempted targets may retry. The default runtime has no executable real-proof route.
A new exact public business, source plan, URLs, classes, versioned route, and durable result-bound
authority are required before another live read. The full Phase 6 gate still requires a new candidate,
automated evidence review, genuine exact-version human acceptance, and atomic local release.

## Durable authority and execution

The runtime order is:

1. create one immutable real search for an authorized executable route and record its history-first outcomes;
2. derive result, global identity, and account identity from the exact committed plan;
3. record the user's already-provided exact goal authority as `user-goal-authority`, bound to the
   complete result projection, history, request, plan snapshot, plan hash, byte lengths, and seven-day
   validity window;
4. atomically claim that authority once;
5. close SQLite before any public request;
6. execute the exact plan through `PublicReader`;
7. reopen one short transaction and revalidate the run, cancellation state, claimed approval,
   result, account, history, and current repository manifest before inserting one immutable pending
   dossier candidate.

Target progression is monotonic. A later selected target cannot enter its authority flow until every
earlier selected target is terminal without a candidate or non-executable. A candidate stops the
pair; the later alternative cannot run. Once a later target enters that flow, an earlier target
cannot be reopened. The only recovery is one new approval and one new run
when the target's first and sole pre-recovery run was genuinely startup-recovered as interrupted. If
a later target has already entered authority, every later alternative must also be terminal with no
candidate. Eligibility requires the exact
append-only claim/recovery audit pair, no candidate or released package for the target, and no prior
recovery approval or run. Robots, access, redirect, destination, content, privacy, and budget
failures never qualify. The authority endpoint, new and idempotent claims, public-read preflight,
final candidate transaction, and visible controls all recheck the same rule.

Revoke-first denies a claim. A revocation after a committed claim is non-retroactive for only that
one synchronous run and never restores or reuses the approval. A committed claim consumes the
target's attempt. Cancellation that commits before the final insert creates no candidate and may
unlock the next exact alternative, but it never restores the same target. A source failure records
only its safe reason code and does not retry, add a URL, or choose a third business.

## Reader boundary

`mission_control/public_reader.py` uses only the Python standard library and accepts only validated
plans from the exact manifest. For every robots or source request it:

- requires an ASCII, credential-free, query-free HTTPS URL already in the plan;
- resolves the hostname afresh in a killable, joined process and rejects the complete answer set if
  any address is non-global, private, loopback, link-local, multicast, unspecified, reserved,
  carrier-grade NAT, documentation space, or IPv4-mapped IPv6;
- connects to a validated numeric address while retaining normal TLS hostname and certificate
  verification, then verifies the connected peer against the approved answer set;
- ignores proxy configuration and sends only fixed GET headers with identity encoding;
- follows at most three redirects and only when the exact target URL is independently present in the
  same plan, rechecking robots policy for each hop;
- fails closed for missing or malformed robots policy, denial, access control, unsupported status,
  attachment disposition, compression, ambiguous framing, unsupported or missing content type,
  invalid charset, header/body/text limits, or monotonic deadline exhaustion; and
- parses visible HTML text inertly, accepts only balanced paragraph prose inside semantic
  `main`/`article` or `role=main` content, suppresses header, navigation, footer, aside, menu, dialog,
  interactive controls, and equivalent accessibility-role chrome, and ignores executable/non-visible
  elements and attributes;
- rejects `text/plain`, malformed semantic boundaries, and retained text without at least two bounded
  qualifying sentences; removes safely recognizable visible URLs and contact values, fails closed on
  ambiguous Unicode contact routes or credential-like assignments, and never returns raw body or
  extracted text.

The durable source record contains only exact provenance, safe status, dates, content type, SHA-256
hashes, byte lengths, a bounded scrubbed summary, and conflict state. These summaries are evidence
inventory only: source class and prose shape do not create or complete business-research claims.
Claim-level verification is required before a broader category may be populated. Generic product
pages do not establish audience, market, reputation, demand, or buying intent. A failed record contains
no body hash, text hash, content summary, or transport detail.

## Dossier and release contract

The exhausted proof routes and their immutable artifacts remain `schema_version=2` and
`synthetic=false`; the v1 synthetic schemas also remain unchanged. Their inventory-only validators
still recompute the exact two mechanical claims and nine gaps for historical compatibility.

Every future newly authorized real route must instead configure the additive
`schema_version=3` callable claim projector before search or any public read. Its exact output covers all nine
nonmechanical categories with ordered source attempts, binds observed values to hashed substrings of
successful scrubbed source summaries, and permits only product-photo/video inferences backed by
verified current premises plus the canonical denial of expressed demand, budget, buying intent, and
UGC aversion. The candidate embeds that canonical projection and an automated evidence-review
attestation. Candidate and package validators independently re-derive all claims, gaps,
qualification, graph projection, and hashes and rebind the request ID and age policy; filter intent,
source class, and prose shape cannot create a claim.
The machine-readable schemas accept both frozen v2 and additive v3 artifacts without changing the
synthetic v1 contracts.

The pending dossier, final dossier, and package all carry an authority object in which
`local_data_handoff_only` is true and generation, contact, outreach, external writes, agent
invocation, and deployment are false. The package is a deterministic canonical projection of the
exact accepted dossier; every read revalidates that projection.

A real candidate cannot be accepted by a simulated Mission Control actor. It remains
`pending_research_quality_review` until the user supplies one exact-version `accept`, `changes`, or
`reject` decision. Only that explicit decision is recorded as `user-human-reviewer`. Acceptance
atomically creates one immutable final local dossier, one graph-ready package, and one audit event.

Live runtime databases and dossiers remain gitignored local state. They must never be committed.

## Verification

Focused coverage is in:

- `tests/prospecting/test_phase6_real_contract.py`;
- `tests/prospecting/test_phase6_claim_projection.py`;
- `tests/prospecting/test_phase6_public_reader.py`; and
- `tests/prospecting/test_phase6_real_runtime.py`.

The tests use injected deterministic transports and make no live requests. They cover exact manifest
hashes and tampering, history protection, no-demand semantics, source-record privacy, destination and
TLS binding, robots and redirect handling, budgets and joined shutdown, SQLite transaction
separation, cancellation and revocation ordering, restart durability, pending review, canonical
package release, historical-route execution denial, versioned route compatibility, single-attempt
claim and reader ownership, candidate-stops-failover ordering, single-recovery exhaustion,
append-only recovery evidence, terminal-state mismatch rejection, and the loopback approval packet.
The current warning-strict repository runner passes `385/385`; both official skill validators pass.
V3 Chromium QA at desktop and `375x812` verified TUUCI-then-Miansai ordering, authority control
only on the first eligible target, no horizontal overflow, visible keyboard focus, announced validation
errors, a clean normal-flow console, and loopback-only requests without invoking a public read. A
claim-verified fixture replay additionally rendered the projection/review hashes, verified-claim and
explicit-gap counts, inert authority, and cross-business-unit denial without a public read.
