#!/usr/bin/env python3
"""Validate the exact Phase 6 public-source plan and real dossier contracts."""

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
from urllib.parse import urlsplit

from classify_duplicate import validate_history
from common import ValidationError
from normalize_domain import normalize_domain
from validate_phase6b_contract import (
    BASIS_STATES,
    BLOCKED_KEY_PARTS,
    CATEGORIES,
    CREDENTIAL_PATTERN,
    EDGE_TYPES,
    ELIGIBLE_HISTORY_STATES,
    FRESHNESS_STATES,
    GAP_STATES,
    ID_PATTERN,
    NODE_TYPES,
    OPPORTUNITY_TYPES,
    PACKAGE_MAX_ARRAY_ITEMS,
    PACKAGE_MAX_CLAIMS,
    PACKAGE_MAX_DOCUMENT_BYTES,
    PACKAGE_MAX_EDGES,
    PACKAGE_MAX_EVIDENCE,
    PACKAGE_MAX_NODES,
    PHONE_PATTERN,
    SOURCE_KINDS,
    EMAIL_PATTERN,
    _check_document,
    _exact_object,
    _identifier,
    _iso_date,
    _normalized_key,
    _string_list,
    _text,
    _utc_datetime,
    _validate_duplicate_history,
    canonical_bytes,
    load_json_strict,
)

