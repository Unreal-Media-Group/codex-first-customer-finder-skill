#!/usr/bin/env python3
"""Validate claim-verified Phase 6 real dossiers without weakening historical v2."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from common import ValidationError
from validate_phase6_real_contract import (
    _canonical_hash,
    _check_real_private_material,
    _exact_keys,
    _real_inventory_only_categories,
    _real_package_payload,
    build_real_customer_dossier,
    validate_real_customer_dossier,
    validate_real_lead_intelligence_package,
    validate_real_research_bundle,
    validate_real_source_plan,
)
from validate_phase6b_contract import (
    CATEGORIES,
    ContractConflict,
    _identifier,
    _iso_date,
    _string_list,
    _text,
    _utc_datetime,
    canonical_bytes,
)

CONTRACT_VERSION = 3
PROJECTION_VERSION = 1
PACKAGE_ID_VERSION = "lip-real-v3"
MECHANICAL_CATEGORIES = {"governance_and_history", "evidence_coverage"}
RESEARCH_CATEGORIES = tuple(item for item in CATEGORIES if item not in MECHANICAL_CATEGORIES)
ALLOWED_OPPORTUNITIES = {"product_photography", "product_video"}
OPPORTUNITY_UNCERTAINTY = (
    "No expressed demand, budget, buying intent, or UGC aversion was found."
)


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _evidence_id(url: str) -> str:
    return f"evidence-real-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]}"


def _bundle_from_dossier(dossier: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_version": dossier["research_run"]["bundle_version"],
        "source_plan_id": dossier["approved_result"]["source_plan_id"],
        "source_plan_hash": dossier["source_plan_hash"],
        "started_at": dossier["research_run"]["started_at"],
        "completed_at": dossier["research_run"]["completed_at"],
        "sources": copy.deepcopy(dossier["source_coverage"]),
    }


def _validate_projection(
    value: Any,
    *,
    research_bundle: Any,
    source_plan: Any,
    approved_result: Any,
) -> dict[str, Any]:
    plan = validate_real_source_plan(source_plan)
    bundle = validate_real_research_bundle(research_bundle, plan, require_substantive=True)
    projection = _exact_keys(
        value,
        "real claim projection",
        {
            "projection_version",
            "source_plan_id",
            "source_plan_hash",
            "result_id",
            "research_bundle_hash",
            "category_attempts",
            "observed_claims",
            "opportunity_inferences",
            "canonical_hash",
        },
    )
    _check_real_private_material(projection, "real claim projection")
    if projection["projection_version"] != PROJECTION_VERSION:
        raise ValidationError("Real claim projection version is unsupported.")
    if (
        projection["source_plan_id"] != plan["source_plan_id"]
        or projection["source_plan_hash"] != plan["source_plan_hash"]
        or not isinstance(approved_result, dict)
        or projection["result_id"] != approved_result.get("result_id")
        or projection["research_bundle_hash"] != _sha256(bundle)
    ):
        raise ValidationError("Real claim projection does not match its exact plan, result, and bundle.")
    hash_payload = copy.deepcopy(projection)
    recorded_hash = hash_payload.pop("canonical_hash")
    if not isinstance(recorded_hash, str) or recorded_hash != _sha256(hash_payload):
        raise ValidationError("Real claim projection canonical hash is invalid.")

    source_records = {item["requested_url"]: item for item in bundle["sources"]}
    attempts = projection["category_attempts"]
    if not isinstance(attempts, list) or len(attempts) != len(RESEARCH_CATEGORIES):
        raise ValidationError("Every nonmechanical research category requires one exact attempt record.")
    attempts_by_category: dict[str, list[str]] = {}
    for index, attempt_value in enumerate(attempts):
        attempt = _exact_keys(
            attempt_value,
            f"real category attempt {index}",
            {"category", "source_urls"},
        )
        if attempt["category"] != RESEARCH_CATEGORIES[index]:
            raise ValidationError("Real category attempts must use the exact canonical category order.")
        urls = _string_list(
            attempt["source_urls"],
            f"real category attempt {attempt['category']} source_urls",
            allow_empty=False,
        )
        if len(urls) != len(set(urls)) or not set(urls) <= set(source_records):
            raise ValidationError("Real category attempts must reference unique exact planned sources.")
        attempts_by_category[attempt["category"]] = urls

    observed = projection["observed_claims"]
    if not isinstance(observed, list) or len(observed) > 100:
        raise ValidationError("Real observed claims must be a bounded array.")
    observed_by_id: dict[str, dict[str, Any]] = {}
    for index, claim_value in enumerate(observed):
        claim = _exact_keys(
            claim_value,
            f"real observed claim {index}",
            {
                "claim_id",
                "category",
                "source_url",
                "value",
                "value_sha256",
                "confidence_reason",
                "uncertainty",
            },
        )
        claim_id = _identifier(claim["claim_id"], "real observed claim_id")
        if claim_id in observed_by_id:
            raise ValidationError("Real observed claim IDs must be unique.")
        category = claim["category"]
        if category not in RESEARCH_CATEGORIES or category == "opportunity_and_fit":
            raise ValidationError("Observed claims must use a nonmechanical non-opportunity category.")
        source_url = claim["source_url"]
        if source_url not in attempts_by_category[category]:
            raise ValidationError("Observed claim source was not attempted for its category.")
        record = source_records[source_url]
        if record["status"] != "success":
            raise ValidationError("Observed claims require one successful exact source.")
        value_text = _text(claim["value"], "real observed claim value", maximum=500)
        if value_text not in record["summary"]:
            raise ValidationError("Observed claim value must be an exact scrubbed summary substring.")
        if claim["value_sha256"] != hashlib.sha256(value_text.encode("utf-8")).hexdigest():
            raise ValidationError("Observed claim value hash does not match its exact substring.")
        _text(claim["confidence_reason"], "real observed claim confidence_reason", maximum=500)
        _text(claim["uncertainty"], "real observed claim uncertainty", maximum=500)
        observed_by_id[claim_id] = claim

    inferences = projection["opportunity_inferences"]
    if not isinstance(inferences, list) or len(inferences) > len(ALLOWED_OPPORTUNITIES):
        raise ValidationError("Real opportunity inferences must be a bounded array.")
    inference_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for index, inference_value in enumerate(inferences):
        inference = _exact_keys(
            inference_value,
            f"real opportunity inference {index}",
            {
                "claim_id",
                "opportunity_kind",
                "premise_claim_ids",
                "confidence_reason",
                "uncertainty",
            },
        )
        claim_id = _identifier(inference["claim_id"], "real opportunity inference claim_id")
        kind = inference["opportunity_kind"]
        if (
            claim_id in observed_by_id
            or claim_id in inference_ids
            or not isinstance(kind, str)
            or kind not in ALLOWED_OPPORTUNITIES
            or kind in seen_kinds
        ):
            raise ValidationError("Real opportunity inference identity or kind is invalid.")
        premise_ids = _string_list(
            inference["premise_claim_ids"],
            "real opportunity inference premise_claim_ids",
            allow_empty=False,
        )
        if len(premise_ids) != len(set(premise_ids)) or not set(premise_ids) <= set(observed_by_id):
            raise ValidationError("Real opportunity inference premise must be a verified observed claim.")
        premise_sources = {observed_by_id[item]["source_url"] for item in premise_ids}
        if not premise_sources <= set(attempts_by_category["opportunity_and_fit"]):
            raise ValidationError("Opportunity inference premises were not attempted for opportunity fit.")
        _text(inference["confidence_reason"], "real opportunity confidence_reason", maximum=500)
        uncertainty = _text(inference["uncertainty"], "real opportunity uncertainty", maximum=500)
        if uncertainty != OPPORTUNITY_UNCERTAINTY:
            raise ValidationError(
                "Opportunity inference uncertainty must use the canonical no-demand and no-UGC denial."
            )
        inference_ids.add(claim_id)
        seen_kinds.add(kind)
    if not inferences:
        raise ValidationError("An evidence-backed opportunity inference is required; filter intent alone is insufficient.")
    return projection


def validate_real_claim_projection(
    value: Any,
    *,
    research_bundle: Any,
    source_plan: Any,
    approved_result: Any,
) -> dict[str, Any]:
    """Validate one inert claim projection against its exact public-read inputs."""
    return _validate_projection(
        value,
        research_bundle=research_bundle,
        source_plan=source_plan,
        approved_result=approved_result,
    )


def _freshness(record: dict[str, Any], *, cutoff: datetime, maximum_age_days: int) -> str:
    if record["conflict_state"] == "conflicted":
        return "conflicted"
    if record["source_date"] is None:
        return "unknown"
    age = (cutoff.date() - _iso_date(record["source_date"], "real claim source date")).days
    return "stale" if age > maximum_age_days else "current"


def _project_categories(
    *,
    base: dict[str, Any],
    projection: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = {item["requested_url"]: item for item in base["source_coverage"]}
    evidence_by_url = {
        item["source_url"]: item
        for item in base["evidence_inventory"]
        if item["evidence_kind"] == "public_source"
    }
    attempts = {item["category"]: item["source_urls"] for item in projection["category_attempts"]}
    cutoff = _utc_datetime(base["research_cutoff"], "real dossier research_cutoff")
    maximum_age = base["maximum_evidence_age_days"]
    subject = base["entities"][0]["node_id"]
    claims_by_id: dict[str, dict[str, Any]] = {}
    categories: dict[str, list[dict[str, Any]]] = {item: [] for item in CATEGORIES}

    for claim in projection["observed_claims"]:
        record = records[claim["source_url"]]
        state = _freshness(record, cutoff=cutoff, maximum_age_days=maximum_age)
        if state != "current":
            continue
        evidence = evidence_by_url[claim["source_url"]]
        projected = {
            "claim_id": claim["claim_id"],
            "category": claim["category"],
            "subject_node_id": subject,
            "typed_value": {"type": "string", "value": claim["value"]},
            "basis": "observed",
            "evidence_refs": [evidence["evidence_id"]],
            "confidence_reason": claim["confidence_reason"],
            "uncertainty": claim["uncertainty"],
            "source_date": record["source_date"],
            "observed_at": record["observed_at"],
            "freshness_state": "current",
        }
        claims_by_id[claim["claim_id"]] = projected
        categories[claim["category"]].append(projected)

    eligible_categories: set[str] = set()
    for category in RESEARCH_CATEGORIES:
        assigned = [records[url] for url in attempts[category]]
        successful = [item for item in assigned if item["status"] == "success"]
        states = [
            _freshness(item, cutoff=cutoff, maximum_age_days=maximum_age)
            for item in successful
        ]
        if len(successful) == len(assigned) and "conflicted" not in states:
            eligible_categories.add(category)
    claims_by_id = {
        claim_id: claim
        for claim_id, claim in claims_by_id.items()
        if claim["category"] in eligible_categories
    }
    for category in RESEARCH_CATEGORIES:
        if category not in eligible_categories:
            categories[category] = []

    for inference in projection["opportunity_inferences"]:
        if "opportunity_and_fit" not in eligible_categories:
            continue
        premises = [claims_by_id.get(item) for item in inference["premise_claim_ids"]]
        if any(item is None for item in premises):
            continue
        premise_claims = [item for item in premises if item is not None]
        evidence_refs = sorted({ref for item in premise_claims for ref in item["evidence_refs"]})
        source_dates = sorted({item["source_date"] for item in premise_claims})
        observed_times = sorted({item["observed_at"] for item in premise_claims})
        projected = {
            "claim_id": inference["claim_id"],
            "category": "opportunity_and_fit",
            "subject_node_id": subject,
            "typed_value": {
                "type": "object",
                "value": {
                    "opportunity_kind": inference["opportunity_kind"],
                    "signal_claim_ids": list(inference["premise_claim_ids"]),
                },
            },
            "basis": "inferred_low_confidence",
            "evidence_refs": evidence_refs,
            "confidence_reason": inference["confidence_reason"],
            "uncertainty": inference["uncertainty"],
            "source_date": source_dates[-1],
            "observed_at": observed_times[-1],
            "freshness_state": "current",
        }
        categories["opportunity_and_fit"].append(projected)

    mechanical = {item["category"]: copy.deepcopy(item) for item in base["categories"] if item["category"] in MECHANICAL_CATEGORIES}
    output: list[dict[str, Any]] = []
    for category in CATEGORIES:
        if category in mechanical:
            output.append(mechanical[category])
            continue
        assigned = [records[url] for url in attempts[category]]
        successful = [item for item in assigned if item["status"] == "success"]
        states = [_freshness(item, cutoff=cutoff, maximum_age_days=maximum_age) for item in successful]
        category_claims = sorted(categories[category], key=lambda item: item["claim_id"])
        if "conflicted" in states:
            state = "conflicted"
        elif len(successful) != len(assigned):
            state = "unknown"
        elif category_claims:
            output.append({"category": category, "coverage_state": "complete", "claims": category_claims})
            continue
        elif states and all(item == "stale" for item in states):
            state = "stale"
        elif "current" in states:
            state = "not_found"
        else:
            state = "unknown"
        output.append({
            "category": category,
            "coverage_state": state,
            "gap_explanation": (
                f"The exact sources assigned to {category} were evaluated; "
                f"{len(successful)} of {len(assigned)} succeeded and no current verified claim was established."
            ),
            "claims": [],
        })
    opportunity = next(
        item for item in output if item["category"] == "opportunity_and_fit"
    )
    matched_kinds = [
        claim["typed_value"]["value"]["opportunity_kind"]
        for claim in opportunity["claims"]
    ]
    if not matched_kinds:
        raise ValidationError("No current verified opportunity inference survived freshness and conflict checks.")
    qualification = {
        "state": "potential_product_creative_fit",
        "basis": "inferred_low_confidence",
        "search_intent_is_demand_evidence": False,
        "demand_evidence_found": False,
        "reason": (
            "Verified public business signals support bounded fit review for "
            + ", ".join(sorted(matched_kinds))
            + "; no expressed demand, budget, buying intent, or UGC aversion is claimed."
        ),
    }
    return output, qualification


def _expected_package_entities(
    package: dict[str, Any],
    *,
    source_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    business_unit = package["business_unit"]
    organization_node_id = (
        "organization-real-"
        + hashlib.sha256(package["approved_result"]["account_id"].encode("utf-8")).hexdigest()[:20]
    )
    entities = [{
        "node_id": organization_node_id,
        "node_type": "organization",
        "label": source_plan["organization_name"],
        "business_unit": business_unit,
        "attributes": {"canonical_domain": source_plan["canonical_domain"]},
    }]
    evidence_by_url = {
        item["source_url"]: item
        for item in package["evidence_inventory"]
        if item["evidence_kind"] == "public_source"
    }
    ordered_public_evidence = [
        evidence_by_url[item["requested_url"]]
        for item in package["source_coverage"]
        if item["status"] == "success"
    ]
    if not ordered_public_evidence:
        raise ValidationError("Claim-verified package requires successful public evidence.")
    relationships: list[dict[str, Any]] = []
    for related_domain in source_plan["approved_related_domains"]:
        node_id = f"brand-real-{hashlib.sha256(related_domain.encode('utf-8')).hexdigest()[:20]}"
        entities.append({
            "node_id": node_id,
            "node_type": "brand",
            "label": related_domain,
            "business_unit": business_unit,
            "attributes": {"canonical_domain": related_domain},
        })
        related_evidence = next(
            (
                item["evidence_id"]
                for item in ordered_public_evidence
                if (urlsplit(item["source_url"]).hostname or "").endswith(related_domain)
            ),
            ordered_public_evidence[0]["evidence_id"],
        )
        relationships.append({
            "edge_id": (
                "affiliated-real-"
                + hashlib.sha256((organization_node_id + related_domain).encode("utf-8")).hexdigest()[:20]
            ),
            "edge_type": "affiliated_with",
            "from_node_id": node_id,
            "to_node_id": organization_node_id,
            "business_unit": business_unit,
            "claim_refs": [],
            "evidence_refs": [related_evidence],
        })

    return (
        sorted(entities, key=lambda item: item["node_id"]),
        sorted(relationships, key=lambda item: item["edge_id"]),
    )


def _reviewed_payload(dossier: dict[str, Any]) -> dict[str, Any]:
    return {
        "approved_result": copy.deepcopy(dossier["approved_result"]),
        "source_plan_hash": dossier["source_plan_hash"],
        "research_run": copy.deepcopy(dossier["research_run"]),
        "source_coverage": copy.deepcopy(dossier["source_coverage"]),
        "evidence_inventory": sorted(
            copy.deepcopy(dossier["evidence_inventory"]),
            key=lambda item: item["evidence_id"],
        ),
        "categories": copy.deepcopy(dossier["categories"]),
        "qualification": copy.deepcopy(dossier["qualification"]),
        "entities": sorted(
            copy.deepcopy(dossier["entities"]), key=lambda item: item["node_id"]
        ),
        "relationships": sorted(
            copy.deepcopy(dossier["relationships"]), key=lambda item: item["edge_id"]
        ),
    }


def _automated_review(dossier: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    claims = [claim for category in dossier["categories"] for claim in category["claims"]]
    return {
        "review_version": 1,
        "state": "passed",
        "source_plan_hash": dossier["source_plan_hash"],
        "result_id": dossier["approved_result"]["result_id"],
        "research_bundle_hash": projection["research_bundle_hash"],
        "claim_projection_hash": projection["canonical_hash"],
        "reviewed_payload_hash": _sha256(_reviewed_payload(dossier)),
        "claim_count": len(claims),
        "gap_count": sum(item["coverage_state"] != "complete" for item in dossier["categories"]),
    }


def build_claim_verified_real_dossier(
    *,
    plan: dict[str, Any],
    research_bundle: dict[str, Any],
    claim_projection: dict[str, Any],
    history: dict[str, Any],
    approved_result: dict[str, Any],
    approval_id: str,
    search_request_id: str,
    history_fingerprint: str,
    research_cutoff: str,
    maximum_evidence_age_days: int,
    version: int,
) -> dict[str, Any]:
    projection = _validate_projection(
        claim_projection,
        research_bundle=research_bundle,
        source_plan=plan,
        approved_result=approved_result,
    )
    dossier = build_real_customer_dossier(
        plan=plan,
        research_bundle=research_bundle,
        history=history,
        approved_result=approved_result,
        approval_id=approval_id,
        search_request_id=search_request_id,
        history_fingerprint=history_fingerprint,
        research_cutoff=research_cutoff,
        maximum_evidence_age_days=maximum_evidence_age_days,
        version=version,
    )
    categories, qualification = _project_categories(base=dossier, projection=projection)
    dossier["schema_version"] = CONTRACT_VERSION
    dossier["categories"] = categories
    dossier["qualification"] = qualification
    dossier["claim_projection"] = copy.deepcopy(projection)
    dossier["claim_projection_hash"] = projection["canonical_hash"]
    dossier["automated_evidence_review"] = _automated_review(dossier, projection)
    return validate_claim_verified_real_dossier(
        dossier,
        claim_projection=projection,
        history=history,
        approved_result=approved_result,
        source_plan=plan,
    )


def validate_claim_verified_real_dossier(
    value: Any,
    *,
    claim_projection: Any | None = None,
    history: Any,
    approved_result: Any,
    source_plan: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Claim-verified real dossier must be an object.")
    dossier = copy.deepcopy(value)
    if set(dossier) != {
        "schema_version", "synthetic", "dossier_id", "version", "idempotency_identity",
        "search_request_id", "business_unit", "approved_result", "source_plan_hash",
        "research_cutoff", "maximum_evidence_age_days", "history_fingerprint",
        "duplicate_history", "qualification", "research_run", "source_coverage",
        "evidence_inventory", "categories", "entities", "relationships", "review_state",
        "release_state", "authority", "claim_projection", "claim_projection_hash",
        "automated_evidence_review",
    } or dossier["schema_version"] != CONTRACT_VERSION:
        raise ValidationError("Claim-verified real dossier must use the exact schema version 3 fields.")
    if claim_projection is not None and canonical_bytes(claim_projection) != canonical_bytes(dossier["claim_projection"]):
        raise ValidationError("Claim-verified real dossier projection input changed.")
    projection = _validate_projection(
        dossier["claim_projection"],
        research_bundle=_bundle_from_dossier(dossier),
        source_plan=source_plan,
        approved_result=approved_result,
    )
    compatible = copy.deepcopy(dossier)
    compatible.pop("claim_projection")
    compatible.pop("claim_projection_hash")
    compatible.pop("automated_evidence_review")
    compatible["schema_version"] = 2
    validate_real_customer_dossier(
        compatible,
        history=history,
        approved_result=approved_result,
        source_plan=source_plan,
    )
    base = build_real_customer_dossier(
        plan=source_plan,
        research_bundle=_bundle_from_dossier(dossier),
        history=history,
        approved_result=approved_result,
        approval_id=dossier["approved_result"]["approval_id"],
        search_request_id=dossier["search_request_id"],
        history_fingerprint=dossier["history_fingerprint"],
        research_cutoff=dossier["research_cutoff"],
        maximum_evidence_age_days=dossier["maximum_evidence_age_days"],
        version=dossier["version"],
    )
    expected_categories, expected_qualification = _project_categories(base=base, projection=projection)
    if canonical_bytes(dossier["categories"]) != canonical_bytes(expected_categories):
        raise ValidationError("Claim-verified real dossier categories do not match the exact projection.")
    if canonical_bytes(dossier["qualification"]) != canonical_bytes(expected_qualification):
        raise ValidationError("Claim-verified real dossier qualification is not evidence-backed.")
    if (
        canonical_bytes(dossier["entities"]) != canonical_bytes(base["entities"])
        or canonical_bytes(dossier["relationships"])
        != canonical_bytes(base["relationships"])
    ):
        raise ValidationError("Claim-verified real dossier graph is not the exact source-plan projection.")
    if dossier["claim_projection_hash"] != projection["canonical_hash"]:
        raise ValidationError("Claim-verified real dossier projection hash changed.")
    expected_review = _automated_review(dossier, projection)
    if dossier["automated_evidence_review"] != expected_review:
        raise ValidationError("Claim-verified real dossier automated evidence review is invalid.")
    return dossier


def _package_payload(dossier: dict[str, Any]) -> dict[str, Any]:
    package = _real_package_payload(dossier)
    package["schema_version"] = CONTRACT_VERSION
    package["package_id"] = (
        f"{PACKAGE_ID_VERSION}-"
        f"{hashlib.sha256((dossier['business_unit'] + '|' + dossier['idempotency_identity']).encode('utf-8')).hexdigest()[:24]}"
    )
    package["claim_projection_hash"] = dossier["claim_projection_hash"]
    package["claim_projection"] = copy.deepcopy(dossier["claim_projection"])
    package["automated_evidence_review"] = copy.deepcopy(dossier["automated_evidence_review"])
    package["canonical_hash"] = _sha256({key: value for key, value in package.items() if key != "canonical_hash"})
    return package


def build_claim_verified_real_package(
    dossier: dict[str, Any],
    *,
    claim_projection: Any | None = None,
    history: Any,
    approved_result: Any,
    source_plan: Any,
    prior_packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dossier = validate_claim_verified_real_dossier(
        dossier,
        claim_projection=claim_projection,
        history=history,
        approved_result=approved_result,
        source_plan=source_plan,
    )
    if dossier["review_state"] != "research_quality_accepted" or dossier["release_state"] != "released_local_data_only":
        raise ValidationError("Claim-verified package release requires exact human acceptance.")
    package = _package_payload(dossier)
    validate_claim_verified_real_package(
        package,
        claim_projection=claim_projection,
        source_plan=source_plan,
    )
    matches: list[dict[str, Any]] = []
    for item in prior_packages or []:
        if not isinstance(item, dict):
            raise ValidationError("Prior lead-intelligence packages must be objects.")
        if (
            item.get("business_unit") == package["business_unit"]
            and item.get("idempotency_identity") == package["idempotency_identity"]
        ):
            matches.append(item)
    for existing in matches:
        validate_claim_verified_real_package(
            existing,
            claim_projection=claim_projection,
            source_plan=source_plan,
        )
        if canonical_bytes(existing) != canonical_bytes(package):
            raise ContractConflict("Claim-verified package idempotency conflicts with changed content.")
    return copy.deepcopy(matches[0]) if matches else package


def validate_claim_verified_real_package(
    value: Any,
    *,
    claim_projection: Any | None = None,
    source_plan: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Claim-verified package must be an object.")
    package = copy.deepcopy(value)
    expected_keys = {
        "schema_version", "synthetic", "package_id", "idempotency_identity", "business_unit",
        "approved_result", "source_plan_hash", "dossier_id", "dossier_version",
        "research_cutoff", "maximum_evidence_age_days", "canonical_hash", "history_fingerprint",
        "qualification", "research_run", "source_coverage", "coverage_states", "claims",
        "evidence_inventory", "nodes", "edges", "duplicate_history", "review_release", "authority",
        "claim_projection", "claim_projection_hash", "automated_evidence_review",
    }
    if set(package) != expected_keys or package["schema_version"] != CONTRACT_VERSION:
        raise ValidationError("Claim-verified package must use the exact schema version 3 fields.")
    hash_payload = {key: copy.deepcopy(item) for key, item in package.items() if key != "canonical_hash"}
    if package["canonical_hash"] != _sha256(hash_payload):
        raise ValidationError("Claim-verified package canonical hash is invalid.")
    if not isinstance(package["business_unit"], str) or not isinstance(
        package["idempotency_identity"], str
    ):
        raise ValidationError("Claim-verified package identity fields are invalid.")
    expected_id = (
        f"{PACKAGE_ID_VERSION}-"
        f"{hashlib.sha256((package['business_unit'] + '|' + package['idempotency_identity']).encode('utf-8')).hexdigest()[:24]}"
    )
    if package["package_id"] != expected_id:
        raise ValidationError("Claim-verified package ID is invalid.")
    compatible = copy.deepcopy(package)
    compatible.pop("claim_projection")
    compatible.pop("claim_projection_hash")
    compatible.pop("automated_evidence_review")
    compatible["schema_version"] = 2
    compatible["package_id"] = (
        "lip-real-v2-"
        + hashlib.sha256((package["business_unit"] + "|" + package["idempotency_identity"]).encode("utf-8")).hexdigest()[:24]
    )
    compatible["canonical_hash"] = _sha256({key: item for key, item in compatible.items() if key != "canonical_hash"})
    validate_real_lead_intelligence_package(
        compatible,
        source_plan=source_plan,
        require_inventory_only=False,
    )
    bundle = {
        "bundle_version": package["research_run"]["bundle_version"],
        "source_plan_id": package["approved_result"]["source_plan_id"],
        "source_plan_hash": package["source_plan_hash"],
        "started_at": package["research_run"]["started_at"],
        "completed_at": package["research_run"]["completed_at"],
        "sources": copy.deepcopy(package["source_coverage"]),
    }
    if claim_projection is not None and canonical_bytes(claim_projection) != canonical_bytes(package["claim_projection"]):
        raise ValidationError("Claim-verified package projection input changed.")
    projection = _validate_projection(
        package["claim_projection"],
        research_bundle=bundle,
        source_plan=source_plan,
        approved_result={"result_id": package["approved_result"]["result_id"]},
    )
    successful = [record for record in package["source_coverage"] if record["status"] == "success"]
    organization_node_id = (
        "organization-real-"
        + hashlib.sha256(package["approved_result"]["account_id"].encode("utf-8")).hexdigest()[:20]
    )
    history_evidence_id = f"evidence-history-{package['history_fingerprint'][:20]}"
    inventory_categories = _real_inventory_only_categories(
        result_id=package["approved_result"]["result_id"],
        organization_node_id=organization_node_id,
        history_status=package["duplicate_history"]["status"],
        history_evidence_id=history_evidence_id,
        evidence_refs=[
            _evidence_id(record["requested_url"])
            for record in successful
        ] + [history_evidence_id],
        approved_source_count=len(source_plan["sources"]),
        successful_source_count=len(successful),
        research_cutoff=package["research_cutoff"],
        cutoff_date=_utc_datetime(
            package["research_cutoff"], "claim-verified package research_cutoff"
        ).date().isoformat(),
    )
    expected_categories, expected_qualification = _project_categories(
        base={
            "source_coverage": copy.deepcopy(package["source_coverage"]),
            "evidence_inventory": copy.deepcopy(package["evidence_inventory"]),
            "research_cutoff": package["research_cutoff"],
            "maximum_evidence_age_days": package["maximum_evidence_age_days"],
            "entities": [{"node_id": organization_node_id}],
            "categories": inventory_categories,
        },
        projection=projection,
    )
    expected_coverage = [
        {
            "category": item["category"],
            "coverage_state": item["coverage_state"],
            "gap_explanation": item.get("gap_explanation"),
        }
        for item in expected_categories
    ]
    expected_claims = sorted(
        (claim for category in expected_categories for claim in category["claims"]),
        key=lambda item: item["claim_id"],
    )
    if (
        canonical_bytes(package["coverage_states"]) != canonical_bytes(expected_coverage)
        or canonical_bytes(package["claims"]) != canonical_bytes(expected_claims)
        or canonical_bytes(package["qualification"]) != canonical_bytes(expected_qualification)
    ):
        raise ValidationError("Claim-verified package is not the exact verified-claim projection.")
    expected_entities, expected_relationships = _expected_package_entities(
        package,
        source_plan=validate_real_source_plan(source_plan),
    )
    actual_entities = sorted(
        (item for item in package["nodes"] if item["node_type"] != "evidence"),
        key=lambda item: item["node_id"],
    )
    actual_relationships = sorted(
        (item for item in package["edges"] if item["edge_type"] != "evidence_supports"),
        key=lambda item: item["edge_id"],
    )
    expected_node_count = len(expected_entities) + len(package["evidence_inventory"])
    expected_edge_count = len(expected_relationships) + sum(
        len(item["evidence_refs"]) for item in package["claims"]
    )
    if (
        len(package["nodes"]) != expected_node_count
        or len(package["edges"]) != expected_edge_count
        or canonical_bytes(actual_entities) != canonical_bytes(expected_entities)
        or canonical_bytes(actual_relationships) != canonical_bytes(expected_relationships)
    ):
        raise ValidationError("Claim-verified package graph is not the exact dossier projection.")
    claims_by_category: dict[str, list[dict[str, Any]]] = {item: [] for item in CATEGORIES}
    for claim in package["claims"]:
        claims_by_category[claim["category"]].append(copy.deepcopy(claim))
    categories = []
    for coverage in package["coverage_states"]:
        item = {
            "category": coverage["category"],
            "coverage_state": coverage["coverage_state"],
            "claims": sorted(claims_by_category[coverage["category"]], key=lambda claim: claim["claim_id"]),
        }
        if coverage.get("gap_explanation") is not None:
            item["gap_explanation"] = coverage["gap_explanation"]
        categories.append(item)
    dossier_view = {
        "approved_result": copy.deepcopy(package["approved_result"]),
        "source_plan_hash": package["source_plan_hash"],
        "research_run": copy.deepcopy(package["research_run"]),
        "source_coverage": copy.deepcopy(package["source_coverage"]),
        "evidence_inventory": copy.deepcopy(package["evidence_inventory"]),
        "categories": categories,
        "qualification": copy.deepcopy(package["qualification"]),
        "entities": copy.deepcopy(actual_entities),
        "relationships": copy.deepcopy(actual_relationships),
    }
    expected_review = {
        "review_version": 1,
        "state": "passed",
        "source_plan_hash": package["source_plan_hash"],
        "result_id": package["approved_result"]["result_id"],
        "research_bundle_hash": projection["research_bundle_hash"],
        "claim_projection_hash": projection["canonical_hash"],
        "reviewed_payload_hash": _sha256(dossier_view),
        "claim_count": len(package["claims"]),
        "gap_count": sum(item["coverage_state"] != "complete" for item in package["coverage_states"]),
    }
    if package["claim_projection_hash"] != projection["canonical_hash"] or package["automated_evidence_review"] != expected_review:
        raise ValidationError("Claim-verified package automated evidence review is invalid.")
    return package
