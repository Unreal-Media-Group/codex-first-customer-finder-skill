"""Exact-plan and real public dossier contract tests for Phase 6."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "shared" / "prospecting-core" / "scripts"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from common import ValidationError  # noqa: E402
from validate_phase6_real_contract import (  # noqa: E402
    AUTHORIZED_PLAN_HASHES,
    _real_package_payload,
    build_real_customer_dossier,
    build_real_lead_intelligence_package,
    build_real_result_projection,
    derive_real_account_id,
    derive_real_global_identity_id,
    derive_real_result_id,
    is_substantive_real_summary,
    load_real_source_manifest,
    validate_real_customer_dossier,
    validate_real_lead_intelligence_package,
    validate_real_research_bundle,
    validate_real_result_projection,
    validate_real_source_plan,
)
from validate_phase6b_contract import canonical_bytes  # noqa: E402

MANIFEST = ROOT / "shared" / "prospecting-core" / "manifests" / "phase6-live-proof-source-plans.json"
SCHEMAS = [
    ROOT / "shared" / "prospecting-core" / "schemas" / "phase6-real-source-plan.schema.json",
    ROOT / "shared" / "prospecting-core" / "schemas" / "customer-dossier-real.schema.json",
    ROOT / "shared" / "prospecting-core" / "schemas" / "lead-intelligence-package-real.schema.json",
]
OBSERVED = "2026-07-21T20:00:00Z"
COMPLETED = "2026-07-21T20:00:01Z"


def empty_history() -> dict:
    return {"version": 1, "generated_at": OBSERVED, "prospects": []}


def new_classification() -> dict:
    return {
        "status": "new_prospect",
        "reason": "No canonical identity or relationship matched history.",
        "matched_prospect_id": None,
        "matched_prospect_ids": [],
        "reengagement": False,
    }


def bundle(plan: dict, *, failed: set[str] | None = None) -> dict:
    failed = failed or set()
    robots = {
        url.split("/robots.txt", 1)[0]: url for url in plan["robots_policy_urls"]
    }
    records = []
    for source in plan["sources"]:
        origin = source["url"].split("/", 3)[:3]
        origin = "/".join(origin)
        if source["url"] in failed:
            records.append({
                "source_record_version": 1,
                "requested_url": source["url"],
                "final_url": None,
                "redirect_chain": [],
                "source_class": source["source_class"],
                "source_kind": {
                    "official press and current activity": "public_news",
                    "official careers and operational signals": "public_business_route",
                    "official public business roles and leadership": "public_professional_profile",
                    "official organization and corporate overview": "public_business_profile",
                    "official investor and corporate profile": "public_business_profile",
                    "official company history and ownership context": "public_business_profile",
                }.get(source["source_class"], "official_site"),
                "robots_url": robots[origin],
                "observed_at": COMPLETED,
                "source_date": None,
                "date_state": "unavailable",
                "status": "failed",
                "safe_reason_code": "robots_denied",
                "content_type": None,
                "body_sha256": None,
                "body_byte_length": 0,
                "extracted_text_sha256": None,
                "extracted_text_byte_length": 0,
                "summary": "",
                "conflict_state": "none",
            })
            continue
        summary = (
            f"The official source documents {source['source_class']} for the organization. "
            "It provides current public business evidence for the approved research plan."
        )
        body = f"body:{source['url']}".encode()
        text = summary.encode()
        records.append({
            "source_record_version": 1,
            "requested_url": source["url"],
            "final_url": source["url"],
            "redirect_chain": [],
            "source_class": source["source_class"],
            "source_kind": {
                "official press and current activity": "public_news",
                "official careers and operational signals": "public_business_route",
                "official public business roles and leadership": "public_professional_profile",
                "official organization and corporate overview": "public_business_profile",
                "official investor and corporate profile": "public_business_profile",
                "official company history and ownership context": "public_business_profile",
            }.get(source["source_class"], "official_site"),
            "robots_url": robots[origin],
            "observed_at": COMPLETED,
            "source_date": "2026-07-21",
            "date_state": "observed_date_fallback",
            "status": "success",
            "safe_reason_code": "ok",
            "content_type": "text/html",
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_byte_length": len(body),
            "extracted_text_sha256": hashlib.sha256(text).hexdigest(),
            "extracted_text_byte_length": len(text),
            "summary": summary,
            "conflict_state": "none",
        })
    return {
        "bundle_version": 1,
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
        "started_at": OBSERVED,
        "completed_at": COMPLETED,
        "sources": records,
    }


class Phase6RealContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_real_source_manifest(MANIFEST)
        cls.plans = cls.manifest["plans"]

    def setUp(self) -> None:
        self.plan = copy.deepcopy(self.plans[0])
        self.history = empty_history()
        self.candidate, self.result = build_real_result_projection(
            self.plan, new_classification()
        )
        self.research = bundle(self.plan)
        self.dossier = build_real_customer_dossier(
            plan=self.plan,
            research_bundle=self.research,
            history=self.history,
            approved_result=self.result,
            approval_id="dapproval-real-test",
            search_request_id="search-real-test",
            history_fingerprint=hashlib.sha256(canonical_bytes(self.history)).hexdigest(),
            research_cutoff=COMPLETED,
            maximum_evidence_age_days=365,
            version=1,
        )

    def test_exact_manifest_and_derivations_are_stable(self) -> None:
        self.assertEqual(
            [plan["source_plan_id"] for plan in self.plans],
            list(AUTHORIZED_PLAN_HASHES),
        )
        self.assertEqual(
            [plan["source_plan_hash"] for plan in self.plans],
            list(AUTHORIZED_PLAN_HASHES.values()),
        )
        for plan in self.plans:
            global_id = derive_real_global_identity_id(plan["stable_identity_seed"])
            self.assertTrue(global_id.startswith("global-v1-"))
            self.assertTrue(derive_real_result_id(plan).startswith("result-real-v1-"))
            self.assertTrue(derive_real_account_id(plan["canonical_domain"], global_id).startswith("account-v1-"))

        budgets = {
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": 8,
            "request_deadline_seconds": 15,
            "run_deadline_seconds": 120,
            "max_redirects_per_source": 3,
            "max_requests_total": 12,
            "max_header_bytes": 32768,
            "max_body_bytes": 1500000,
            "max_robots_bytes": 262144,
            "max_extracted_text_bytes": 200000,
        }
        opportunity_filter = {
            "include_any": ["product_photography", "product_video"],
            "exclude": ["ugc_ad"],
        }
        expected_v3 = [
            {
                "contract_version": 2,
                "synthetic": False,
                "source_plan_id": "phase6-live-proof-tuuci-v1",
                "organization_name": "The Ultimate Umbrella Company, Inc. / Tuuci",
                "location_basis": "Miami-Hialeah, Florida",
                "canonical_domain": "tuuci.com",
                "approved_related_domains": [],
                "stable_identity_seed": "ultimate-umbrella-company-inc|tuuci.com",
                "business_unit": "unreal-media-group",
                "opportunity_filter": opportunity_filter,
                "approved_origins": ["https://www.tuuci.com"],
                "robots_policy_urls": ["https://www.tuuci.com/robots.txt"],
                "sources": [
                    {"url": "https://www.tuuci.com/", "source_class": "official organization site"},
                    {"url": "https://www.tuuci.com/our-world/company/", "source_class": "official company history and ownership context"},
                    {"url": "https://www.tuuci.com/products/", "source_class": "official brand and product portfolio"},
                    {"url": "https://www.tuuci.com/in-the-news/", "source_class": "official press and current activity"},
                    {"url": "https://www.tuuci.com/in-the-news/tuuci-marks-a-record-breaking-year-of-excellence/", "source_class": "official press and current activity"},
                ],
                "budgets": budgets,
                "source_plan_hash": "bebc6febbce3d5d8b8c74afb5d23142b6aa820dbc287c526af106b20b646ad51",
            },
            {
                "contract_version": 2,
                "synthetic": False,
                "source_plan_id": "phase6-live-proof-miansai-v1",
                "organization_name": "Miansai",
                "location_basis": "Miami, Florida",
                "canonical_domain": "miansai.com",
                "approved_related_domains": [],
                "stable_identity_seed": "miansai|miansai.com",
                "business_unit": "unreal-media-group",
                "opportunity_filter": opportunity_filter,
                "approved_origins": ["https://www.miansai.com"],
                "robots_policy_urls": ["https://www.miansai.com/robots.txt"],
                "sources": [
                    {"url": "https://www.miansai.com/", "source_class": "official brand-owned site"},
                    {"url": "https://www.miansai.com/pages/about", "source_class": "official company and brand information"},
                    {"url": "https://www.miansai.com/collections/new-arrivals-man", "source_class": "official product portfolio"},
                    {"url": "https://www.miansai.com/collections/men", "source_class": "official brand and product portfolio"},
                    {"url": "https://www.miansai.com/collections/women", "source_class": "official brand and product portfolio"},
                ],
                "budgets": budgets,
                "source_plan_hash": "3cfcbdf8df08e9aadae4eb9a4575cd941b31d927a93f0f09cf2882efc63d7cf5",
            },
        ]
        expected_v4 = {
            "contract_version": 2,
            "synthetic": False,
            "source_plan_id": "phase6-live-proof-coolibar-v1",
            "organization_name": "Coolibar, Inc.",
            "location_basis": "Miami, Florida",
            "canonical_domain": "coolibar.com",
            "approved_related_domains": [],
            "stable_identity_seed": "coolibar-inc|coolibar.com",
            "business_unit": "unreal-media-group",
            "opportunity_filter": opportunity_filter,
            "approved_origins": ["https://www.coolibar.com"],
            "robots_policy_urls": ["https://www.coolibar.com/robots.txt"],
            "sources": [
                {"url": "https://www.coolibar.com/", "source_class": "official organization site"},
                {"url": "https://www.coolibar.com/pages/about-us", "source_class": "official company and brand information"},
                {"url": "https://www.coolibar.com/pages/why-coolibar", "source_class": "official company and brand information"},
                {"url": "https://www.coolibar.com/collections/top-sellers", "source_class": "official product portfolio"},
                {"url": "https://www.coolibar.com/blogs/news/from-minneapolis-to-miami-coolibar-finalizes-corporate-headquarters-move", "source_class": "official press and current activity"},
            ],
            "budgets": budgets,
            "source_plan_hash": "11ecd3ca38a6da32235afcef3f7f43e819d480a1183ccb6c4b5a26b1ac3834c3",
        }
        self.assertEqual(self.plans[-3:-1], expected_v3)
        self.assertEqual(self.plans[-1], expected_v4)
        exact_plans = {
            "phase6-live-proof-tuuci-v1": (1474, "global-v1-f8de1b187e050aa62fe792a5", "account-v1-319e190f1842af5a8c7c580c", "result-real-v1-f2fd1af5859f5cbec1ef87f5"),
            "phase6-live-proof-miansai-v1": (1382, "global-v1-8cab981850777a7beb94e28b", "account-v1-b368242de54e01b48b0c0ad3", "result-real-v1-a70c37b08ed9c9ad23ff2b4a"),
            "phase6-live-proof-coolibar-v1": (1473, "global-v1-45b1afeeb9b6376cb42a27ba", "account-v1-70f245df6d088152054ae33d", "result-real-v1-0f4401ad53aadba4d9a7338f"),
        }
        plans_by_id = {plan["source_plan_id"]: plan for plan in self.plans}
        for plan_id, (length, global_id, account_id, result_id) in exact_plans.items():
            with self.subTest(plan_id=plan_id):
                plan = plans_by_id[plan_id]
                self.assertEqual(len(canonical_bytes(plan)), length)
                self.assertEqual(derive_real_global_identity_id(plan["stable_identity_seed"]), global_id)
                self.assertEqual(derive_real_account_id(plan["canonical_domain"], global_id), account_id)
                self.assertEqual(derive_real_result_id(plan), result_id)

    def test_every_plan_binding_tamper_fails_closed(self) -> None:
        mutations = {
            "organization_name": "Changed Organization",
            "location_basis": "Changed location",
            "canonical_domain": "changed.example.net",
            "stable_identity_seed": "changed|celsiusholdingsinc.com",
            "business_unit": "unreal-talent",
            "approved_related_domains": [],
            "approved_origins": ["https://www.celsius.com"],
            "robots_policy_urls": ["https://www.celsius.com/robots.txt"],
            "sources": self.plan["sources"][:-1],
            "budgets": {**self.plan["budgets"], "max_requests_total": 11},
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(self.plan)
                changed[field] = replacement
                with self.assertRaises(ValidationError):
                    validate_real_source_plan(changed)

    def test_filter_is_exact_intent_and_never_demand_evidence(self) -> None:
        self.assertTrue(self.result["selected"])
        self.assertEqual(self.result["qualification_state"], "pending_bounded_public_research")
        self.assertFalse(self.result["search_intent_is_demand_evidence"])
        self.assertEqual(
            [item["kind"] for item in self.candidate["opportunities"]],
            ["product_photography", "product_video"],
        )

    def test_history_protection_prevents_selection(self) -> None:
        protected = {
            "status": "existing_client",
            "reason": "Matched account is an active client.",
            "matched_prospect_id": "history-client",
            "matched_prospect_ids": ["history-client"],
            "reengagement": False,
        }
        candidate, decision = build_real_result_projection(self.plan, protected)
        self.assertFalse(decision["selected"])
        self.assertEqual(decision["filter_state"], "not_evaluated_history_protection")
        validate_real_result_projection(candidate, decision, self.plan)

    def test_research_bundle_is_exact_ordered_and_contains_no_raw_body(self) -> None:
        validated = validate_real_research_bundle(self.research, self.plan)
        self.assertEqual(
            [record["requested_url"] for record in validated["sources"]],
            [source["url"] for source in self.plan["sources"]],
        )
        serialized = json.dumps(validated)
        self.assertNotIn('"body"', serialized)
        self.assertNotIn('"extracted_text"', serialized)

    def test_strict_research_bundle_requires_html_source_evidence(self) -> None:
        changed = copy.deepcopy(self.research)
        changed["sources"][0]["content_type"] = "text/plain"
        validate_real_research_bundle(changed, self.plan)
        with self.assertRaisesRegex(ValidationError, "HTML"):
            validate_real_research_bundle(changed, self.plan, require_substantive=True)

    def test_research_bundle_rejects_unapproved_redirect_contact_and_extra_raw_field(self) -> None:
        changed = copy.deepcopy(self.research)
        changed["sources"][0]["redirect_chain"] = ["https://unapproved.example.net/"]
        with self.assertRaises(ValidationError):
            validate_real_research_bundle(changed, self.plan)
        changed = copy.deepcopy(self.research)
        changed["sources"][0]["summary"] = "Call 305-555-0187 for details."
        with self.assertRaises(ValidationError):
            validate_real_research_bundle(changed, self.plan)
        changed = copy.deepcopy(self.research)
        changed["sources"][0]["body"] = "raw page"
        with self.assertRaises(ValidationError):
            validate_real_research_bundle(changed, self.plan)

    def test_research_bundle_rejects_obfuscated_contact_routes_urls_and_credentials(self) -> None:
        prohibited = (
            "press [at] example [dot] org",
            "press＠example．org",
            "press@example。org",
            "press@пример.рф",
            "press @ пример.рф",
            "press (at) пример (dot) рф",
            "press at пример dot рф",
            "press@exаmple.com",
            "press@exa\u0301mple.com",
            "press\u200b@\u200bexample\u200b.\u200borg",
            "Call 212.555.0100 for details.",
            "Call 212\u200b-\u200b555\u200b-\u200b0100 for details.",
            "Visit www.unapproved.invalid/contact for details.",
            "https://unapproved.invalid/contact",
            "unapproved.invalid/contact",
            "example.com",
            "Temporary password: hunter2",
            "api.key=super-secret-material",
            "api\u200b_key=super-secret-material",
            "api\u034f_key=super-secret-material",
            "api_k\u034fey=super-secret-material",
            "api_kеy=super-secret-material",
            "api_kЕy=super-secret-material",
            "АPI_KEY=super-secret-material",
        )
        for value in prohibited:
            with self.subTest(value=value):
                changed = copy.deepcopy(self.research)
                changed["sources"][0]["summary"] = value
                with self.assertRaises(ValidationError):
                    validate_real_research_bundle(changed, self.plan)

    def test_substantive_summary_gate_rejects_punctuated_navigation_labels(self) -> None:
        navigation = (
            "Shop New Arrivals, Products, Collections, Gifts, Stores, About Us.",
            "Menu Search Catalog Collections Featured New Arrivals Offers.",
            "menu search catalog collections featured new arrivals offers.",
        )
        for value in navigation:
            with self.subTest(value=value):
                self.assertFalse(is_substantive_real_summary(value))
        self.assertTrue(
            is_substantive_real_summary(
                "The company designs and manufactures outdoor products in Miami. "
                "Its official page explains the current business offering and product portfolio."
            )
        )

    def test_failed_source_records_are_safe_and_product_evidence_is_required(self) -> None:
        failed_nonproduct = bundle(self.plan, failed={self.plan["sources"][0]["url"]})
        validate_real_research_bundle(failed_nonproduct, self.plan)
        failed_products = {
            source["url"] for source in self.plan["sources"] if "product" in source["source_class"]
        }
        with self.assertRaisesRegex(ValidationError, "product evidence"):
            build_real_customer_dossier(
                plan=self.plan,
                research_bundle=bundle(self.plan, failed=failed_products),
                history=self.history,
                approved_result=self.result,
                approval_id="dapproval-real-test",
                search_request_id="search-real-test",
                history_fingerprint=hashlib.sha256(canonical_bytes(self.history)).hexdigest(),
                research_cutoff=COMPLETED,
                maximum_evidence_age_days=365,
                version=1,
            )

    def test_navigation_summaries_are_not_new_bundle_or_category_evidence(self) -> None:
        navigation = ("Menu Search Catalog Collections Featured New Arrivals " * 20).strip()
        changed = copy.deepcopy(self.research)
        for source in changed["sources"]:
            if source["status"] == "success":
                source["summary"] = navigation[:1_000]
        with self.assertRaisesRegex(ValidationError, "substantive"):
            validate_real_research_bundle(changed, self.plan, require_substantive=True)
        with self.assertRaisesRegex(ValidationError, "product evidence"):
            build_real_customer_dossier(
                plan=self.plan,
                research_bundle=changed,
                history=self.history,
                approved_result=self.result,
                approval_id="dapproval-real-test",
                search_request_id="search-real-test",
                history_fingerprint=hashlib.sha256(canonical_bytes(self.history)).hexdigest(),
                research_cutoff=COMPLETED,
                maximum_evidence_age_days=365,
                version=1,
            )

    def test_source_summaries_are_inventory_only_until_claim_level_verification(self) -> None:
        public_evidence = {
            item["evidence_id"] for item in self.dossier["evidence_inventory"]
            if item["evidence_kind"] == "public_source"
        }
        claim_refs = {
            ref for category in self.dossier["categories"]
            if category["category"] != "evidence_coverage"
            for claim in category["claims"]
            for ref in claim["evidence_refs"]
        }
        broad_categories = [
            item for item in self.dossier["categories"]
            if item["category"] not in {"governance_and_history", "evidence_coverage"}
        ]
        self.assertTrue(public_evidence)
        self.assertTrue(public_evidence.isdisjoint(claim_refs))
        self.assertTrue(all(item["coverage_state"] == "not_found" for item in broad_categories))
        self.assertTrue(all(item["claims"] == [] for item in broad_categories))

    def test_real_dossier_has_exact_coverage_truth_and_inert_authority(self) -> None:
        self.assertEqual(self.dossier["schema_version"], 2)
        self.assertFalse(self.dossier["synthetic"])
        self.assertEqual([item["category"] for item in self.dossier["categories"]], [
            "identity_and_relationships",
            "company_and_commercial_context",
            "operations_and_digital_footprint",
            "audiences_market_and_reputation",
            "brand_and_messaging",
            "activity_and_signals",
            "opportunity_and_fit",
            "public_people_and_contact_paths",
            "governance_and_history",
            "evidence_coverage",
            "additional_material_facts",
        ])
        self.assertFalse(self.dossier["qualification"]["demand_evidence_found"])
        audience = next(
            item for item in self.dossier["categories"]
            if item["category"] == "audiences_market_and_reputation"
        )
        self.assertEqual(audience["coverage_state"], "not_found")
        self.assertEqual(
            [item["category"] for item in self.dossier["categories"] if item["coverage_state"] == "complete"],
            ["governance_and_history", "evidence_coverage"],
        )
        self.assertEqual(self.dossier["review_state"], "pending_research_quality_review")
        self.assertTrue(self.dossier["authority"]["local_data_handoff_only"])
        self.assertTrue(all(
            value is False for key, value in self.dossier["authority"].items()
            if key != "local_data_handoff_only"
        ))

    def test_real_dossier_rejects_binding_freshness_and_authority_tampering(self) -> None:
        changed = copy.deepcopy(self.dossier)
        changed["approved_result"]["account_id"] = "account-v1-wrong"
        with self.assertRaises(ValidationError):
            validate_real_customer_dossier(changed, history=self.history, approved_result=self.result, source_plan=self.plan)
        changed = copy.deepcopy(self.dossier)
        claim = next(item["claims"][0] for item in changed["categories"] if item["claims"])
        claim["freshness_state"] = "stale"
        with self.assertRaises(ValidationError):
            validate_real_customer_dossier(changed, history=self.history, approved_result=self.result, source_plan=self.plan)
        changed = copy.deepcopy(self.dossier)
        changed["authority"]["outreach"] = True
        with self.assertRaises(ValidationError):
            validate_real_customer_dossier(changed, history=self.history, approved_result=self.result, source_plan=self.plan)

    def test_new_release_rejects_forged_inventory_summary_category_claim(self) -> None:
        changed = copy.deepcopy(self.dossier)
        public_evidence = next(
            item for item in changed["evidence_inventory"]
            if item["evidence_kind"] == "public_source"
        )
        identity = next(
            item for item in changed["categories"]
            if item["category"] == "identity_and_relationships"
        )
        identity.clear()
        identity.update({
            "category": "identity_and_relationships",
            "coverage_state": "complete",
            "claims": [{
                "claim_id": "claim-real-forged-inventory-summary",
                "category": "identity_and_relationships",
                "subject_node_id": changed["entities"][0]["node_id"],
                "typed_value": {"type": "string", "value": "Forged broad summary claim."},
                "basis": "inferred_low_confidence",
                "evidence_refs": [public_evidence["evidence_id"]],
                "confidence_reason": "A retained source summary was incorrectly promoted.",
                "uncertainty": "The source summary has no claim-level verification.",
                "source_date": public_evidence["source_date"],
                "observed_at": public_evidence["observed_at"],
                "freshness_state": "current",
            }],
        })
        validate_real_customer_dossier(
            changed,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        changed["review_state"] = "research_quality_accepted"
        changed["release_state"] = "released_local_data_only"
        with self.assertRaisesRegex(ValidationError, "inventory-only"):
            build_real_lead_intelligence_package(
                changed,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
            )
        package = _real_package_payload(changed)
        package["canonical_hash"] = hashlib.sha256(canonical_bytes(package)).hexdigest()
        with self.assertRaisesRegex(ValidationError, "inventory-only"):
            validate_real_lead_intelligence_package(package, source_plan=self.plan)

    def test_strict_release_requires_exact_mechanical_category_claims(self) -> None:
        for category_name, replace in (
            ("governance_and_history", True),
            ("evidence_coverage", False),
        ):
            with self.subTest(category=category_name):
                changed = copy.deepcopy(self.dossier)
                public_evidence = next(
                    item for item in changed["evidence_inventory"]
                    if item["evidence_kind"] == "public_source"
                )
                category = next(
                    item for item in changed["categories"]
                    if item["category"] == category_name
                )
                forged_claim = {
                    "claim_id": f"claim-real-forged-{category_name}",
                    "category": category_name,
                    "subject_node_id": changed["entities"][0]["node_id"],
                    "typed_value": {"type": "string", "value": "Forged retained summary claim."},
                    "basis": "inferred_low_confidence",
                    "evidence_refs": [public_evidence["evidence_id"]],
                    "confidence_reason": "A retained summary was incorrectly promoted.",
                    "uncertainty": "No claim-level verifier established this value.",
                    "source_date": public_evidence["source_date"],
                    "observed_at": public_evidence["observed_at"],
                    "freshness_state": "current",
                }
                category["claims"] = [forged_claim] if replace else [*category["claims"], forged_claim]
                validate_real_customer_dossier(
                    changed,
                    history=self.history,
                    approved_result=self.result,
                    source_plan=self.plan,
                )
                with self.assertRaisesRegex(ValidationError, "canonical inventory-only"):
                    validate_real_customer_dossier(
                        changed,
                        history=self.history,
                        approved_result=self.result,
                        source_plan=self.plan,
                        require_inventory_only=True,
                    )
                changed["review_state"] = "research_quality_accepted"
                changed["release_state"] = "released_local_data_only"
                with self.assertRaisesRegex(ValidationError, "canonical inventory-only"):
                    build_real_lead_intelligence_package(
                        changed,
                        history=self.history,
                        approved_result=self.result,
                        source_plan=self.plan,
                    )
                package = _real_package_payload(changed)
                package["canonical_hash"] = hashlib.sha256(canonical_bytes(package)).hexdigest()
                with self.assertRaisesRegex(ValidationError, "canonical inventory-only"):
                    validate_real_lead_intelligence_package(package, source_plan=self.plan)

    def test_real_package_is_deterministic_graph_ready_and_conflict_safe(self) -> None:
        released = copy.deepcopy(self.dossier)
        released["review_state"] = "research_quality_accepted"
        released["release_state"] = "released_local_data_only"
        first = build_real_lead_intelligence_package(
            released,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        second = build_real_lead_intelligence_package(
            released,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
            prior_packages=[first],
        )
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertTrue(first["package_id"].startswith("lip-real-v2-"))
        self.assertTrue(first["nodes"])
        self.assertTrue(first["edges"])
        validate_real_lead_intelligence_package(first, source_plan=self.plan)
        conflicting = copy.deepcopy(first)
        conflicting["qualification"]["reason"] = "Changed content."
        payload = copy.deepcopy(conflicting)
        payload.pop("canonical_hash")
        conflicting["canonical_hash"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        with self.assertRaises(ValidationError):
            build_real_lead_intelligence_package(
                released,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
                prior_packages=[conflicting],
            )

    def test_pending_dossier_cannot_produce_a_package(self) -> None:
        with self.assertRaisesRegex(ValidationError, "accepted"):
            build_real_lead_intelligence_package(
                self.dossier,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
            )

    def test_standalone_real_package_rejects_self_consistent_projection_rewrites(self) -> None:
        released = copy.deepcopy(self.dossier)
        released["review_state"] = "research_quality_accepted"
        released["release_state"] = "released_local_data_only"
        package = build_real_lead_intelligence_package(
            released,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )

        def rehash(value: dict) -> dict:
            payload = copy.deepcopy(value)
            payload.pop("canonical_hash", None)
            value["canonical_hash"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
            return value

        mutations = []
        extra_evidence = copy.deepcopy(package)
        extra_evidence["evidence_inventory"][0]["unexpected"] = "rewrite"
        mutations.append(extra_evidence)
        stale_claim = copy.deepcopy(package)
        stale_claim["claims"][0]["freshness_state"] = "stale"
        mutations.append(stale_claim)
        changed_node = copy.deepcopy(package)
        evidence_node = next(node for node in changed_node["nodes"] if node["node_type"] == "evidence")
        evidence_node["attributes"]["source_date"] = "2026-07-20"
        mutations.append(changed_node)
        missing_projection = copy.deepcopy(package)
        missing_projection["edges"] = [
            edge for edge in missing_projection["edges"]
            if edge["edge_type"] != "evidence_supports"
        ]
        mutations.append(missing_projection)
        for changed in mutations:
            with self.subTest(mutation=len(changed.get("edges", []))):
                with self.assertRaises(ValidationError):
                    validate_real_lead_intelligence_package(
                        rehash(changed), source_plan=self.plan
                    )

    def test_real_schemas_are_strict_json_and_v1_contracts_remain_synthetic(self) -> None:
        for path in SCHEMAS:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(parsed["additionalProperties"], path)
        source_schema = json.loads(SCHEMAS[0].read_text(encoding="utf-8"))
        filter_properties = source_schema["properties"]["opportunity_filter"]["properties"]
        self.assertEqual(filter_properties["include_any"]["const"], ["product_photography", "product_video"])
        self.assertEqual(filter_properties["exclude"]["const"], ["ugc_ad"])
        v1_dossier = json.loads(
            (ROOT / "shared" / "prospecting-core" / "schemas" / "customer-dossier.schema.json").read_text()
        )
        self.assertEqual(v1_dossier["properties"]["schema_version"]["const"], 1)
        self.assertTrue(v1_dossier["properties"]["synthetic"]["const"])


if __name__ == "__main__":
    unittest.main()
