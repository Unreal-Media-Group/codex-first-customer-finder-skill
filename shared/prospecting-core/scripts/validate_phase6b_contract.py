#!/usr/bin/env python3
"""Validate deterministic Phase 6B dossier contracts and local graph packages."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from classify_duplicate import classify_duplicate, validate_history
from common import ValidationError, require_object
from normalize_domain import normalize_domain
from validate_campaign import validate_campaign

BUSINESS_UNITS = {"unreal-media-group", "unreal-talent"}
OPPORTUNITY_TYPES = {"product_photography", "product_video", "ugc_ad"}
CATEGORIES = (
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
)
GAP_STATES = {"not_found", "not_applicable", "conflicted", "stale", "unknown"}
BASIS_STATES = {"observed", "inferred_high_confidence", "inferred_low_confidence"}
FRESHNESS_STATES = {"current", "stale", "conflicted", "unknown"}
SOURCE_KINDS = {
    "official_site",
    "public_business_profile",
    "public_business_route",
    "public_professional_profile",
    "public_marketplace",
    "public_social",
    "public_news",
}
NODE_TYPES = {
    "organization",
    "brand",
    "business_role",
    "business_contact_route",
    "product_service",
    "audience_market",
    "campaign_asset_reference",
    "signal",
    "opportunity",
    "restriction",
    "evidence",
}
EDGE_TYPES = {
    "parent_subbrand",
    "agency_client",
    "partner",
    "affiliated_with",
    "responsible_for",
    "contact_path_for",
    "offers",
    "targets",
    "campaign_for",
    "evidence_supports",
    "duplicate",
    "prior_discovery",
    "reengagement",
}
ELIGIBLE_HISTORY_STATES = {"new_prospect", "existing_new_trigger", "distinct_subbrand"}
DUPLICATE_HISTORY_STATES = {
    "new_prospect",
    "possible_duplicate_needs_review",
    "suppressed",
    "existing_client",
    "existing_partner",
    "existing_active_outreach",
    "parent_company_relationship",
    "distinct_subbrand",
    "agency_brand_overlap",
    "existing_no_new_trigger",
    "existing_new_trigger",
}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
EMAIL_PATTERN = re.compile(r"(?i)(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![a-z0-9.-])")
PHONE_PATTERN = re.compile(r"(?<!\d)\+?\d[\d ()-]{7,}\d(?!\d)")
CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:bearer\s+|api[ _-]?key\s*[:=]|access[ _-]?token\s*[:=]|client[ _-]?secret\s*[:=]|private[ _-]?key)"
)
BLOCKED_KEY_PARTS = {
    "email",
    "phone",
    "telephone",
    "mobile",
    "password",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "authorization",
    "bearer",
    "privatekey",
    "accesskey",
    "clientsecret",
    "credential",
}
MAX_DOCUMENT_BYTES = 512_000
MAX_DEPTH = 12
MAX_ARRAY_ITEMS = 300
MAX_OBJECT_KEYS = 120
MAX_STRING_LENGTH = 4096
PACKAGE_MAX_DOCUMENT_BYTES = 1_500_000
PACKAGE_MAX_ARRAY_ITEMS = 600
PACKAGE_MAX_CLAIMS = 300
PACKAGE_MAX_EVIDENCE = 300
PACKAGE_MAX_NODES = 600
PACKAGE_MAX_EDGES = 600


class ContractConflict(ValidationError):
    """Raised when one idempotency identity is reused with changed content."""


def load_json_strict(path: str | Path, label: str = "JSON") -> Any:
    target = Path(path)
    if not target.is_file():
        raise ValidationError(f"{label} file does not exist: {target}")

    def reject_constant(value: str) -> None:
        raise ValidationError(f"{label} contains non-standard numeric value {value}.")

    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle, parse_constant=reject_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot load {label} from {target}: {exc}") from exc


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Value cannot be represented as canonical JSON: {exc}") from exc


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _check_limits(value: Any, label: str, *, depth: int = 0, max_array_items: int = MAX_ARRAY_ITEMS) -> None:
    if depth > MAX_DEPTH:
        raise ValidationError(f"{label} exceeds the maximum nesting depth.")
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{label} contains a non-finite number.")
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ValidationError(f"{label} contains a string longer than {MAX_STRING_LENGTH} characters.")
        return
    if isinstance(value, list):
        if len(value) > max_array_items:
            raise ValidationError(f"{label} contains too many array items.")
        for item in value:
            _check_limits(item, label, depth=depth + 1, max_array_items=max_array_items)
        return
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise ValidationError(f"{label} contains too many object keys.")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValidationError(f"{label} object keys must be bounded non-empty strings.")
            _check_limits(item, label, depth=depth + 1, max_array_items=max_array_items)
        return
    raise ValidationError(f"{label} contains unsupported value type {type(value).__name__}.")


def _check_document(
    value: Any,
    label: str,
    *,
    max_array_items: int = MAX_ARRAY_ITEMS,
    max_document_bytes: int = MAX_DOCUMENT_BYTES,
) -> None:
    _check_limits(value, label, max_array_items=max_array_items)
    if len(canonical_bytes(value)) > max_document_bytes:
        raise ValidationError(f"{label} exceeds the {max_document_bytes}-byte canonical limit.")


def _exact_object(value: Any, label: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    item = require_object(value, label)
    optional = optional or set()
    missing = sorted(required - set(item))
    unknown = sorted(set(item) - required - optional)
    if missing:
        raise ValidationError(f"{label} is missing: {', '.join(missing)}.")
    if unknown:
        raise ValidationError(f"{label} contains unsupported fields: {', '.join(unknown)}.")
    return item


def _text(value: Any, label: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{label} must be a non-empty string of at most {maximum} characters.")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label, maximum=128)
    if not ID_PATTERN.fullmatch(text):
        raise ValidationError(f"{label} must be a stable lowercase identifier.")
    return text


def _utc_datetime(value: Any, label: str) -> datetime:
    raw = _text(value, label, maximum=40)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO date-time.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValidationError(f"{label} must use explicit UTC semantics.")
    return parsed.astimezone(timezone.utc)


def _iso_date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(_text(value, label, maximum=10))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO date.") from exc


def _example_domain(value: Any, label: str) -> str:
    domain = normalize_domain(_text(value, label, maximum=253))
    if not domain.endswith(".example"):
        raise ValidationError(f"{label} must use a synthetic .example domain.")
    return domain


def _example_url(value: Any, label: str) -> str:
    raw = _text(value, label, maximum=2048)
    if "%" in raw or unquote(raw) != raw:
        raise ValidationError(f"{label} may not contain percent-encoded material.")
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError(f"{label} contains an invalid port.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not parsed.hostname.endswith(".example")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port is not None
    ):
        raise ValidationError(f"{label} must be a credential-free, query-free synthetic HTTP(S) URL.")
    if any(character.isspace() or ord(character) < 32 for character in raw) or any(character in raw for character in '<>"\'\\'):
        raise ValidationError(f"{label} contains unsafe characters.")
    return raw


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _check_private_material(value: Any, label: str = "Phase 6B data") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in BLOCKED_KEY_PARTS):
                raise ValidationError(f"{label} contains prohibited private or credential field {key}.")
            _check_private_material(item, label)
        return
    if isinstance(value, list):
        for item in value:
            _check_private_material(item, label)
        return
    if not isinstance(value, str):
        return
    if EMAIL_PATTERN.search(value) or CREDENTIAL_PATTERN.search(value):
        raise ValidationError(f"{label} contains prohibited contact or credential material.")
    try:
        date.fromisoformat(value[:10])
        looks_like_date = len(value) in {10, 20, 25}
    except ValueError:
        looks_like_date = False
    is_digest = re.fullmatch(r"[0-9a-f]{64}", value) is not None
    if not looks_like_date and not is_digest and PHONE_PATTERN.search(value):
        raise ValidationError(f"{label} contains a prohibited telephone value.")
    if value.startswith(("http://", "https://")):
        _example_url(value, label)


def _string_list(value: Any, label: str, *, allowed: set[str] | None = None, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(f"{label} must be an array{'' if allow_empty else ' with at least one item'}.")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{label} must contain non-empty strings.")
    if len(value) != len(set(value)):
        raise ValidationError(f"{label} must not contain duplicates.")
    if allowed is not None and any(item not in allowed for item in value):
        raise ValidationError(f"{label} contains an unsupported value.")
    return value


def _validate_duplicate_history(value: Any, label: str) -> dict[str, Any]:
    duplicate = _exact_object(
        value,
        label,
        {"status", "reason", "matched_prospect_id", "matched_prospect_ids", "reengagement"},
    )
    status = duplicate["status"]
    if status not in DUPLICATE_HISTORY_STATES:
        raise ValidationError(f"{label} status is unsupported.")
    _text(duplicate["reason"], f"{label} reason", maximum=1000)
    matched_ids = _string_list(duplicate["matched_prospect_ids"], f"{label} matched_prospect_ids")
    for index, prospect_id in enumerate(matched_ids):
        _identifier(prospect_id, f"{label} matched_prospect_ids {index}")
    matched_id = duplicate["matched_prospect_id"]
    if matched_id is not None:
        _identifier(matched_id, f"{label} matched_prospect_id")
    if matched_id != (matched_ids[0] if matched_ids else None):
        raise ValidationError(f"{label} matched prospect fields are inconsistent.")
    if status == "new_prospect":
        if matched_ids:
            raise ValidationError(f"{label} new prospect cannot reference history matches.")
    elif not matched_ids:
        raise ValidationError(f"{label} non-new classification requires a history match.")
    expected_reengagement = status == "existing_new_trigger"
    if not isinstance(duplicate["reengagement"], bool) or duplicate["reengagement"] is not expected_reengagement:
        raise ValidationError(f"{label} reengagement flag is inconsistent with status.")
    return duplicate


def validate_search_request(value: Any) -> dict[str, Any]:
    request = _exact_object(
        value,
        "Phase 6B search request",
        {"version", "request_id", "idempotency_identity", "business_unit", "campaign"},
        {"opportunity_filter"},
    )
    _check_document(request, "Phase 6B search request")
    _check_private_material(request, "Phase 6B search request")
    if request["version"] != 1:
        raise ValidationError("Phase 6B search request version must be 1.")
    _identifier(request["request_id"], "request_id")
    _identifier(request["idempotency_identity"], "idempotency_identity")
    if request["business_unit"] not in BUSINESS_UNITS:
        raise ValidationError("Phase 6B search request business_unit is invalid.")
    campaign = require_object(request["campaign"], "campaign")
    validate_campaign(campaign, require_history_file=False)
    if campaign["business_unit"] != request["business_unit"]:
        raise ValidationError("Campaign business unit does not match the Phase 6B search request.")
    if "opportunity_filter" in request:
        opportunity_filter = _exact_object(
            request["opportunity_filter"],
            "opportunity_filter",
            set(),
            {"include_any", "exclude"},
        )
        if not opportunity_filter:
            raise ValidationError("opportunity_filter cannot be empty.")
        include_any = _string_list(opportunity_filter.get("include_any", []), "opportunity_filter include_any", allowed=OPPORTUNITY_TYPES)
        exclude = _string_list(opportunity_filter.get("exclude", []), "opportunity_filter exclude", allowed=OPPORTUNITY_TYPES)
        if not include_any and not exclude:
            raise ValidationError("opportunity_filter must include at least one intent value.")
        if set(include_any) & set(exclude):
            raise ValidationError("opportunity_filter values cannot be included and excluded together.")
    return request


def _validate_candidate(value: Any, *, business_unit: str, today: date) -> dict[str, Any]:
    candidate = _exact_object(
        value,
        "candidate",
        {
            "candidate_id",
            "result_id",
            "global_identity_id",
            "account_id",
            "business_unit",
            "company_name",
            "domain",
            "company_type",
            "qualification_score",
            "opportunities",
            "evidence",
        },
        {"social_handles", "parent_company", "independent_subbrand", "new_trigger"},
    )
    _check_document(candidate, "candidate")
    _check_private_material(candidate, "candidate")
    if candidate["business_unit"] != business_unit:
        raise ValidationError("Candidate business unit does not match request.")
    for key in ("candidate_id", "result_id", "global_identity_id", "account_id"):
        _identifier(candidate[key], key)
    _text(candidate["company_name"], "candidate company_name", maximum=160)
    if not candidate["company_name"].casefold().startswith("synthetic "):
        raise ValidationError("Candidate company_name must be obviously synthetic.")
    _example_domain(candidate["domain"], "candidate domain")
    if candidate["company_type"] not in {"brand", "agency"}:
        raise ValidationError("candidate company_type must be brand or agency.")
    score = candidate["qualification_score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
        raise ValidationError("candidate qualification_score must be finite and from 0 to 100.")
    evidence = candidate["evidence"]
    if not isinstance(evidence, list):
        raise ValidationError("candidate evidence must be an array.")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        item = _exact_object(item, f"candidate evidence {index}", {"evidence_id", "source_url", "source_date", "observed_at"})
        evidence_id = _identifier(item["evidence_id"], f"candidate evidence {index} evidence_id")
        if evidence_id in evidence_ids:
            raise ValidationError("candidate evidence IDs must be unique.")
        evidence_ids.add(evidence_id)
        _example_url(item["source_url"], f"candidate evidence {index} source_url")
        if _iso_date(item["source_date"], f"candidate evidence {index} source_date") > today:
            raise ValidationError("candidate evidence cannot be future-dated.")
        if _utc_datetime(item["observed_at"], f"candidate evidence {index} observed_at").date() > today:
            raise ValidationError("candidate evidence observation cannot be future-dated.")
    opportunities = candidate["opportunities"]
    if not isinstance(opportunities, list):
        raise ValidationError("candidate opportunities must be an array.")
    seen_kinds: set[str] = set()
    for index, item in enumerate(opportunities):
        item = _exact_object(item, f"candidate opportunity {index}", {"kind", "evidence_refs"})
        if item["kind"] not in OPPORTUNITY_TYPES or item["kind"] in seen_kinds:
            raise ValidationError("candidate opportunity kinds must be unique controlled values.")
        seen_kinds.add(item["kind"])
        refs = _string_list(item["evidence_refs"], f"candidate opportunity {index} evidence_refs", allow_empty=False)
        if not set(refs) <= evidence_ids:
            raise ValidationError("candidate opportunity evidence reference is unresolved.")
    return candidate


def _validate_synthetic_history(value: Any) -> dict[str, Any]:
    history = require_object(value, "Phase 6B history")
    _check_document(history, "Phase 6B history")
    validate_history(history)
    _check_private_material(history, "Phase 6B history")
    for index, record in enumerate(history["prospects"]):
        if not record["canonical_company_name"].casefold().startswith("synthetic "):
            raise ValidationError(f"History prospect {index} must be obviously synthetic.")
        for domain in [record["canonical_domain"], *record["domains"]]:
            _example_domain(domain, f"history prospect {index} domain")
        for signal in record["prior_signals"]:
            _example_url(signal["source_url"], f"history prospect {index} signal URL")
    return history


def filter_candidates(request_value: Any, candidate_values: Any, history_value: Any, *, today: date) -> dict[str, Any]:
    request = validate_search_request(request_value)
    history = _validate_synthetic_history(history_value)
    if not isinstance(candidate_values, list):
        raise ValidationError("Candidates must be an array.")
    business_unit = request["business_unit"]
    candidates = [_validate_candidate(value, business_unit=business_unit, today=today) for value in candidate_values]
    candidate_ids = [item["candidate_id"] for item in candidates]
    result_ids = [item["result_id"] for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)) or len(result_ids) != len(set(result_ids)):
        raise ValidationError("Candidate and result IDs must be unique.")
    opportunity_filter = request.get("opportunity_filter")
    include_any = set(opportunity_filter.get("include_any", [])) if opportunity_filter else set()
    exclude = set(opportunity_filter.get("exclude", [])) if opportunity_filter else set()
    decisions: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["candidate_id"]):
        classification = classify_duplicate(candidate, history, request["campaign"], today=today)
        kinds = sorted(item["kind"] for item in candidate["opportunities"])
        decision = {
            "candidate_id": candidate["candidate_id"],
            "result_id": candidate["result_id"],
            "global_identity_id": candidate["global_identity_id"],
            "account_id": candidate["account_id"],
            "canonical_domain": _example_domain(candidate["domain"], "candidate domain"),
            "business_unit": business_unit,
            "history_classification": classification,
            "matched_opportunities": kinds,
            "filter_state": "not_evaluated_history_protection",
            "qualification_state": "not_evaluated_history_protection",
            "qualification_score": candidate["qualification_score"],
            "selected": False,
        }
        if classification["status"] in ELIGIBLE_HISTORY_STATES:
            if not opportunity_filter:
                decision["filter_state"] = "not_requested"
            elif exclude & set(kinds):
                decision["filter_state"] = "excluded"
            elif include_any and not include_any & set(kinds):
                decision["filter_state"] = "no_match"
            else:
                decision["filter_state"] = "matched"
            if decision["filter_state"] in {"not_requested", "matched"}:
                if candidate["qualification_score"] >= request["campaign"]["minimum_qualification_score"]:
                    decision["qualification_state"] = "qualified"
                else:
                    decision["qualification_state"] = "below_threshold"
            else:
                decision["qualification_state"] = "not_evaluated_filter_veto"
        decisions.append(decision)
    qualified = sorted(
        (item for item in decisions if item["qualification_state"] == "qualified"),
        key=lambda item: (-item["qualification_score"], item["candidate_id"]),
    )
    cap = request["campaign"]["target_prospect_count"]
    for index, item in enumerate(qualified):
        if index < cap:
            item["selected"] = True
        else:
            item["qualification_state"] = "target_cap"
    return {
        "version": 1,
        "request_id": request["request_id"],
        "idempotency_identity": request["idempotency_identity"],
        "business_unit": business_unit,
        "opportunity_filter": copy.deepcopy(opportunity_filter) if opportunity_filter else None,
        "history_fingerprint": _canonical_hash(history),
        "decisions": decisions,
        "selected_result_ids": sorted(item["result_id"] for item in decisions if item["selected"]),
    }


def _validate_typed_value(value: Any, label: str, *, contact_category: bool) -> None:
    typed = _exact_object(value, label, {"type", "value"})
    kind = typed["type"]
    item = typed["value"]
    if kind == "string":
        _text(item, f"{label} value", maximum=2000)
    elif kind == "number":
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValidationError(f"{label} number must be finite.")
    elif kind == "boolean":
        if not isinstance(item, bool):
            raise ValidationError(f"{label} boolean value is invalid.")
    elif kind == "string_list":
        _string_list(item, f"{label} value", allow_empty=False)
    elif kind == "date":
        _iso_date(item, f"{label} value")
    elif kind == "url":
        _example_url(item, f"{label} value")
    elif kind == "object":
        if not isinstance(item, dict):
            raise ValidationError(f"{label} object value is invalid.")
    else:
        raise ValidationError(f"{label} type is unsupported.")
    if contact_category:
        if kind != "object":
            raise ValidationError("Public people and contact-path claims must use a bounded role object.")
        contact = _exact_object(
            item,
            "public business role claim",
            {"role_id", "role_title", "responsibility", "professional_profile_url", "official_business_route_url"},
        )
        _identifier(contact["role_id"], "public business role_id")
        _text(contact["role_title"], "public business role_title", maximum=160)
        _text(contact["responsibility"], "public business role responsibility", maximum=500)
        _example_url(contact["professional_profile_url"], "public professional profile URL")
        _example_url(contact["official_business_route_url"], "official business route URL")


def _validate_claim_freshness(
    claim: dict[str, Any],
    refs: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
    cutoff: datetime,
    maximum_age: int,
    label: str,
) -> None:
    source_date = _iso_date(claim["source_date"], f"{label} source_date")
    observed_at = _utc_datetime(claim["observed_at"], f"{label} observed_at")
    if source_date > cutoff.date() or observed_at > cutoff:
        raise ValidationError(f"{label} cannot be future-dated.")
    referenced = [evidence_by_id[ref] for ref in refs]
    if claim["source_date"] not in {item["source_date"] for item in referenced}:
        raise ValidationError(f"{label} source_date is not present in its evidence.")
    has_conflict = any(item["conflict_state"] == "conflicted" for item in referenced)
    has_stale = any((cutoff.date() - _iso_date(item["source_date"], "evidence source_date")).days > maximum_age for item in referenced)
    expected_freshness = "conflicted" if has_conflict else "stale" if has_stale else "current"
    if claim["freshness_state"] != expected_freshness:
        raise ValidationError(f"{label} freshness_state must be {expected_freshness}.")


def validate_customer_dossier(value: Any, *, history: Any, approved_result: Any) -> dict[str, Any]:
    required = {
        "schema_version",
        "synthetic",
        "dossier_id",
        "version",
        "idempotency_identity",
        "search_request_id",
        "business_unit",
        "approved_result",
        "research_cutoff",
        "maximum_evidence_age_days",
        "history_fingerprint",
        "duplicate_history",
        "evidence_inventory",
        "categories",
        "entities",
        "relationships",
        "review_state",
        "release_state",
    }
    dossier = _exact_object(value, "customer dossier", required)
    _check_document(dossier, "customer dossier")
    _check_private_material(dossier, "customer dossier")
    if dossier["schema_version"] != 1 or dossier["synthetic"] is not True:
        raise ValidationError("Customer dossier must be schema version 1 and explicitly synthetic.")
    _identifier(dossier["dossier_id"], "dossier_id")
    _identifier(dossier["idempotency_identity"], "dossier idempotency_identity")
    _identifier(dossier["search_request_id"], "dossier search_request_id")
    if isinstance(dossier["version"], bool) or not isinstance(dossier["version"], int) or dossier["version"] < 1:
        raise ValidationError("Customer dossier version must be a positive integer.")
    if dossier["business_unit"] not in BUSINESS_UNITS:
        raise ValidationError("Customer dossier business_unit is invalid.")
    history = _validate_synthetic_history(history)
    if dossier["history_fingerprint"] != _canonical_hash(history):
        raise ValidationError("Customer dossier history fingerprint does not match validated history.")
    approved_result = require_object(approved_result, "approved result projection")
    if approved_result.get("selected") is not True:
        raise ValidationError("Customer dossier requires an explicitly selected approved result.")
    binding = _exact_object(
        dossier["approved_result"],
        "customer dossier approved_result",
        {"approval_id", "result_id", "global_identity_id", "account_id", "canonical_domain"},
    )
    _identifier(binding["approval_id"], "approval_id")
    for key in ("result_id", "global_identity_id", "account_id"):
        _identifier(binding[key], key)
        if binding[key] != approved_result.get(key):
            raise ValidationError(f"Customer dossier {key} does not match the exact approved result.")
    if _example_domain(binding["canonical_domain"], "approved result canonical_domain") != approved_result.get("canonical_domain"):
        raise ValidationError("Customer dossier canonical_domain does not match the exact approved result.")
    if dossier["business_unit"] != approved_result.get("business_unit"):
        raise ValidationError("Customer dossier business unit does not match the exact approved result.")
    duplicate_history = _validate_duplicate_history(dossier["duplicate_history"], "customer dossier duplicate_history")
    if canonical_bytes(duplicate_history) != canonical_bytes(approved_result.get("history_classification")):
        raise ValidationError("Customer dossier duplicate/history classification does not match the approved result.")
    cutoff = _utc_datetime(dossier["research_cutoff"], "research_cutoff")
    maximum_age = dossier["maximum_evidence_age_days"]
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int) or not 1 <= maximum_age <= 3650:
        raise ValidationError("maximum_evidence_age_days must be an integer from 1 to 3650.")

    evidence_items = dossier["evidence_inventory"]
    if not isinstance(evidence_items, list) or not evidence_items:
        raise ValidationError("Customer dossier evidence_inventory must be non-empty.")
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(evidence_items):
        item = _exact_object(
            item,
            f"evidence {index}",
            {"evidence_id", "source_url", "source_kind", "source_date", "observed_at", "original_source", "summary", "conflict_state"},
        )
        evidence_id = _identifier(item["evidence_id"], f"evidence {index} evidence_id")
        if evidence_id in evidence_by_id:
            raise ValidationError("Evidence IDs must be unique.")
        evidence_by_id[evidence_id] = item
        _example_url(item["source_url"], f"evidence {index} source_url")
        if item["source_kind"] not in SOURCE_KINDS:
            raise ValidationError(f"Evidence {index} source_kind is unsupported.")
        source_date = _iso_date(item["source_date"], f"evidence {index} source_date")
        observed_at = _utc_datetime(item["observed_at"], f"evidence {index} observed_at")
        if source_date > cutoff.date() or observed_at > cutoff:
            raise ValidationError("Evidence cannot be future-dated relative to the research cutoff.")
        if not isinstance(item["original_source"], bool):
            raise ValidationError("Evidence original_source must be boolean.")
        _text(item["summary"], f"evidence {index} summary", maximum=1000)
        if item["conflict_state"] not in {"none", "conflicted"}:
            raise ValidationError("Evidence conflict_state must be none or conflicted.")

    entities = dossier["entities"]
    if not isinstance(entities, list) or not entities:
        raise ValidationError("Customer dossier entities must be non-empty.")
    node_ids: set[str] = set()
    for index, item in enumerate(entities):
        item = _exact_object(item, f"entity {index}", {"node_id", "node_type", "label", "business_unit", "attributes"})
        node_id = _identifier(item["node_id"], f"entity {index} node_id")
        if node_id in node_ids:
            raise ValidationError("Entity node IDs must be unique.")
        node_ids.add(node_id)
        if item["node_type"] not in NODE_TYPES - {"evidence"}:
            raise ValidationError(f"Entity {index} node_type is unsupported.")
        if item["business_unit"] != dossier["business_unit"]:
            raise ValidationError("Entity business unit does not match dossier.")
        _text(item["label"], f"entity {index} label", maximum=200)
        if not isinstance(item["attributes"], dict):
            raise ValidationError(f"Entity {index} attributes must be an object.")

    categories = dossier["categories"]
    if not isinstance(categories, list) or [item.get("category") for item in categories if isinstance(item, dict)] != list(CATEGORIES):
        raise ValidationError("Customer dossier must contain the exact eleven categories in canonical order.")
    claim_ids: set[str] = set()
    claims: list[dict[str, Any]] = []
    for category_index, category in enumerate(categories):
        category = _exact_object(
            category,
            f"category {category_index}",
            {"category", "coverage_state", "claims"},
            {"gap_explanation"},
        )
        coverage_state = category["coverage_state"]
        category_claims = category["claims"]
        if not isinstance(category_claims, list):
            raise ValidationError(f"Category {category['category']} claims must be an array.")
        if coverage_state == "complete":
            if not category_claims or category.get("gap_explanation") not in {None, ""}:
                raise ValidationError(f"Complete category {category['category']} requires claims and no gap explanation.")
        elif coverage_state in GAP_STATES:
            if category_claims or not isinstance(category.get("gap_explanation"), str) or not category["gap_explanation"].strip():
                raise ValidationError(f"Gap category {category['category']} requires one explanation and no claims.")
            _text(category["gap_explanation"], f"category {category['category']} gap_explanation", maximum=500)
        else:
            raise ValidationError(f"Category {category['category']} coverage_state is invalid.")
        for claim_index, claim in enumerate(category_claims):
            claim = _exact_object(
                claim,
                f"claim {claim_index} in {category['category']}",
                {
                    "claim_id",
                    "category",
                    "subject_node_id",
                    "typed_value",
                    "basis",
                    "evidence_refs",
                    "confidence_reason",
                    "uncertainty",
                    "source_date",
                    "observed_at",
                    "freshness_state",
                },
            )
            claim_id = _identifier(claim["claim_id"], "claim_id")
            if claim_id in claim_ids:
                raise ValidationError("Claim IDs must be unique.")
            claim_ids.add(claim_id)
            claims.append(claim)
            if claim["category"] != category["category"]:
                raise ValidationError("Claim category does not match its category container.")
            if claim["subject_node_id"] not in node_ids:
                raise ValidationError("Claim subject_node_id is unresolved.")
            _validate_typed_value(
                claim["typed_value"],
                f"claim {claim_id} typed_value",
                contact_category=category["category"] == "public_people_and_contact_paths",
            )
            if claim["basis"] not in BASIS_STATES:
                raise ValidationError(f"Claim {claim_id} basis is invalid.")
            refs = _string_list(claim["evidence_refs"], f"claim {claim_id} evidence_refs", allow_empty=False)
            if not set(refs) <= set(evidence_by_id):
                raise ValidationError(f"Claim {claim_id} evidence reference is unresolved.")
            _text(claim["confidence_reason"], f"claim {claim_id} confidence_reason", maximum=500)
            _text(claim["uncertainty"], f"claim {claim_id} uncertainty", maximum=500)
            _validate_claim_freshness(claim, refs, evidence_by_id, cutoff, maximum_age, f"Claim {claim_id}")

    relationships = dossier["relationships"]
    if not isinstance(relationships, list):
        raise ValidationError("Customer dossier relationships must be an array.")
    edge_ids: set[str] = set()
    for index, edge in enumerate(relationships):
        edge = _exact_object(
            edge,
            f"relationship {index}",
            {"edge_id", "edge_type", "from_node_id", "to_node_id", "business_unit", "claim_refs", "evidence_refs"},
        )
        edge_id = _identifier(edge["edge_id"], f"relationship {index} edge_id")
        if edge_id in edge_ids:
            raise ValidationError("Relationship edge IDs must be unique.")
        edge_ids.add(edge_id)
        if edge["edge_type"] not in EDGE_TYPES - {"evidence_supports"}:
            raise ValidationError("Relationship edge_type is unsupported.")
        if edge["from_node_id"] not in node_ids or edge["to_node_id"] not in node_ids:
            raise ValidationError("Relationship node reference is unresolved.")
        if edge["business_unit"] != dossier["business_unit"]:
            raise ValidationError("Relationship business unit does not match dossier.")
        edge_claims = _string_list(edge["claim_refs"], f"relationship {index} claim_refs")
        edge_evidence = _string_list(edge["evidence_refs"], f"relationship {index} evidence_refs")
        if not edge_claims and not edge_evidence:
            raise ValidationError("Customer dossier relationship must be provenance-linked.")
        if not set(edge_claims) <= claim_ids or not set(edge_evidence) <= set(evidence_by_id):
            raise ValidationError("Relationship claim or evidence reference is unresolved.")

    if dossier["review_state"] not in {"pending_research_quality_review", "research_quality_accepted", "changes_requested", "rejected"}:
        raise ValidationError("Customer dossier review_state is invalid.")
    if dossier["release_state"] not in {"not_released", "released_local_data_only"}:
        raise ValidationError("Customer dossier release_state is invalid.")
    if dossier["release_state"] == "released_local_data_only" and dossier["review_state"] != "research_quality_accepted":
        raise ValidationError("Only research-quality-accepted dossiers may be released for local data handoff.")
    _validate_projection_limits(dossier)
    return dossier


def _expected_package_id(business_unit: str, idempotency_identity: str) -> str:
    return "lip-" + hashlib.sha256(f"{business_unit}|{idempotency_identity}".encode("utf-8")).hexdigest()[:24]


def _package_payload(dossier: dict[str, Any]) -> dict[str, Any]:
    claims = sorted(
        (copy.deepcopy(claim) for category in dossier["categories"] for claim in category["claims"]),
        key=lambda item: item["claim_id"],
    )
    evidence = sorted(copy.deepcopy(dossier["evidence_inventory"]), key=lambda item: item["evidence_id"])
    nodes = sorted(copy.deepcopy(dossier["entities"]), key=lambda item: item["node_id"])
    for item in evidence:
        nodes.append({
            "node_id": f"evidence:{item['evidence_id']}",
            "node_type": "evidence",
            "label": item["evidence_id"],
            "business_unit": dossier["business_unit"],
            "attributes": {"source_url": item["source_url"], "source_date": item["source_date"]},
        })
    nodes.sort(key=lambda item: item["node_id"])
    edges = sorted(copy.deepcopy(dossier["relationships"]), key=lambda item: item["edge_id"])
    for claim in claims:
        for evidence_id in claim["evidence_refs"]:
            edges.append({
                "edge_id": f"evidence-supports:{evidence_id}:{claim['claim_id']}",
                "edge_type": "evidence_supports",
                "from_node_id": f"evidence:{evidence_id}",
                "to_node_id": claim["subject_node_id"],
                "business_unit": dossier["business_unit"],
                "claim_refs": [claim["claim_id"]],
                "evidence_refs": [evidence_id],
            })
    edges.sort(key=lambda item: item["edge_id"])
    coverage = [
        {
            "category": item["category"],
            "coverage_state": item["coverage_state"],
            "gap_explanation": item.get("gap_explanation"),
        }
        for item in dossier["categories"]
    ]
    return {
        "schema_version": 1,
        "package_id": _expected_package_id(dossier["business_unit"], dossier["idempotency_identity"]),
        "idempotency_identity": dossier["idempotency_identity"],
        "business_unit": dossier["business_unit"],
        "approved_result": copy.deepcopy(dossier["approved_result"]),
        "dossier_id": dossier["dossier_id"],
        "dossier_version": dossier["version"],
        "research_cutoff": dossier["research_cutoff"],
        "maximum_evidence_age_days": dossier["maximum_evidence_age_days"],
        "history_fingerprint": dossier["history_fingerprint"],
        "coverage_states": coverage,
        "claims": claims,
        "evidence_inventory": evidence,
        "nodes": nodes,
        "edges": edges,
        "duplicate_history": copy.deepcopy(dossier["duplicate_history"]),
        "review_release": {
            "review_state": dossier["review_state"],
            "release_state": dossier["release_state"],
        },
        "authority": {
            "local_data_handoff_only": True,
            "generation": False,
            "contact": False,
            "outreach": False,
            "external_write": False,
            "agent_invocation": False,
            "deployment": False,
        },
    }


def _validate_projection_limits(dossier: dict[str, Any]) -> None:
    payload = _package_payload(dossier)
    if len(payload["claims"]) > PACKAGE_MAX_CLAIMS:
        raise ValidationError("Customer dossier projects more claims than the package limit.")
    if len(payload["evidence_inventory"]) > PACKAGE_MAX_EVIDENCE:
        raise ValidationError("Customer dossier projects more evidence than the package limit.")
    if len(payload["nodes"]) > PACKAGE_MAX_NODES:
        raise ValidationError("Customer dossier projects more nodes than the package limit.")
    if len(payload["edges"]) > PACKAGE_MAX_EDGES:
        raise ValidationError("Customer dossier projects more edges than the package limit.")
    for items, item_name in ((payload["nodes"], "node"), (payload["edges"], "edge")):
        identifiers = [_identifier(item[f"{item_name}_id"], f"projected package {item_name}_id") for item in items]
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError(f"projected package {item_name} IDs must be unique.")
    projected = copy.deepcopy(payload)
    projected["canonical_hash"] = "0" * 64
    _check_document(
        projected,
        "projected lead-intelligence package",
        max_array_items=PACKAGE_MAX_ARRAY_ITEMS,
        max_document_bytes=PACKAGE_MAX_DOCUMENT_BYTES,
    )


def validate_lead_intelligence_package(value: Any) -> dict[str, Any]:
    package = _exact_object(
        value,
        "lead-intelligence package",
        {
            "schema_version",
            "package_id",
            "idempotency_identity",
            "business_unit",
            "approved_result",
            "dossier_id",
            "dossier_version",
            "research_cutoff",
            "maximum_evidence_age_days",
            "canonical_hash",
            "history_fingerprint",
            "coverage_states",
            "claims",
            "evidence_inventory",
            "nodes",
            "edges",
            "duplicate_history",
            "review_release",
            "authority",
        },
    )
    _check_document(
        package,
        "lead-intelligence package",
        max_array_items=PACKAGE_MAX_ARRAY_ITEMS,
        max_document_bytes=PACKAGE_MAX_DOCUMENT_BYTES,
    )
    _check_private_material(package, "lead-intelligence package")
    if package["schema_version"] != 1 or package["business_unit"] not in BUSINESS_UNITS:
        raise ValidationError("Lead-intelligence package version or business unit is invalid.")
    for key in ("package_id", "idempotency_identity", "dossier_id"):
        _identifier(package[key], key)
    if package["package_id"] != _expected_package_id(package["business_unit"], package["idempotency_identity"]):
        raise ValidationError("Lead-intelligence package package_id does not match its derived identity.")
    binding = _exact_object(
        package["approved_result"],
        "lead-intelligence package approved_result",
        {"approval_id", "result_id", "global_identity_id", "account_id", "canonical_domain"},
    )
    for key in ("approval_id", "result_id", "global_identity_id", "account_id"):
        _identifier(binding[key], f"package approved_result {key}")
    _example_domain(binding["canonical_domain"], "package approved_result canonical_domain")
    if isinstance(package["dossier_version"], bool) or not isinstance(package["dossier_version"], int) or package["dossier_version"] < 1:
        raise ValidationError("Lead-intelligence package dossier_version must be a positive integer.")
    cutoff = _utc_datetime(package["research_cutoff"], "package research_cutoff")
    maximum_age = package["maximum_evidence_age_days"]
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int) or not 1 <= maximum_age <= 3650:
        raise ValidationError("Lead-intelligence package maximum_evidence_age_days must be an integer from 1 to 3650.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(package["history_fingerprint"])):
        raise ValidationError("Lead-intelligence package history_fingerprint is invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(package["canonical_hash"])):
        raise ValidationError("Lead-intelligence package canonical_hash is invalid.")
    hash_payload = copy.deepcopy(package)
    recorded_hash = hash_payload.pop("canonical_hash")
    if _canonical_hash(hash_payload) != recorded_hash:
        raise ValidationError("Lead-intelligence package canonical hash does not match its content.")
    _validate_duplicate_history(package["duplicate_history"], "lead-intelligence package duplicate_history")
    if not isinstance(package["coverage_states"], list) or len(package["coverage_states"]) != len(CATEGORIES):
        raise ValidationError("Lead-intelligence package coverage is incomplete or unordered.")
    if [item.get("category") for item in package["coverage_states"] if isinstance(item, dict)] != list(CATEGORIES):
        raise ValidationError("Lead-intelligence package coverage is incomplete or unordered.")
    for index, coverage in enumerate(package["coverage_states"]):
        coverage = _exact_object(coverage, f"package coverage {index}", {"category", "coverage_state", "gap_explanation"})
        if coverage["coverage_state"] not in {"complete", *GAP_STATES}:
            raise ValidationError("Lead-intelligence package coverage state is invalid.")
        gap = coverage["gap_explanation"]
        if coverage["coverage_state"] == "complete":
            if gap is not None:
                raise ValidationError("Complete package coverage cannot have a gap explanation.")
        else:
            _text(gap, "package coverage gap_explanation", maximum=500)

    if not isinstance(package["evidence_inventory"], list) or not 1 <= len(package["evidence_inventory"]) <= PACKAGE_MAX_EVIDENCE:
        raise ValidationError("Lead-intelligence package evidence_inventory is outside its limit.")
    evidence_ids: list[str] = []
    for index, evidence in enumerate(package["evidence_inventory"]):
        evidence = _exact_object(
            evidence,
            f"package evidence {index}",
            {"evidence_id", "source_url", "source_kind", "source_date", "observed_at", "original_source", "summary", "conflict_state"},
        )
        evidence_ids.append(_identifier(evidence["evidence_id"], f"package evidence {index} evidence_id"))
        _example_url(evidence["source_url"], f"package evidence {index} source_url")
        if evidence["source_kind"] not in SOURCE_KINDS:
            raise ValidationError("Lead-intelligence package evidence source kind is invalid.")
        source_date = _iso_date(evidence["source_date"], f"package evidence {index} source_date")
        observed_at = _utc_datetime(evidence["observed_at"], f"package evidence {index} observed_at")
        if source_date > cutoff.date() or observed_at > cutoff:
            raise ValidationError("Lead-intelligence package evidence cannot be future-dated.")
        if not isinstance(evidence["original_source"], bool) or evidence["conflict_state"] not in {"none", "conflicted"}:
            raise ValidationError("Lead-intelligence package evidence metadata is invalid.")
        _text(evidence["summary"], f"package evidence {index} summary", maximum=1000)
    evidence_by_id = {item["evidence_id"]: item for item in package["evidence_inventory"]}

    if not isinstance(package["nodes"], list) or not 1 <= len(package["nodes"]) <= PACKAGE_MAX_NODES:
        raise ValidationError("Lead-intelligence package nodes are outside their limit.")
    node_ids: list[str] = []
    for index, node in enumerate(package["nodes"]):
        node = _exact_object(node, f"package node {index}", {"node_id", "node_type", "label", "business_unit", "attributes"})
        node_ids.append(_identifier(node["node_id"], f"package node {index} node_id"))
        if node["node_type"] not in NODE_TYPES or node["business_unit"] != package["business_unit"]:
            raise ValidationError("Lead-intelligence package node type or business unit is invalid.")
        _text(node["label"], f"package node {index} label", maximum=200)
        if not isinstance(node["attributes"], dict):
            raise ValidationError("Lead-intelligence package node attributes must be an object.")

    if not isinstance(package["claims"], list) or len(package["claims"]) > PACKAGE_MAX_CLAIMS:
        raise ValidationError("Lead-intelligence package claims are outside their limit.")
    claim_ids: list[str] = []
    for index, claim in enumerate(package["claims"]):
        claim = _exact_object(
            claim,
            f"package claim {index}",
            {"claim_id", "category", "subject_node_id", "typed_value", "basis", "evidence_refs", "confidence_reason", "uncertainty", "source_date", "observed_at", "freshness_state"},
        )
        claim_ids.append(_identifier(claim["claim_id"], f"package claim {index} claim_id"))
        if claim["category"] not in CATEGORIES or claim["subject_node_id"] not in node_ids:
            raise ValidationError("Lead-intelligence package claim category or subject is invalid.")
        _validate_typed_value(claim["typed_value"], f"package claim {index} typed_value", contact_category=claim["category"] == "public_people_and_contact_paths")
        if claim["basis"] not in BASIS_STATES or claim["freshness_state"] not in FRESHNESS_STATES:
            raise ValidationError("Lead-intelligence package claim basis or freshness is invalid.")
        refs = _string_list(claim["evidence_refs"], f"package claim {index} evidence_refs", allow_empty=False)
        if not set(refs) <= set(evidence_ids):
            raise ValidationError("Lead-intelligence package claim has a dangling evidence reference.")
        _text(claim["confidence_reason"], f"package claim {index} confidence_reason", maximum=500)
        _text(claim["uncertainty"], f"package claim {index} uncertainty", maximum=500)
        _validate_claim_freshness(claim, refs, evidence_by_id, cutoff, maximum_age, f"Package claim {index}")

    claim_categories = {claim["category"] for claim in package["claims"]}
    for coverage in package["coverage_states"]:
        has_claim = coverage["category"] in claim_categories
        if (coverage["coverage_state"] == "complete") != has_claim:
            raise ValidationError("Lead-intelligence package coverage and claims are inconsistent.")

    if not isinstance(package["edges"], list) or len(package["edges"]) > PACKAGE_MAX_EDGES:
        raise ValidationError("Lead-intelligence package edges are outside their limit.")
    edge_ids: list[str] = []
    for index, edge in enumerate(package["edges"]):
        edge = _exact_object(edge, f"package edge {index}", {"edge_id", "edge_type", "from_node_id", "to_node_id", "business_unit", "claim_refs", "evidence_refs"})
        edge_ids.append(_identifier(edge["edge_id"], f"package edge {index} edge_id"))
        if edge["edge_type"] not in EDGE_TYPES or edge["business_unit"] != package["business_unit"]:
            raise ValidationError("Lead-intelligence package edge type or business unit is invalid.")
        edge_claims = _string_list(edge["claim_refs"], f"package edge {index} claim_refs")
        edge_evidence = _string_list(edge["evidence_refs"], f"package edge {index} evidence_refs")
        if not edge_claims and not edge_evidence:
            raise ValidationError("Lead-intelligence package edge must be provenance-linked.")
    for values, label in ((node_ids, "node"), (claim_ids, "claim"), (evidence_ids, "evidence"), (edge_ids, "edge")):
        if any(not isinstance(item, str) for item in values) or len(values) != len(set(values)):
            raise ValidationError(f"Lead-intelligence package {label} IDs must be unique.")
    for edge in package["edges"]:
        if edge.get("from_node_id") not in node_ids or edge.get("to_node_id") not in node_ids:
            raise ValidationError("Lead-intelligence package edge has a dangling node reference.")
        if not set(edge.get("claim_refs", [])) <= set(claim_ids) or not set(edge.get("evidence_refs", [])) <= set(evidence_ids):
            raise ValidationError("Lead-intelligence package edge has a dangling claim or evidence reference.")
    if evidence_ids != sorted(evidence_ids) or claim_ids != sorted(claim_ids) or node_ids != sorted(node_ids) or edge_ids != sorted(edge_ids):
        raise ValidationError("Lead-intelligence package collections must use canonical identifier order.")

    expected_evidence_nodes = sorted(
        (
            {
                "node_id": f"evidence:{evidence['evidence_id']}",
                "node_type": "evidence",
                "label": evidence["evidence_id"],
                "business_unit": package["business_unit"],
                "attributes": {"source_url": evidence["source_url"], "source_date": evidence["source_date"]},
            }
            for evidence in package["evidence_inventory"]
        ),
        key=lambda item: item["node_id"],
    )
    actual_evidence_nodes = [node for node in package["nodes"] if node["node_type"] == "evidence"]
    if canonical_bytes(actual_evidence_nodes) != canonical_bytes(expected_evidence_nodes):
        raise ValidationError("Lead-intelligence package evidence-node projection is incomplete or altered.")

    expected_support_edges = sorted(
        (
            {
                "edge_id": f"evidence-supports:{evidence_id}:{claim['claim_id']}",
                "edge_type": "evidence_supports",
                "from_node_id": f"evidence:{evidence_id}",
                "to_node_id": claim["subject_node_id"],
                "business_unit": package["business_unit"],
                "claim_refs": [claim["claim_id"]],
                "evidence_refs": [evidence_id],
            }
            for claim in package["claims"]
            for evidence_id in claim["evidence_refs"]
        ),
        key=lambda item: item["edge_id"],
    )
    actual_support_edges = [edge for edge in package["edges"] if edge["edge_type"] == "evidence_supports"]
    if canonical_bytes(actual_support_edges) != canonical_bytes(expected_support_edges):
        raise ValidationError("Lead-intelligence package evidence-support projection is incomplete or altered.")
    review_release = _exact_object(package["review_release"], "lead-intelligence package review_release", {"review_state", "release_state"})
    if review_release["review_state"] not in {"pending_research_quality_review", "research_quality_accepted", "changes_requested", "rejected"}:
        raise ValidationError("Lead-intelligence package review state is invalid.")
    if review_release["release_state"] not in {"not_released", "released_local_data_only"}:
        raise ValidationError("Lead-intelligence package release state is invalid.")
    if review_release["release_state"] == "released_local_data_only" and review_release["review_state"] != "research_quality_accepted":
        raise ValidationError("Only research-quality-accepted packages may be locally released.")
    authority = _exact_object(
        package["authority"],
        "lead-intelligence package authority",
        {"local_data_handoff_only", "generation", "contact", "outreach", "external_write", "agent_invocation", "deployment"},
    )
    if authority["local_data_handoff_only"] is not True or any(authority[key] is not False for key in authority if key != "local_data_handoff_only"):
        raise ValidationError("Lead-intelligence package must grant no downstream action authority.")
    return package


def build_lead_intelligence_package(
    dossier_value: Any,
    *,
    history: Any,
    approved_result: Any,
    prior_packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dossier = validate_customer_dossier(dossier_value, history=history, approved_result=approved_result)
    payload = _package_payload(dossier)
    package = copy.deepcopy(payload)
    package["canonical_hash"] = _canonical_hash(payload)
    validate_lead_intelligence_package(package)
    replay: dict[str, Any] | None = None
    for prior in prior_packages or []:
        prior = validate_lead_intelligence_package(prior)
        if prior["idempotency_identity"] != package["idempotency_identity"]:
            continue
        if canonical_bytes(prior) != canonical_bytes(package):
            raise ContractConflict("Phase 6B idempotency identity was reused with changed package content.")
        replay = prior
    return copy.deepcopy(replay) if replay is not None else package


def validate_fixture_bundle(bundle_value: Any, history_bundle_value: Any) -> dict[str, Any]:
    bundle = _exact_object(bundle_value, "Phase 6B fixture bundle", {"version", "search_requests", "candidates", "dossiers"})
    history_bundle = _exact_object(history_bundle_value, "Phase 6B history bundle", {"version", "histories"})
    _check_document(bundle, "Phase 6B fixture bundle")
    _check_document(history_bundle, "Phase 6B history bundle")
    if bundle["version"] != 1 or history_bundle["version"] != 1:
        raise ValidationError("Phase 6B fixture and history bundle versions must be 1.")
    histories = _exact_object(history_bundle["histories"], "Phase 6B histories", BUSINESS_UNITS)
    for history in histories.values():
        _validate_synthetic_history(history)
    if not isinstance(bundle["search_requests"], list) or not isinstance(bundle["candidates"], list) or not isinstance(bundle["dossiers"], list):
        raise ValidationError("Phase 6B fixture bundle arrays are invalid.")
    requests: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    routed_business_units: set[str] = set()
    for request_value in bundle["search_requests"]:
        request = validate_search_request(request_value)
        if request["request_id"] in request_ids:
            raise ValidationError("Phase 6B search request IDs must be unique.")
        request_ids.add(request["request_id"])
        routed_business_units.add(request["business_unit"])
        requests.append(request)

    candidates: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    result_ids: set[str] = set()
    for index, candidate_value in enumerate(bundle["candidates"]):
        candidate = require_object(candidate_value, f"Phase 6B candidate {index}")
        business_unit = candidate.get("business_unit")
        if business_unit not in BUSINESS_UNITS:
            raise ValidationError(f"Phase 6B candidate {index} has an unsupported business unit.")
        if business_unit not in routed_business_units:
            raise ValidationError(f"Phase 6B candidate {index} has no search-request route.")
        candidate = _validate_candidate(candidate, business_unit=business_unit, today=date(2026, 7, 21))
        if candidate["candidate_id"] in candidate_ids or candidate["result_id"] in result_ids:
            raise ValidationError("Phase 6B candidate and result IDs must be globally unique.")
        candidate_ids.add(candidate["candidate_id"])
        result_ids.add(candidate["result_id"])
        candidates.append(candidate)

    evaluations: dict[str, dict[str, Any]] = {}
    for request in requests:
        business_unit = request["business_unit"]
        evaluations[request["request_id"]] = filter_candidates(
            request,
            [candidate for candidate in candidates if candidate["business_unit"] == business_unit],
            histories[business_unit],
            today=date(2026, 7, 21),
        )
    validated_dossiers: list[str] = []
    dossier_ids: set[str] = set()
    packages: list[dict[str, Any]] = []
    for dossier in bundle["dossiers"]:
        dossier = require_object(dossier, "customer dossier fixture")
        dossier_id = _identifier(dossier.get("dossier_id"), "customer dossier fixture dossier_id")
        if dossier_id in dossier_ids:
            raise ValidationError("Customer dossier IDs must be unique within a fixture bundle.")
        dossier_ids.add(dossier_id)
        evaluation = evaluations.get(dossier.get("search_request_id"))
        if not evaluation:
            raise ValidationError("Customer dossier references an unknown search request.")
        result_id = require_object(dossier.get("approved_result"), "customer dossier approved_result").get("result_id")
        approved = next((item for item in evaluation["decisions"] if item["result_id"] == result_id), None)
        if not approved:
            raise ValidationError("Customer dossier references an unknown approved result.")
        business_unit = dossier.get("business_unit")
        if business_unit not in histories:
            raise ValidationError("Customer dossier has an unsupported business unit.")
        validated = validate_customer_dossier(dossier, history=histories[business_unit], approved_result=approved)
        packages.append(
            build_lead_intelligence_package(
                validated,
                history=histories[business_unit],
                approved_result=approved,
                prior_packages=packages,
            )
        )
        validated_dossiers.append(validated["dossier_id"])
    return {
        "validated_dossiers": sorted(validated_dossiers),
        "validated_packages": len(packages),
        "package_hashes": sorted(item["canonical_hash"] for item in packages),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("history", type=Path)
    args = parser.parse_args()
    try:
        result = validate_fixture_bundle(
            load_json_strict(args.fixtures, "Phase 6B fixtures"),
            load_json_strict(args.history, "Phase 6B history"),
        )
    except ValidationError as exc:
        raise SystemExit(f"Validation error: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
