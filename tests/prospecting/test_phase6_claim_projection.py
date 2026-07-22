"""Claim-verified Phase 6 real dossier contract tests."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "shared" / "prospecting-core" / "scripts"
TESTS = Path(__file__).resolve().parent
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from common import ValidationError  # noqa: E402
from test_phase6_real_contract import (  # noqa: E402
    COMPLETED,
    MANIFEST,
    bundle,
    empty_history,
    new_classification,
)
from validate_phase6_claim_projection import (  # noqa: E402
    build_claim_verified_real_dossier,
    build_claim_verified_real_package,
    validate_claim_verified_real_dossier,
    validate_claim_verified_real_package,
)
from validate_phase6_real_contract import (  # noqa: E402
    build_real_customer_dossier,
    build_real_lead_intelligence_package,
    build_real_result_projection,
    load_real_source_manifest,
)
from validate_phase6b_contract import CATEGORIES, canonical_bytes  # noqa: E402


MECHANICAL_CATEGORIES = {"governance_and_history", "evidence_coverage"}
DOSSIER_SCHEMA = ROOT / "shared" / "prospecting-core" / "schemas" / "customer-dossier-real.schema.json"
PACKAGE_SCHEMA = ROOT / "shared" / "prospecting-core" / "schemas" / "lead-intelligence-package-real.schema.json"
PROJECTION_SCHEMA = ROOT / "shared" / "prospecting-core" / "schemas" / "phase6-real-claim-projection.schema.json"


class Phase6ClaimProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_real_source_manifest(MANIFEST)["plans"][0]

    def setUp(self) -> None:
        self.plan = copy.deepcopy(self.plan)
        self.history = empty_history()
        _candidate, self.result = build_real_result_projection(
            self.plan, new_classification()
        )
        self.research = bundle(self.plan)
        successful = [item for item in self.research["sources"] if item["status"] == "success"]
        product = next(item for item in successful if "product" in item["source_class"])
        identity = successful[0]
        identity_value = identity["summary"].split(". ", 1)[0] + "."
        product_value = product["summary"].split(". ", 1)[0] + "."
        attempts = []
        for category in CATEGORIES:
            if category in MECHANICAL_CATEGORIES:
                continue
            source = product if category in {"activity_and_signals", "opportunity_and_fit"} else identity
            attempts.append({"category": category, "source_urls": [source["requested_url"]]})
        projection = {
            "projection_version": 1,
            "source_plan_id": self.plan["source_plan_id"],
            "source_plan_hash": self.plan["source_plan_hash"],
            "result_id": self.result["result_id"],
            "research_bundle_hash": hashlib.sha256(canonical_bytes(self.research)).hexdigest(),
            "category_attempts": attempts,
            "observed_claims": [
                {
                    "claim_id": "claim-verified-identity",
                    "category": "identity_and_relationships",
                    "source_url": identity["requested_url"],
                    "value": identity_value,
                    "value_sha256": hashlib.sha256(identity_value.encode("utf-8")).hexdigest(),
                    "confidence_reason": "The exact scrubbed source sentence states this observation.",
                    "uncertainty": "The observation is limited to the approved source and date.",
                },
                {
                    "claim_id": "claim-verified-product-signal",
                    "category": "activity_and_signals",
                    "source_url": product["requested_url"],
                    "value": product_value,
                    "value_sha256": hashlib.sha256(product_value.encode("utf-8")).hexdigest(),
                    "confidence_reason": "The exact scrubbed source sentence states this observation.",
                    "uncertainty": "The observation is not evidence of demand or budget.",
                },
            ],
            "opportunity_inferences": [{
                "claim_id": "claim-verified-product-photo-fit",
                "opportunity_kind": "product_photography",
                "premise_claim_ids": ["claim-verified-product-signal"],
                "confidence_reason": "A verified public product signal supports low-confidence fit review.",
                "uncertainty": "No expressed demand, budget, buying intent, or UGC aversion was found.",
            }],
        }
        projection["canonical_hash"] = hashlib.sha256(canonical_bytes(projection)).hexdigest()
        self.projection = projection
        self.kwargs = {
            "plan": self.plan,
            "research_bundle": self.research,
            "claim_projection": self.projection,
            "history": self.history,
            "approved_result": self.result,
            "approval_id": "dapproval-claim-verified-test",
            "search_request_id": "search-claim-verified-test",
            "history_fingerprint": hashlib.sha256(canonical_bytes(self.history)).hexdigest(),
            "research_cutoff": COMPLETED,
            "maximum_evidence_age_days": 365,
            "version": 1,
        }

    def build(self) -> dict:
        return build_claim_verified_real_dossier(**self.kwargs)

    def test_claim_verified_dossier_populates_categories_and_qa(self) -> None:
        dossier = self.build()
        self.assertEqual(dossier["schema_version"], 3)
        states = {item["category"]: item["coverage_state"] for item in dossier["categories"]}
        self.assertEqual(states["identity_and_relationships"], "complete")
        self.assertEqual(states["activity_and_signals"], "complete")
        self.assertEqual(states["opportunity_and_fit"], "complete")
        self.assertEqual(states["brand_and_messaging"], "not_found")
        self.assertTrue(dossier["automated_evidence_review"]["state"] == "passed")
        self.assertFalse(dossier["qualification"]["search_intent_is_demand_evidence"])
        self.assertFalse(dossier["qualification"]["demand_evidence_found"])
        validate_claim_verified_real_dossier(
            dossier,
            claim_projection=self.projection,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )

    def test_filter_or_source_class_without_verified_opportunity_is_insufficient(self) -> None:
        changed = copy.deepcopy(self.projection)
        changed["opportunity_inferences"] = []
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaisesRegex(ValidationError, "opportunity"):
            self.build()

    def test_claim_must_be_exact_hash_bound_summary_substring(self) -> None:
        for mutation in ("value", "value_sha256", "source_url"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.projection)
                claim = changed["observed_claims"][0]
                if mutation == "value":
                    claim[mutation] = "Unanchored claim."
                    claim["value_sha256"] = hashlib.sha256(claim[mutation].encode()).hexdigest()
                elif mutation == "value_sha256":
                    claim[mutation] = "0" * 64
                else:
                    claim[mutation] = "https://not-approved.example/"
                changed.pop("canonical_hash")
                changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
                self.kwargs["claim_projection"] = changed
                with self.assertRaises(ValidationError):
                    self.build()

    def test_inference_requires_verified_observed_premise(self) -> None:
        changed = copy.deepcopy(self.projection)
        changed["opportunity_inferences"][0]["premise_claim_ids"] = ["claim-missing"]
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaisesRegex(ValidationError, "premise"):
            self.build()

    def test_inference_kind_rejects_non_string_without_crashing(self) -> None:
        changed = copy.deepcopy(self.projection)
        changed["opportunity_inferences"][0]["opportunity_kind"] = []
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaisesRegex(ValidationError, "kind"):
            self.build()

    def test_inference_uncertainty_requires_canonical_denial(self) -> None:
        changed = copy.deepcopy(self.projection)
        changed["opportunity_inferences"][0]["uncertainty"] = (
            "Demand is confirmed and UGC aversion is proven."
        )
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaisesRegex(ValidationError, "canonical no-demand"):
            self.build()

    def test_private_or_contact_value_fails_closed(self) -> None:
        changed = copy.deepcopy(self.projection)
        claim = changed["observed_claims"][0]
        source = next(item for item in self.research["sources"] if item["requested_url"] == claim["source_url"])
        source["summary"] += " Contact press@example.com."
        source["extracted_text_sha256"] = hashlib.sha256(source["summary"].encode()).hexdigest()
        source["extracted_text_byte_length"] = len(source["summary"].encode())
        claim["value"] = "press@example.com"
        claim["value_sha256"] = hashlib.sha256(claim["value"].encode()).hexdigest()
        changed["research_bundle_hash"] = hashlib.sha256(canonical_bytes(self.research)).hexdigest()
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaises(ValidationError):
            self.build()

    def test_qa_and_package_projection_are_exact(self) -> None:
        dossier = self.build()
        changed = copy.deepcopy(dossier)
        changed["automated_evidence_review"]["reviewed_payload_hash"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "automated evidence"):
            validate_claim_verified_real_dossier(
                changed,
                claim_projection=self.projection,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
            )
        released = copy.deepcopy(dossier)
        released["review_state"] = "research_quality_accepted"
        released["release_state"] = "released_local_data_only"
        package = build_claim_verified_real_package(
            released,
            claim_projection=self.projection,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        self.assertEqual(package["schema_version"], 3)
        self.assertEqual(package["automated_evidence_review"], released["automated_evidence_review"])
        validate_claim_verified_real_package(
            package,
            claim_projection=self.projection,
            source_plan=self.plan,
        )
        package["claims"][0]["typed_value"]["value"] = "Changed."
        with self.assertRaises(ValidationError):
            validate_claim_verified_real_package(
                package,
                claim_projection=self.projection,
                source_plan=self.plan,
            )

    def test_candidate_rejects_self_consistent_graph_rewrite(self) -> None:
        dossier = self.build()
        changed = copy.deepcopy(dossier)
        organization = next(
            node for node in changed["entities"] if node["node_type"] == "organization"
        )
        organization["label"] = "Self-consistent organization rewrite"
        reviewed = {
            "approved_result": copy.deepcopy(changed["approved_result"]),
            "source_plan_hash": changed["source_plan_hash"],
            "research_run": copy.deepcopy(changed["research_run"]),
            "source_coverage": copy.deepcopy(changed["source_coverage"]),
            "evidence_inventory": sorted(
                copy.deepcopy(changed["evidence_inventory"]),
                key=lambda item: item["evidence_id"],
            ),
            "categories": copy.deepcopy(changed["categories"]),
            "qualification": copy.deepcopy(changed["qualification"]),
            "entities": sorted(
                copy.deepcopy(changed["entities"]), key=lambda item: item["node_id"]
            ),
            "relationships": sorted(
                copy.deepcopy(changed["relationships"]), key=lambda item: item["edge_id"]
            ),
        }
        changed["automated_evidence_review"]["reviewed_payload_hash"] = hashlib.sha256(
            canonical_bytes(reviewed)
        ).hexdigest()
        with self.assertRaisesRegex(ValidationError, "exact source-plan projection"):
            validate_claim_verified_real_dossier(
                changed,
                claim_projection=self.projection,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
            )

    def test_standalone_package_rejects_self_consistent_claim_rewrite(self) -> None:
        dossier = self.build()
        dossier["review_state"] = "research_quality_accepted"
        dossier["release_state"] = "released_local_data_only"
        package = build_claim_verified_real_package(
            dossier,
            claim_projection=self.projection,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        rewritten = next(
            claim for claim in package["claims"]
            if claim["claim_id"] == "claim-verified-identity"
        )
        rewritten["typed_value"]["value"] = "Self-consistent rewrite."
        claims_by_category = {item: [] for item in CATEGORIES}
        for claim in package["claims"]:
            claims_by_category[claim["category"]].append(copy.deepcopy(claim))
        categories = []
        for coverage in package["coverage_states"]:
            category = {
                "category": coverage["category"],
                "coverage_state": coverage["coverage_state"],
                "claims": sorted(
                    claims_by_category[coverage["category"]],
                    key=lambda item: item["claim_id"],
                ),
            }
            if coverage["gap_explanation"] is not None:
                category["gap_explanation"] = coverage["gap_explanation"]
            categories.append(category)
        reviewed = {
            "approved_result": copy.deepcopy(package["approved_result"]),
            "source_plan_hash": package["source_plan_hash"],
            "research_run": copy.deepcopy(package["research_run"]),
            "source_coverage": copy.deepcopy(package["source_coverage"]),
            "evidence_inventory": copy.deepcopy(package["evidence_inventory"]),
            "categories": categories,
            "qualification": copy.deepcopy(package["qualification"]),
        }
        package["automated_evidence_review"]["reviewed_payload_hash"] = hashlib.sha256(
            canonical_bytes(reviewed)
        ).hexdigest()
        hash_payload = {key: value for key, value in package.items() if key != "canonical_hash"}
        package["canonical_hash"] = hashlib.sha256(canonical_bytes(hash_payload)).hexdigest()
        with self.assertRaisesRegex(ValidationError, "exact verified-claim projection"):
            validate_claim_verified_real_package(package, source_plan=self.plan)

    def test_package_identity_type_fails_closed_as_validation_error(self) -> None:
        dossier = self.build()
        dossier["review_state"] = "research_quality_accepted"
        dossier["release_state"] = "released_local_data_only"
        package = build_claim_verified_real_package(
            dossier,
            claim_projection=self.projection,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        for field, invalid in (("business_unit", []), ("idempotency_identity", {})):
            with self.subTest(field=field):
                changed = copy.deepcopy(package)
                changed[field] = invalid
                changed["canonical_hash"] = hashlib.sha256(canonical_bytes({
                    key: value for key, value in changed.items() if key != "canonical_hash"
                })).hexdigest()
                with self.assertRaisesRegex(ValidationError, "identity fields"):
                    validate_claim_verified_real_package(changed, source_plan=self.plan)

    def test_failed_source_becomes_explicit_unknown_gap(self) -> None:
        source_url = self.projection["observed_claims"][0]["source_url"]
        source = next(
            item for item in self.research["sources"] if item["requested_url"] == source_url
        )
        source.update({
            "final_url": None,
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
        source["source_date"] = None
        source["date_state"] = "unavailable"
        changed = copy.deepcopy(self.projection)
        changed["observed_claims"] = [
            claim for claim in changed["observed_claims"]
            if claim["source_url"] != source_url
        ]
        current_url = next(
            item["requested_url"]
            for item in self.research["sources"]
            if item["status"] == "success"
        )
        current_source = next(
            item for item in self.research["sources"]
            if item["requested_url"] == current_url
        )
        current_value = current_source["summary"].split(". ", 1)[0] + "."
        changed["observed_claims"].append({
            "claim_id": "claim-partial-brand",
            "category": "brand_and_messaging",
            "source_url": current_url,
            "value": current_value,
            "value_sha256": hashlib.sha256(current_value.encode()).hexdigest(),
            "confidence_reason": "The exact scrubbed source sentence states this observation.",
            "uncertainty": "The category also has one failed assigned source.",
        })
        brand_attempt = next(
            item for item in changed["category_attempts"]
            if item["category"] == "brand_and_messaging"
        )
        brand_attempt["source_urls"] = [source_url, current_url]
        changed["research_bundle_hash"] = hashlib.sha256(
            canonical_bytes(self.research)
        ).hexdigest()
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        dossier = self.build()
        category = next(
            item for item in dossier["categories"]
            if item["category"] == "identity_and_relationships"
        )
        self.assertEqual(category["coverage_state"], "unknown")
        self.assertEqual(category["claims"], [])
        brand = next(
            item for item in dossier["categories"]
            if item["category"] == "brand_and_messaging"
        )
        self.assertEqual(brand["coverage_state"], "unknown")

    def test_partial_premise_or_opportunity_attempt_cannot_qualify(self) -> None:
        product_url = self.projection["observed_claims"][1]["source_url"]
        failed_url = next(
            item["requested_url"]
            for item in self.research["sources"]
            if item["requested_url"] != product_url and item["status"] == "success"
        )
        for category in ("activity_and_signals", "opportunity_and_fit"):
            with self.subTest(category=category):
                research = copy.deepcopy(self.research)
                failed = next(
                    item for item in research["sources"]
                    if item["requested_url"] == failed_url
                )
                failed.update({
                    "final_url": None,
                    "status": "failed",
                    "safe_reason_code": "access_control",
                    "content_type": None,
                    "source_date": None,
                    "date_state": "unavailable",
                    "body_sha256": None,
                    "body_byte_length": 0,
                    "extracted_text_sha256": None,
                    "extracted_text_byte_length": 0,
                    "summary": "",
                    "conflict_state": "none",
                })
                projection = copy.deepcopy(self.projection)
                projection["observed_claims"] = [
                    claim for claim in projection["observed_claims"]
                    if claim["source_url"] != failed_url
                ]
                attempt = next(
                    item for item in projection["category_attempts"]
                    if item["category"] == category
                )
                attempt["source_urls"].append(failed_url)
                projection["research_bundle_hash"] = hashlib.sha256(
                    canonical_bytes(research)
                ).hexdigest()
                projection.pop("canonical_hash")
                projection["canonical_hash"] = hashlib.sha256(
                    canonical_bytes(projection)
                ).hexdigest()
                with self.assertRaisesRegex(ValidationError, "No current verified opportunity"):
                    build_claim_verified_real_dossier(
                        **{
                            **self.kwargs,
                            "research_bundle": research,
                            "claim_projection": projection,
                        }
                    )

    def test_conflicted_assigned_source_fails_closed(self) -> None:
        source_url = self.projection["observed_claims"][0]["source_url"]
        source = next(
            item for item in self.research["sources"] if item["requested_url"] == source_url
        )
        source["conflict_state"] = "conflicted"
        changed = copy.deepcopy(self.projection)
        changed["research_bundle_hash"] = hashlib.sha256(
            canonical_bytes(self.research)
        ).hexdigest()
        changed.pop("canonical_hash")
        changed["canonical_hash"] = hashlib.sha256(canonical_bytes(changed)).hexdigest()
        self.kwargs["claim_projection"] = changed
        with self.assertRaisesRegex(ValidationError, "freshness must be conflicted"):
            self.build()

    def test_standalone_package_rejects_self_consistent_graph_rewrite(self) -> None:
        dossier = self.build()
        dossier["review_state"] = "research_quality_accepted"
        dossier["release_state"] = "released_local_data_only"
        package = build_claim_verified_real_package(
            dossier,
            claim_projection=self.projection,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        organization = next(
            node for node in package["nodes"] if node["node_type"] == "organization"
        )
        organization["label"] = "Self-consistent organization rewrite"
        payload = {key: value for key, value in package.items() if key != "canonical_hash"}
        package["canonical_hash"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        with self.assertRaisesRegex(ValidationError, "exact dossier projection"):
            validate_claim_verified_real_package(package, source_plan=self.plan)

    def test_historical_v2_contract_is_unchanged(self) -> None:
        legacy = build_real_customer_dossier(
            **{key: value for key, value in self.kwargs.items() if key != "claim_projection"}
        )
        self.assertEqual(legacy["schema_version"], 2)
        released = copy.deepcopy(legacy)
        released["review_state"] = "research_quality_accepted"
        released["release_state"] = "released_local_data_only"
        package = build_real_lead_intelligence_package(
            released,
            history=self.history,
            approved_result=self.result,
            source_plan=self.plan,
        )
        self.assertEqual(package["schema_version"], 2)

    def test_malformed_prior_package_fails_closed(self) -> None:
        dossier = self.build()
        dossier["review_state"] = "research_quality_accepted"
        dossier["release_state"] = "released_local_data_only"
        with self.assertRaisesRegex(ValidationError, "must be objects"):
            build_claim_verified_real_package(
                dossier,
                claim_projection=self.projection,
                history=self.history,
                approved_result=self.result,
                source_plan=self.plan,
                prior_packages=[[]],
            )

    def test_machine_readable_schemas_add_v3_without_removing_v2(self) -> None:
        dossier_schema = json.loads(DOSSIER_SCHEMA.read_text(encoding="utf-8"))
        package_schema = json.loads(PACKAGE_SCHEMA.read_text(encoding="utf-8"))
        projection_schema = json.loads(PROJECTION_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(dossier_schema["properties"]["schema_version"]["enum"], [2, 3])
        self.assertEqual(package_schema["properties"]["schema_version"]["enum"], [2, 3])
        self.assertIn("claimProjection", dossier_schema["$defs"])
        self.assertIn("automatedEvidenceReview", dossier_schema["$defs"])
        self.assertEqual(
            dossier_schema["$defs"]["observedClaim"]["properties"]["uncertainty"]["type"],
            "string",
        )
        self.assertEqual(
            dossier_schema["$defs"]["opportunityInference"]["properties"]["uncertainty"]["const"],
            "No expressed demand, budget, buying intent, or UGC aversion was found.",
        )
        self.assertEqual(
            projection_schema["$ref"],
            "customer-dossier-real.schema.json#/$defs/claimProjection",
        )


if __name__ == "__main__":
    unittest.main()