REAL_CONTRACT_VERSION = 2
REAL_ACCOUNT_IDENTITY_VERSION = "account-v1"
REAL_GLOBAL_IDENTITY_VERSION = "global-v1"
REAL_RESULT_ID_VERSION = "result-real-v1"
REAL_PACKAGE_ID_VERSION = "lip-real-v2"
REAL_BUSINESS_UNIT = "unreal-media-group"
REAL_INCLUDE_ANY = ("product_photography", "product_video")
REAL_EXCLUDE = ("ugc_ad",)
REAL_SOURCE_STATUSES = {"success", "failed"}
REAL_SOURCE_REASON_CODES = {
    "ok",
    "access_control",
    "body_limit",
    "content_rejected",
    "deadline",
    "dns_rejected",
    "http_status",
    "privacy_rejected",
    "redirect_rejected",
    "robots_denied",
    "robots_unavailable",
    "tls_rejected",
    "transport_error",
}
REAL_SOURCE_KIND_BY_CLASS = {
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
}
AUTHORIZED_PLAN_HASHES = {
    "phase6-live-proof-celsius-v1": "d80f8ad433ad3d44f726b566539b1c1f59890087ca224d2ceed5fff636a318bf",
    "phase6-live-proof-jazwares-v1": "4ff2de65b2ffda9d2e0e75ae8821c98eeadbe5a0b46b562dd3c022098d54aaeb",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DOMAIN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
REAL_OBFUSCATED_EMAIL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9._%+-])[a-z0-9._%+-]+\s*"
    r"(?:@|\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+)\s*"
    r"[a-z0-9-]+(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\.)\s*"
    r"[a-z0-9-]+)+(?![a-z0-9.-])"
)
REAL_PHONE_PATTERN = re.compile(r"(?<!\d)\+?\d[\d .()/\-\u2013\u2014]{7,}\d(?!\d)")
REAL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:bearer\s+|api[ _-]?key\s*[:=]|access[ _-]?token\s*[:=]|"
    r"client[ _-]?secret\s*[:=]|private[ _-]?key(?:\s*[:=])?|"
    r"(?:temporary\s+)?password\s*[:=]|passcode\s*[:=])"
)
REAL_EMBEDDED_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
REAL_BARE_URL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9.-])www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s<>'\"]*)?"
)
REAL_PUBLIC_TEXT_DOMAIN_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9@._-])(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})(?::\d{1,5})?(?:[/?#][^\s<>'\"]*)?"
)


def scrub_real_public_text(value: str) -> str:
    """Remove contact routes and URLs; reject credential-like visible text."""
    if not isinstance(value, str) or REAL_CREDENTIAL_PATTERN.search(value):
        raise ValidationError("Public source text contains prohibited credential material.")
    value = REAL_OBFUSCATED_EMAIL_PATTERN.sub("[redacted]", value)
    value = EMAIL_PATTERN.sub("[redacted]", value)
    value = REAL_PHONE_PATTERN.sub("[redacted]", value)
    value = REAL_EMBEDDED_URL_PATTERN.sub("[redacted]", value)
    value = REAL_BARE_URL_PATTERN.sub("[redacted]", value)
    value = REAL_PUBLIC_TEXT_DOMAIN_PATTERN.sub("[redacted]", value)
    value = " ".join(value.split())
    if (
        EMAIL_PATTERN.search(value)
        or REAL_OBFUSCATED_EMAIL_PATTERN.search(value)
        or REAL_PHONE_PATTERN.search(value)
        or REAL_CREDENTIAL_PATTERN.search(value)
        or REAL_EMBEDDED_URL_PATTERN.search(value)
        or REAL_BARE_URL_PATTERN.search(value)
        or REAL_PUBLIC_TEXT_DOMAIN_PATTERN.search(value)
    ):
        raise ValidationError("Public source text contains prohibited private material.")
    return value


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _check_real_private_material(
    value: Any,
    label: str = "Phase 6 real data",
    *,
    field: str = "",
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in BLOCKED_KEY_PARTS):
                raise ValidationError(f"{label} contains prohibited private or credential field {key}.")
            _check_real_private_material(item, label, field=normalized)
        return
    if isinstance(value, list):
        for item in value:
            _check_real_private_material(item, label, field=field)
        return
    if not isinstance(value, str):
        return
    if (
        EMAIL_PATTERN.search(value)
        or REAL_OBFUSCATED_EMAIL_PATTERN.search(value)
        or CREDENTIAL_PATTERN.search(value)
        or REAL_CREDENTIAL_PATTERN.search(value)
    ):
        raise ValidationError(f"{label} contains prohibited contact or credential material.")
    looks_like_date = False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
            looks_like_date = True
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            looks_like_date = parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    except ValueError:
        pass
    is_digest = _HEX64.fullmatch(value) is not None
    is_identifier = (
        field.endswith(("id", "ids", "identity", "refs"))
        and ID_PATTERN.fullmatch(value) is not None
    )
    if (
        not looks_like_date
        and not is_digest
        and not is_identifier
        and (PHONE_PATTERN.search(value) or REAL_PHONE_PATTERN.search(value))
    ):
        raise ValidationError(f"{label} contains a prohibited telephone value.")
    embedded_urls = REAL_EMBEDDED_URL_PATTERN.findall(value)
    if embedded_urls and not (len(embedded_urls) == 1 and embedded_urls[0] == value):
        raise ValidationError(f"{label} contains an embedded unvalidated URL.")
    if not embedded_urls and REAL_BARE_URL_PATTERN.search(value):
        raise ValidationError(f"{label} contains an embedded unvalidated URL.")


def _exact_keys(
    value: Any,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    return _exact_object(value, label, required, optional or set())


def _public_domain(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DOMAIN.fullmatch(value):
        raise ValidationError(f"{label} must be a normalized public DNS name.")
    try:
        normalized = normalize_domain(value)
    except ValidationError as exc:
        raise ValidationError(f"{label} must be a normalized public DNS name.") from exc
    if normalized != value or value.endswith(".example") or value.endswith(".invalid"):
        raise ValidationError(f"{label} must be a real normalized public DNS name.")
    return value


def _public_hostname(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DOMAIN.fullmatch(value):
        raise ValidationError(f"{label} must be a public DNS hostname.")
    if value.endswith((".example", ".invalid", ".localhost")) or value == "localhost":
        raise ValidationError(f"{label} must be a real public DNS hostname.")
    try:
        normalize_domain(value)
    except ValidationError as exc:
        raise ValidationError(f"{label} must be a public DNS hostname.") from exc
    return value


def _safe_https_url(value: Any, label: str, *, origin_only: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValidationError(f"{label} must be a bounded HTTPS URL.")
    if not value.isascii() or "%" in value or "\\" in value:
        raise ValidationError(f"{label} contains an unsupported URL representation.")
    if any(character.isspace() or ord(character) < 33 for character in value):
        raise ValidationError(f"{label} contains whitespace or control characters.")
    if any(character in value for character in '<>"\''):
        raise ValidationError(f"{label} contains unsafe delimiter characters.")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError(f"{label} contains an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(f"{label} must use unauthenticated HTTPS on port 443 without query or fragment.")
    _public_hostname(parsed.hostname, f"{label} hostname")
    if origin_only:
        if parsed.path:
            raise ValidationError(f"{label} must be an origin without a path.")
    elif not parsed.path.startswith("/"):
        raise ValidationError(f"{label} must contain an absolute path.")
    return value


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port == 443 and ":443" in parsed.netloc else ""
    return f"https://{host}{port}"


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in plan.items() if key != "source_plan_hash"}


def validate_real_source_plan(value: Any) -> dict[str, Any]:
    required = {
        "contract_version",
        "synthetic",
        "source_plan_id",
        "organization_name",
        "location_basis",
        "canonical_domain",
        "approved_related_domains",
        "stable_identity_seed",
        "business_unit",
        "opportunity_filter",
        "approved_origins",
        "robots_policy_urls",
        "sources",
        "budgets",
        "source_plan_hash",
    }
    plan = _exact_keys(value, "real source plan", required)
    _check_document(plan, "real source plan", max_array_items=100, max_document_bytes=100_000)
    _check_real_private_material(plan, "real source plan")
    if plan["contract_version"] != REAL_CONTRACT_VERSION or plan["synthetic"] is not False:
        raise ValidationError("Real source plan must be contract version 2 and explicitly non-synthetic.")
    plan_id = _identifier(plan["source_plan_id"], "source_plan_id")
    _text(plan["organization_name"], "source plan organization_name", maximum=200)
    _text(plan["location_basis"], "source plan location_basis", maximum=300)
    canonical_domain = _public_domain(plan["canonical_domain"], "source plan canonical_domain")
    related = _string_list(
        plan["approved_related_domains"],
        "source plan approved_related_domains",
        allow_empty=True,
    )
    normalized_related = [_public_domain(item, "approved related domain") for item in related]
    if len(normalized_related) != len(set(normalized_related)) or canonical_domain in normalized_related:
        raise ValidationError("Approved related domains must be unique and exclude the canonical domain.")
    expected_seed_suffix = f"|{canonical_domain}"
    if (
        not isinstance(plan["stable_identity_seed"], str)
        or not 3 <= len(plan["stable_identity_seed"]) <= 300
        or not plan["stable_identity_seed"].endswith(expected_seed_suffix)
        or any(ord(character) < 33 for character in plan["stable_identity_seed"])
    ):
        raise ValidationError("Stable identity seed must be bounded and end with the canonical domain.")
    if plan["business_unit"] != REAL_BUSINESS_UNIT:
        raise ValidationError("The authorized real source plans are limited to unreal-media-group.")
    opportunity_filter = _exact_keys(
        plan["opportunity_filter"],
        "real opportunity_filter",
        {"include_any", "exclude"},
    )
    include_any = _string_list(
        opportunity_filter["include_any"],
        "real opportunity_filter include_any",
        allowed=OPPORTUNITY_TYPES,
        allow_empty=False,
    )
    exclude = _string_list(
        opportunity_filter["exclude"],
        "real opportunity_filter exclude",
        allowed=OPPORTUNITY_TYPES,
        allow_empty=False,
    )
    if tuple(include_any) != REAL_INCLUDE_ANY or tuple(exclude) != REAL_EXCLUDE:
        raise ValidationError("Real source plan opportunity filter does not match the exact live-proof authorization.")

    origins = _string_list(plan["approved_origins"], "approved_origins", allow_empty=False)
    if len(origins) != len(set(origins)) or len(origins) > 12:
        raise ValidationError("Approved origins must be a unique bounded list.")
    allowed_roots = {canonical_domain, *normalized_related}
    for origin in origins:
        _safe_https_url(origin, "approved origin", origin_only=True)
        hostname = urlsplit(origin).hostname or ""
        if not any(hostname == root or hostname.endswith(f".{root}") for root in allowed_roots):
            raise ValidationError("Approved origin is not beneath an approved organization domain.")

    robots = _string_list(plan["robots_policy_urls"], "robots_policy_urls", allow_empty=False)
    if len(robots) != len(set(robots)) or len(robots) > 12:
        raise ValidationError("Robots-policy URLs must be a unique bounded list.")
    robots_by_origin: dict[str, str] = {}
    for url in robots:
        _safe_https_url(url, "robots-policy URL")
        if urlsplit(url).path != "/robots.txt":
            raise ValidationError("Robots-policy URLs must use the exact /robots.txt path.")
        source_origin = _origin(url)
        if source_origin in robots_by_origin:
            raise ValidationError("Each approved origin must have exactly one robots-policy URL.")
        robots_by_origin[source_origin] = url
    if set(robots_by_origin) != set(origins):
        raise ValidationError("Robots-policy origins must exactly cover the approved origins.")

    sources = plan["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 12:
        raise ValidationError("Real source plan sources must be a bounded non-empty list.")
    source_urls: list[str] = []
    for index, source_value in enumerate(sources):
        source = _exact_keys(source_value, f"real source {index}", {"url", "source_class"})
        url = _safe_https_url(source["url"], f"real source {index} URL")
        if _origin(url) not in origins:
            raise ValidationError("Real source URL is outside the exact approved origins.")
        source_class = _text(source["source_class"], f"real source {index} source_class", maximum=160)
        if source_class not in REAL_SOURCE_KIND_BY_CLASS:
            raise ValidationError("Real source class is outside the exact authorized vocabulary.")
        source_urls.append(url)
    if len(source_urls) != len(set(source_urls)):
        raise ValidationError("Real source URLs must be unique.")

    budgets = _exact_keys(
        plan["budgets"],
        "real source budgets",
        {
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "request_deadline_seconds",
            "run_deadline_seconds",
            "max_redirects_per_source",
            "max_requests_total",
            "max_header_bytes",
            "max_body_bytes",
            "max_robots_bytes",
            "max_extracted_text_bytes",
        },
    )
    ranges = {
        "connect_timeout_seconds": (1, 15),
        "read_timeout_seconds": (1, 30),
        "request_deadline_seconds": (2, 60),
        "run_deadline_seconds": (10, 300),
        "max_redirects_per_source": (0, 3),
        "max_requests_total": (2, 40),
        "max_header_bytes": (4_096, 65_536),
        "max_body_bytes": (1_024, 2_000_000),
        "max_robots_bytes": (1_024, 524_288),
        "max_extracted_text_bytes": (1_024, 500_000),
    }
    for key, (minimum, maximum) in ranges.items():
        number = budgets[key]
        if isinstance(number, bool) or not isinstance(number, int) or not minimum <= number <= maximum:
            raise ValidationError(f"Real source budget {key} is outside its allowed range.")
    if (
        budgets["connect_timeout_seconds"] > budgets["request_deadline_seconds"]
        or budgets["read_timeout_seconds"] > budgets["request_deadline_seconds"]
        or budgets["request_deadline_seconds"] > budgets["run_deadline_seconds"]
        or budgets["max_requests_total"] < len(sources) + len(robots)
    ):
        raise ValidationError("Real source budgets cannot satisfy the exact request plan.")

    recorded_hash = plan["source_plan_hash"]
    if not isinstance(recorded_hash, str) or not _HEX64.fullmatch(recorded_hash):
        raise ValidationError("Real source plan hash is invalid.")
    if _canonical_hash(_plan_payload(plan)) != recorded_hash:
        raise ValidationError("Real source plan hash does not match its canonical content.")
    if AUTHORIZED_PLAN_HASHES.get(plan_id) != recorded_hash:
        raise ValidationError("Real source plan is not one of the exact repository-authorized plans.")
    return plan


def validate_real_source_manifest(value: Any) -> dict[str, Any]:
    manifest = _exact_keys(value, "real source manifest", {"manifest_version", "plans"})
    if manifest["manifest_version"] != 1 or not isinstance(manifest["plans"], list):
        raise ValidationError("Real source manifest must be version 1 with a plans array.")
    plans = [validate_real_source_plan(plan) for plan in manifest["plans"]]
    ids = [plan["source_plan_id"] for plan in plans]
    if ids != list(AUTHORIZED_PLAN_HASHES):
        raise ValidationError("Real source manifest must contain the exact authorized plans in order.")
    return manifest


def load_real_source_manifest(path: Path) -> dict[str, Any]:
    return validate_real_source_manifest(load_json_strict(path, "Phase 6 real source manifest"))


def derive_real_global_identity_id(stable_identity_seed: str) -> str:
    payload = {"stable_identity_seed": stable_identity_seed}
    return f"{REAL_GLOBAL_IDENTITY_VERSION}-{_canonical_hash(payload)[:24]}"


def derive_real_account_id(canonical_domain: str, global_identity_id: str) -> str:
    _public_domain(canonical_domain, "account canonical_domain")
    _identifier(global_identity_id, "account global_identity_id")
    payload = {
        "canonical_domain": canonical_domain,
        "global_identity_id": global_identity_id,
    }
    return f"{REAL_ACCOUNT_IDENTITY_VERSION}-{_canonical_hash(payload)[:24]}"


def derive_real_result_id(plan: dict[str, Any]) -> str:
    validate_real_source_plan(plan)
    global_identity_id = derive_real_global_identity_id(plan["stable_identity_seed"])
    payload = {
        "business_unit": plan["business_unit"],
        "global_identity_id": global_identity_id,
        "opportunity_filter": plan["opportunity_filter"],
        "source_plan_hash": plan["source_plan_hash"],
    }
    return f"{REAL_RESULT_ID_VERSION}-{_canonical_hash(payload)[:24]}"


def build_real_result_projection(
    plan: dict[str, Any],
    history_classification: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_real_source_plan(plan)
    classification = _validate_duplicate_history(
        history_classification,
        "real result history_classification",
    )
    global_identity_id = derive_real_global_identity_id(plan["stable_identity_seed"])
    account_id = derive_real_account_id(plan["canonical_domain"], global_identity_id)
    result_id = derive_real_result_id(plan)
    selected = classification["status"] in ELIGIBLE_HISTORY_STATES
    candidate = {
        "contract_version": REAL_CONTRACT_VERSION,
        "synthetic": False,
        "candidate_id": f"candidate-{result_id}",
        "result_id": result_id,
        "global_identity_id": global_identity_id,
        "account_id": account_id,
        "business_unit": plan["business_unit"],
        "company_name": plan["organization_name"],
        "domain": plan["canonical_domain"],
        "company_type": "brand",
        "qualification_score": 0,
        "opportunities": [
            {"kind": kind, "evidence_refs": [], "basis": "authorized_search_intent_only"}
            for kind in plan["opportunity_filter"]["include_any"]
        ],
        "evidence": [],
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
    }
    decision = {
        "candidate_id": candidate["candidate_id"],
        "result_id": result_id,
        "global_identity_id": global_identity_id,
        "account_id": account_id,
        "canonical_domain": plan["canonical_domain"],
        "business_unit": plan["business_unit"],
        "history_classification": copy.deepcopy(classification),
        "matched_opportunities": list(plan["opportunity_filter"]["include_any"]),
        "filter_state": "matched" if selected else "not_evaluated_history_protection",
        "qualification_state": (
            "pending_bounded_public_research"
            if selected
            else "not_evaluated_history_protection"
        ),
        "qualification_score": 0,
        "search_intent_is_demand_evidence": False,
        "selected": selected,
    }
    validate_real_result_projection(candidate, decision, plan)
    return candidate, decision


def validate_real_result_projection(
    candidate_value: Any,
    decision_value: Any,
    plan_value: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = validate_real_source_plan(plan_value)
    candidate = _exact_keys(
        candidate_value,
        "real candidate",
        {
            "contract_version",
            "synthetic",
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
            "source_plan_id",
            "source_plan_hash",
        },
    )
    decision = _exact_keys(
        decision_value,
        "real search decision",
        {
            "candidate_id",
            "result_id",
            "global_identity_id",
            "account_id",
            "canonical_domain",
            "business_unit",
            "history_classification",
            "matched_opportunities",
            "filter_state",
            "qualification_state",
            "qualification_score",
            "search_intent_is_demand_evidence",
            "selected",
        },
    )
    _check_document(candidate, "real candidate", max_array_items=40, max_document_bytes=100_000)
    _check_real_private_material(candidate, "real candidate")
    expected_global = derive_real_global_identity_id(plan["stable_identity_seed"])
    expected_account = derive_real_account_id(plan["canonical_domain"], expected_global)
    expected_result = derive_real_result_id(plan)
    expected_candidate = f"candidate-{expected_result}"
    if not all((
        candidate["contract_version"] == REAL_CONTRACT_VERSION,
        candidate["synthetic"] is False,
        candidate["candidate_id"] == expected_candidate,
        candidate["result_id"] == expected_result,
        candidate["global_identity_id"] == expected_global,
        candidate["account_id"] == expected_account,
        candidate["business_unit"] == plan["business_unit"],
        candidate["company_name"] == plan["organization_name"],
        candidate["domain"] == plan["canonical_domain"],
        candidate["company_type"] == "brand",
        candidate["qualification_score"] == 0,
        candidate["evidence"] == [],
        candidate["source_plan_id"] == plan["source_plan_id"],
        candidate["source_plan_hash"] == plan["source_plan_hash"],
    )):
        raise ValidationError("Real candidate does not match its exact source plan derivation.")
    opportunities = candidate["opportunities"]
    expected_opportunities = [
        {"kind": kind, "evidence_refs": [], "basis": "authorized_search_intent_only"}
        for kind in plan["opportunity_filter"]["include_any"]
    ]
    if opportunities != expected_opportunities:
        raise ValidationError("Real candidate opportunities must remain explicit search intent only.")
    classification = _validate_duplicate_history(
        decision["history_classification"],
        "real search decision history_classification",
    )
    selected = classification["status"] in ELIGIBLE_HISTORY_STATES
    expected_decision = {
        "candidate_id": expected_candidate,
        "result_id": expected_result,
        "global_identity_id": expected_global,
        "account_id": expected_account,
        "canonical_domain": plan["canonical_domain"],
        "business_unit": plan["business_unit"],
        "history_classification": classification,
        "matched_opportunities": list(plan["opportunity_filter"]["include_any"]),
        "filter_state": "matched" if selected else "not_evaluated_history_protection",
        "qualification_state": (
            "pending_bounded_public_research"
            if selected
            else "not_evaluated_history_protection"
        ),
        "qualification_score": 0,
        "search_intent_is_demand_evidence": False,
        "selected": selected,
    }
    if decision != expected_decision:
        raise ValidationError("Real search decision does not match history-first exact-plan routing.")
    return candidate, decision


def validate_real_research_bundle(value: Any, plan_value: Any) -> dict[str, Any]:
    plan = validate_real_source_plan(plan_value)
    bundle = _exact_keys(
        value,
        "real research bundle",
        {"bundle_version", "source_plan_id", "source_plan_hash", "started_at", "completed_at", "sources"},
    )
    _check_document(bundle, "real research bundle", max_array_items=100, max_document_bytes=200_000)
    _check_real_private_material(bundle, "real research bundle")
    if (
        bundle["bundle_version"] != 1
        or bundle["source_plan_id"] != plan["source_plan_id"]
        or bundle["source_plan_hash"] != plan["source_plan_hash"]
    ):
        raise ValidationError("Real research bundle does not match its exact source plan.")
    started = _utc_datetime(bundle["started_at"], "real research started_at")
    completed = _utc_datetime(bundle["completed_at"], "real research completed_at")
    if completed < started:
        raise ValidationError("Real research completion cannot precede its start.")
    if not isinstance(bundle["sources"], list) or len(bundle["sources"]) != len(plan["sources"]):
        raise ValidationError("Real research bundle must contain one ordered record per exact source.")
    robots_by_origin = {_origin(url): url for url in plan["robots_policy_urls"]}
    approved_urls = {source["url"] for source in plan["sources"]}
    for index, (record_value, planned) in enumerate(zip(bundle["sources"], plan["sources"], strict=True)):
        record = _exact_keys(
            record_value,
            f"real source record {index}",
            {
                "source_record_version",
                "requested_url",
                "final_url",
                "redirect_chain",
                "source_class",
                "source_kind",
                "robots_url",
                "observed_at",
                "source_date",
                "date_state",
                "status",
                "safe_reason_code",
                "content_type",
                "body_sha256",
                "body_byte_length",
                "extracted_text_sha256",
                "extracted_text_byte_length",
                "summary",
                "conflict_state",
            },
        )
        if (
            record["source_record_version"] != 1
            or record["requested_url"] != planned["url"]
            or record["source_class"] != planned["source_class"]
            or record["source_kind"] != REAL_SOURCE_KIND_BY_CLASS[planned["source_class"]]
            or record["robots_url"] != robots_by_origin[_origin(planned["url"])]
            or record["status"] not in REAL_SOURCE_STATUSES
            or record["safe_reason_code"] not in REAL_SOURCE_REASON_CODES
            or record["conflict_state"] not in {"none", "conflicted"}
        ):
            raise ValidationError("Real source record does not match the exact approved source metadata.")
        observed = _utc_datetime(record["observed_at"], f"real source record {index} observed_at")
        if observed < started or observed > completed:
            raise ValidationError("Real source observation must fall inside the bounded run interval.")
        chain = _string_list(record["redirect_chain"], f"real source record {index} redirect_chain")
        if len(chain) > plan["budgets"]["max_redirects_per_source"]:
            raise ValidationError("Real source redirect chain exceeds its exact budget.")
        for redirected in chain:
            _safe_https_url(redirected, "real source redirect URL")
            if redirected not in approved_urls:
                raise ValidationError("Real source redirect is not an exact approved URL.")
        if record["final_url"] is not None:
            _safe_https_url(record["final_url"], "real source final_url")
            if record["final_url"] not in approved_urls:
                raise ValidationError("Real source final URL is not an exact approved URL.")
        if record["status"] == "success":
            if (
                record["safe_reason_code"] != "ok"
                or record["final_url"] is None
                or record["content_type"] not in {"text/html", "text/plain"}
                or not isinstance(record["source_date"], str)
                or record["date_state"] not in {"published_date", "observed_date_fallback"}
                or not isinstance(record["summary"], str)
                or not record["summary"].strip()
                or len(record["summary"]) > 1_000
            ):
                raise ValidationError("Successful real source record has incomplete bounded metadata.")
            if scrub_real_public_text(record["summary"]) != record["summary"]:
                raise ValidationError("Successful real source summary contains prohibited material.")
            source_date = _iso_date(record["source_date"], f"real source record {index} source_date")
            if source_date > completed.date():
                raise ValidationError("Real source date cannot be future-dated.")
            for hash_key in ("body_sha256", "extracted_text_sha256"):
                if not isinstance(record[hash_key], str) or not _HEX64.fullmatch(record[hash_key]):
                    raise ValidationError("Successful real source hashes must be lowercase SHA-256 values.")
            body_length = record["body_byte_length"]
            text_length = record["extracted_text_byte_length"]
            if (
                isinstance(body_length, bool)
                or not isinstance(body_length, int)
                or not 0 < body_length <= plan["budgets"]["max_body_bytes"]
                or isinstance(text_length, bool)
                or not isinstance(text_length, int)
                or not 0 < text_length <= plan["budgets"]["max_extracted_text_bytes"]
            ):
                raise ValidationError("Successful real source lengths exceed their exact budgets.")
        else:
            if (
                record["safe_reason_code"] == "ok"
                or record["source_date"] is not None
                or record["date_state"] != "unavailable"
                or record["content_type"] is not None
                or record["body_sha256"] is not None
                or record["body_byte_length"] != 0
                or record["extracted_text_sha256"] is not None
                or record["extracted_text_byte_length"] != 0
                or record["summary"] != ""
            ):
                raise ValidationError("Failed real source records may contain safe failure metadata only.")
    return bundle


def _freshness(
    source_date: str,
    cutoff: datetime,
    maximum_age_days: int,
    *,
    conflicted: bool,
) -> str:
    if conflicted:
        return "conflicted"
    return "stale" if (cutoff.date() - _iso_date(source_date, "source date")).days > maximum_age_days else "current"


def _category_sources(source_class: str) -> set[str]:
    mapping = {
        "official organization and corporate overview": {
            "identity_and_relationships", "company_and_commercial_context", "operations_and_digital_footprint"
        },
        "official brand-owned site": {"operations_and_digital_footprint", "brand_and_messaging"},
        "official company and brand information": {
            "identity_and_relationships", "company_and_commercial_context", "brand_and_messaging"
        },
        "official product portfolio": {
            "company_and_commercial_context", "operations_and_digital_footprint",
            "audiences_market_and_reputation", "brand_and_messaging", "opportunity_and_fit"
        },
        "official product detail": {
            "operations_and_digital_footprint", "audiences_market_and_reputation",
            "brand_and_messaging", "opportunity_and_fit"
        },
        "official investor and corporate profile": {
            "identity_and_relationships", "company_and_commercial_context", "additional_material_facts"
        },
        "official organization site": {
            "identity_and_relationships", "company_and_commercial_context",
            "operations_and_digital_footprint", "brand_and_messaging"
        },
        "official company history and ownership context": {
            "identity_and_relationships", "company_and_commercial_context", "additional_material_facts"
        },
        "official brand and product portfolio": {
            "company_and_commercial_context", "operations_and_digital_footprint",
            "audiences_market_and_reputation", "brand_and_messaging", "opportunity_and_fit"
        },
        "official press and current activity": {"activity_and_signals", "additional_material_facts"},
        "official careers and operational signals": {"operations_and_digital_footprint", "activity_and_signals"},
        "official public business roles and leadership": {
            "identity_and_relationships", "public_people_and_contact_paths"
        },
    }
    return mapping[source_class]


def build_real_customer_dossier(
    *,
    plan: dict[str, Any],
    research_bundle: dict[str, Any],
    history: dict[str, Any],
    approved_result: dict[str, Any],
    approval_id: str,
    search_request_id: str,
    history_fingerprint: str,
    research_cutoff: str,
    maximum_evidence_age_days: int,
    version: int,
) -> dict[str, Any]:
    plan = validate_real_source_plan(plan)
    bundle = validate_real_research_bundle(research_bundle, plan)
    validate_history(history)
    expected_history_hash = _canonical_hash(history)
    if history_fingerprint != expected_history_hash:
        raise ValidationError("Real dossier history fingerprint does not match the validated durable history.")
    cutoff = _utc_datetime(research_cutoff, "real dossier research_cutoff")
    if isinstance(maximum_evidence_age_days, bool) or not isinstance(maximum_evidence_age_days, int) or not 1 <= maximum_evidence_age_days <= 3650:
        raise ValidationError("Real dossier maximum evidence age is invalid.")
    candidate_stub, expected_result = build_real_result_projection(
        plan,
        approved_result.get("history_classification"),
    )
    expected_result["selected"] = True
    if approved_result != expected_result:
        raise ValidationError("Real dossier approved result is not the exact selected runtime result.")
    successful = [record for record in bundle["sources"] if record["status"] == "success"]
    product_sources = [
        record for record in successful
        if "product" in record["source_class"] or "brand and product" in record["source_class"]
    ]
    if not product_sources:
        raise ValidationError("Real dossier has no successful official product evidence for the requested opportunity intent.")
    global_identity_id = derive_real_global_identity_id(plan["stable_identity_seed"])
    account_id = derive_real_account_id(plan["canonical_domain"], global_identity_id)
    result_id = derive_real_result_id(plan)
    family_id = f"dossier-real-v2-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:24]}"
    org_node_id = f"organization-real-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:20]}"

    evidence: list[dict[str, Any]] = []
    source_to_evidence: dict[str, str] = {}
    for record in successful:
        evidence_id = f"evidence-real-{hashlib.sha256(record['requested_url'].encode('utf-8')).hexdigest()[:20]}"
        source_to_evidence[record["requested_url"]] = evidence_id
        evidence.append({
            "evidence_id": evidence_id,
            "evidence_kind": "public_source",
            "source_url": record["requested_url"],
            "final_url": record["final_url"],
            "source_class": record["source_class"],
            "source_kind": record["source_kind"],
            "source_date": record["source_date"],
            "observed_at": record["observed_at"],
            "original_source": True,
            "summary": record["summary"],
            "conflict_state": record["conflict_state"],
            "body_sha256": record["body_sha256"],
            "body_byte_length": record["body_byte_length"],
            "extracted_text_sha256": record["extracted_text_sha256"],
            "extracted_text_byte_length": record["extracted_text_byte_length"],
        })
    history_evidence_id = f"evidence-history-{history_fingerprint[:20]}"
    history_raw = canonical_bytes(history)
    evidence.append({
        "evidence_id": history_evidence_id,
        "evidence_kind": "durable_history",
        "source_url": None,
        "final_url": None,
        "source_class": "durable_history_projection",
        "source_kind": "internal_history",
        "source_date": cutoff.date().isoformat(),
        "observed_at": research_cutoff,
        "original_source": True,
        "summary": f"Durable history classifier state: {approved_result['history_classification']['status']}.",
        "conflict_state": "none",
        "body_sha256": history_fingerprint,
        "body_byte_length": len(history_raw),
        "extracted_text_sha256": history_fingerprint,
        "extracted_text_byte_length": len(history_raw),
    })

    entities = [{
        "node_id": org_node_id,
        "node_type": "organization",
        "label": plan["organization_name"],
        "business_unit": plan["business_unit"],
        "attributes": {"canonical_domain": plan["canonical_domain"]},
    }]
    relationships: list[dict[str, Any]] = []
    for related_domain in plan["approved_related_domains"]:
        node_id = f"brand-real-{hashlib.sha256(related_domain.encode('utf-8')).hexdigest()[:20]}"
        entities.append({
            "node_id": node_id,
            "node_type": "brand",
            "label": related_domain,
            "business_unit": plan["business_unit"],
            "attributes": {"canonical_domain": related_domain},
        })
        related_evidence = next(
            (item["evidence_id"] for item in evidence if isinstance(item["source_url"], str) and (urlsplit(item["source_url"]).hostname or "").endswith(related_domain)),
            evidence[0]["evidence_id"],
        )
        relationships.append({
            "edge_id": f"affiliated-real-{hashlib.sha256((org_node_id + related_domain).encode('utf-8')).hexdigest()[:20]}",
            "edge_type": "affiliated_with",
            "from_node_id": node_id,
            "to_node_id": org_node_id,
            "business_unit": plan["business_unit"],
            "claim_refs": [],
            "evidence_refs": [related_evidence],
        })

    category_records: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORIES}
    for record in successful:
        for category in _category_sources(record["source_class"]):
            category_records[category].append(record)
    category_records["governance_and_history"] = []
    category_records["evidence_coverage"] = successful
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        records = category_records[category]
        if category == "governance_and_history":
            claim = {
                "claim_id": f"claim-real-governance-{result_id}",
                "category": category,
                "subject_node_id": org_node_id,
                "typed_value": {"type": "string", "value": approved_result["history_classification"]["status"]},
                "basis": "observed",
                "evidence_refs": [history_evidence_id],
                "confidence_reason": "The durable history-first classifier produced the exact bound state.",
                "uncertainty": "This state is limited to the recorded history fingerprint.",
                "source_date": cutoff.date().isoformat(),
                "observed_at": research_cutoff,
                "freshness_state": "current",
            }
            categories.append({"category": category, "coverage_state": "complete", "claims": [claim]})
            continue
        if category == "evidence_coverage":
            refs = [item["evidence_id"] for item in evidence]
            claim = {
                "claim_id": f"claim-real-coverage-{result_id}",
                "category": category,
                "subject_node_id": org_node_id,
                "typed_value": {
                    "type": "object",
                    "value": {
                        "approved_sources": len(plan["sources"]),
                        "successful_sources": len(successful),
                        "failed_sources": len(plan["sources"]) - len(successful),
                    },
                },
                "basis": "observed",
                "evidence_refs": refs,
                "confidence_reason": "The exact source plan and bounded reader produced these counts.",
                "uncertainty": "Failed or blocked sources remain explicit and were not retried.",
                "source_date": cutoff.date().isoformat(),
                "observed_at": research_cutoff,
                "freshness_state": "current",
            }
            categories.append({"category": category, "coverage_state": "complete", "claims": [claim]})
            continue
        if not records:
            categories.append({
                "category": category,
                "coverage_state": "not_found",
                "gap_explanation": "No successful approved source established this category within the bounded proof; no value was guessed.",
                "claims": [],
            })
            continue
        refs = [source_to_evidence[record["requested_url"]] for record in records]
        summaries = " ".join(
            f"{record['source_class']}: {record['summary']}" for record in records
        )[:1_900].strip()
        basis = "inferred_low_confidence" if category in {"audiences_market_and_reputation", "opportunity_and_fit"} else "observed"
        uncertainty = (
            "Official product context supports potential fit only; no demand, budget, buying intent, or UGC exclusion is inferred."
            if category == "opportunity_and_fit"
            else "The claim is limited to bounded visible text from the exact approved public sources."
        )
        has_stale = any(
            (cutoff.date() - _iso_date(record["source_date"], "category source_date")).days
            > maximum_evidence_age_days
            for record in records
        )
        claim = {
            "claim_id": f"claim-real-{category}-{result_id}",
            "category": category,
            "subject_node_id": org_node_id,
            "typed_value": {"type": "string", "value": summaries},
            "basis": basis,
            "evidence_refs": refs,
            "confidence_reason": "The cited exact official sources were fetched and summarized under the bounded reader contract.",
            "uncertainty": uncertainty,
            "source_date": records[0]["source_date"],
            "observed_at": records[0]["observed_at"],
            "freshness_state": (
                "conflicted"
                if any(record["conflict_state"] == "conflicted" for record in records)
                else "stale" if has_stale else "current"
            ),
        }
        categories.append({"category": category, "coverage_state": "complete", "claims": [claim]})

    dossier = {
        "schema_version": REAL_CONTRACT_VERSION,
        "synthetic": False,
        "dossier_id": family_id,
        "version": version,
        "idempotency_identity": f"phase6-real:{plan['business_unit']}:{result_id}:{version}",
        "search_request_id": search_request_id,
        "business_unit": plan["business_unit"],
        "approved_result": {
            "approval_id": approval_id,
            "result_id": result_id,
            "global_identity_id": global_identity_id,
            "account_id": account_id,
            "canonical_domain": plan["canonical_domain"],
            "source_plan_id": plan["source_plan_id"],
            "source_plan_hash": plan["source_plan_hash"],
        },
        "source_plan_hash": plan["source_plan_hash"],
        "research_cutoff": research_cutoff,
        "maximum_evidence_age_days": maximum_evidence_age_days,
        "history_fingerprint": history_fingerprint,
        "duplicate_history": copy.deepcopy(approved_result["history_classification"]),
        "qualification": {
            "state": "potential_product_creative_fit",
            "basis": "inferred_low_confidence",
            "search_intent_is_demand_evidence": False,
            "demand_evidence_found": False,
            "reason": "Successful official product evidence supports bounded product-photo/video fit review without claiming expressed demand.",
        },
        "research_run": {
            "bundle_version": bundle["bundle_version"],
            "started_at": bundle["started_at"],
            "completed_at": bundle["completed_at"],
        },
        "source_coverage": copy.deepcopy(bundle["sources"]),
        "evidence_inventory": evidence,
        "categories": categories,
        "entities": entities,
        "relationships": relationships,
        "review_state": "pending_research_quality_review",
        "release_state": "not_released",
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
    return validate_real_customer_dossier(
        dossier,
        history=history,
        approved_result=approved_result,
        source_plan=plan,
    )


def _validate_real_typed_value(value: Any, label: str, *, approved_urls: set[str]) -> dict[str, Any]:
    typed = _exact_keys(value, label, {"type", "value"})
    kind = typed["type"]
    item = typed["value"]
    if kind == "string":
        _text(item, f"{label} string", maximum=2_000)
    elif kind == "number":
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValidationError(f"{label} number must be finite.")
    elif kind == "boolean":
        if not isinstance(item, bool):
            raise ValidationError(f"{label} boolean must be boolean.")
    elif kind == "string_list":
        _string_list(item, f"{label} string_list", allow_empty=False)
    elif kind == "date":
        _iso_date(item, f"{label} date")
    elif kind == "url":
        _safe_https_url(item, f"{label} URL")
        if item not in approved_urls:
            raise ValidationError(f"{label} URL is not in the exact approved source plan.")
    elif kind == "object":
        if not isinstance(item, dict) or len(item) > 120:
            raise ValidationError(f"{label} object is invalid.")
        _check_document(item, f"{label} object", max_array_items=100, max_document_bytes=20_000)
        _check_real_private_material(item, f"{label} object")
    else:
        raise ValidationError(f"{label} type is unsupported.")
    return typed


def _authority(value: Any, label: str) -> dict[str, Any]:
    expected = {
        "local_data_handoff_only": True,
        "generation": False,
        "contact": False,
        "outreach": False,
        "external_write": False,
        "agent_invocation": False,
        "deployment": False,
    }
    authority = _exact_keys(value, label, set(expected))
    if authority != expected:
        raise ValidationError(f"{label} must deny every downstream action authority.")
    return authority


def validate_real_customer_dossier(
    value: Any,
    *,
    history: Any,
    approved_result: Any,
    source_plan: Any,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "synthetic",
        "dossier_id",
        "version",
        "idempotency_identity",
        "search_request_id",
        "business_unit",
        "approved_result",
        "source_plan_hash",
        "research_cutoff",
        "maximum_evidence_age_days",
        "history_fingerprint",
        "duplicate_history",
        "qualification",
        "research_run",
        "source_coverage",
        "evidence_inventory",
        "categories",
        "entities",
        "relationships",
        "review_state",
        "release_state",
        "authority",
    }
    dossier = _exact_keys(value, "real customer dossier", required)
    _check_document(
        dossier,
        "real customer dossier",
        max_array_items=PACKAGE_MAX_ARRAY_ITEMS,
        max_document_bytes=PACKAGE_MAX_DOCUMENT_BYTES,
    )
    _check_real_private_material(dossier, "real customer dossier")
    plan = validate_real_source_plan(source_plan)
    if dossier["schema_version"] != REAL_CONTRACT_VERSION or dossier["synthetic"] is not False:
        raise ValidationError("Real customer dossier must be schema version 2 and explicitly non-synthetic.")
    _identifier(dossier["dossier_id"], "real dossier_id")
    _identifier(dossier["idempotency_identity"], "real dossier idempotency_identity")
    _identifier(dossier["search_request_id"], "real dossier search_request_id")
    if isinstance(dossier["version"], bool) or not isinstance(dossier["version"], int) or dossier["version"] < 1:
        raise ValidationError("Real customer dossier version must be a positive integer.")
    if dossier["business_unit"] != plan["business_unit"] or dossier["source_plan_hash"] != plan["source_plan_hash"]:
        raise ValidationError("Real customer dossier does not match its exact business unit and source plan.")
    history = validate_history(history)
    history_hash = _canonical_hash(history)
    if dossier["history_fingerprint"] != history_hash:
        raise ValidationError("Real customer dossier history fingerprint does not match validated history.")
    expected_candidate, expected_result = build_real_result_projection(
        plan,
        approved_result.get("history_classification") if isinstance(approved_result, dict) else None,
    )
    expected_result["selected"] = True
    if approved_result != expected_result:
        raise ValidationError("Real customer dossier requires the exact selected approved result projection.")
    binding = _exact_keys(
        dossier["approved_result"],
        "real dossier approved_result",
        {
            "approval_id",
            "result_id",
            "global_identity_id",
            "account_id",
            "canonical_domain",
            "source_plan_id",
            "source_plan_hash",
        },
    )
    _identifier(binding["approval_id"], "real approval_id")
    expected_binding = {
        "result_id": expected_candidate["result_id"],
        "global_identity_id": expected_candidate["global_identity_id"],
        "account_id": expected_candidate["account_id"],
        "canonical_domain": plan["canonical_domain"],
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
    }
    if any(binding[key] != expected for key, expected in expected_binding.items()):
        raise ValidationError("Real dossier approved-result binding is inconsistent.")
    duplicate = _validate_duplicate_history(dossier["duplicate_history"], "real dossier duplicate_history")
    if canonical_bytes(duplicate) != canonical_bytes(approved_result["history_classification"]):
        raise ValidationError("Real dossier duplicate/history state differs from the approved result.")
    cutoff = _utc_datetime(dossier["research_cutoff"], "real dossier research_cutoff")
    maximum_age = dossier["maximum_evidence_age_days"]
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int) or not 1 <= maximum_age <= 3650:
        raise ValidationError("Real dossier maximum evidence age is invalid.")
    qualification = _exact_keys(
        dossier["qualification"],
        "real dossier qualification",
        {"state", "basis", "search_intent_is_demand_evidence", "demand_evidence_found", "reason"},
    )
    if (
        qualification["state"] != "potential_product_creative_fit"
        or qualification["basis"] != "inferred_low_confidence"
        or qualification["search_intent_is_demand_evidence"] is not False
        or qualification["demand_evidence_found"] is not False
    ):
        raise ValidationError("Real dossier qualification must preserve the bounded no-demand-evidence interpretation.")
    _text(qualification["reason"], "real dossier qualification reason", maximum=500)
    research_run = _exact_keys(
        dossier["research_run"],
        "real dossier research_run",
        {"bundle_version", "started_at", "completed_at"},
    )
    reconstructed_bundle = {
        "bundle_version": research_run["bundle_version"],
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
        "started_at": research_run["started_at"],
        "completed_at": research_run["completed_at"],
        "sources": dossier["source_coverage"],
    }
    validate_real_research_bundle(reconstructed_bundle, plan)
    if cutoff != _utc_datetime(research_run["completed_at"], "real dossier completed_at"):
        raise ValidationError("Real dossier research cutoff must equal the completed bounded-read time.")

    evidence_items = dossier["evidence_inventory"]
    if not isinstance(evidence_items, list) or not 2 <= len(evidence_items) <= PACKAGE_MAX_EVIDENCE:
        raise ValidationError("Real dossier evidence inventory must contain public evidence and durable history.")
    approved_urls = {source["url"] for source in plan["sources"]}
    successful_records = {
        record["requested_url"]: record
        for record in dossier["source_coverage"]
        if record["status"] == "success"
    }
    evidence_by_id: dict[str, dict[str, Any]] = {}
    public_evidence_urls: set[str] = set()
    history_evidence_count = 0
    for index, evidence_value in enumerate(evidence_items):
        evidence = _exact_keys(
            evidence_value,
            f"real dossier evidence {index}",
            {
                "evidence_id",
                "evidence_kind",
                "source_url",
                "final_url",
                "source_class",
                "source_kind",
                "source_date",
                "observed_at",
                "original_source",
                "summary",
                "conflict_state",
                "body_sha256",
                "body_byte_length",
                "extracted_text_sha256",
                "extracted_text_byte_length",
            },
        )
        evidence_id = _identifier(evidence["evidence_id"], f"real dossier evidence {index} evidence_id")
        if evidence_id in evidence_by_id:
            raise ValidationError("Real dossier evidence IDs must be unique.")
        evidence_by_id[evidence_id] = evidence
        if evidence["original_source"] is not True or evidence["conflict_state"] not in {"none", "conflicted"}:
            raise ValidationError("Real dossier evidence truth metadata is invalid.")
        source_date = _iso_date(evidence["source_date"], f"real dossier evidence {index} source_date")
        observed_at = _utc_datetime(evidence["observed_at"], f"real dossier evidence {index} observed_at")
        if source_date > cutoff.date() or observed_at > cutoff:
            raise ValidationError("Real dossier evidence cannot be future-dated.")
        _text(evidence["summary"], f"real dossier evidence {index} summary", maximum=1_000)
        for hash_key in ("body_sha256", "extracted_text_sha256"):
            if not isinstance(evidence[hash_key], str) or not _HEX64.fullmatch(evidence[hash_key]):
                raise ValidationError("Real dossier evidence hashes are invalid.")
        for length_key in ("body_byte_length", "extracted_text_byte_length"):
            if isinstance(evidence[length_key], bool) or not isinstance(evidence[length_key], int) or evidence[length_key] <= 0:
                raise ValidationError("Real dossier evidence lengths must be positive integers.")
        if evidence["evidence_kind"] == "public_source":
            source_url = evidence["source_url"]
            if source_url not in approved_urls or source_url not in successful_records:
                raise ValidationError("Real dossier public evidence is not a successful exact source.")
            record = successful_records[source_url]
            expected = {
                "final_url": record["final_url"],
                "source_class": record["source_class"],
                "source_kind": record["source_kind"],
                "source_date": record["source_date"],
                "observed_at": record["observed_at"],
                "summary": record["summary"],
                "conflict_state": record["conflict_state"],
                "body_sha256": record["body_sha256"],
                "body_byte_length": record["body_byte_length"],
                "extracted_text_sha256": record["extracted_text_sha256"],
                "extracted_text_byte_length": record["extracted_text_byte_length"],
            }
            if any(evidence[key] != expected_value for key, expected_value in expected.items()):
                raise ValidationError("Real dossier public evidence differs from its bounded source record.")
            public_evidence_urls.add(source_url)
        elif evidence["evidence_kind"] == "durable_history":
            history_evidence_count += 1
            if (
                evidence["source_url"] is not None
                or evidence["final_url"] is not None
                or evidence["source_class"] != "durable_history_projection"
                or evidence["source_kind"] != "internal_history"
                or evidence["body_sha256"] != history_hash
                or evidence["extracted_text_sha256"] != history_hash
            ):
                raise ValidationError("Real dossier durable-history evidence is inconsistent.")
        else:
            raise ValidationError("Real dossier evidence kind is unsupported.")
    if public_evidence_urls != set(successful_records) or history_evidence_count != 1:
        raise ValidationError("Real dossier evidence inventory must exactly cover successful sources and one history projection.")

    entities = dossier["entities"]
    if not isinstance(entities, list) or not 1 <= len(entities) <= PACKAGE_MAX_NODES:
        raise ValidationError("Real dossier entities are outside their bounded limit.")
    node_ids: set[str] = set()
    for index, entity_value in enumerate(entities):
        entity = _exact_keys(
            entity_value,
            f"real dossier entity {index}",
            {"node_id", "node_type", "label", "business_unit", "attributes"},
        )
        node_id = _identifier(entity["node_id"], f"real dossier entity {index} node_id")
        if node_id in node_ids:
            raise ValidationError("Real dossier entity IDs must be unique.")
        node_ids.add(node_id)
        if entity["node_type"] not in NODE_TYPES - {"evidence"} or entity["business_unit"] != dossier["business_unit"]:
            raise ValidationError("Real dossier entity type or business unit is invalid.")
        _text(entity["label"], f"real dossier entity {index} label", maximum=200)
        if not isinstance(entity["attributes"], dict) or len(entity["attributes"]) > 120:
            raise ValidationError("Real dossier entity attributes are invalid.")

    categories = dossier["categories"]
    if not isinstance(categories, list) or [item.get("category") for item in categories if isinstance(item, dict)] != list(CATEGORIES):
        raise ValidationError("Real dossier must contain the exact eleven categories in canonical order.")
    claim_ids: set[str] = set()
    claims: list[dict[str, Any]] = []
    for category_index, category_value in enumerate(categories):
        category = _exact_keys(
            category_value,
            f"real dossier category {category_index}",
            {"category", "coverage_state", "claims"},
            {"gap_explanation"},
        )
        category_claims = category["claims"]
        if not isinstance(category_claims, list):
            raise ValidationError("Real dossier category claims must be an array.")
        if category["coverage_state"] == "complete":
            if not category_claims or category.get("gap_explanation") not in {None, ""}:
                raise ValidationError("Complete real dossier categories require claims and no gap explanation.")
        elif category["coverage_state"] in GAP_STATES:
            if category_claims or not isinstance(category.get("gap_explanation"), str) or not category["gap_explanation"].strip():
                raise ValidationError("Real dossier gap categories require one explanation and no claims.")
            _text(category["gap_explanation"], "real dossier gap explanation", maximum=500)
        else:
            raise ValidationError("Real dossier category coverage state is invalid.")
        for claim_index, claim_value in enumerate(category_claims):
            claim = _exact_keys(
                claim_value,
                f"real dossier claim {claim_index}",
                {
                    "claim_id", "category", "subject_node_id", "typed_value", "basis",
                    "evidence_refs", "confidence_reason", "uncertainty", "source_date",
                    "observed_at", "freshness_state",
                },
            )
            claim_id = _identifier(claim["claim_id"], "real dossier claim_id")
            if claim_id in claim_ids:
                raise ValidationError("Real dossier claim IDs must be unique.")
            claim_ids.add(claim_id)
            claims.append(claim)
            if claim["category"] != category["category"] or claim["subject_node_id"] not in node_ids:
                raise ValidationError("Real dossier claim category or subject is unresolved.")
            _validate_real_typed_value(claim["typed_value"], f"real dossier claim {claim_id} typed_value", approved_urls=approved_urls)
            if claim["basis"] not in BASIS_STATES:
                raise ValidationError("Real dossier claim basis is invalid.")
            refs = _string_list(claim["evidence_refs"], f"real dossier claim {claim_id} evidence_refs", allow_empty=False)
            if not set(refs) <= set(evidence_by_id):
                raise ValidationError("Real dossier claim evidence reference is unresolved.")
            _text(claim["confidence_reason"], f"real dossier claim {claim_id} confidence_reason", maximum=500)
            _text(claim["uncertainty"], f"real dossier claim {claim_id} uncertainty", maximum=500)
            claim_date = _iso_date(claim["source_date"], f"real dossier claim {claim_id} source_date")
            claim_observed = _utc_datetime(claim["observed_at"], f"real dossier claim {claim_id} observed_at")
            referenced = [evidence_by_id[ref] for ref in refs]
            if claim_date > cutoff.date() or claim_observed > cutoff or claim["source_date"] not in {item["source_date"] for item in referenced}:
                raise ValidationError("Real dossier claim date does not match its evidence and cutoff.")
            conflicted = any(item["conflict_state"] == "conflicted" for item in referenced)
            stale = any(
                (cutoff.date() - _iso_date(item["source_date"], "real evidence source_date")).days > maximum_age
                for item in referenced
            )
            expected_freshness = "conflicted" if conflicted else "stale" if stale else "current"
            if claim["freshness_state"] != expected_freshness:
                raise ValidationError(f"Real dossier claim freshness must be {expected_freshness}.")

    relationships = dossier["relationships"]
    if not isinstance(relationships, list) or len(relationships) > PACKAGE_MAX_EDGES:
        raise ValidationError("Real dossier relationships are outside their bounded limit.")
    edge_ids: set[str] = set()
    for index, edge_value in enumerate(relationships):
        edge = _exact_keys(
            edge_value,
            f"real dossier relationship {index}",
            {"edge_id", "edge_type", "from_node_id", "to_node_id", "business_unit", "claim_refs", "evidence_refs"},
        )
        edge_id = _identifier(edge["edge_id"], "real dossier edge_id")
        if edge_id in edge_ids:
            raise ValidationError("Real dossier relationship IDs must be unique.")
        edge_ids.add(edge_id)
        if edge["edge_type"] not in EDGE_TYPES - {"evidence_supports"}:
            raise ValidationError("Real dossier relationship type is unsupported.")
        if edge["from_node_id"] not in node_ids or edge["to_node_id"] not in node_ids or edge["business_unit"] != dossier["business_unit"]:
            raise ValidationError("Real dossier relationship reference or business unit is invalid.")
        claim_refs = _string_list(edge["claim_refs"], "real dossier relationship claim_refs")
        evidence_refs = _string_list(edge["evidence_refs"], "real dossier relationship evidence_refs")
        if not claim_refs and not evidence_refs:
            raise ValidationError("Real dossier relationships must be provenance-linked.")
        if not set(claim_refs) <= claim_ids or not set(evidence_refs) <= set(evidence_by_id):
            raise ValidationError("Real dossier relationship provenance is unresolved.")

    if dossier["review_state"] not in {"pending_research_quality_review", "research_quality_accepted", "changes_requested", "rejected"}:
        raise ValidationError("Real dossier review state is invalid.")
    if dossier["release_state"] not in {"not_released", "released_local_data_only"}:
        raise ValidationError("Real dossier release state is invalid.")
    if dossier["release_state"] == "released_local_data_only" and dossier["review_state"] != "research_quality_accepted":
        raise ValidationError("Only accepted real dossiers may be released locally.")
    _authority(dossier["authority"], "real dossier authority")
    projected_nodes = len(entities) + len(evidence_items)
    projected_edges = len(relationships) + sum(len(claim["evidence_refs"]) for claim in claims)
    if len(claims) > PACKAGE_MAX_CLAIMS or projected_nodes > PACKAGE_MAX_NODES or projected_edges > PACKAGE_MAX_EDGES:
        raise ValidationError("Real dossier exceeds graph-package projection limits.")
    return dossier


def _real_package_payload(dossier: dict[str, Any]) -> dict[str, Any]:
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
            "label": f"Evidence: {item['evidence_kind']}",
            "business_unit": dossier["business_unit"],
            "attributes": {
                "evidence_kind": item["evidence_kind"],
                "source_url": item["source_url"],
                "source_date": item["source_date"],
                "body_sha256": item["body_sha256"],
                "extracted_text_sha256": item["extracted_text_sha256"],
            },
        })
    nodes.sort(key=lambda item: item["node_id"])
    edges = sorted(copy.deepcopy(dossier["relationships"]), key=lambda item: item["edge_id"])
    for claim in claims:
        for evidence_id in claim["evidence_refs"]:
            edge_identity = hashlib.sha256(
                f"{evidence_id}|{claim['claim_id']}".encode("utf-8")
            ).hexdigest()[:24]
            edges.append({
                "edge_id": f"evidence-supports-real-{edge_identity}",
                "edge_type": "evidence_supports",
                "from_node_id": f"evidence:{evidence_id}",
                "to_node_id": claim["subject_node_id"],
                "business_unit": dossier["business_unit"],
                "claim_refs": [claim["claim_id"]],
                "evidence_refs": [evidence_id],
            })
    edges.sort(key=lambda item: item["edge_id"])
    return {
        "schema_version": REAL_CONTRACT_VERSION,
        "synthetic": False,
        "package_id": (
            f"{REAL_PACKAGE_ID_VERSION}-"
            f"{hashlib.sha256((dossier['business_unit'] + '|' + dossier['idempotency_identity']).encode('utf-8')).hexdigest()[:24]}"
        ),
        "idempotency_identity": dossier["idempotency_identity"],
        "business_unit": dossier["business_unit"],
        "approved_result": copy.deepcopy(dossier["approved_result"]),
        "source_plan_hash": dossier["source_plan_hash"],
        "dossier_id": dossier["dossier_id"],
        "dossier_version": dossier["version"],
        "research_cutoff": dossier["research_cutoff"],
        "maximum_evidence_age_days": dossier["maximum_evidence_age_days"],
        "history_fingerprint": dossier["history_fingerprint"],
        "qualification": copy.deepcopy(dossier["qualification"]),
        "research_run": copy.deepcopy(dossier["research_run"]),
        "source_coverage": copy.deepcopy(dossier["source_coverage"]),
        "coverage_states": [
            {
                "category": item["category"],
                "coverage_state": item["coverage_state"],
                "gap_explanation": item.get("gap_explanation"),
            }
            for item in dossier["categories"]
        ],
        "claims": claims,
        "evidence_inventory": evidence,
        "nodes": nodes,
        "edges": edges,
        "duplicate_history": copy.deepcopy(dossier["duplicate_history"]),
        "review_release": {
            "review_state": dossier["review_state"],
            "release_state": dossier["release_state"],
        },
        "authority": copy.deepcopy(dossier["authority"]),
    }


def build_real_lead_intelligence_package(
    dossier: dict[str, Any],
    *,
    history: Any,
    approved_result: Any,
    source_plan: Any,
    prior_packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dossier = validate_real_customer_dossier(
        dossier,
        history=history,
        approved_result=approved_result,
        source_plan=source_plan,
    )
    if dossier["review_state"] != "research_quality_accepted" or dossier["release_state"] != "released_local_data_only":
        raise ValidationError("Real package release requires an accepted exact dossier version.")
    payload = _real_package_payload(dossier)
    package = copy.deepcopy(payload)
    package["canonical_hash"] = _canonical_hash(payload)
    package = validate_real_lead_intelligence_package(package, source_plan=source_plan)
    matches = [
        existing for existing in prior_packages or []
        if existing.get("business_unit") == package["business_unit"]
        and existing.get("idempotency_identity") == package["idempotency_identity"]
    ]
    for existing in matches:
        validate_real_lead_intelligence_package(existing, source_plan=source_plan)
        if canonical_bytes(existing) != canonical_bytes(package):
            raise ValidationError("Real package idempotency identity conflicts with changed content.")
    return copy.deepcopy(matches[0]) if matches else package


def validate_real_lead_intelligence_package(value: Any, *, source_plan: Any) -> dict[str, Any]:
    package = _exact_keys(
        value,
        "real lead-intelligence package",
        {
            "schema_version", "synthetic", "package_id", "idempotency_identity", "business_unit",
            "approved_result", "source_plan_hash", "dossier_id", "dossier_version",
            "research_cutoff", "maximum_evidence_age_days", "canonical_hash", "history_fingerprint",
            "qualification", "research_run", "source_coverage", "coverage_states", "claims",
            "evidence_inventory", "nodes", "edges", "duplicate_history", "review_release", "authority",
        },
    )
    _check_document(
        package,
        "real lead-intelligence package",
        max_array_items=PACKAGE_MAX_ARRAY_ITEMS,
        max_document_bytes=PACKAGE_MAX_DOCUMENT_BYTES,
    )
    _check_real_private_material(package, "real lead-intelligence package")
    plan = validate_real_source_plan(source_plan)
    if (
        package["schema_version"] != REAL_CONTRACT_VERSION
        or package["synthetic"] is not False
        or package["business_unit"] != plan["business_unit"]
        or package["source_plan_hash"] != plan["source_plan_hash"]
    ):
        raise ValidationError("Real lead package version, type, business unit, or source plan is invalid.")
    for key in ("package_id", "idempotency_identity", "dossier_id"):
        _identifier(package[key], f"real package {key}")
    expected_package_id = (
        f"{REAL_PACKAGE_ID_VERSION}-"
        f"{hashlib.sha256((package['business_unit'] + '|' + package['idempotency_identity']).encode('utf-8')).hexdigest()[:24]}"
    )
    if package["package_id"] != expected_package_id:
        raise ValidationError("Real lead package ID does not match its derived identity.")
    if isinstance(package["dossier_version"], bool) or not isinstance(package["dossier_version"], int) or package["dossier_version"] < 1:
        raise ValidationError("Real lead package dossier version is invalid.")
    if not isinstance(package["canonical_hash"], str) or not _HEX64.fullmatch(package["canonical_hash"]):
        raise ValidationError("Real lead package canonical hash is invalid.")
    hash_payload = copy.deepcopy(package)
    recorded_hash = hash_payload.pop("canonical_hash")
    if _canonical_hash(hash_payload) != recorded_hash:
        raise ValidationError("Real lead package canonical hash does not match its content.")
    binding = _exact_keys(
        package["approved_result"],
        "real package approved_result",
        {
            "approval_id", "result_id", "global_identity_id", "account_id", "canonical_domain",
            "source_plan_id", "source_plan_hash",
        },
    )
    expected_global = derive_real_global_identity_id(plan["stable_identity_seed"])
    expected_account = derive_real_account_id(plan["canonical_domain"], expected_global)
    if not all((
        binding["result_id"] == derive_real_result_id(plan),
        binding["global_identity_id"] == expected_global,
        binding["account_id"] == expected_account,
        binding["canonical_domain"] == plan["canonical_domain"],
        binding["source_plan_id"] == plan["source_plan_id"],
        binding["source_plan_hash"] == plan["source_plan_hash"],
    )):
        raise ValidationError("Real lead package approved-result binding is invalid.")
    _identifier(binding["approval_id"], "real package approval_id")
    cutoff = _utc_datetime(package["research_cutoff"], "real package research_cutoff")
    maximum_age = package["maximum_evidence_age_days"]
    if isinstance(maximum_age, bool) or not isinstance(maximum_age, int) or not 1 <= maximum_age <= 3650:
        raise ValidationError("Real lead package maximum evidence age is invalid.")
    if not isinstance(package["history_fingerprint"], str) or not _HEX64.fullmatch(package["history_fingerprint"]):
        raise ValidationError("Real lead package history fingerprint is invalid.")
    _validate_duplicate_history(package["duplicate_history"], "real package duplicate_history")
    qualification = _exact_keys(
        package["qualification"],
        "real package qualification",
        {"state", "basis", "search_intent_is_demand_evidence", "demand_evidence_found", "reason"},
    )
    if (
        qualification["state"] != "potential_product_creative_fit"
        or qualification["basis"] != "inferred_low_confidence"
        or qualification["search_intent_is_demand_evidence"] is not False
        or qualification["demand_evidence_found"] is not False
    ):
        raise ValidationError("Real package qualification overstates the bounded evidence.")
    _text(qualification["reason"], "real package qualification reason", maximum=500)
    research_run = _exact_keys(
        package["research_run"],
        "real package research_run",
        {"bundle_version", "started_at", "completed_at"},
    )
    source_coverage = package["source_coverage"]
    validate_real_research_bundle({
        "bundle_version": research_run["bundle_version"],
        "source_plan_id": plan["source_plan_id"],
        "source_plan_hash": plan["source_plan_hash"],
        "started_at": research_run["started_at"],
        "completed_at": research_run["completed_at"],
        "sources": source_coverage,
    }, plan)
    if cutoff != _utc_datetime(research_run["completed_at"], "real package completed_at"):
        raise ValidationError("Real package cutoff must equal its bounded read completion.")
    if not isinstance(package["coverage_states"], list) or [item.get("category") for item in package["coverage_states"] if isinstance(item, dict)] != list(CATEGORIES):
        raise ValidationError("Real lead package coverage is incomplete or unordered.")
    for index, coverage_value in enumerate(package["coverage_states"]):
        coverage = _exact_keys(
            coverage_value,
            f"real package coverage {index}",
            {"category", "coverage_state", "gap_explanation"},
        )
        if coverage["coverage_state"] == "complete":
            if coverage["gap_explanation"] is not None:
                raise ValidationError("Complete real package coverage cannot have a gap explanation.")
        elif coverage["coverage_state"] in GAP_STATES:
            _text(coverage["gap_explanation"], "real package gap explanation", maximum=500)
        else:
            raise ValidationError("Real package coverage state is invalid.")
    if not isinstance(package["evidence_inventory"], list) or not 2 <= len(package["evidence_inventory"]) <= PACKAGE_MAX_EVIDENCE:
        raise ValidationError("Real package evidence inventory is outside its limit.")
    successful_records = {
        record["requested_url"]: record
        for record in source_coverage
        if record["status"] == "success"
    }
    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, dict[str, Any]] = {}
    public_evidence_urls: set[str] = set()
    history_evidence_count = 0
    for index, evidence_value in enumerate(package["evidence_inventory"]):
        evidence = _exact_keys(
            evidence_value,
            f"real package evidence {index}",
            {
                "evidence_id", "evidence_kind", "source_url", "final_url", "source_class",
                "source_kind", "source_date", "observed_at", "original_source", "summary",
                "conflict_state", "body_sha256", "body_byte_length", "extracted_text_sha256",
                "extracted_text_byte_length",
            },
        )
        evidence_id = _identifier(evidence["evidence_id"], "real package evidence_id")
        if evidence_id in evidence_ids:
            raise ValidationError("Real package evidence IDs must be unique.")
        evidence_ids.add(evidence_id)
        evidence_by_id[evidence_id] = evidence
        if evidence["original_source"] is not True or evidence["conflict_state"] not in {"none", "conflicted"}:
            raise ValidationError("Real package evidence truth metadata is invalid.")
        source_date = _iso_date(evidence["source_date"], "real package evidence source_date")
        observed = _utc_datetime(evidence["observed_at"], "real package evidence observed_at")
        if source_date > cutoff.date() or observed > cutoff:
            raise ValidationError("Real package evidence cannot be future-dated.")
        _text(evidence["summary"], "real package evidence summary", maximum=1_000)
        if not isinstance(evidence["body_sha256"], str) or not _HEX64.fullmatch(evidence["body_sha256"]):
            raise ValidationError("Real package evidence hash is invalid.")
        if not isinstance(evidence["extracted_text_sha256"], str) or not _HEX64.fullmatch(evidence["extracted_text_sha256"]):
            raise ValidationError("Real package extracted-text hash is invalid.")
        if any(
            isinstance(evidence[key], bool) or not isinstance(evidence[key], int) or evidence[key] <= 0
            for key in ("body_byte_length", "extracted_text_byte_length")
        ):
            raise ValidationError("Real package evidence lengths are invalid.")
        if evidence["evidence_kind"] == "public_source":
            record = successful_records.get(evidence["source_url"])
            if record is None:
                raise ValidationError("Real package public evidence is not an exact successful source.")
            expected = {
                "final_url": record["final_url"],
                "source_class": record["source_class"],
                "source_kind": record["source_kind"],
                "source_date": record["source_date"],
                "observed_at": record["observed_at"],
                "summary": record["summary"],
                "conflict_state": record["conflict_state"],
                "body_sha256": record["body_sha256"],
                "body_byte_length": record["body_byte_length"],
                "extracted_text_sha256": record["extracted_text_sha256"],
                "extracted_text_byte_length": record["extracted_text_byte_length"],
            }
            if any(evidence[key] != value for key, value in expected.items()):
                raise ValidationError("Real package public evidence differs from source coverage.")
            public_evidence_urls.add(evidence["source_url"])
        elif evidence["evidence_kind"] == "durable_history":
            history_evidence_count += 1
            if (
                evidence["source_url"] is not None
                or evidence["final_url"] is not None
                or evidence["source_class"] != "durable_history_projection"
                or evidence["source_kind"] != "internal_history"
                or evidence["body_sha256"] != package["history_fingerprint"]
                or evidence["extracted_text_sha256"] != package["history_fingerprint"]
            ):
                raise ValidationError("Real package durable-history evidence is inconsistent.")
        else:
            raise ValidationError("Real package evidence kind is invalid.")
    if public_evidence_urls != set(successful_records) or history_evidence_count != 1:
        raise ValidationError("Real package evidence must exactly cover successful sources and history.")
    if not isinstance(package["nodes"], list) or not 1 <= len(package["nodes"]) <= PACKAGE_MAX_NODES:
        raise ValidationError("Real package nodes are outside their limit.")
    node_ids: set[str] = set()
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for index, node_value in enumerate(package["nodes"]):
        node = _exact_keys(
            node_value,
            f"real package node {index}",
            {"node_id", "node_type", "label", "business_unit", "attributes"},
        )
        node_id = _identifier(node["node_id"], "real package node_id")
        if node_id in node_ids:
            raise ValidationError("Real package node IDs must be unique.")
        node_ids.add(node_id)
        nodes_by_id[node_id] = node
        if node["node_type"] not in NODE_TYPES or node["business_unit"] != package["business_unit"]:
            raise ValidationError("Real package node type or business unit is invalid.")
        _text(node["label"], "real package node label", maximum=200)
        if not isinstance(node["attributes"], dict) or len(node["attributes"]) > 120:
            raise ValidationError("Real package node attributes are invalid.")
        _check_document(node["attributes"], "real package node attributes", max_array_items=100, max_document_bytes=20_000)
    for evidence_id, evidence in evidence_by_id.items():
        expected_node = {
            "node_id": f"evidence:{evidence_id}",
            "node_type": "evidence",
            "label": f"Evidence: {evidence['evidence_kind']}",
            "business_unit": package["business_unit"],
            "attributes": {
                "evidence_kind": evidence["evidence_kind"],
                "source_url": evidence["source_url"],
                "source_date": evidence["source_date"],
                "body_sha256": evidence["body_sha256"],
                "extracted_text_sha256": evidence["extracted_text_sha256"],
            },
        }
        if nodes_by_id.get(expected_node["node_id"]) != expected_node:
            raise ValidationError("Real package evidence-node projection is inconsistent.")
    if not isinstance(package["claims"], list) or len(package["claims"]) > PACKAGE_MAX_CLAIMS:
        raise ValidationError("Real package claims are outside their limit.")
    claim_ids: set[str] = set()
    claims_by_category: dict[str, int] = {category: 0 for category in CATEGORIES}
    approved_urls = {source["url"] for source in plan["sources"]}
    for index, claim_value in enumerate(package["claims"]):
        claim = _exact_keys(
            claim_value,
            f"real package claim {index}",
            {
                "claim_id", "category", "subject_node_id", "typed_value", "basis",
                "evidence_refs", "confidence_reason", "uncertainty", "source_date",
                "observed_at", "freshness_state",
            },
        )
        claim_id = _identifier(claim["claim_id"], "real package claim_id")
        if claim_id in claim_ids:
            raise ValidationError("Real package claim IDs must be unique.")
        claim_ids.add(claim_id)
        if claim["category"] not in CATEGORIES or claim["subject_node_id"] not in node_ids:
            raise ValidationError("Real package claim category or subject is invalid.")
        claims_by_category[claim["category"]] += 1
        _validate_real_typed_value(claim["typed_value"], f"real package claim {claim_id} typed_value", approved_urls=approved_urls)
        if claim["basis"] not in BASIS_STATES or claim["freshness_state"] not in FRESHNESS_STATES:
            raise ValidationError("Real package claim basis or freshness is invalid.")
        refs = _string_list(claim["evidence_refs"], "real package claim evidence_refs", allow_empty=False)
        if not set(refs) <= evidence_ids:
            raise ValidationError("Real package claim evidence is unresolved.")
        _text(claim["confidence_reason"], "real package confidence reason", maximum=500)
        _text(claim["uncertainty"], "real package uncertainty", maximum=500)
        claim_date = _iso_date(claim["source_date"], "real package claim source_date")
        claim_observed = _utc_datetime(claim["observed_at"], "real package claim observed_at")
        referenced = [evidence_by_id[ref] for ref in refs]
        if (
            claim_date > cutoff.date()
            or claim_observed > cutoff
            or claim["source_date"] not in {item["source_date"] for item in referenced}
        ):
            raise ValidationError("Real package claim timing is inconsistent with its evidence.")
        conflicted = any(item["conflict_state"] == "conflicted" for item in referenced)
        stale = any(
            (cutoff.date() - _iso_date(item["source_date"], "real package evidence date")).days
            > maximum_age
            for item in referenced
        )
        expected_freshness = "conflicted" if conflicted else "stale" if stale else "current"
        if claim["freshness_state"] != expected_freshness:
            raise ValidationError(f"Real package claim freshness must be {expected_freshness}.")
    for coverage in package["coverage_states"]:
        count = claims_by_category[coverage["category"]]
        if (coverage["coverage_state"] == "complete") != (count > 0):
            raise ValidationError("Real package coverage state does not match its claim projection.")
    if not isinstance(package["edges"], list) or len(package["edges"]) > PACKAGE_MAX_EDGES:
        raise ValidationError("Real package edges are outside their limit.")
    edge_ids: set[str] = set()
    edges_by_id: dict[str, dict[str, Any]] = {}
    for index, edge_value in enumerate(package["edges"]):
        edge = _exact_keys(
            edge_value,
            f"real package edge {index}",
            {
                "edge_id", "edge_type", "from_node_id", "to_node_id", "business_unit",
                "claim_refs", "evidence_refs",
            },
        )
        edge_id = _identifier(edge["edge_id"], "real package edge_id")
        if edge_id in edge_ids:
            raise ValidationError("Real package edge IDs must be unique.")
        edge_ids.add(edge_id)
        edges_by_id[edge_id] = edge
        if edge["edge_type"] not in EDGE_TYPES or edge["from_node_id"] not in node_ids or edge["to_node_id"] not in node_ids:
            raise ValidationError("Real package edge type or node reference is invalid.")
        if edge["business_unit"] != package["business_unit"]:
            raise ValidationError("Real package edge business unit is invalid.")
        claim_refs = _string_list(edge["claim_refs"], "real package edge claim_refs")
        evidence_refs = _string_list(edge["evidence_refs"], "real package edge evidence_refs")
        if not claim_refs and not evidence_refs:
            raise ValidationError("Real package edges must be provenance-linked.")
        if not set(claim_refs) <= claim_ids or not set(evidence_refs) <= evidence_ids:
            raise ValidationError("Real package edge provenance is unresolved.")
    for claim in package["claims"]:
        for evidence_id in claim["evidence_refs"]:
            edge_identity = hashlib.sha256(
                f"{evidence_id}|{claim['claim_id']}".encode("utf-8")
            ).hexdigest()[:24]
            expected_edge = {
                "edge_id": f"evidence-supports-real-{edge_identity}",
                "edge_type": "evidence_supports",
                "from_node_id": f"evidence:{evidence_id}",
                "to_node_id": claim["subject_node_id"],
                "business_unit": package["business_unit"],
                "claim_refs": [claim["claim_id"]],
                "evidence_refs": [evidence_id],
            }
            if edges_by_id.get(expected_edge["edge_id"]) != expected_edge:
                raise ValidationError("Real package evidence-support projection is inconsistent.")
    if sum(edge["edge_type"] == "evidence_supports" for edge in package["edges"]) != sum(
        len(claim["evidence_refs"]) for claim in package["claims"]
    ):
        raise ValidationError("Real package contains an unexpected evidence-support edge.")
    review_release = _exact_keys(
        package["review_release"],
        "real package review_release",
        {"review_state", "release_state"},
    )
    if review_release != {
        "review_state": "research_quality_accepted",
        "release_state": "released_local_data_only",
    }:
        raise ValidationError("Real lead package requires exact accepted local-only release state.")
    _authority(package["authority"], "real package authority")
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    manifest = load_real_source_manifest(args.manifest)
    print(json.dumps({
        "plans": [
            {
                "source_plan_id": plan["source_plan_id"],
                "source_plan_hash": plan["source_plan_hash"],
                "result_id": derive_real_result_id(plan),
                "global_identity_id": derive_real_global_identity_id(plan["stable_identity_seed"]),
                "account_id": derive_real_account_id(
                    plan["canonical_domain"],
                    derive_real_global_identity_id(plan["stable_identity_seed"]),
                ),
            }
            for plan in manifest["plans"]
        ]
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
