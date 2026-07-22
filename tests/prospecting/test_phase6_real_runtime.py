"""Durable runtime tests for the exact Phase 6 public-business proof."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "apps" / "prospecting-mission-control"
CORE = ROOT / "shared" / "prospecting-core" / "scripts"
for path in (APP_ROOT, CORE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mission_control.agent import RegisteredAgentService  # noqa: E402
from mission_control.application import CounterIds, FixtureRepository, MissionControlError  # noqa: E402
from mission_control.dossier import (  # noqa: E402
    REAL_LATEST_PROOF_REQUEST_ID,
    REAL_GOAL_AUTHORITY,
    REAL_HUMAN_REVIEWER,
    REAL_PROOF_ROUTES,
    DossierService,
)
from mission_control.enrichment import EnrichmentService  # noqa: E402
from mission_control.store import SqliteStore  # noqa: E402
from mission_control.web import WebApplication  # noqa: E402
from validate_phase6_real_contract import load_real_source_manifest  # noqa: E402
from validate_phase6b_contract import CATEGORIES, canonical_bytes  # noqa: E402

PHASE3_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase3" / "prospects.json"
BRIEF_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "brief-fixtures.json"
DOSSIER_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "dossier-fixtures.json"
HISTORY_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "dossier-history.json"
REAL_MANIFEST = ROOT / "shared" / "prospecting-core" / "manifests" / "phase6-live-proof-source-plans.json"
FIXED = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.value = FIXED

    def __call__(self) -> datetime:
        return self.value


def research_bundle(plan: dict, *, fail_product: bool = False) -> dict:
    records = []
    robots = {url.removesuffix("/robots.txt"): url for url in plan["robots_policy_urls"]}
    for source in plan["sources"]:
        origin = "/".join(source["url"].split("/", 3)[:3])
        is_product = "product" in source["source_class"]
        if fail_product and is_product:
            records.append({
                "source_record_version": 1,
                "requested_url": source["url"],
                "final_url": None,
                "redirect_chain": [],
                "source_class": source["source_class"],
                "source_kind": {
                    "official product portfolio": "official_site",
                    "official product detail": "official_site",
                    "official brand and product portfolio": "official_site",
                }[source["source_class"]],
                "robots_url": robots[origin],
                "observed_at": "2026-07-21T20:00:00Z",
                "source_date": None,
                "date_state": "unavailable",
                "status": "failed",
                "safe_reason_code": "access_control",
                "content_type": None,
                "body_sha256": None,
                "body_byte_length": 0,
                "extracted_text_sha256": None,
                "extracted_text_byte_length": 0,
                "summary": "",
                "conflict_state": "none",
            })
            continue
        body = f"official body {source['url']}".encode()
        text = (
            f"The official source describes {plan['organization_name']} and its product portfolio. "
            "It provides current public business evidence for the approved research plan."
        ).encode()
        kind = {
            "official organization and corporate overview": "public_business_profile",
            "official brand-owned site": "official_site",
            "official company and brand information": "official_site",
            "official product portfolio": "official_site",
            "official product detail": "official_site",
            "official investor and corporate profile": "public_business_profile",
            "official organization site": "official_site",
            "official company history and ownership context": "public_business_profile",
            "official brand and product portfolio": "official_site",
            "official press and current activity": "public_news",
            "official careers and operational signals": "public_business_route",
            "official public business roles and leadership": "public_professional_profile",
        }[source["source_class"]]
        records.append({
            "source_record_version": 1,
            "requested_url": source["url"],
            "final_url": source["url"],
            "redirect_chain": [],
            "source_class": source["source_class"],
            "source_kind": kind,
            "robots_url": robots[origin],
            "observed_at": "2026-07-21T20:00:00Z",
            "source_date": "2026-07-21",
            "date_state": "observed_date_fallback",
            "status": "success",
            "safe_reason_code": "ok",
            "content_type": "text/html",
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_byte_length": len(body),
            "extracted_text_sha256": hashlib.sha256(text).hexdigest(),
            "extracted_text_byte_length": len(text),
            "summary": text.decode(),
            "conflict_state": "none",
        })
    return {
        "bundle_version": 1,
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
        "started_at": "2026-07-21T20:00:00Z",
        "completed_at": "2026-07-21T20:00:01Z",
        "sources": records,
    }


class FakeReader:
    def __init__(self, plans: list[dict]) -> None:
        self.plans = {item["source_plan_id"]: item for item in plans}
        self.calls: list[str] = []
        self.before_read = None
        self.fail_product = False
        self.invalid_summary = False
        self.non_substantive_summary = False

    def read_plan(self, plan_id: str) -> dict:
        self.calls.append(plan_id)
        if self.before_read is not None:
            self.before_read()
        bundle = research_bundle(self.plans[plan_id], fail_product=self.fail_product)
        if self.invalid_summary:
            successful = next(source for source in bundle["sources"] if source["status"] == "success")
            successful["summary"] += " badiaspices.com"
        if self.non_substantive_summary:
            for source in bundle["sources"]:
                if source["status"] == "success":
                    source["summary"] = (
                        "Menu Search Catalog Collections Featured New Arrivals " * 10
                    ).strip()
        return bundle


def claim_projection(plan: dict, bundle: dict, approved_result: dict) -> dict:
    successful = [item for item in bundle["sources"] if item["status"] == "success"]
    identity = successful[0]
    product = next(
        (item for item in successful if "product" in item["source_class"]),
        identity,
    )
    identity_value = identity["summary"].split(". ", 1)[0] + "."
    product_value = product["summary"].split(". ", 1)[0] + "."
    attempts = []
    for category in CATEGORIES:
        if category in {"governance_and_history", "evidence_coverage"}:
            continue
        source = product if category in {"activity_and_signals", "opportunity_and_fit"} else identity
        attempts.append({"category": category, "source_urls": [source["requested_url"]]})
    projection = {
        "projection_version": 1,
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
        "result_id": approved_result["result_id"],
        "research_bundle_hash": hashlib.sha256(canonical_bytes(bundle)).hexdigest(),
        "category_attempts": attempts,
        "observed_claims": [
            {
                "claim_id": "claim-runtime-identity",
                "category": "identity_and_relationships",
                "source_url": identity["requested_url"],
                "value": identity_value,
                "value_sha256": hashlib.sha256(identity_value.encode()).hexdigest(),
                "confidence_reason": "The exact scrubbed source sentence states this observation.",
                "uncertainty": "The observation is limited to the approved source and date.",
            },
            {
                "claim_id": "claim-runtime-product-signal",
                "category": "activity_and_signals",
                "source_url": product["requested_url"],
                "value": product_value,
                "value_sha256": hashlib.sha256(product_value.encode()).hexdigest(),
                "confidence_reason": "The exact scrubbed source sentence states this observation.",
                "uncertainty": "The observation is not evidence of demand or budget.",
            },
        ],
        "opportunity_inferences": [{
            "claim_id": "claim-runtime-product-fit",
            "opportunity_kind": "product_photography",
            "premise_claim_ids": ["claim-runtime-product-signal"],
            "confidence_reason": "A verified product signal supports low-confidence fit review.",
            "uncertainty": "No expressed demand, budget, buying intent, or UGC aversion was found.",
        }],
    }
    projection["canonical_hash"] = hashlib.sha256(canonical_bytes(projection)).hexdigest()
    return projection


class AuthorizedDossierService(DossierService):
    _real_execution_request_id = REAL_LATEST_PROOF_REQUEST_ID


class Env:
    def __init__(self, directory: Path) -> None:
        self.clock = Clock()
        self.path = directory / "phase6-real.sqlite3"
        self.repository = FixtureRepository(clock=self.clock, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE)
        self.store = SqliteStore(self.path)
        self.agent = RegisteredAgentService(self.repository, self.store, clock=self.clock)
        self.enrichment = EnrichmentService(self.agent, fixture_path=BRIEF_FIXTURE, clock=self.clock)
        self.all_plans = load_real_source_manifest(REAL_MANIFEST)["plans"]
        plans_by_id = {plan["source_plan_id"]: plan for plan in self.all_plans}
        self.plans = [
            plans_by_id[plan_id]
            for plan_id in REAL_PROOF_ROUTES[REAL_LATEST_PROOF_REQUEST_ID]["source_plan_ids"]
        ]
        self.reader = FakeReader(self.all_plans)
        self.dossier = AuthorizedDossierService(
            self.enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            real_manifest_path=REAL_MANIFEST,
            public_reader=self.reader,
            claim_projector=claim_projection,
            clock=self.clock,
        )

    def search(self, key: str = "real-search") -> dict:
        return self.dossier.create_real_search("noah", "unreal-media-group", idempotency_key=key)[0]

    def approve(self, search: dict, index: int = 0, *, decision: str = "approved", expected: str | None = None) -> dict:
        result = search["results"][index]
        return self.dossier.record_real_goal_approval(
            "noah",
            "unreal-media-group",
            search["search_id"],
            result["result_id"],
            decision=decision,
            reason="The user's exact Phase 6 goal authorizes this bounded public read.",
            expected_leaf_id=expected,
        )


class Phase6RealRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.env = Env(self.directory)

    def test_real_search_evaluates_only_exact_ordered_targets_and_history_first(self) -> None:
        search = self.env.search()
        self.assertEqual(search["request"]["contract_version"], 2)
        self.assertFalse(search["request"]["synthetic"])
        self.assertEqual(search["request"]["request_id"], REAL_LATEST_PROOF_REQUEST_ID)
        self.assertEqual(
            [item["candidate"]["source_plan_id"] for item in search["results"]],
            [item["source_plan_id"] for item in self.env.plans],
        )
        self.assertTrue(all(item["selected"] for item in search["results"]))
        self.assertTrue(all(not item["decision"]["search_intent_is_demand_evidence"] for item in search["results"]))
        replay = self.env.search()
        self.assertEqual(replay["search_id"], search["search_id"])
        second = self.env.search("real-search-two")
        self.assertTrue(all(not item["selected"] for item in second["results"]))
        self.assertTrue(all(item["decision"]["history_classification"]["status"] == "existing_no_new_trigger" for item in second["results"]))

    def test_exhausted_routes_remain_verifiable_and_v3_is_test_enabled_only(self) -> None:
        self.assertIsNone(DossierService._real_execution_request_id)
        self.assertEqual(self.env.dossier._real_execution_request_id, "search-real-live-proof-v3")
        historical_routes = {
            "search-real-live-proof-v1": [
                "phase6-live-proof-celsius-v1", "phase6-live-proof-jazwares-v1"
            ],
            "search-real-live-proof-v2": [
                "phase6-live-proof-4ocean-v1", "phase6-live-proof-badia-v1"
            ],
        }
        for request_id, expected_plans in historical_routes.items():
            with self.subTest(request_id=request_id):
                historical = self.env.dossier._real_search_request(
                    "unreal-media-group", request_id=request_id
                )
                self.env.dossier._require_exact_real_search_request(
                    historical, "unreal-media-group"
                )
                self.assertEqual(historical["source_plan_ids"], expected_plans)
                with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
                    self.env.dossier._require_current_real_execution_request(
                        historical, "unreal-media-group"
                    )
        self.assertEqual(
            self.env.dossier._real_search_request("unreal-media-group")["source_plan_ids"],
            ["phase6-live-proof-tuuci-v1", "phase6-live-proof-miansai-v1"],
        )
        self.assertEqual(
            self.env.dossier._real_search_request("unreal-media-group")["idempotency_identity"],
            "phase6-real:search:umg:live-proof-v3",
        )

    def test_explicitly_sealed_runtime_has_no_unspent_real_proof_route(self) -> None:
        historical = self.env.search("sealed-v3-history")
        exhausted = DossierService(
            self.env.enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            real_manifest_path=REAL_MANIFEST,
            public_reader=self.env.reader,
            clock=self.env.clock,
        )
        with self.assertRaisesRegex(MissionControlError, "No unspent real-proof route"):
            exhausted.create_real_search(
                "noah", "unreal-media-group", idempotency_key="exhausted-route"
            )
        self.assertFalse(exhausted.real_goal_authority_ready(
            "noah",
            "unreal-media-group",
            historical["search_id"],
            historical["results"][0]["result_id"],
        ))
        with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
            exhausted.record_real_goal_approval(
                "noah",
                "unreal-media-group",
                historical["search_id"],
                historical["results"][0]["result_id"],
                decision="approved",
                reason="An exhausted route must not receive fresh authority.",
            )
        app = WebApplication(
            csrf_token="exhausted-csrf",
            agent_service=self.env.agent,
            enrichment_service=self.env.enrichment,
            dossier_service=exhausted,
        )
        status, landing = app.get(
            "/phase6-dossiers",
            {"actor": ["noah"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("No executable real-proof route is configured", landing)
        self.assertIn("new versioned route requires new exact target", landing)
        self.assertNotIn("Evaluate the exact two authorized targets", landing)
        self.assertEqual(self.env.reader.calls, [])

    def test_real_route_without_claim_projector_fails_before_search_or_read(self) -> None:
        for label, projector in (("missing", None), ("noncallable", object())):
            with self.subTest(label=label):
                unprojected = AuthorizedDossierService(
                    self.env.enrichment,
                    fixture_path=DOSSIER_FIXTURE,
                    history_path=HISTORY_FIXTURE,
                    real_manifest_path=REAL_MANIFEST,
                    public_reader=self.env.reader,
                    claim_projector=projector,
                    clock=self.env.clock,
                )
                self.assertFalse(unprojected.real_execution_available())
                with self.assertRaisesRegex(MissionControlError, "claim-level research projector"):
                    unprojected.create_real_search(
                        "noah", "unreal-media-group", idempotency_key=f"{label}-projector"
                    )
        self.assertEqual(self.env.reader.calls, [])

    def test_noncallable_projector_swap_fails_before_read(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "projector-swap"
        )
        self.env.dossier.claim_projector = object()
        with self.assertRaisesRegex(MissionControlError, "no public read occurred"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        self.assertEqual(self.env.reader.calls, [])

    def test_historical_v1_and_v2_reopen_but_cannot_reenter_any_execution_gate(self) -> None:
        service = self.env.dossier
        for request_id, first_plan_id in (
            ("search-real-live-proof-v1", "phase6-live-proof-celsius-v1"),
            ("search-real-live-proof-v2", "phase6-live-proof-4ocean-v1"),
        ):
            with self.subTest(request_id=request_id):
                current_execution_request_id = service._real_execution_request_id
                service._real_execution_request_id = request_id
                try:
                    search = service.create_real_search(
                        "noah", "unreal-media-group", idempotency_key=f"historical-{request_id}"
                    )[0]
                finally:
                    service._real_execution_request_id = current_execution_request_id

                result = search["results"][0]
                self.assertEqual(search["request"]["request_id"], request_id)
                self.assertEqual(
                    service.get_search("noah", "unreal-media-group", search["search_id"])["request"]["request_id"],
                    request_id,
                )
                self.assertFalse(service.real_goal_authority_ready(
                    "noah", "unreal-media-group", search["search_id"], result["result_id"]
                ))
                with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
                    service.record_real_goal_approval(
                        "noah", "unreal-media-group", search["search_id"], result["result_id"],
                        decision="approved", reason="Historical authority must remain inert.",
                    )

                current_gate = service._require_current_real_execution_request
                service._require_current_real_execution_request = service._require_exact_real_search_request
                try:
                    approval = service.record_real_goal_approval(
                        "noah", "unreal-media-group", search["search_id"], result["result_id"],
                        decision="approved", reason="Seed a legitimate pre-correction historical approval.",
                    )
                finally:
                    service._require_current_real_execution_request = current_gate

                with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
                    service.claim_dossier(
                        "noah", "unreal-media-group", approval["approval_event_id"], f"historical-{request_id}-run"
                    )

                service._require_current_real_execution_request = service._require_exact_real_search_request
                try:
                    run = service.claim_dossier(
                        "noah", "unreal-media-group", approval["approval_event_id"], f"historical-{request_id}-run"
                    )[0]
                finally:
                    service._require_current_real_execution_request = current_gate

                with self.assertRaises(MissionControlError):
                    service.claim_dossier(
                        "noah", "unreal-media-group", approval["approval_event_id"], f"historical-{request_id}-run"
                    )
                reader_calls = list(self.env.reader.calls)
                with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
                    service._real_bundle_for_run(
                        "noah", "unreal-media-group", run["dossier_run_id"]
                    )
                self.assertEqual(self.env.reader.calls, reader_calls)
                historical_plan = next(
                    plan for plan in self.env.all_plans if plan["source_plan_id"] == first_plan_id
                )
                with self.assertRaisesRegex(MissionControlError, "exhausted real-proof route"):
                    service._complete_dossier(
                        "noah", "unreal-media-group", run["dossier_run_id"],
                        research_bundle=research_bundle(historical_plan),
                    )
                service.cancel_dossier("noah", "unreal-media-group", run["dossier_run_id"])
        with closing(self.env.store._connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM dossier_candidates").fetchone()[0],
                0,
            )

    def test_goal_authority_is_truthful_exact_and_restart_durable(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        self.assertEqual(approval["reviewer_actor"], REAL_GOAL_AUTHORITY)
        self.assertEqual(approval["source_plan"]["source_plan_hash"], approval["source_plan_hash"])
        reopened = Env(self.directory)
        stored = reopened.dossier.get_approval(
            "noah", "unreal-media-group", approval["approval_event_id"]
        )
        self.assertEqual(stored["source_plan"], self.env.plans[0])
        tampered = copy.deepcopy(self.env.plans[0])
        tampered["organization_name"] = "Changed"
        with self.assertRaises(MissionControlError):
            self.env.dossier.record_real_goal_approval(
                "noah", "unreal-media-group", search["search_id"],
                search["results"][0]["result_id"], decision="approved", reason="Exact goal.",
                source_plan=tampered,
            )

    def test_real_search_order_is_revalidated_against_the_repository_manifest(self) -> None:
        search = self.env.search()
        with closing(self.env.store._connect()) as connection:
            connection.execute("DROP TRIGGER dossier_searches_no_update")
            row = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=?", (search["search_id"],)
            ).fetchone()
            request = json.loads(row["request_snapshot"])
            request["source_plan_ids"].reverse()
            raw = json.dumps(request, sort_keys=True, separators=(",", ":"))
            request_hash = hashlib.sha256(raw.encode()).hexdigest()
            fingerprint_payload = {
                "actor": row["initiating_actor"],
                "business_unit": row["business_unit"],
                "request_hash": request_hash,
            }
            fingerprint = hashlib.sha256(json.dumps(
                fingerprint_payload, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            connection.execute(
                "UPDATE dossier_searches SET request_snapshot=?,request_hash=?,"
                "request_byte_length=?,request_fingerprint=? WHERE search_id=?",
                (raw, request_hash, len(raw.encode()), fingerprint, search["search_id"]),
            )
            connection.commit()
        with self.assertRaisesRegex(MissionControlError, "search request changed"):
            self.env.dossier.get_search(
                "noah", "unreal-media-group", search["search_id"]
            )

    def test_later_target_waits_for_earlier_target_to_become_terminal(self) -> None:
        search = self.env.search()
        with self.assertRaisesRegex(MissionControlError, "earlier eligible"):
            self.env.approve(search, index=1)

        first = self.env.approve(search)
        with self.assertRaisesRegex(MissionControlError, "remains executable"):
            self.env.approve(search, index=1)

        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "ordered-first"
        )
        with self.assertRaisesRegex(MissionControlError, "still running"):
            self.env.approve(search, index=1)

        revoked = self.env.approve(
            search, decision="revoked", expected=first["approval_event_id"]
        )
        with self.assertRaisesRegex(MissionControlError, "still running"):
            self.env.approve(search, index=1)

        self.env.dossier.cancel_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        second = self.env.approve(search, index=1)
        self.assertEqual(second["result_id"], search["results"][1]["result_id"])
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            self.env.approve(
                search,
                expected=revoked["approval_event_id"],
            )

    def test_real_claim_is_single_attempt_and_candidate_stops_failover(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "single-attempt"
        )
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            self.env.dossier.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "second-attempt"
            )
        replay, created = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "single-attempt"
        )
        self.assertFalse(created)
        self.assertEqual(replay["dossier_run_id"], run["dossier_run_id"])
        with self.assertRaisesRegex(MissionControlError, "already in progress"):
            self.env.dossier.start_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "single-attempt"
            )
        self.assertEqual(self.env.reader.calls, [])

        candidate = self.env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(len(self.env.reader.calls), 1)
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            self.env.approve(search, expected=approval["approval_event_id"])

        revoked = self.env.approve(
            search,
            decision="revoked",
            expected=approval["approval_event_id"],
        )
        self.assertEqual(revoked["decision"], "revoked")
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][1]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "produced a candidate"):
            self.env.approve(search, index=1)
        self.assertEqual(len(self.env.reader.calls), 1)
        self.assertEqual(candidate["result_id"], search["results"][0]["result_id"])

    def test_concurrent_completion_enters_the_public_reader_once(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "completion-owner"
        )
        entered = threading.Event()
        release = threading.Event()
        results: list[dict] = []
        errors: list[Exception] = []

        def hold_reader() -> None:
            entered.set()
            release.wait(5)

        def complete() -> None:
            try:
                results.append(self.env.dossier.complete_dossier(
                    "noah", "unreal-media-group", run["dossier_run_id"]
                ))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        self.env.reader.before_read = hold_reader
        worker = threading.Thread(target=complete)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(MissionControlError, "already being completed"):
                self.env.dossier.complete_dossier(
                    "noah", "unreal-media-group", run["dossier_run_id"]
                )
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(self.env.reader.calls, [self.env.plans[0]["source_plan_id"]])
        self.assertEqual(len(self.env.dossier.candidates("noah", "unreal-media-group")), 1)

    def test_one_interrupted_earlier_run_can_recover_after_terminal_alternatives(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        first_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "interrupted-first"
        )
        self.env.dossier._unregister_active(first_run["dossier_run_id"])
        reopened = Env(self.directory)
        with closing(reopened.store._connect()) as connection:
            recovered = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=?",
                (first_run["dossier_run_id"],),
            ).fetchone()
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["failure_class"], "interrupted_execution_recovered")

        second = reopened.approve(search, index=1)
        second_run, _ = reopened.dossier.claim_dossier(
            "noah", "unreal-media-group", second["approval_event_id"], "terminal-second"
        )
        reopened.dossier.cancel_dossier(
            "noah", "unreal-media-group", second_run["dossier_run_id"]
        )
        self.assertTrue(reopened.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        self.assertFalse(reopened.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][1]["result_id"]
        ))
        app = WebApplication(
            csrf_token="recovery-csrf",
            agent_service=reopened.agent,
            enrichment_service=reopened.enrichment,
            dossier_service=reopened.dossier,
        )
        recovery_page = app.phase6_search_detail(
            "noah", "unreal-media-group", search["search_id"]
        )
        self.assertIn("Bind one infrastructure-recovery authority", recovery_page)
        self.assertEqual(
            recovery_page.count("Bind one infrastructure-recovery authority"), 1
        )
        self.assertNotIn("Bind the existing exact user-goal authority", recovery_page)
        self.assertIn(
            f'name="expected_leaf_id" value="{first["approval_event_id"]}"',
            recovery_page,
        )

        retry = reopened.approve(
            search, expected=first["approval_event_id"]
        )
        claim_page = app.phase6_search_detail(
            "noah", "unreal-media-group", search["search_id"]
        )
        self.assertIn("Run the exact bounded public read", claim_page)
        self.assertNotIn("Bind one infrastructure-recovery authority", claim_page)
        candidate, created = reopened.dossier.start_dossier(
            "noah", "unreal-media-group", retry["approval_event_id"], "one-recovery"
        )
        self.assertTrue(created)
        self.assertEqual(candidate["result_id"], search["results"][0]["result_id"])
        replay, created = reopened.dossier.claim_dossier(
            "noah", "unreal-media-group", retry["approval_event_id"], "one-recovery"
        )
        self.assertFalse(created)
        self.assertEqual(replay["state"], "succeeded")
        with self.assertRaisesRegex(MissionControlError, "one recovery has already been used"):
            reopened.approve(search, expected=retry["approval_event_id"])

    def test_interrupted_run_allows_exactly_one_recovery_across_restarts(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        first_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "restart-one"
        )
        self.env.dossier._unregister_active(first_run["dossier_run_id"])

        reopened = Env(self.directory)
        recovery = reopened.approve(search, expected=first["approval_event_id"])
        second_run, _ = reopened.dossier.claim_dossier(
            "noah", "unreal-media-group", recovery["approval_event_id"], "restart-two"
        )
        reopened.dossier._unregister_active(second_run["dossier_run_id"])

        twice_reopened = Env(self.directory)
        self.assertFalse(twice_reopened.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "one recovery has already been used"):
            twice_reopened.approve(
                search, expected=recovery["approval_event_id"]
            )
        with closing(twice_reopened.store._connect()) as connection:
            approvals = connection.execute(
                "SELECT COUNT(*) AS count FROM dossier_approval_events WHERE result_record_id=?",
                (search["results"][0]["result_record_id"],),
            ).fetchone()["count"]
            runs = connection.execute(
                "SELECT state,failure_class FROM dossier_runs WHERE result_record_id=? ORDER BY rowid",
                (search["results"][0]["result_record_id"],),
            ).fetchall()
        self.assertEqual(approvals, 2)
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(
            row["state"] == "failed"
            and row["failure_class"] == "interrupted_execution_recovered"
            for row in runs
        ))

    def test_interrupted_run_after_cancelled_attempt_has_no_recovery(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        cancelled_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "cancel-before-interruption"
        )
        self.env.dossier.cancel_dossier(
            "noah", "unreal-media-group", cancelled_run["dossier_run_id"]
        )
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            self.env.approve(search, expected=first["approval_event_id"])
        reopened = Env(self.directory)
        self.assertFalse(reopened.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            reopened.approve(search, expected=first["approval_event_id"])
        with closing(reopened.store._connect()) as connection:
            runs = connection.execute(
                "SELECT state,failure_class FROM dossier_runs WHERE result_record_id=? ORDER BY rowid",
                (search["results"][0]["result_record_id"],),
            ).fetchall()
        self.assertEqual(
            [(row["state"], row["failure_class"]) for row in runs],
            [("cancelled", None)],
        )
        self.assertEqual(self.env.reader.calls, [])

    def test_earlier_recovery_rejects_later_run_without_matching_terminal_audit(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        first_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "interrupted-before-later"
        )
        self.env.dossier._unregister_active(first_run["dossier_run_id"])
        reopened = Env(self.directory)
        later = reopened.approve(search, index=1)
        later_run, _ = reopened.dossier.claim_dossier(
            "noah", "unreal-media-group", later["approval_event_id"], "later-transition-mismatch"
        )
        self.addCleanup(reopened.dossier._unregister_active, later_run["dossier_run_id"])
        connection = sqlite3.connect(reopened.path)
        try:
            connection.execute(
                "UPDATE dossier_runs SET state='cancelled',cancel_requested=1,completed_at=?,"
                "failure_class=NULL,remediation=NULL WHERE dossier_run_id=?",
                (reopened.clock().isoformat().replace("+00:00", "Z"), later_run["dossier_run_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(reopened.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "cannot be reopened"):
            reopened.approve(search, expected=first["approval_event_id"])

    def test_later_target_rejects_prior_run_without_matching_terminal_audit(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        first_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "prior-transition-mismatch"
        )
        self.addCleanup(self.env.dossier._unregister_active, first_run["dossier_run_id"])
        connection = sqlite3.connect(self.env.path)
        try:
            connection.execute(
                "UPDATE dossier_runs SET state='cancelled',cancel_requested=1,completed_at=? "
                "WHERE dossier_run_id=?",
                (self.env.clock().isoformat().replace("+00:00", "Z"), first_run["dossier_run_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][1]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "inconsistent transition evidence"):
            self.env.approve(search, index=1)

    def test_recovery_rejects_relabelled_source_failure_and_audit_is_append_only(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "relabel-source-failure"
        )
        self.env.reader.fail_product = True
        with self.assertRaisesRegex(MissionControlError, "contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        connection = sqlite3.connect(self.env.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE audit_events SET safe_status='changed' WHERE run_id=?",
                    (run["dossier_run_id"],),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM audit_events WHERE run_id=?",
                    (run["dossier_run_id"],),
                )
            connection.rollback()
            connection.execute(
                "UPDATE dossier_runs SET failure_class='interrupted_execution_recovered',"
                "remediation='Create a new exact approval before another research attempt.' "
                "WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(MissionControlError, "inconsistent transition evidence"):
            self.env.approve(search, index=1)
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "one recovery has already been used"):
            self.env.approve(search, expected=first["approval_event_id"])

    def test_source_failure_cannot_reopen_after_terminal_projection_reclassification(self) -> None:
        mutations = {
            "missing-failure-class": (
                "state='failed',cancel_requested=0,failure_class=NULL,remediation=NULL",
                "failed",
            ),
            "unknown-failure-class": (
                "state='failed',cancel_requested=0,failure_class='unexpected_failure',"
                "remediation='No automatic retry.'",
                "failed",
            ),
            "relabeled-cancelled": (
                "state='cancelled',cancel_requested=1,failure_class=NULL,remediation=NULL",
                "cancelled",
            ),
        }
        for name, (mutation, expected_state) in mutations.items():
            with self.subTest(name=name):
                env = Env(self.directory / name)
                search = env.search(name)
                approval = env.approve(search)
                run, _ = env.dossier.claim_dossier(
                    "noah", "unreal-media-group", approval["approval_event_id"], name
                )
                env.reader.fail_product = True
                with self.assertRaisesRegex(MissionControlError, "contract"):
                    env.dossier.complete_dossier(
                        "noah", "unreal-media-group", run["dossier_run_id"]
                    )
                connection = sqlite3.connect(env.path)
                try:
                    connection.execute(
                        f"UPDATE dossier_runs SET {mutation} WHERE dossier_run_id=?",
                        (run["dossier_run_id"],),
                    )
                    connection.commit()
                    stored_state = connection.execute(
                        "SELECT state FROM dossier_runs WHERE dossier_run_id=?",
                        (run["dossier_run_id"],),
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(stored_state, expected_state)
                self.assertFalse(env.dossier.real_goal_authority_ready(
                    "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
                ))
                with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
                    env.approve(search, expected=approval["approval_event_id"])

    def test_failed_run_relabelled_running_cannot_replay_or_read_again(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        key = "failed-replay-transition"
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], key
        )
        self.env.reader.fail_product = True
        with self.assertRaisesRegex(MissionControlError, "contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        self.assertEqual(len(self.env.reader.calls), 1)
        connection = sqlite3.connect(self.env.path)
        try:
            connection.execute(
                "UPDATE dossier_runs SET state='running',completed_at=NULL,"
                "failure_class=NULL,remediation=NULL WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "transition evidence"):
            self.env.dossier.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], key
            )
        with self.assertRaisesRegex(MissionControlError, "transition evidence"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        self.assertEqual(len(self.env.reader.calls), 1)

    def test_cancel_during_read_cannot_be_relabelled_running_before_persistence(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "cancel-relabel-race"
        )

        def cancel_then_relabel() -> None:
            self.env.dossier.cancel_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
            connection = sqlite3.connect(self.env.path)
            try:
                connection.execute(
                    "UPDATE dossier_runs SET state='running',cancel_requested=0,completed_at=NULL "
                    "WHERE dossier_run_id=?",
                    (run["dossier_run_id"],),
                )
                connection.commit()
            finally:
                connection.close()

        self.env.reader.before_read = cancel_then_relabel
        with self.assertRaisesRegex(MissionControlError, "transition evidence"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        self.assertEqual(len(self.env.reader.calls), 1)
        self.assertEqual(self.env.dossier.candidates("noah", "unreal-media-group"), [])

    def test_recovery_rejects_relabelled_succeeded_run_with_released_package(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        candidate, _ = self.env.dossier.start_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "released-before-relabel"
        )
        released, _ = self.env.dossier.record_real_human_review(
            "unreal-media-group",
            candidate["candidate_version_id"],
            decision="accepted",
            reason="Exact test candidate acceptance before a corruption reproduction.",
            idempotency_key="released-before-relabel-review",
        )
        self.assertIsNotNone(released["package"])
        connection = sqlite3.connect(self.env.path)
        try:
            connection.execute(
                "UPDATE dossier_runs SET state='failed',failure_class='interrupted_execution_recovered',"
                "remediation='Create a new exact approval before another research attempt.',"
                "candidate_version_id=NULL WHERE dossier_run_id=?",
                (candidate["dossier_run_id"],),
            )
            connection.commit()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM lead_intelligence_packages"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "one recovery has already been used"):
            self.env.approve(search, expected=approval["approval_event_id"])

    def test_noninfrastructure_failure_never_reopens_an_earlier_target(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", first["approval_event_id"], "contract-failure"
        )
        self.env.reader.fail_product = True
        with self.assertRaisesRegex(MissionControlError, "contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with closing(self.env.store._connect()) as connection:
            failed = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            ).fetchone()
        self.assertEqual(failed["failure_class"], "source_read_failed_no_retry")
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "cannot be retried"):
            self.env.approve(search, expected=first["approval_event_id"])
        second = self.env.approve(search, index=1)
        second_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", second["approval_event_id"], "terminal-later"
        )
        self.env.dossier.cancel_dossier(
            "noah", "unreal-media-group", second_run["dossier_run_id"]
        )
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "cannot be retried"):
            self.env.approve(search, expected=first["approval_event_id"])

    def test_reader_bundle_contract_failure_records_terminal_no_retry_metadata(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "invalid-summary"
        )
        self.env.reader.invalid_summary = True
        with self.assertRaisesRegex(MissionControlError, "failed its contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with closing(self.env.store._connect()) as connection:
            failed = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            ).fetchone()
        self.assertEqual(failed["failure_class"], "source_read_failed_no_retry")
        self.assertEqual(
            failed["remediation"],
            "The exact public-source attempt failed closed and must not be retried.",
        )
        self.assertEqual(len(self.env.reader.calls), 1)
        self.assertEqual(self.env.dossier.candidates("noah", "unreal-media-group"), [])
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))

    def test_navigation_only_bundle_is_terminal_no_retry_and_creates_no_candidate(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "navigation-only"
        )
        self.env.reader.non_substantive_summary = True
        with self.assertRaisesRegex(MissionControlError, "failed its contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with closing(self.env.store._connect()) as connection:
            failed = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            ).fetchone()
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM dossier_candidates"
            ).fetchone()[0]
        self.assertEqual(failed["failure_class"], "source_read_failed_no_retry")
        self.assertEqual(candidate_count, 0)
        self.assertFalse(self.env.dossier.real_goal_authority_ready(
            "noah", "unreal-media-group", search["search_id"], search["results"][0]["result_id"]
        ))
        with self.assertRaisesRegex(MissionControlError, "cannot be retried"):
            self.env.approve(search, expected=approval["approval_event_id"])

    def test_claim_projector_failure_after_read_is_terminal_no_retry(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "projector-failure"
        )

        def fail_projection(_plan: dict, _bundle: dict, _result: dict) -> dict:
            raise RuntimeError("local projector failure")

        self.env.dossier.claim_projector = fail_projection
        with self.assertRaisesRegex(MissionControlError, "projection failed closed"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with closing(self.env.store._connect()) as connection:
            failed = connection.execute(
                "SELECT failure_class FROM dossier_runs WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            ).fetchone()
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM dossier_candidates"
            ).fetchone()[0]
        self.assertEqual(failed["failure_class"], "source_read_failed_no_retry")
        self.assertEqual(candidate_count, 0)
        self.assertEqual(self.env.reader.calls, [self.env.plans[0]["source_plan_id"]])

    def test_candidate_rebinds_request_identity_and_freshness_policy(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "request-binding"
        )
        candidate = self.env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        candidate_id = candidate["candidate_version_id"]
        with closing(self.env.store._connect()) as connection:
            connection.execute("DROP TRIGGER dossier_candidates_no_update")
            original = connection.execute(
                "SELECT dossier_snapshot,content_hash,byte_length FROM dossier_candidates "
                "WHERE candidate_version_id=?",
                (candidate_id,),
            ).fetchone()
            for field, value in (
                ("search_request_id", "search-forged-request"),
                ("maximum_evidence_age_days", 3650),
            ):
                changed = json.loads(original["dossier_snapshot"])
                changed[field] = value
                raw = json.dumps(changed, sort_keys=True, separators=(",", ":"))
                encoded = raw.encode("utf-8")
                connection.execute(
                    "UPDATE dossier_candidates SET dossier_snapshot=?,content_hash=?,byte_length=? "
                    "WHERE candidate_version_id=?",
                    (raw, hashlib.sha256(encoded).hexdigest(), len(encoded), candidate_id),
                )
                connection.commit()
                with self.subTest(field=field):
                    with self.assertRaisesRegex(MissionControlError, "integrity"):
                        self.env.dossier.get_candidate(
                            "noah", "unreal-media-group", candidate_id
                        )
                connection.execute(
                    "UPDATE dossier_candidates SET dossier_snapshot=?,content_hash=?,byte_length=? "
                    "WHERE candidate_version_id=?",
                    (
                        original["dossier_snapshot"],
                        original["content_hash"],
                        original["byte_length"],
                        candidate_id,
                    ),
                )
                connection.commit()

    def test_invalid_projector_mapping_after_read_is_terminal_no_retry(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "invalid-projector"
        )
        self.env.dossier.claim_projector = lambda _plan, _bundle, _result: {"invalid": True}
        with self.assertRaisesRegex(MissionControlError, "failed its contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with closing(self.env.store._connect()) as connection:
            failed = connection.execute(
                "SELECT failure_class,remediation FROM dossier_runs WHERE dossier_run_id=?",
                (run["dossier_run_id"],),
            ).fetchone()
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM dossier_candidates"
            ).fetchone()[0]
        self.assertEqual(failed["failure_class"], "source_read_failed_no_retry")
        self.assertEqual(
            failed["remediation"],
            "The exact public-source attempt failed closed and must not be retried.",
        )
        self.assertEqual(candidate_count, 0)
        self.assertEqual(self.env.reader.calls, [self.env.plans[0]["source_plan_id"]])

    def test_claim_blocks_successor_authority_and_terminal_source_failure_retry(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        replacement = self.env.approve(
            search, expected=first["approval_event_id"]
        )
        future = self.env.approve(
            search, expected=replacement["approval_event_id"]
        )
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", future["approval_event_id"], "source-failure"
        )
        with self.assertRaisesRegex(MissionControlError, "bounded real-proof"):
            self.env.approve(search, expected=future["approval_event_id"])
        self.env.reader.fail_product = True
        with self.assertRaisesRegex(MissionControlError, "contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        with self.assertRaisesRegex(MissionControlError, "cannot be retried"):
            self.env.dossier.claim_dossier(
                "noah", "unreal-media-group", future["approval_event_id"], "reserved-after-failure"
            )

    def test_reader_runs_outside_sqlite_transaction_and_candidate_stays_pending(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "real-run"
        )

        def prove_unlocked() -> None:
            with self.env.store.transaction() as connection:
                connection.execute("SELECT 1").fetchone()

        self.env.reader.before_read = prove_unlocked
        candidate = self.env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(self.env.reader.calls, [self.env.plans[0]["source_plan_id"]])
        self.assertEqual(candidate["dossier"]["schema_version"], 3)
        self.assertEqual(candidate["dossier"]["automated_evidence_review"]["state"], "passed")
        self.assertFalse(candidate["dossier"]["synthetic"])
        self.assertEqual(candidate["dossier"]["review_state"], "pending_research_quality_review")
        self.assertEqual(len(candidate["dossier"]["categories"]), 11)
        self.assertEqual(set(candidate["dossier"]["authority"].values()), {False, True})
        self.assertTrue(candidate["dossier"]["authority"]["local_data_handoff_only"])
        self.assertEqual(self.env.dossier.packages("noah", "unreal-media-group"), [])

    def test_revoke_first_denies_claim_but_claim_first_may_finish(self) -> None:
        search = self.env.search()
        first = self.env.approve(search)
        revoked = self.env.approve(
            search, decision="revoked", expected=first["approval_event_id"]
        )
        with self.assertRaisesRegex(MissionControlError, "not approved"):
            self.env.dossier.claim_dossier(
                "noah", "unreal-media-group", revoked["approval_event_id"], "revoked-first"
            )

        second = self.env.approve(search, expected=revoked["approval_event_id"])
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", second["approval_event_id"], "claimed-first"
        )
        self.env.approve(search, decision="revoked", expected=second["approval_event_id"])
        candidate = self.env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(candidate["dossier"]["review_state"], "pending_research_quality_review")

    def test_cancel_during_read_wins_and_source_failure_creates_no_candidate(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "cancel-during-read"
        )
        self.env.reader.before_read = lambda: self.env.dossier.cancel_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        with self.assertRaisesRegex(MissionControlError, "cancelled"):
            self.env.dossier.complete_dossier("noah", "unreal-media-group", run["dossier_run_id"])
        self.assertEqual(self.env.dossier.candidates("noah", "unreal-media-group"), [])

        other = self.env.approve(search, index=1)
        other_run, _ = self.env.dossier.claim_dossier(
            "noah", "unreal-media-group", other["approval_event_id"], "failed-products"
        )
        self.env.reader.before_read = None
        self.env.reader.fail_product = True
        with self.assertRaisesRegex(MissionControlError, "contract"):
            self.env.dossier.complete_dossier(
                "noah", "unreal-media-group", other_run["dossier_run_id"]
            )
        self.assertEqual(self.env.dossier.candidates("noah", "unreal-media-group"), [])

    def test_real_release_requires_reserved_human_review_and_is_atomic(self) -> None:
        search = self.env.search()
        approval = self.env.approve(search)
        candidate, _ = self.env.dossier.start_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "release-run"
        )
        with self.assertRaisesRegex(MissionControlError, "human"):
            self.env.dossier.review_candidate(
                "rob", "unreal-media-group", candidate["candidate_version_id"],
                decision="accepted", reason="Simulated actors cannot accept a real dossier.",
                idempotency_key="wrong-reviewer",
            )
        outcome, created = self.env.dossier.record_real_human_review(
            "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Explicit exact-version human acceptance test fixture.",
            idempotency_key="human-review",
        )
        self.assertTrue(created)
        self.assertEqual(outcome["review"]["reviewer_actor"], REAL_HUMAN_REVIEWER)
        self.assertEqual(outcome["package"]["package"]["schema_version"], 3)
        self.assertFalse(outcome["package"]["package"]["synthetic"])
        self.assertTrue(outcome["package"]["package"]["authority"]["local_data_handoff_only"])
        self.assertFalse(outcome["package"]["package"]["authority"]["outreach"])
        with closing(self.env.store._connect()) as connection:
            connection.execute("DROP TRIGGER lead_intelligence_packages_no_update")
            row = connection.execute("SELECT * FROM lead_intelligence_packages").fetchone()
            package = json.loads(row["package_snapshot"])
            package["qualification"]["reason"] = "Changed but structurally valid low-confidence wording."
            payload = copy.deepcopy(package)
            payload.pop("canonical_hash")
            package["canonical_hash"] = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            raw = json.dumps(package, sort_keys=True, separators=(",", ":"))
            encoded = raw.encode()
            connection.execute(
                "UPDATE lead_intelligence_packages SET package_snapshot=?,content_hash=?,"
                "byte_length=?,request_fingerprint=? WHERE package_record_id=?",
                (
                    raw,
                    hashlib.sha256(encoded).hexdigest(),
                    len(encoded),
                    package["canonical_hash"],
                    row["package_record_id"],
                ),
            )
            connection.commit()
        with self.assertRaisesRegex(MissionControlError, "contract validation"):
            self.env.dossier.packages("noah", "unreal-media-group")

    def test_loopback_surface_exposes_exact_proof_but_no_real_review_authority(self) -> None:
        app = WebApplication(
            csrf_token="real-csrf",
            agent_service=self.env.agent,
            enrichment_service=self.env.enrichment,
            dossier_service=self.env.dossier,
        )
        context = {"actor": ["noah"], "business_unit": ["unreal-media-group"]}
        status, landing = app.get("/phase6-dossiers", context)
        self.assertEqual(status, 200)
        self.assertIn("Exact authorized public-business proof", landing)
        self.assertIn("TUUCI then Miansai", landing)
        self.assertNotIn("4ocean then Badia", landing)
        self.assertIn("@media(max-width:640px)", landing)
        status, search_page = app.post("/phase6-real-searches", {
            "csrf_token": "real-csrf",
            "actor": "noah",
            "business_unit": "unreal-media-group",
            "idempotency_key": "web-real-search",
        })
        self.assertEqual(status, 200)
        self.assertEqual(search_page.count("Bind the existing exact user-goal authority"), 1)
        self.assertIn("Waiting for every earlier eligible real-proof target", search_page)
        search = self.env.dossier.searches("noah", "unreal-media-group")[0]
        result = search["results"][0]
        status, approved_page = app.post("/phase6-real-goal-approvals", {
            "csrf_token": "real-csrf",
            "actor": "noah",
            "business_unit": "unreal-media-group",
            "search_id": search["search_id"],
            "result_id": result["result_id"],
            "decision": "approved",
            "reason": "The user's exact Phase 6 goal authorizes this bounded public read.",
        })
        self.assertEqual(status, 201)
        self.assertIn("Run the exact bounded public read", approved_page)
        self.assertNotIn("Bind the existing exact user-goal authority", approved_page)
        approval = self.env.dossier.approvals("noah", "unreal-media-group")[0]
        status, candidate_page = app.post("/phase6-dossier-runs", {
            "csrf_token": "real-csrf",
            "actor": "noah",
            "business_unit": "unreal-media-group",
            "approval_event_id": approval["approval_event_id"],
            "idempotency_key": "web-real-run",
        })
        self.assertEqual(status, 200)
        self.assertIn("Exact human approval packet", candidate_page)
        self.assertIn("Pending genuine human review", candidate_page)
        self.assertIn("Source-plan hash", candidate_page)
        self.assertIn("Claim-projection hash", candidate_page)
        self.assertIn("Automated evidence review", candidate_page)
        self.assertIn("Verified claims", candidate_page)
        self.assertNotIn("Record one terminal review", candidate_page)


if __name__ == "__main__":
    unittest.main()
