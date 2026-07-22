from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "shared/prospecting-core/scripts"
sys.path.insert(0, str(CORE))

from common import ValidationError  # noqa: E402
from validate_phase6b_contract import (  # noqa: E402
    ContractConflict,
    build_lead_intelligence_package,
    canonical_bytes,
    filter_candidates,
    load_json_strict,
    validate_customer_dossier,
    validate_fixture_bundle,
    validate_lead_intelligence_package,
    validate_search_request,
)


class Phase6BContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_path = ROOT / "fixtures/prospecting/phase6/dossier-fixtures.json"
        cls.history_path = ROOT / "fixtures/prospecting/phase6/dossier-history.json"
        cls.bundle = load_json_strict(cls.fixture_path, "Phase 6B fixtures")
        cls.history_bundle = load_json_strict(cls.history_path, "Phase 6B history")
        cls.requests = {item["request_id"]: item for item in cls.bundle["search_requests"]}
        cls.dossiers = {item["dossier_id"]: item for item in cls.bundle["dossiers"]}

    def history_for(self, business_unit: str) -> dict:
        return copy.deepcopy(self.history_bundle["histories"][business_unit])

    def candidates_for(self, business_unit: str) -> list[dict]:
        return [copy.deepcopy(item) for item in self.bundle["candidates"] if item["business_unit"] == business_unit]

    def evaluation(self, request_id: str) -> dict:
        request = copy.deepcopy(self.requests[request_id])
        business_unit = request["business_unit"]
        return filter_candidates(
            request,
            self.candidates_for(business_unit),
            self.history_for(business_unit),
            today=date(2026, 7, 21),
        )

    def approved_result(self, dossier: dict) -> dict:
        evaluation = self.evaluation(dossier["search_request_id"])
        result_id = dossier["approved_result"]["result_id"]
        return next(item for item in evaluation["decisions"] if item["result_id"] == result_id)

    def validate_dossier(self, dossier_id: str) -> dict:
        dossier = copy.deepcopy(self.dossiers[dossier_id])
        return validate_customer_dossier(
            dossier,
            history=self.history_for(dossier["business_unit"]),
            approved_result=self.approved_result(dossier),
        )

    def package_for(self, dossier_id: str = "dossier-umg-orbit") -> dict:
        dossier = self.validate_dossier(dossier_id)
        return build_lead_intelligence_package(
            dossier,
            history=self.history_for(dossier["business_unit"]),
            approved_result=self.approved_result(dossier),
        )

    @staticmethod
    def rehash_package(package: dict) -> dict:
        package = copy.deepcopy(package)
        package.pop("canonical_hash", None)
        package["canonical_hash"] = hashlib.sha256(canonical_bytes(package)).hexdigest()
        return package

    def test_fixture_bundle_and_both_business_units_validate(self) -> None:
        result = validate_fixture_bundle(self.bundle, self.history_bundle)
        self.assertEqual(result["validated_dossiers"], ["dossier-talent-lumen", "dossier-umg-orbit"])
        self.assertEqual(result["validated_packages"], 2)

    def test_bundle_rejects_malformed_unsupported_and_unroutable_candidates(self) -> None:
        invalid_candidates = [
            "not-an-object",
            {"business_unit": "unsupported"},
            {**copy.deepcopy(self.bundle["candidates"][0]), "business_unit": "unreal-talent", "candidate_id": "candidate-unroutable"},
        ]
        for candidate in invalid_candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                bundle = copy.deepcopy(self.bundle)
                if isinstance(candidate, dict) and candidate.get("candidate_id") == "candidate-unroutable":
                    bundle["search_requests"] = [request for request in bundle["search_requests"] if request["business_unit"] != "unreal-talent"]
                    bundle["dossiers"] = [dossier for dossier in bundle["dossiers"] if dossier["business_unit"] != "unreal-talent"]
                bundle["candidates"].append(candidate)
                validate_fixture_bundle(bundle, self.history_bundle)

    def test_bundle_rejects_duplicate_dossiers_and_idempotency_conflicts(self) -> None:
        duplicate = copy.deepcopy(self.bundle)
        duplicate["dossiers"].append(copy.deepcopy(duplicate["dossiers"][0]))
        with self.assertRaisesRegex(ValidationError, "dossier IDs"):
            validate_fixture_bundle(duplicate, self.history_bundle)

        conflict = copy.deepcopy(self.bundle)
        changed = copy.deepcopy(conflict["dossiers"][0])
        changed["dossier_id"] = "dossier-umg-orbit-v2"
        changed["version"] = 2
        changed["categories"][0]["claims"][0]["typed_value"]["value"] += " changed"
        conflict["dossiers"].append(changed)
        with self.assertRaises(ContractConflict):
            validate_fixture_bundle(conflict, self.history_bundle)

    def test_product_photo_video_inclusion_and_ugc_exclusion(self) -> None:
        evaluation = self.evaluation("search-umg-creative")
        decisions = {item["candidate_id"]: item for item in evaluation["decisions"]}
        self.assertEqual(decisions["candidate-umg-orbit"]["filter_state"], "matched")
        self.assertTrue(decisions["candidate-umg-orbit"]["selected"])
        self.assertEqual(decisions["candidate-umg-ugc"]["filter_state"], "excluded")
        self.assertFalse(decisions["candidate-umg-ugc"]["selected"])
        self.assertEqual(decisions["candidate-umg-low-score"]["filter_state"], "matched")
        self.assertEqual(decisions["candidate-umg-low-score"]["qualification_state"], "below_threshold")

    def test_absent_filter_preserves_unfiltered_behavior(self) -> None:
        request = copy.deepcopy(self.requests["search-talent-open"])
        self.assertNotIn("opportunity_filter", request)
        evaluation = self.evaluation("search-talent-open")
        decision = evaluation["decisions"][0]
        self.assertEqual(decision["filter_state"], "not_requested")
        self.assertTrue(decision["selected"])

    def test_filter_rejects_malformed_unknown_duplicate_overlap_and_empty(self) -> None:
        base = copy.deepcopy(self.requests["search-umg-creative"])
        invalid_filters = [
            [],
            {},
            {"include_any": "product_video"},
            {"include_any": ["unknown"]},
            {"include_any": ["product_video", "product_video"]},
            {"include_any": ["product_video"], "exclude": ["product_video"]},
            {"include_any": [], "exclude": []},
            {"include_any": ["product_video"], "extra": []},
        ]
        for value in invalid_filters:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                request = copy.deepcopy(base)
                request["opportunity_filter"] = value
                validate_search_request(request)

    def test_exclusion_precedes_inclusion(self) -> None:
        evaluation = self.evaluation("search-umg-creative")
        decision = next(item for item in evaluation["decisions"] if item["candidate_id"] == "candidate-umg-ugc")
        self.assertEqual(decision["matched_opportunities"], ["product_video", "ugc_ad"])
        self.assertEqual(decision["filter_state"], "excluded")

    def test_history_protections_and_reengagement_precede_filtering(self) -> None:
        decisions = {item["candidate_id"]: item for item in self.evaluation("search-umg-creative")["decisions"]}
        expected = {
            "candidate-umg-duplicate": "existing_no_new_trigger",
            "candidate-umg-suppressed": "suppressed",
            "candidate-umg-client": "existing_client",
            "candidate-umg-cooldown": "existing_no_new_trigger",
            "candidate-umg-subbrand": "distinct_subbrand",
            "candidate-umg-reengage": "existing_new_trigger",
        }
        for candidate_id, status in expected.items():
            with self.subTest(candidate_id=candidate_id):
                self.assertEqual(decisions[candidate_id]["history_classification"]["status"], status)
        for candidate_id in ("candidate-umg-duplicate", "candidate-umg-suppressed", "candidate-umg-client", "candidate-umg-cooldown"):
            self.assertEqual(decisions[candidate_id]["filter_state"], "not_evaluated_history_protection")
            self.assertFalse(decisions[candidate_id]["selected"])
        self.assertTrue(decisions["candidate-umg-reengage"]["selected"])

    def test_changed_filter_never_turns_existing_identity_new(self) -> None:
        request = copy.deepcopy(self.requests["search-umg-creative"])
        request["opportunity_filter"] = {"include_any": ["product_photography"]}
        decisions = filter_candidates(
            request,
            self.candidates_for("unreal-media-group"),
            self.history_for("unreal-media-group"),
            today=date(2026, 7, 21),
        )["decisions"]
        duplicate = next(item for item in decisions if item["candidate_id"] == "candidate-umg-duplicate")
        self.assertEqual(duplicate["history_classification"]["status"], "existing_no_new_trigger")

    def test_missing_invalid_or_ambiguous_history_fails_closed(self) -> None:
        request = copy.deepcopy(self.requests["search-umg-creative"])
        candidates = self.candidates_for("unreal-media-group")
        for history in (None, {}, {"version": 1, "generated_at": "bad", "prospects": []}):
            with self.subTest(history=history), self.assertRaises(ValidationError):
                filter_candidates(request, candidates, history, today=date(2026, 7, 21))
        ambiguous = self.history_for("unreal-media-group")
        duplicate = copy.deepcopy(ambiguous["prospects"][0])
        duplicate.update({"id": "history-ambiguous", "canonical_company_name": "Synthetic Other", "canonical_domain": "other.example", "domains": [ambiguous["prospects"][0]["canonical_domain"]]})
        ambiguous["prospects"].append(duplicate)
        with self.assertRaisesRegex(ValidationError, "ambiguous"):
            filter_candidates(request, candidates, ambiguous, today=date(2026, 7, 21))

    def test_business_unit_scope_rejects_foreign_candidate_without_disclosure(self) -> None:
        request = copy.deepcopy(self.requests["search-umg-creative"])
        foreign = self.candidates_for("unreal-talent")[0]
        with self.assertRaisesRegex(ValidationError, "business unit does not match request") as raised:
            filter_candidates(request, [foreign], self.history_for("unreal-media-group"), today=date(2026, 7, 21))
        self.assertNotIn(foreign["candidate_id"], str(raised.exception))

    def test_exact_eleven_category_coverage_for_both_business_units(self) -> None:
        for dossier_id in self.dossiers:
            with self.subTest(dossier_id=dossier_id):
                dossier = self.validate_dossier(dossier_id)
                self.assertEqual(len(dossier["categories"]), 11)
                self.assertEqual(len({item["category"] for item in dossier["categories"]}), 11)

    def test_all_gap_states_and_state_claim_combinations(self) -> None:
        dossier = self.validate_dossier("dossier-talent-lumen")
        gaps = {item["coverage_state"] for item in dossier["categories"] if item["coverage_state"] != "complete"}
        self.assertEqual(gaps, {"not_found", "not_applicable", "conflicted", "stale", "unknown"})

        complete = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        complete["categories"][0]["claims"] = []
        with self.assertRaises(ValidationError):
            validate_customer_dossier(complete, history=self.history_for(complete["business_unit"]), approved_result=self.approved_result(complete))

        gap_with_claim = copy.deepcopy(self.dossiers["dossier-talent-lumen"])
        gap_category = next(item for item in gap_with_claim["categories"] if item["coverage_state"] != "complete")
        gap_category["claims"] = [copy.deepcopy(next(item for item in gap_with_claim["categories"] if item["coverage_state"] == "complete")["claims"][0])]
        with self.assertRaises(ValidationError):
            validate_customer_dossier(gap_with_claim, history=self.history_for(gap_with_claim["business_unit"]), approved_result=self.approved_result(gap_with_claim))

    def test_stale_future_conflicting_and_missing_evidence(self) -> None:
        dossier = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        claim = dossier["categories"][0]["claims"][0]
        evidence = next(item for item in dossier["evidence_inventory"] if item["evidence_id"] in claim["evidence_refs"])

        stale = copy.deepcopy(dossier)
        stale_evidence = next(item for item in stale["evidence_inventory"] if item["evidence_id"] == evidence["evidence_id"])
        stale_evidence["source_date"] = "2020-01-01"
        for category in stale["categories"]:
            for stale_claim in category["claims"]:
                if evidence["evidence_id"] in stale_claim["evidence_refs"]:
                    stale_claim.update({"source_date": "2020-01-01", "freshness_state": "stale"})
        validate_customer_dossier(stale, history=self.history_for(stale["business_unit"]), approved_result=self.approved_result(stale))

        future = copy.deepcopy(dossier)
        future["evidence_inventory"][0]["source_date"] = "2099-01-01"
        with self.assertRaises(ValidationError):
            validate_customer_dossier(future, history=self.history_for(future["business_unit"]), approved_result=self.approved_result(future))

        conflicted = copy.deepcopy(dossier)
        conflicted["evidence_inventory"][0]["conflict_state"] = "conflicted"
        with self.assertRaises(ValidationError):
            validate_customer_dossier(conflicted, history=self.history_for(conflicted["business_unit"]), approved_result=self.approved_result(conflicted))

        missing = copy.deepcopy(dossier)
        missing["categories"][0]["claims"][0]["evidence_refs"] = ["missing-evidence"]
        with self.assertRaises(ValidationError):
            validate_customer_dossier(missing, history=self.history_for(missing["business_unit"]), approved_result=self.approved_result(missing))

    def test_duplicate_and_dangling_identifiers_or_references_fail(self) -> None:
        dossier = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        dossier["categories"][1]["claims"][0]["claim_id"] = dossier["categories"][0]["claims"][0]["claim_id"]
        with self.assertRaises(ValidationError):
            validate_customer_dossier(dossier, history=self.history_for(dossier["business_unit"]), approved_result=self.approved_result(dossier))

        dossier = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        dossier["relationships"][0]["claim_refs"] = []
        dossier["relationships"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(ValidationError, "provenance"):
            validate_customer_dossier(dossier, history=self.history_for(dossier["business_unit"]), approved_result=self.approved_result(dossier))

        dossier = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        dossier["relationships"][0]["to_node_id"] = "missing-node"
        with self.assertRaises(ValidationError):
            validate_customer_dossier(dossier, history=self.history_for(dossier["business_unit"]), approved_result=self.approved_result(dossier))

    def test_exact_result_identity_and_business_unit_binding(self) -> None:
        base = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        for field, replacement in (("result_id", "result-other"), ("global_identity_id", "global-other"), ("account_id", "account-other")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                dossier = copy.deepcopy(base)
                dossier["approved_result"][field] = replacement
                validate_customer_dossier(dossier, history=self.history_for(dossier["business_unit"]), approved_result=self.approved_result(base))
        foreign = copy.deepcopy(base)
        foreign["business_unit"] = "unreal-talent"
        with self.assertRaises(ValidationError):
            validate_customer_dossier(foreign, history=self.history_for("unreal-talent"), approved_result=self.approved_result(base))

    def test_unsafe_urls_credentials_contacts_nonfinite_and_limits_fail(self) -> None:
        mutations = []
        unsafe_url = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        unsafe_url["evidence_inventory"][0]["source_url"] = "https://user:secret@orbit.example/source"
        mutations.append(unsafe_url)
        invalid_port = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        invalid_port["evidence_inventory"][0]["source_url"] = "https://orbit.example:invalid/source"
        mutations.append(invalid_port)
        private_contact = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        private_contact["categories"][0]["claims"][0]["typed_value"]["value"] = "person@example.com"
        mutations.append(private_contact)
        phone = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        phone["categories"][0]["claims"][0]["typed_value"]["value"] = "+1 212 555 1234"
        mutations.append(phone)
        credential = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        credential["entities"][0]["attributes"]["accessToken"] = "synthetic-secret"
        mutations.append(credential)
        nonfinite = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        nonfinite["categories"][0]["claims"][0]["typed_value"] = {"type": "number", "value": math.nan}
        mutations.append(nonfinite)
        too_large = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        too_large["categories"][0]["claims"][0]["uncertainty"] = "x" * 5000
        mutations.append(too_large)
        too_deep = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        too_deep["entities"][0]["attributes"]["nested"] = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": "x"}}}}}}}}}}
        mutations.append(too_deep)
        for dossier in mutations:
            with self.subTest(value=dossier["dossier_id"]), self.assertRaises(ValidationError):
                validate_customer_dossier(dossier, history=self.history_for(dossier["business_unit"]), approved_result=self.approved_result(dossier))

    def test_package_is_canonical_deterministic_and_reference_complete(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        approved = self.approved_result(dossier)
        history = self.history_for(dossier["business_unit"])
        first = build_lead_intelligence_package(dossier, history=history, approved_result=approved)
        second = build_lead_intelligence_package(copy.deepcopy(dossier), history=copy.deepcopy(history), approved_result=copy.deepcopy(approved))
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))
        self.assertEqual(first["package_id"], second["package_id"])
        node_ids = {item["node_id"] for item in first["nodes"]}
        claim_ids = {item["claim_id"] for item in first["claims"]}
        evidence_ids = {item["evidence_id"] for item in first["evidence_inventory"]}
        for edge in first["edges"]:
            self.assertIn(edge["from_node_id"], node_ids)
            self.assertIn(edge["to_node_id"], node_ids)
            self.assertLessEqual(set(edge["claim_refs"]), claim_ids)
            self.assertLessEqual(set(edge["evidence_refs"]), evidence_ids)

    def test_changed_content_under_same_idempotency_identity_conflicts(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        approved = self.approved_result(dossier)
        history = self.history_for(dossier["business_unit"])
        existing = build_lead_intelligence_package(dossier, history=history, approved_result=approved)
        replay = build_lead_intelligence_package(dossier, history=history, approved_result=approved, prior_packages=[existing])
        self.assertEqual(canonical_bytes(existing), canonical_bytes(replay))
        changed = copy.deepcopy(dossier)
        changed["categories"][0]["claims"][0]["typed_value"]["value"] += " changed"
        with self.assertRaises(ContractConflict):
            build_lead_intelligence_package(changed, history=history, approved_result=approved, prior_packages=[existing])

    def test_prior_package_conflicts_are_order_independent(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        approved = self.approved_result(dossier)
        history = self.history_for(dossier["business_unit"])
        identical = build_lead_intelligence_package(dossier, history=history, approved_result=approved)
        changed = copy.deepcopy(dossier)
        changed["categories"][0]["claims"][0]["typed_value"]["value"] += " changed"
        conflicting = build_lead_intelligence_package(changed, history=history, approved_result=approved)

        for prior_packages in ([identical, conflicting], [conflicting, identical]):
            with self.subTest(order=[item["canonical_hash"] for item in prior_packages]), self.assertRaises(ContractConflict):
                build_lead_intelligence_package(
                    dossier,
                    history=history,
                    approved_result=approved,
                    prior_packages=prior_packages,
                )

        replay = build_lead_intelligence_package(
            dossier,
            history=history,
            approved_result=approved,
            prior_packages=[copy.deepcopy(identical), identical],
        )
        self.assertEqual(canonical_bytes(replay), canonical_bytes(identical))

    def test_entity_ids_are_stable_across_dossier_versions(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        approved = self.approved_result(dossier)
        history = self.history_for(dossier["business_unit"])
        first = build_lead_intelligence_package(dossier, history=history, approved_result=approved)
        changed = copy.deepcopy(dossier)
        changed["version"] = 2
        changed["idempotency_identity"] = "phase6b:umg:result-umg-orbit:2"
        changed["categories"][0]["claims"][0]["typed_value"]["value"] += " version two"
        second = build_lead_intelligence_package(changed, history=history, approved_result=approved)
        first_entities = {item["node_id"] for item in first["nodes"] if item["node_type"] != "evidence"}
        second_entities = {item["node_id"] for item in second["nodes"] if item["node_type"] != "evidence"}
        self.assertEqual(first_entities, second_entities)

    def test_reload_reproduces_history_classification_dossier_and_package(self) -> None:
        first_bundle = load_json_strict(self.fixture_path)
        second_bundle = load_json_strict(self.fixture_path)
        first_history = load_json_strict(self.history_path)
        second_history = load_json_strict(self.history_path)
        self.assertEqual(canonical_bytes(first_bundle), canonical_bytes(second_bundle))
        self.assertEqual(canonical_bytes(first_history), canonical_bytes(second_history))
        self.assertEqual(validate_fixture_bundle(first_bundle, first_history), validate_fixture_bundle(second_bundle, second_history))

    def test_package_authority_flags_are_inert(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        package = build_lead_intelligence_package(
            dossier,
            history=self.history_for(dossier["business_unit"]),
            approved_result=self.approved_result(dossier),
        )
        self.assertTrue(package["authority"]["local_data_handoff_only"])
        self.assertEqual(
            {key for key, value in package["authority"].items() if value is False},
            {"generation", "contact", "outreach", "external_write", "agent_invocation", "deployment"},
        )

    def test_dossier_rejects_projected_identifier_collisions(self) -> None:
        original = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        evidence_id = original["evidence_inventory"][0]["evidence_id"]
        claim_id = original["categories"][0]["claims"][0]["claim_id"]
        collisions = {
            "node": lambda dossier: dossier["entities"].append(
                {
                    **copy.deepcopy(dossier["entities"][0]),
                    "node_id": f"evidence:{evidence_id}",
                    "label": "Synthetic projected-node collision",
                }
            ),
            "edge": lambda dossier: dossier["relationships"].append(
                {
                    **copy.deepcopy(dossier["relationships"][0]),
                    "edge_id": f"evidence-supports:{evidence_id}:{claim_id}",
                }
            ),
        }
        for label, mutate in collisions.items():
            with self.subTest(label=label), self.assertRaisesRegex(ValidationError, f"projected package {label} IDs must be unique"):
                dossier = copy.deepcopy(original)
                mutate(dossier)
                validate_customer_dossier(
                    dossier,
                    history=self.history_for(dossier["business_unit"]),
                    approved_result=self.approved_result(dossier),
                )

    def test_rehashed_package_rejects_derived_identity_order_and_projection_mutations(self) -> None:
        original = self.package_for()

        def remove_projection(package: dict) -> None:
            package["nodes"] = [node for node in package["nodes"] if node["node_type"] != "evidence"]
            package["edges"] = [edge for edge in package["edges"] if edge["edge_type"] != "evidence_supports"]

        mutations = {
            "package_id": lambda package: package.__setitem__("package_id", "lip-any-valid-id"),
            "duplicate_history": lambda package: package.__setitem__("duplicate_history", {}),
            "claims_order": lambda package: package.__setitem__("claims", list(reversed(package["claims"]))),
            "evidence_order": lambda package: package.__setitem__("evidence_inventory", list(reversed(package["evidence_inventory"]))),
            "nodes_order": lambda package: package.__setitem__("nodes", list(reversed(package["nodes"]))),
            "edges_order": lambda package: package.__setitem__("edges", list(reversed(package["edges"]))),
            "projection": remove_projection,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValidationError):
                package = copy.deepcopy(original)
                mutate(package)
                validate_lead_intelligence_package(self.rehash_package(package))

    def test_duplicate_history_requires_allowed_consistent_semantics(self) -> None:
        original = self.package_for()
        mutations = [
            {},
            {**original["duplicate_history"], "status": "unsupported"},
            {**original["duplicate_history"], "matched_prospect_id": "history-unlisted"},
            {**original["duplicate_history"], "reengagement": True},
        ]
        for duplicate_history in mutations:
            with self.subTest(duplicate_history=duplicate_history), self.assertRaises(ValidationError):
                package = copy.deepcopy(original)
                package["duplicate_history"] = duplicate_history
                validate_lead_intelligence_package(self.rehash_package(package))

    def test_rehashed_package_rejects_temporal_and_freshness_inconsistency(self) -> None:
        original = self.package_for()

        def future_evidence_date(package: dict) -> None:
            evidence = package["evidence_inventory"][0]
            evidence["source_date"] = "2026-07-22"
            next(node for node in package["nodes"] if node["node_id"] == f"evidence:{evidence['evidence_id']}")["attributes"]["source_date"] = "2026-07-22"

        def future_evidence_observation(package: dict) -> None:
            package["evidence_inventory"][0]["observed_at"] = "2026-07-22T00:00:00Z"

        def future_claim_date(package: dict) -> None:
            package["claims"][0]["source_date"] = "2026-07-22"

        def future_claim_observation(package: dict) -> None:
            package["claims"][0]["observed_at"] = "2026-07-22T00:00:00Z"

        def mismatched_source_date(package: dict) -> None:
            package["claims"][0]["source_date"] = "2026-07-09"

        def conflicted_current(package: dict) -> None:
            evidence_id = package["claims"][0]["evidence_refs"][0]
            next(item for item in package["evidence_inventory"] if item["evidence_id"] == evidence_id)["conflict_state"] = "conflicted"

        def stale_current(package: dict) -> None:
            evidence_id = package["claims"][0]["evidence_refs"][0]
            next(item for item in package["evidence_inventory"] if item["evidence_id"] == evidence_id)["source_date"] = "2025-01-01"
            next(node for node in package["nodes"] if node["node_id"] == f"evidence:{evidence_id}")["attributes"]["source_date"] = "2025-01-01"
            for claim in package["claims"]:
                if evidence_id in claim["evidence_refs"]:
                    claim["source_date"] = "2025-01-01"

        mutations = {
            "future evidence date": future_evidence_date,
            "future evidence observation": future_evidence_observation,
            "future claim date": future_claim_date,
            "future claim observation": future_claim_observation,
            "claim/evidence date mismatch": mismatched_source_date,
            "conflicted evidence with current claim": conflicted_current,
            "stale evidence with current claim": stale_current,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), self.assertRaises(ValidationError):
                package = copy.deepcopy(original)
                mutate(package)
                validate_lead_intelligence_package(self.rehash_package(package))

    def test_package_carries_bounded_maximum_evidence_age(self) -> None:
        original = self.package_for()
        self.assertEqual(original.get("maximum_evidence_age_days"), 365)

        for invalid_age in (True, 0, 3651):
            with self.subTest(maximum_evidence_age_days=invalid_age), self.assertRaises(ValidationError):
                package = copy.deepcopy(original)
                package["maximum_evidence_age_days"] = invalid_age
                validate_lead_intelligence_package(self.rehash_package(package))

    def test_dossier_projection_limits_admit_151_entities_and_evidence(self) -> None:
        dossier = copy.deepcopy(self.dossiers["dossier-umg-orbit"])
        evidence_template = copy.deepcopy(dossier["evidence_inventory"][0])
        entity_template = copy.deepcopy(dossier["entities"][0])
        index = 2
        while len(dossier["evidence_inventory"]) < 151:
            evidence = copy.deepcopy(evidence_template)
            evidence.update({"evidence_id": f"evidence-extra-{index}", "source_url": f"https://extra-{index}.example/source"})
            dossier["evidence_inventory"].append(evidence)
            index += 1
        index = 2
        while len(dossier["entities"]) < 151:
            entity = copy.deepcopy(entity_template)
            entity.update({"node_id": f"org-extra-{index}", "label": f"Synthetic Extra {index}", "attributes": {"canonical_domain": f"extra-{index}.example"}})
            dossier["entities"].append(entity)
            index += 1
        approved = self.approved_result(dossier)
        package = build_lead_intelligence_package(
            dossier,
            history=self.history_for(dossier["business_unit"]),
            approved_result=approved,
        )
        self.assertEqual(len(package["nodes"]), 302)
        validate_lead_intelligence_package(package)

    def test_standalone_package_rejects_inconsistent_coverage(self) -> None:
        dossier = self.validate_dossier("dossier-umg-orbit")
        package = build_lead_intelligence_package(
            dossier,
            history=self.history_for(dossier["business_unit"]),
            approved_result=self.approved_result(dossier),
        )
        package["coverage_states"][0].update({"coverage_state": "unknown", "gap_explanation": "Synthetic gap."})
        payload = copy.deepcopy(package)
        payload.pop("canonical_hash")
        package["canonical_hash"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        with self.assertRaisesRegex(ValidationError, "coverage and claims"):
            validate_lead_intelligence_package(package)

    def test_contract_module_has_no_prohibited_execution_surface(self) -> None:
        source = (CORE / "validate_phase6b_contract.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        prohibited_modules = {"requests", "urllib.request", "http.client", "socket", "subprocess", "sqlite3"}
        self.assertFalse(imports & prohibited_modules)
        for token in ("os.system(", "eval(", "exec(", "urlopen(", "Popen("):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_three_phase6b_schemas_are_strict_json(self) -> None:
        names = (
            "phase6b-search-request.schema.json",
            "customer-dossier.schema.json",
            "lead-intelligence-package.schema.json",
        )
        for name in names:
            with self.subTest(name=name):
                path = ROOT / "shared/prospecting-core/schemas" / name
                with path.open("r", encoding="utf-8") as handle:
                    schema = json.load(handle, parse_constant=lambda value: self.fail(f"non-standard constant {value}"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertFalse(schema["additionalProperties"])

    def test_generated_vocabulary_and_typed_values_match_published_schemas(self) -> None:
        schema_root = ROOT / "shared/prospecting-core/schemas"
        dossier_schema = json.loads((schema_root / "customer-dossier.schema.json").read_text(encoding="utf-8"))
        package_schema = json.loads((schema_root / "lead-intelligence-package.schema.json").read_text(encoding="utf-8"))
        dossier = self.validate_dossier("dossier-umg-orbit")
        package = self.package_for()

        dossier_edge_types = set(dossier_schema["$defs"]["edge"]["properties"]["edge_type"]["enum"])
        package_edge_types = set(package_schema["$defs"]["edge"]["properties"]["edge_type"]["enum"])
        self.assertLessEqual({edge["edge_type"] for edge in dossier["relationships"]}, dossier_edge_types)
        self.assertNotIn("evidence_supports", dossier_edge_types)
        self.assertLessEqual({edge["edge_type"] for edge in package["edges"]}, package_edge_types)
        self.assertIn("evidence_supports", package_edge_types)
        self.assertEqual(package_schema["properties"]["edges"]["items"]["$ref"], "#/$defs/edge")
        self.assertIn("maximum_evidence_age_days", package_schema["required"])
        self.assertEqual(package_schema["properties"]["maximum_evidence_age_days"], {"type": "integer", "minimum": 1, "maximum": 3650})

        branches = {
            branch["properties"]["type"]["const"]: branch["properties"]["value"]
            for branch in dossier_schema["$defs"]["typedValue"]["oneOf"]
        }
        claims = [claim for category in dossier["categories"] for claim in category["claims"]]
        self.assertEqual(set(branches), {claim["typed_value"]["type"] for claim in claims})
        python_types = {
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "string_list": list,
            "date": str,
            "url": str,
            "object": dict,
        }
        for claim in claims:
            typed = claim["typed_value"]
            rule = branches[typed["type"]]
            self.assertIsInstance(typed["value"], python_types[typed["type"]])
            if typed["type"] == "number":
                self.assertNotIsInstance(typed["value"], bool)
                self.assertTrue(math.isfinite(typed["value"]))
            if "pattern" in rule:
                self.assertRegex(typed["value"], re.compile(rule["pattern"]))


if __name__ == "__main__":
    unittest.main()
