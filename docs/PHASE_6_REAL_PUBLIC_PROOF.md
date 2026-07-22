# Phase 6 exact public-business proof

Phase 6 adds one explicitly versioned real-source path beside the frozen synthetic contracts. It is
limited to two user-authorized public-business alternatives, one business unit, one opportunity
filter, and the exact URLs in the repository manifest. It is not a crawler, a general web research
tool, an outreach system, or authority for any downstream action.

## Exact authorization

The canonical manifest is
`shared/prospecting-core/manifests/phase6-live-proof-source-plans.json`. Its plan hashes exclude only
the `source_plan_hash` field itself and use compact sorted-key UTF-8 JSON.

| Order | Organization | Plan | Plan hash | Result | Account |
| --- | --- | --- | --- | --- | --- |
| 1 | Celsius Holdings, Inc. / CELSIUS | `phase6-live-proof-celsius-v1` | `d80f8ad433ad3d44f726b566539b1c1f59890087ca224d2ceed5fff636a318bf` | `result-real-v1-569b9ffde583d4bfc01e77ec` | `account-v1-13c98ba8965a8319d0cf7b3a` |
| 2 | Jazwares, LLC | `phase6-live-proof-jazwares-v1` | `4ff2de65b2ffda9d2e0e75ae8821c98eeadbe5a0b46b562dd3c022098d54aaeb` | `result-real-v1-92c0f82008a2bce7a52fbf8c` | `account-v1-f865f1fbf50fc7239a69cf17` |

Both plans use `include_any=[product_photography, product_video]` and
`exclude=[ugc_ad]`. That filter is search intent, not evidence of demand, budget, buying intent, or
UGC aversion. Before a public read, both targets pass through the durable identity, suppression,
relationship, cooldown, duplicate, and re-engagement classifier. No third target can be derived or
substituted.

## Durable authority and execution

The runtime order is:

1. create one immutable v2 real search and record both history-first outcomes;
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
earlier selected target is terminal or non-executable. Once a later target enters that flow, an
earlier target ordinarily cannot be reopened. The only recovery is one new approval and one new run
when the target's first and sole pre-recovery run was genuinely startup-recovered as interrupted. If
a later target has already entered authority, every later alternative must also be terminal with no
candidate. Eligibility requires the exact
append-only claim/recovery audit pair, no candidate or released package for the target, and no prior
recovery approval or run. Robots, access, redirect, destination, content, privacy, and budget
failures never qualify. The authority endpoint, new and idempotent claims, public-read preflight,
final candidate transaction, and visible controls all recheck the same rule.

Revoke-first denies a claim. A revocation after a committed claim is non-retroactive for only that
one synchronous run and never restores or reuses the approval. Cancellation that commits before the
final insert creates no candidate and remains an ordinary new-authority path, not an infrastructure
recovery. A source failure records only its safe reason code and does not retry, add a URL, or choose
a third business.

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
- parses visible HTML text inertly, ignores executable/non-visible elements and attributes, removes
  visible URLs and contact values (including values split across HTML nodes), rejects credential-like
  material, and never returns raw body or extracted text.

The durable source record contains only exact provenance, safe status, dates, content type, SHA-256
hashes, byte lengths, a bounded scrubbed summary, and conflict state. A failed record contains no
body hash, text hash, content summary, or transport detail.

## Dossier and release contract

Real dossiers and packages are `schema_version=2` and `synthetic=false`; the v1 synthetic schemas
remain unchanged. Every dossier has the canonical eleven categories. Each category contains typed,
evidence-linked, freshness-aware claims or an explicit gap. At least one successful approved product
source is required before the target can be described as a low-confidence potential product-creative
fit. The qualification always states that demand evidence was not found.

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
- `tests/prospecting/test_phase6_public_reader.py`; and
- `tests/prospecting/test_phase6_real_runtime.py`.

The tests use injected deterministic transports and make no live requests. They cover exact manifest
hashes and tampering, history protection, no-demand semantics, source-record privacy, destination and
TLS binding, robots and redirect handling, budgets and joined shutdown, SQLite transaction
separation, cancellation and revocation ordering, restart durability, pending review, canonical
package release, monotonic two-target progression, single-recovery exhaustion, append-only recovery
evidence, terminal-state mismatch rejection, and the loopback approval packet. The current
warning-strict repository runner passes `340/340`; both official skill validators pass. Real Chrome
QA at desktop and `375x812` verifies the one-next-target control, no horizontal overflow, visible
keyboard focus, a clean console, and loopback-only requests without invoking the public-read action.
