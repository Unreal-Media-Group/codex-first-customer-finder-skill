"""Durable, local Phase 6A fixture enrichment and brief review service."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .agent import RegisteredAgentService
from .application import ACTOR_SCOPE, MissionControlError
from .store import MAX_SNAPSHOT_BYTES, SqliteStore, canonical_json

APPROVAL_SCOPE = "phase6a_fixture_enrichment"
APPROVAL_TTL = timedelta(days=7)
BRIEF_SCHEMA = "phase6a-synthetic-campaign-brief@1"
REQUIRED_BRIEF_FIELDS = (
    "product_extraction",
    "current_campaigns",
    "brand_voice",
    "visual_direction",
    "existing_ad_angles",
    "claims_and_restrictions",
    "target_audience",
    "hero_product",
    "campaign_objective",
    "creative_opportunity",
)
_APPROVAL_DECISIONS = {"approved", "rejected", "revoked", "invalidated"}
_REVIEW_DECISIONS = {"accepted", "rejected", "changes_requested"}
_FIELD_BASES = {"observed", "inferred_high_confidence", "inferred_low_confidence", "explicit_unknown"}
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_FIXTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?:\+?\d[\d .()-]{7,}\d)")
_ISO_DATE_OR_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T[^\s]+)?$")
_MAX_FIXTURE_BYTES = 65_536
_MAX_TEXT_BYTES = 512
_FIXTURE_ROOT_KEYS = {"version", "synthetic_fixture_only", "profiles"}
_PROFILE_KEYS = {
    "unreal-media-group": {"target", "sources", "fields", "conflicts", "umg"},
    "unreal-talent": {"target", "sources", "fields", "conflicts", "talent"},
}
_TARGET_KEYS = {"business_unit", "result_id", "global_identity_id", "account_name", "domain"}
_SOURCE_KEYS = {
    "source_id", "url", "source_type", "title", "summary", "quote",
    "observed_at", "source_date", "official_source",
}
_FIELD_KEYS = {"value", "basis", "evidence_ids", "confidence_reason", "uncertainty"}
_RESEARCH_FIELD_TYPES = {
    "product_extraction": "text_list",
    "current_campaigns": "text_list",
    "brand_voice": "text",
    "visual_direction": "text",
    "existing_ad_angles": "text_list",
    "claims_and_restrictions": "text_list",
    "target_audience": "text",
    "hero_product": "text",
    "campaign_objective": "text",
    "creative_opportunity": "text",
}
_APPROVAL_EVENT_FIELDS = (
    "approval_event_id", "task_id", "run_id", "output_id", "result_id", "result_hash",
    "result_byte_length", "output_hash", "output_byte_length", "configuration_hash",
    "protection_hash", "global_identity_id", "business_unit", "scope", "decision",
    "proposer_actor", "reviewer_actor", "reason", "effective_at", "recorded_at", "expires_at",
    "supersedes_id", "audit_correlation_id",
)
_BRIEF_REVIEW_EVENT_FIELDS = (
    "review_event_id", "brief_id", "brief_hash", "brief_byte_length", "business_unit",
    "decision", "proposer_actor", "reviewer_actor", "reason", "effective_at", "recorded_at",
    "supersedes_id", "authority_granted", "audit_correlation_id",
)
_BUSINESS_UNIT_SECTION_TYPES = {
    "unreal-media-group": {
        "product_truth": "text",
        "packaging_claim_boundaries": "text",
        "hero_rationale": "text",
        "umg_relevance": "text",
        "campaign_format": "text",
        "channels": "text_list",
        "product_accuracy_review_needs": "text_list",
    },
    "unreal-talent": {
        "brand_or_agency_path": "text",
        "talent_archetype": "text",
        "territory": "text_list",
        "channels": "text_list",
        "duration": "text",
        "intended_usage": "text",
        "rights_status": "text",
        "roster_authorization_state": "text",
        "category_conflicts": "text",
        "exclusivity_conflicts": "text",
        "brand_safety_state": "text",
        "human_review_owner": "text",
        "named_talent": "none",
        "availability_claimed": "false",
        "endorsement_claimed": "false",
        "clearance_claimed": "false",
        "likeness_authorized": "false",
    },
}
_CREDENTIAL_PARTS = {
    "authorization", "bearer", "credential", "credentials", "password", "passwd",
    "secret", "token", "private_key", "access_token", "refresh_token", "client_secret",
    "secret_key", "api_key", "apikey",
}
_CONTACT_PARTS = {"contact", "email", "phone", "telephone", "mobile"}
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:\bauthorization\s*[:=]\s*)?\bbearer\s+[A-Za-z0-9._~+/=-]+"
    r"|\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|client[ _-]?secret|"
    r"private[ _-]?key|secret[ _-]?key|password|passwd|token)\s*[:=]\s*\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_PROTECTION_FIELDS = (
    "global_identity_id", "business_unit", "duplicate_state", "duplicate_classification",
    "effective_duplicate_state", "effective_matched_identity_id", "queue", "rejection_reason",
    "effective_rejection", "suppressed", "effective_suppressed", "cooldown_until",
    "effective_cooldown", "relationship", "rights_state", "brand_safety_state", "named_talent",
    "current_decision",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise MissionControlError(400, "A timezone-aware timestamp is required.")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise MissionControlError(409, f"Stored {label} is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise MissionControlError(409, f"Stored {label} is invalid.") from exc
    return _utc(parsed)


def _digest(raw: str) -> tuple[str, int]:
    encoded = raw.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError, UnicodeError, RecursionError, ValueError) as exc:
        raise MissionControlError(409, f"Stored {label} failed its integrity check.") from exc
    if not isinstance(value, dict):
        raise MissionControlError(409, f"Stored {label} failed its integrity check.")
    return value


def _safe_text(value: Any, label: str, *, maximum: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissionControlError(400, f"{label} is required.")
    text = value.strip()
    if len(text.encode("utf-8")) > maximum:
        raise MissionControlError(400, f"{label} exceeds its bounded size.")
    if _EMAIL.search(text) or (_PHONE.search(text) and not _ISO_DATE_OR_TIME.fullmatch(text)):
        raise MissionControlError(400, f"{label} must not contain private contact data.")
    if _CREDENTIAL_VALUE.search(text):
        raise MissionControlError(400, f"{label} must not contain credentials.")
    return text


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant {value} is not allowed.")


def _normalized_key(value: str) -> str:
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _validate_safe_value(value: Any, label: str, *, depth: int = 0) -> None:
    if depth > 8:
        raise MissionControlError(400, f"{label} exceeds its bounded nesting depth.")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MissionControlError(400, f"{label} must contain only finite numbers.")
        return
    if isinstance(value, str):
        _safe_text(value, label)
        return
    if isinstance(value, list) and len(value) <= 30:
        for item in value:
            _validate_safe_value(item, label, depth=depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 30:
        for key, item in value.items():
            if not isinstance(key, str):
                raise MissionControlError(400, f"{label} has an invalid shape.")
            _safe_text(key, f"{label} key", maximum=100)
            normalized_key = _normalized_key(key)
            key_parts = set(normalized_key.split("_"))
            if key_parts.intersection(_CONTACT_PARTS):
                raise MissionControlError(400, f"{label} must not contain private contact data.")
            if (
                key_parts.intersection({"password", "passwd", "secret", "credential", "credentials", "token"})
                or normalized_key in _CREDENTIAL_PARTS
            ):
                raise MissionControlError(400, f"{label} must not contain credentials.")
            _validate_safe_value(item, label, depth=depth + 1)
        return
    raise MissionControlError(400, f"{label} has an invalid shape.")


def _event_material(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: record[field] for field in fields}


def _validate_event_integrity(record: Any, fields: tuple[str, ...], label: str) -> None:
    try:
        raw = record["event_snapshot"]
        content_hash = record["content_hash"]
        byte_length = record["byte_length"]
    except (IndexError, KeyError) as exc:
        raise MissionControlError(409, f"The {label} integrity material is missing.") from exc
    if not isinstance(raw, str) or not isinstance(content_hash, str) or type(byte_length) is not int:
        raise MissionControlError(409, f"The {label} failed its integrity check.")
    actual_hash, actual_length = _digest(raw)
    snapshot = _json_object(raw, label)
    if (
        actual_hash != content_hash
        or actual_length != byte_length
        or snapshot != _event_material(record, fields)
    ):
        raise MissionControlError(409, f"The {label} failed its integrity check.")


def _validate_research_value(name: str, basis: str, value: Any) -> None:
    if basis == "explicit_unknown":
        if value is not None:
            raise MissionControlError(400, f"Fixture brief field {name} must preserve its unknown value.")
        return
    if value is None:
        raise MissionControlError(400, f"Fixture brief field {name} requires a typed value.")
    kind = _RESEARCH_FIELD_TYPES[name]
    if kind == "text":
        _safe_text(value, f"{name} fixture value")
        return
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 30
        or any(not isinstance(item, str) for item in value)
    ):
        raise MissionControlError(400, f"Fixture brief field {name} has the wrong value type.")
    for item in value:
        _safe_text(item, f"{name} fixture value")


def _validate_business_unit_section(business_unit: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MissionControlError(400, "The business-unit fixture section is missing.")
    expected = _BUSINESS_UNIT_SECTION_TYPES[business_unit]
    if set(value) != set(expected):
        raise MissionControlError(
            400, "The business-unit fixture section has unknown or missing fields."
        )
    for key, kind in expected.items():
        item = value[key]
        if kind == "text":
            _safe_text(item, f"Business-unit field {key}")
        elif kind == "text_list":
            if (
                not isinstance(item, list) or not 1 <= len(item) <= 30
                or any(not isinstance(entry, str) for entry in item)
            ):
                raise MissionControlError(400, f"Business-unit field {key} has the wrong type.")
            for entry in item:
                _safe_text(entry, f"Business-unit field {key}")
        elif kind == "none":
            if item is not None:
                raise MissionControlError(400, "The talent fixture cannot name a person.")
        elif kind == "false" and item is not False:
            raise MissionControlError(400, f"Business-unit field {key} has the wrong type or claim.")
    return value


def _validate_profile_target(
    profile: dict[str, Any], business_unit: str, result: dict[str, Any]
) -> None:
    target = profile.get("target")
    if not isinstance(target, dict) or set(target) != _TARGET_KEYS:
        raise MissionControlError(400, "The synthetic fixture target has an invalid shape.")
    expected = {
        "business_unit": business_unit,
        "result_id": result.get("prospect_id"),
        "global_identity_id": result.get("global_identity_id"),
        "account_name": result.get("account_name"),
        "domain": result.get("domain"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in expected.values()):
        raise MissionControlError(409, "The approved result target binding is invalid.")
    for key, value in target.items():
        _safe_text(value, f"Fixture target {key}", maximum=256)
    if target != expected:
        raise MissionControlError(400, "The synthetic fixture target does not match the approved result.")


def _protection_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    current_review = result.get("current_review")
    if current_review is None:
        current_review = {}
    if not isinstance(current_review, dict):
        raise MissionControlError(409, "The governed protection projection has an invalid shape.")
    leaf_ids: dict[str, Any] = {}
    for domain in ("decision", "suppress", "resolve_identity"):
        leaf = current_review.get(domain)
        if leaf is not None and not isinstance(leaf, dict):
            raise MissionControlError(409, "The governed protection projection has an invalid shape.")
        leaf_ids[domain] = leaf.get("event_id") if leaf else None
    return {
        **{key: result.get(key) for key in _PROTECTION_FIELDS},
        "governed_leaf_ids": leaf_ids,
    }


def _protection_digest(result: dict[str, Any]) -> str:
    raw = canonical_json(_protection_snapshot(result), limit=16_384, label="Protection snapshot")
    return _digest(raw)[0]


class EnrichmentService:
    """One-shot synthetic enrichment behind exact durable human approval."""

    _active_lock = threading.RLock()
    _active_runs: dict[str, set[str]] = {}

    def __init__(
        self,
        agent: RegisteredAgentService,
        *,
        fixture_path: Path,
        clock: Callable[[], datetime],
    ):
        self.agent = agent
        self.store: SqliteStore = agent.store
        self.fixture_path = Path(fixture_path)
        self.clock = clock
        self._active_key = str(self.store.path.resolve())
        self._projection_lock = getattr(self.agent.repository, "_lock", None)
        if self._projection_lock is None:
            raise MissionControlError(
                500, "The current governed projection cannot be locked for Phase 6A."
            )
        self.store.initialize_phase6a()
        self.recovered_run_ids = self._recover_interrupted()

    def _register_active(self, run_id: str) -> None:
        with self._active_lock:
            self._active_runs.setdefault(self._active_key, set()).add(run_id)

    def _unregister_active(self, run_id: str) -> None:
        with self._active_lock:
            active = self._active_runs.get(self._active_key)
            if active is None:
                return
            active.discard(run_id)
            if not active:
                self._active_runs.pop(self._active_key, None)

    def _active_snapshot(self) -> set[str]:
        with self._active_lock:
            return set(self._active_runs.get(self._active_key, set()))

    def _now_dt(self) -> datetime:
        return _utc(self.clock())

    def _now(self) -> str:
        return _format_time(self._now_dt())

    def _recover_interrupted(self) -> list[str]:
        now = self._now()
        recovered: list[str] = []
        with self._active_lock:
            active = self._active_snapshot()
            with self.store.transaction() as connection:
                rows = connection.execute(
                    "SELECT * FROM enrichment_runs WHERE state='running' ORDER BY rowid"
                ).fetchall()
                for run in rows:
                    if run["enrichment_run_id"] in active:
                        continue
                    connection.execute(
                        "UPDATE enrichment_runs SET state='failed',completed_at=?,"
                        " failure_class='interrupted_execution_recovered',"
                        " remediation='Create a new durable approval before another manual attempt.'"
                        " WHERE enrichment_run_id=? AND state='running'",
                        (now, run["enrichment_run_id"]),
                    )
                    approval = connection.execute(
                        "SELECT run_id FROM prospect_approval_events WHERE approval_event_id=?",
                        (run["approval_event_id"],),
                    ).fetchone()
                    self.store.insert_audit(
                        connection, run_id=approval["run_id"],
                        event_type="phase6a_interrupted_run_recovered", actor="startup_recovery",
                        business_unit=run["business_unit"], correlation_id=run["audit_correlation_id"],
                        safe_status=run["enrichment_run_id"], recorded_at=now,
                    )
                    recovered.append(run["enrichment_run_id"])
        return recovered

    @staticmethod
    def _authorize(actor: str, business_unit: str) -> None:
        if actor not in ACTOR_SCOPE:
            raise MissionControlError(403, "Actor is not allowed in this local review surface.")
        if business_unit not in ACTOR_SCOPE[actor]:
            raise MissionControlError(403, "Business-unit access denied.")

    @staticmethod
    def _approval_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _target_context(
        self,
        connection: sqlite3.Connection,
        business_unit: str,
        task_id: str,
        result_id: str,
        *,
        require_current_projection: bool,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT rt.task_id,rt.run_id,rt.output_id,rt.business_unit,rt.state,"
            " rt.allowed_reviewers,rt.created_at AS task_created_at,"
            " wr.run_id AS worker_run_id,wr.output_id AS worker_output_id,"
            " wr.review_task_id AS worker_review_task_id,wr.business_unit AS worker_business_unit,"
            " wr.initiating_actor,wr.state AS run_state,wr.configuration_snapshot,"
            " wr.configuration_hash,wo.output_id AS durable_output_id,"
            " wo.run_id AS output_run_id,wo.business_unit AS output_business_unit,"
            " wo.content_hash AS output_hash,wo.byte_length AS output_byte_length,"
            " wo.result_ids,wo.result_snapshot"
            " FROM review_tasks rt JOIN worker_runs wr ON wr.run_id=rt.run_id"
            " JOIN worker_outputs wo ON wo.output_id=rt.output_id"
            " WHERE rt.task_id=? AND rt.business_unit=?",
            (task_id, business_unit),
        ).fetchone()
        if row is None:
            raise MissionControlError(403, "Business-unit access denied or review task not found.")
        raw = row["result_snapshot"]
        output_hash, output_length = _digest(raw)
        if output_hash != row["output_hash"] or output_length != row["output_byte_length"]:
            raise MissionControlError(409, "The worker output failed its integrity check.")
        try:
            results = json.loads(raw)
            result_ids = json.loads(row["result_ids"])
            configuration = json.loads(row["configuration_snapshot"])
            reviewers = json.loads(row["allowed_reviewers"])
        except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
            raise MissionControlError(409, "The durable review input failed its integrity check.") from exc
        if (
            not isinstance(results, list)
            or not all(isinstance(item, dict) for item in results)
            or not isinstance(result_ids, list)
            or not all(isinstance(item, str) for item in result_ids)
            or result_ids != [item.get("prospect_id") for item in results]
            or not isinstance(configuration, dict)
            or not isinstance(reviewers, list)
            or not reviewers
            or not all(
                isinstance(reviewer, str)
                and reviewer in ACTOR_SCOPE
                and business_unit in ACTOR_SCOPE[reviewer]
                for reviewer in reviewers
            )
        ):
            raise MissionControlError(409, "The durable review input failed its integrity check.")
        configuration_raw = canonical_json(
            configuration, limit=16_384, label="Configuration snapshot"
        )
        configuration_hash = _digest(configuration_raw)[0]
        graph_bindings = (
            row["run_id"] == row["worker_run_id"],
            row["output_id"] == row["durable_output_id"],
            row["output_run_id"] == row["worker_run_id"],
            row["worker_output_id"] == row["durable_output_id"],
            row["worker_review_task_id"] == row["task_id"],
            row["business_unit"] == business_unit,
            row["worker_business_unit"] == business_unit,
            row["output_business_unit"] == business_unit,
            configuration.get("business_unit") == business_unit,
            configuration_hash == row["configuration_hash"],
        )
        if not all(graph_bindings):
            raise MissionControlError(409, "The review task, run, output, or configuration binding is invalid.")
        matches = [item for item in results if isinstance(item, dict) and item.get("prospect_id") == result_id]
        if len(matches) != 1:
            raise MissionControlError(404, "Result was not found in the bound worker output.")
        result = matches[0]
        if result.get("business_unit") != business_unit or not result.get("global_identity_id"):
            raise MissionControlError(409, "Result identity or business-unit binding is invalid.")
        result_raw = canonical_json(result, limit=MAX_SNAPSHOT_BYTES, label="Approved result")
        result_hash, result_length = _digest(result_raw)
        output_protection_hash = _protection_digest(result)
        context = {
            **dict(row),
            "result": result,
            "result_raw": result_raw,
            "result_hash": result_hash,
            "result_length": result_length,
            "output_protection_hash": output_protection_hash,
            "configuration": configuration,
            "reviewers": reviewers,
        }

        if require_current_projection:
            try:
                current = self.agent.repository.get_prospect(
                    row["initiating_actor"], business_unit, result_id
                )
            except MissionControlError as exc:
                raise MissionControlError(
                    409,
                    "The current governed prospect projection is unavailable; a new worker result is required.",
                ) from exc
            if (
                current.get("prospect_id") != result_id
                or current.get("business_unit") != business_unit
                or current.get("global_identity_id") != result.get("global_identity_id")
            ):
                raise MissionControlError(409, "The current governed prospect identity binding is invalid.")
            context["current_result"] = current
            context["current_protection_hash"] = _protection_digest(current)
        return context

    @staticmethod
    def _eligible(context: dict[str, Any]) -> None:
        result = context.get("current_result", context["result"])
        if context["state"] != "pending_review" or context["run_state"] != "succeeded":
            raise MissionControlError(409, "The bound review task is not pending on a succeeded run.")
        if result.get("queue") != "new" or result.get("effective_duplicate_state") not in {None, "new"}:
            raise MissionControlError(409, "Result protections do not permit enrichment.")
        if (
            result.get("effective_suppressed") or result.get("suppressed")
            or result.get("rejection_reason") or result.get("effective_rejection")
            or result.get("relationship") not in {None, "none"}
        ):
            raise MissionControlError(409, "Result protections do not permit enrichment.")
        if result.get("named_talent"):
            raise MissionControlError(409, "Named-person records cannot enter fixture enrichment.")
        if result.get("current_decision") not in {"pending", "approved_for_deeper_research"}:
            raise MissionControlError(409, "Result protections do not permit enrichment.")
        allowed_rights = {"reviewed"} if result["business_unit"] == "unreal-talent" else {"not_applicable", "reviewed"}
        if result.get("rights_state") not in allowed_rights or result.get("brand_safety_state") != "clear":
            raise MissionControlError(409, "Result protections do not permit enrichment.")

    def _leaf(
        self, connection: sqlite3.Connection, task_id: str, result_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT parent.* FROM prospect_approval_events parent"
            " LEFT JOIN prospect_approval_events child ON child.supersedes_id=parent.approval_event_id"
            " WHERE parent.task_id=? AND parent.result_id=? AND parent.scope=?"
            " AND child.approval_event_id IS NULL ORDER BY parent.recorded_at DESC LIMIT 1",
            (task_id, result_id, APPROVAL_SCOPE),
        ).fetchone()

    def _approval_integrity(
        self,
        connection: sqlite3.Connection,
        approval: sqlite3.Row,
        business_unit: str,
        *,
        require_current_projection: bool,
    ) -> tuple[dict[str, Any], datetime, datetime, datetime]:
        _validate_event_integrity(approval, _APPROVAL_EVENT_FIELDS, "durable approval event")
        context = self._target_context(
            connection,
            business_unit,
            approval["task_id"],
            approval["result_id"],
            require_current_projection=require_current_projection,
        )
        effective = _parse_time(approval["effective_at"], "approval effective time")
        recorded = _parse_time(approval["recorded_at"], "approval recorded time")
        expires = _parse_time(approval["expires_at"], "approval expiry")
        if (
            approval["effective_at"] != _format_time(effective)
            or approval["recorded_at"] != _format_time(recorded)
            or approval["expires_at"] != _format_time(expires)
            or expires != effective + APPROVAL_TTL
        ):
            raise MissionControlError(409, "The durable approval time binding failed its integrity check.")
        try:
            _safe_text(approval["reason"], "Stored approval reason")
        except MissionControlError as exc:
            raise MissionControlError(409, "The durable approval metadata failed its integrity check.") from exc
        bindings = (
            approval["business_unit"] == business_unit,
            context["run_id"] == approval["run_id"],
            context["output_id"] == approval["output_id"],
            context["result_hash"] == approval["result_hash"],
            context["result_length"] == approval["result_byte_length"],
            context["output_hash"] == approval["output_hash"],
            context["output_byte_length"] == approval["output_byte_length"],
            context["configuration_hash"] == approval["configuration_hash"],
            context["output_protection_hash"] == approval["protection_hash"],
            context["result"]["global_identity_id"] == approval["global_identity_id"],
            approval["scope"] == APPROVAL_SCOPE,
            approval["decision"] in _APPROVAL_DECISIONS,
            approval["proposer_actor"] == context["initiating_actor"],
            approval["reviewer_actor"] in context["reviewers"],
            approval["reviewer_actor"] != approval["proposer_actor"],
            approval["reviewer_actor"] != context["initiating_actor"],
        )
        if not all(bindings):
            raise MissionControlError(409, "The durable approval binding failed its integrity check.")
        if require_current_projection:
            if context["current_protection_hash"] != approval["protection_hash"]:
                raise MissionControlError(409, "The governed prospect protections changed after approval.")
        return context, effective, recorded, expires

    def record_approval(
        self,
        actor: str,
        business_unit: str,
        task_id: str,
        result_id: str,
        *,
        decision: str,
        reason: str,
        effective_at: datetime | None = None,
        expected_leaf_id: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        if decision not in _APPROVAL_DECISIONS:
            raise MissionControlError(400, "Unknown durable approval decision.")
        reason = _safe_text(reason, "Approval reason")
        recorded = self._now_dt()
        effective = _utc(effective_at) if effective_at else recorded
        expires = effective + APPROVAL_TTL
        with self._projection_lock, self.store.transaction() as connection:
            context = self._target_context(
                connection,
                business_unit,
                task_id,
                result_id,
                require_current_projection=decision == "approved",
            )
            if actor not in context["reviewers"]:
                raise MissionControlError(403, "Actor is not an allowed reviewer for this result.")
            if actor == context["initiating_actor"]:
                raise MissionControlError(403, "The proposing actor cannot self-approve this result.")
            if decision == "approved":
                if context["current_protection_hash"] != context["output_protection_hash"]:
                    raise MissionControlError(409, "The governed prospect protections changed after the worker output.")
                self._eligible(context)
            leaf = self._leaf(connection, task_id, result_id)
            if leaf is not None:
                _validate_event_integrity(leaf, _APPROVAL_EVENT_FIELDS, "durable approval event")
            expected = expected_leaf_id or None
            if (leaf is None and expected is not None) or (
                leaf is not None and expected != leaf["approval_event_id"]
            ):
                raise MissionControlError(409, "The expected approval is not the current leaf.")
            event_id = self.store.next_id(connection, "papproval")
            correlation_id = self.store.next_id(connection, "pcorr")
            event = {
                "approval_event_id": event_id,
                "task_id": task_id,
                "run_id": context["run_id"],
                "output_id": context["output_id"],
                "result_id": result_id,
                "result_hash": context["result_hash"],
                "result_byte_length": context["result_length"],
                "output_hash": context["output_hash"],
                "output_byte_length": context["output_byte_length"],
                "configuration_hash": context["configuration_hash"],
                "protection_hash": context.get(
                    "current_protection_hash", context["output_protection_hash"]
                ),
                "global_identity_id": context["result"]["global_identity_id"],
                "business_unit": business_unit,
                "scope": APPROVAL_SCOPE,
                "decision": decision,
                "proposer_actor": context["initiating_actor"],
                "reviewer_actor": actor,
                "reason": reason,
                "effective_at": _format_time(effective),
                "recorded_at": _format_time(recorded),
                "expires_at": _format_time(expires),
                "supersedes_id": expected,
                "audit_correlation_id": correlation_id,
            }
            event_raw = canonical_json(event, limit=16_384, label="Durable approval event")
            event_hash, event_length = _digest(event_raw)
            try:
                connection.execute(
                    f"INSERT INTO prospect_approval_events ({','.join(_APPROVAL_EVENT_FIELDS)},"
                    "event_snapshot,content_hash,byte_length) VALUES ("
                    f"{','.join('?' for _ in range(len(_APPROVAL_EVENT_FIELDS) + 3))})",
                    tuple(event[field] for field in _APPROVAL_EVENT_FIELDS)
                    + (event_raw, event_hash, event_length),
                )
            except sqlite3.IntegrityError as exc:
                raise MissionControlError(409, "A concurrent decision already changed the current leaf.") from exc
            self.store.insert_audit(
                connection, run_id=context["run_id"], event_type="phase6a_approval_recorded",
                actor=actor, business_unit=business_unit, correlation_id=correlation_id,
                safe_status=f"{decision}:{event_id}", recorded_at=_format_time(recorded),
            )
        return self.get_approval(actor, business_unit, event_id)

    def get_approval(self, actor: str, business_unit: str, approval_event_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM prospect_approval_events WHERE approval_event_id=? AND business_unit=?",
                (approval_event_id, business_unit),
            ).fetchone()
            if row is not None:
                self._approval_integrity(
                    connection, row, business_unit, require_current_projection=False
                )
        finally:
            connection.close()
        if row is None:
            raise MissionControlError(403, "Business-unit access denied or durable approval not found.")
        return self._approval_row(row)

    def approvals(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT pae.*, NOT EXISTS (SELECT 1 FROM prospect_approval_events child"
                " WHERE child.supersedes_id=pae.approval_event_id) AS is_current_leaf,"
                " EXISTS (SELECT 1 FROM enrichment_runs er"
                " WHERE er.approval_event_id=pae.approval_event_id) AS consumed"
                " FROM prospect_approval_events pae WHERE pae.business_unit=? ORDER BY pae.rowid",
                (business_unit,),
            ).fetchall()
            now = self._now_dt()
            result = []
            for row in rows:
                _validate_event_integrity(row, _APPROVAL_EVENT_FIELDS, "durable approval event")
                item = self._approval_row(row)
                item["is_current_leaf"] = bool(item["is_current_leaf"])
                item["consumed"] = bool(item["consumed"])
                item["valid_now"] = False
                item["validity_status"] = "history_only"
                if item["decision"] == "approved":
                    try:
                        context, effective, recorded, expires = self._approval_integrity(
                            connection, row, business_unit, require_current_projection=True
                        )
                        self._eligible(context)
                        item["valid_now"] = recorded <= now and effective <= now < expires
                        item["validity_status"] = "valid" if item["valid_now"] else "outside_time_window"
                    except MissionControlError:
                        item["validity_status"] = "binding_invalid"
                result.append(item)
            return result
        finally:
            connection.close()

    def _validate_approval_for_start(
        self, connection: sqlite3.Connection, approval: sqlite3.Row, business_unit: str
    ) -> dict[str, Any]:
        leaf = self._leaf(connection, approval["task_id"], approval["result_id"])
        if leaf is None or leaf["approval_event_id"] != approval["approval_event_id"] or leaf["decision"] != "approved":
            raise MissionControlError(409, "The approval is not the current approved leaf.")
        now = self._now_dt()
        context, effective, recorded, expires = self._approval_integrity(
            connection, approval, business_unit, require_current_projection=True
        )
        if now < recorded:
            raise MissionControlError(409, "The durable approval recorded time is in the future.")
        if now < effective:
            raise MissionControlError(409, "The durable approval is not effective yet.")
        if now >= expires:
            raise MissionControlError(409, "The durable approval has expired.")
        self._eligible(context)
        return context

    def start_enrichment(
        self,
        actor: str,
        business_unit: str,
        approval_event_id: str,
        idempotency_key: str,
        *,
        before_finish: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._authorize(actor, business_unit)
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise MissionControlError(400, "A bounded alphanumeric idempotency key is required.")
        now = self._now()
        with self._active_lock, self._projection_lock:
            with self.store.transaction() as connection:
                approval = connection.execute(
                    "SELECT * FROM prospect_approval_events WHERE approval_event_id=? AND business_unit=?",
                    (approval_event_id, business_unit),
                ).fetchone()
                if approval is None:
                    raise MissionControlError(403, "Business-unit access denied or durable approval not found.")
                fingerprint = hashlib.sha256(canonical_json({
                    "approval_event_id": approval_event_id,
                    "business_unit": business_unit,
                    "result_id": approval["result_id"],
                    "global_identity_id": approval["global_identity_id"],
                }, limit=4096, label="Enrichment request").encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT * FROM enrichment_runs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    if existing["request_fingerprint"] != fingerprint or existing["business_unit"] != business_unit:
                        raise MissionControlError(409, "The idempotency key conflicts with another enrichment request.")
                    return self._run_row(existing), False
                context = self._validate_approval_for_start(connection, approval, business_unit)
                if actor != approval["proposer_actor"]:
                    raise MissionControlError(
                        403, "Only the bound run proposer may start this approved enrichment."
                    )
                consumed = connection.execute(
                    "SELECT * FROM enrichment_runs WHERE approval_event_id=?", (approval_event_id,)
                ).fetchone()
                if consumed is not None:
                    raise MissionControlError(409, "The durable approval was already consumed.")
                run_id = self.store.next_id(connection, "enrich")
                correlation_id = self.store.next_id(connection, "ecorr")
                connection.execute(
                    "INSERT INTO enrichment_runs (enrichment_run_id,approval_event_id,idempotency_key,"
                    " request_fingerprint,business_unit,result_id,global_identity_id,initiating_actor,state,"
                    " estimated_cost_usd,created_at,audit_correlation_id) VALUES (?,?,?,?,?,?,?,?, 'running',0,?,?)",
                    (
                        run_id, approval_event_id, idempotency_key, fingerprint, business_unit,
                        approval["result_id"], approval["global_identity_id"], actor, now, correlation_id,
                    ),
                )
                self.store.insert_audit(
                    connection, run_id=approval["run_id"], event_type="phase6a_enrichment_claimed",
                    actor=actor, business_unit=business_unit, correlation_id=correlation_id,
                    safe_status=run_id, recorded_at=now,
                )
            self._register_active(run_id)
        try:
            if before_finish:
                before_finish()
            payload = self._build_payload(business_unit, context, approval)
            self._complete_run(run_id, approval, context, payload)
        except Exception as exc:
            self._fail_run(run_id, approval, actor, business_unit, exc)
            raise
        finally:
            self._unregister_active(run_id)
        return self.get_run(actor, business_unit, run_id), True

    def _load_profile(self, business_unit: str) -> dict[str, Any]:
        try:
            raw = self.fixture_path.read_bytes()
        except OSError as exc:
            raise MissionControlError(500, "The bounded synthetic fixture is unavailable.") from exc
        if len(raw) > _MAX_FIXTURE_BYTES:
            raise MissionControlError(400, "The synthetic fixture exceeds its bounded size.")
        try:
            root = json.loads(raw, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError) as exc:
            raise MissionControlError(400, "The synthetic fixture is invalid.") from exc
        if (
            not isinstance(root, dict) or set(root) != _FIXTURE_ROOT_KEYS
            or type(root.get("version")) is not int or root["version"] != 1
            or root.get("synthetic_fixture_only") is not True
        ):
            raise MissionControlError(400, "The synthetic fixture boundary is missing.")
        _validate_safe_value(root, "Synthetic fixture profile")
        profiles = root.get("profiles")
        if not isinstance(profiles, dict) or set(profiles) != set(_PROFILE_KEYS):
            raise MissionControlError(400, "The synthetic fixture profile set is invalid.")
        profile = profiles.get(business_unit)
        if not isinstance(profile, dict) or set(profile) != _PROFILE_KEYS[business_unit]:
            raise MissionControlError(400, "The synthetic fixture has no profile for this business unit.")
        return profile

    def _source(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
            raise MissionControlError(400, "A fixture source has an invalid shape.")
        url = value.get("url")
        if not isinstance(url, str):
            raise MissionControlError(400, "A fixture source URL is required.")
        url = _safe_text(url, "Fixture source URL")
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise MissionControlError(400, "The fixture source URL is invalid.") from exc
        host = (parts.hostname or "").casefold()
        if (
            parts.scheme not in {"http", "https"} or not host.endswith(".example")
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or "%" in url
        ):
            raise MissionControlError(400, "The fixture source URL is outside the reviewed .example boundary.")
        source_id = _safe_text(value.get("source_id"), "Fixture source identifier", maximum=100)
        if not _FIXTURE_ID.fullmatch(source_id):
            raise MissionControlError(400, "A fixture source identifier is invalid.")
        source_type = _safe_text(value.get("source_type"), "Fixture source type", maximum=100)
        title = _safe_text(value.get("title"), "Fixture source title", maximum=160)
        summary = _safe_text(value.get("summary"), "Fixture source summary")
        quote = _safe_text(value.get("quote"), "Fixture source quote", maximum=256)
        if len(quote.split()) > 25:
            raise MissionControlError(400, "A fixture source quote exceeds 25 words.")
        observed = value.get("observed_at")
        _parse_time(observed, "fixture observation time")
        source_date = value.get("source_date")
        if source_date is not None:
            try:
                date.fromisoformat(source_date)
            except (TypeError, ValueError) as exc:
                raise MissionControlError(400, "A fixture source date is invalid.") from exc
        if value.get("official_source") is not False:
            raise MissionControlError(400, "A fixture source cannot claim official-source status.")
        return {
            "source_id": source_id, "source_url": url, "source_type": source_type,
            "title": title, "summary": summary, "quote": quote, "source_date": source_date,
            "observed_at": observed, "official_source": False,
        }

    def _build_payload(
        self, business_unit: str, context: dict[str, Any], approval: sqlite3.Row
    ) -> dict[str, Any]:
        profile = self._load_profile(business_unit)
        result = context["result"]
        _validate_profile_target(profile, business_unit, result)
        raw_sources = profile.get("sources")
        if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 12:
            raise MissionControlError(400, "The fixture source set is outside its reviewed bounds.")
        sources = [self._source(item) for item in raw_sources]
        by_id = {item["source_id"]: item for item in sources}
        if len(by_id) != len(sources):
            raise MissionControlError(400, "Fixture source identifiers must be unique.")
        fields = profile.get("fields")
        if not isinstance(fields, dict) or set(fields) != set(REQUIRED_BRIEF_FIELDS):
            raise MissionControlError(400, "The fixture does not contain the complete brief field set.")
        maximum_age = context["configuration"].get("maximum_evidence_age_days")
        if not isinstance(maximum_age, int) or not 1 <= maximum_age <= 3650:
            raise MissionControlError(409, "The bound maximum evidence age is invalid.")
        research: dict[str, Any] = {}
        links: list[dict[str, Any]] = []
        current_date = self._now_dt().date()
        conflicts = profile.get("conflicts", [])
        if not isinstance(conflicts, list) or any(item not in REQUIRED_BRIEF_FIELDS for item in conflicts):
            raise MissionControlError(400, "Fixture conflicts have an invalid shape.")
        for name in REQUIRED_BRIEF_FIELDS:
            field = fields[name]
            if (
                not isinstance(field, dict) or set(field) != _FIELD_KEYS
                or field.get("basis") not in _FIELD_BASES
            ):
                raise MissionControlError(400, f"Fixture brief field {name} has an invalid basis.")
            evidence_ids = field.get("evidence_ids")
            if (
                not isinstance(evidence_ids, list)
                or not all(
                    isinstance(item, str) and _FIXTURE_ID.fullmatch(item) and item in by_id
                    for item in evidence_ids
                )
                or len(evidence_ids) != len(set(evidence_ids))
            ):
                raise MissionControlError(400, f"Fixture brief field {name} has invalid evidence links.")
            if field["basis"] != "explicit_unknown" and not evidence_ids:
                raise MissionControlError(400, f"Fixture brief field {name} requires evidence.")
            _validate_research_value(name, field["basis"], field.get("value"))
            confidence = _safe_text(field.get("confidence_reason"), f"{name} confidence reason")
            uncertainty = _safe_text(field.get("uncertainty"), f"{name} uncertainty")
            freshness_states: list[str] = []
            lineage = []
            for source_id in evidence_ids:
                source = by_id[source_id]
                dated = source["source_date"]
                if dated is None:
                    state = "unknown_date"
                else:
                    age = (current_date - date.fromisoformat(dated)).days
                    state = "future_dated" if age < 0 else ("stale" if age > maximum_age else "current")
                freshness_states.append(state)
                lineage.append({"source_id": source_id, "url": source["source_url"], "source_date": dated})
                links.append({
                    "field_path": f"research.{name}", "source_id": source_id,
                    "basis": "explicit_unknown" if field["basis"] == "explicit_unknown" else "source_evidence",
                    "confidence_reason": confidence, "uncertainty": uncertainty,
                    "freshness": state, "currentness": state, "quote": source["quote"],
                    "source_lineage": lineage[-1],
                })
            if not evidence_ids:
                links.append({
                    "field_path": f"research.{name}", "source_id": None, "basis": "explicit_unknown",
                    "confidence_reason": confidence, "uncertainty": uncertainty,
                    "freshness": "unknown_date", "currentness": "unknown_date", "quote": "",
                    "source_lineage": {"source_id": None, "url": None, "source_date": None},
                })
            currentness = (
                "future_dated" if "future_dated" in freshness_states else
                "stale" if "stale" in freshness_states else
                "unknown_date" if not freshness_states or "unknown_date" in freshness_states else "current"
            )
            research[name] = {
                "value": field.get("value"), "basis": field["basis"], "evidence_ids": evidence_ids,
                "confidence_reason": confidence, "uncertainty": uncertainty,
                "freshness": freshness_states or ["unknown_date"], "currentness": currentness,
                "source_lineage": lineage, "conflict_state": "requires_human_review" if name in conflicts else "none",
            }
        unit_section = profile.get("umg" if business_unit == "unreal-media-group" else "talent")
        unit_section = _validate_business_unit_section(business_unit, unit_section)
        _validate_safe_value(unit_section, "Business-unit fixture value")
        if business_unit == "unreal-talent":
            required_false = ("availability_claimed", "endorsement_claimed", "clearance_claimed", "likeness_authorized")
            if unit_section.get("named_talent") is not None or any(unit_section.get(key) is not False for key in required_false):
                raise MissionControlError(400, "The talent fixture cannot claim a person, availability, endorsement, clearance, or likeness.")
        unit_lineage: dict[str, Any] = {}
        for name in unit_section:
            unit_lineage[name] = {
                "basis": "observed",
                "evidence_ids": list(by_id),
                "confidence_reason": "The repository-owned synthetic profile states this bounded value.",
                "uncertainty": "Synthetic fixture metadata only; human review remains required.",
                "freshness": [
                    "unknown_date" if source["source_date"] is None else
                    ("future_dated" if (current_date - date.fromisoformat(source["source_date"])).days < 0 else
                     "stale" if (current_date - date.fromisoformat(source["source_date"])).days > maximum_age else "current")
                    for source in sources
                ],
                "currentness": "human_review_required",
                "source_lineage": [
                    {"source_id": source["source_id"], "url": source["source_url"], "source_date": source["source_date"]}
                    for source in sources
                ],
            }
            for source in sources:
                links.append({
                    "field_path": f"{'umg' if business_unit == 'unreal-media-group' else 'talent'}.{name}",
                    "source_id": source["source_id"], "basis": "source_evidence",
                    "confidence_reason": unit_lineage[name]["confidence_reason"],
                    "uncertainty": unit_lineage[name]["uncertainty"],
                    "freshness": unit_lineage[name]["freshness"][sources.index(source)],
                    "currentness": "human_review_required", "quote": source["quote"],
                    "source_lineage": unit_lineage[name]["source_lineage"][sources.index(source)],
                })
        brief = {
            "brief_schema": BRIEF_SCHEMA,
            "research_mode": "synthetic_fixture_only",
            "official_site_research_performed": False,
            "business_unit": business_unit,
            "global_identity_id": approval["global_identity_id"],
            "result_id": approval["result_id"],
            "source_run_id": approval["run_id"],
            "source_output_id": approval["output_id"],
            "source_output_hash": approval["output_hash"],
            "source_result_hash": approval["result_hash"],
            "approval_event_id": approval["approval_event_id"],
            "approval_scope": APPROVAL_SCOPE,
            "account_name": result.get("account_name"),
            "domain": result.get("domain"),
            "research": research,
            "research_state": "conflicts_require_human_review" if conflicts else "ready_for_research_quality_review",
            "limitations": [
                "Synthetic fixture evidence only.",
                "No official-site or other external research was performed.",
                "No production, product, legal, rights, roster, brand-safety, or commercial clearance is implied.",
            ],
            "unresolved_questions": list(conflicts),
            "rights_conflict_review_state": "human_review_required",
            "human_review_required": True,
            "research_review_state": "pending_human_review",
            "business_unit_value_lineage": unit_lineage,
            "created_at": self._now(),
            "umg" if business_unit == "unreal-media-group" else "talent": unit_section,
        }
        return {"sources": sources, "links": links, "brief": brief}

    def _complete_run(
        self, run_id: str, approval: sqlite3.Row, context: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        now = self._now()
        brief_raw = canonical_json(payload["brief"], limit=MAX_SNAPSHOT_BYTES, label="Campaign brief")
        brief_hash, brief_length = _digest(brief_raw)
        with self.store.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM enrichment_runs WHERE enrichment_run_id=? AND state='running'", (run_id,)
            ).fetchone()
            if run is None:
                raise MissionControlError(409, "The enrichment run is no longer finishable.")
            source_records: dict[str, str] = {}
            source_manifest: list[dict[str, Any]] = []
            for source in payload["sources"]:
                source_record_id = self.store.next_id(connection, "esource")
                source_records[source["source_id"]] = source_record_id
                source_record = {
                    "source_record_id": source_record_id,
                    "enrichment_run_id": run_id,
                    "source_key": source["source_id"],
                    "business_unit": approval["business_unit"],
                    "source_url": source["source_url"],
                    "source_type": source["source_type"],
                    "title": source["title"],
                    "summary": source["summary"],
                    "quote": source["quote"],
                    "source_date": source["source_date"],
                    "observed_at": source["observed_at"],
                    "official_source": False,
                }
                source_raw = canonical_json(source_record, limit=4096, label="Source metadata")
                source_hash, source_length = _digest(source_raw)
                connection.execute(
                    "INSERT INTO enrichment_sources (source_record_id,enrichment_run_id,source_key,"
                    " business_unit,source_url,source_type,title,summary,quote,source_date,observed_at,"
                    " official_source,source_snapshot,content_hash,byte_length) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                    (
                        source_record_id, run_id, source["source_id"], approval["business_unit"],
                        source["source_url"], source["source_type"], source["title"], source["summary"],
                        source["quote"], source["source_date"], source["observed_at"], source_raw,
                        source_hash, source_length,
                    ),
                )
                source_manifest.append({
                    "source_record_id": source_record_id,
                    "source_key": source["source_id"],
                    "content_hash": source_hash,
                    "byte_length": source_length,
                })
            brief_id = self.store.next_id(connection, "brief")
            family_id = f"brief-{approval['business_unit']}-{approval['global_identity_id']}"
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM campaign_brief_versions WHERE brief_family_id=?",
                (family_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO campaign_brief_versions (brief_id,brief_family_id,version,enrichment_run_id,"
                " approval_event_id,business_unit,result_id,global_identity_id,brief_schema,research_state,"
                " brief_snapshot,content_hash,byte_length,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    brief_id, family_id, version, run_id, approval["approval_event_id"],
                    approval["business_unit"], approval["result_id"], approval["global_identity_id"],
                    BRIEF_SCHEMA, payload["brief"]["research_state"], brief_raw, brief_hash, brief_length, now,
                ),
            )
            link_manifest: list[dict[str, Any]] = []
            for link in payload["links"]:
                link_id = self.store.next_id(connection, "elink")
                lineage_raw = canonical_json(
                    link["source_lineage"], limit=4096, label="Source lineage"
                )
                link_record = {
                    "link_id": link_id,
                    "brief_id": brief_id,
                    "field_path": link["field_path"],
                    "source_record_id": source_records.get(link["source_id"]),
                    "basis": link["basis"],
                    "confidence_reason": link["confidence_reason"],
                    "uncertainty": link["uncertainty"],
                    "freshness": link["freshness"],
                    "currentness": link["currentness"],
                    "quote": link["quote"],
                    "source_lineage": link["source_lineage"],
                }
                link_raw = canonical_json(link_record, limit=8192, label="Evidence link")
                link_hash, link_length = _digest(link_raw)
                connection.execute(
                    "INSERT INTO brief_evidence_links (link_id,brief_id,field_path,source_record_id,basis,"
                    " confidence_reason,uncertainty,freshness,currentness,quote,source_lineage,"
                    " link_snapshot,content_hash,byte_length) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        link_id, brief_id, link["field_path"], link_record["source_record_id"],
                        link["basis"], link["confidence_reason"], link["uncertainty"], link["freshness"],
                        link["currentness"], link["quote"], lineage_raw, link_raw, link_hash, link_length,
                    ),
                )
                link_manifest.append({
                    "link_id": link_id,
                    "field_path": link["field_path"],
                    "source_record_id": link_record["source_record_id"],
                    "content_hash": link_hash,
                    "byte_length": link_length,
                })
            manifest_id = self.store.next_id(connection, "bmanifest")
            manifest = {
                "manifest_id": manifest_id,
                "brief_id": brief_id,
                "enrichment_run_id": run_id,
                "business_unit": approval["business_unit"],
                "brief_hash": brief_hash,
                "brief_byte_length": brief_length,
                "sources": source_manifest,
                "evidence_links": link_manifest,
            }
            manifest_raw = canonical_json(
                manifest, limit=MAX_SNAPSHOT_BYTES, label="Brief integrity manifest"
            )
            manifest_hash, manifest_length = _digest(manifest_raw)
            connection.execute(
                "INSERT INTO brief_integrity_manifests (manifest_id,brief_id,enrichment_run_id,"
                " business_unit,manifest_snapshot,content_hash,byte_length) VALUES (?,?,?,?,?,?,?)",
                (
                    manifest_id, brief_id, run_id, approval["business_unit"], manifest_raw,
                    manifest_hash, manifest_length,
                ),
            )
            connection.execute(
                "UPDATE enrichment_runs SET state='succeeded',completed_at=?,brief_id=?"
                " WHERE enrichment_run_id=? AND state='running'",
                (now, brief_id, run_id),
            )
            self.store.insert_audit(
                connection, run_id=approval["run_id"], event_type="phase6a_brief_committed",
                actor=run["initiating_actor"], business_unit=approval["business_unit"],
                correlation_id=run["audit_correlation_id"], safe_status=f"{brief_id}:{brief_hash[:16]}",
                recorded_at=now,
            )

    def _fail_run(
        self, run_id: str, approval: sqlite3.Row, actor: str, business_unit: str, exc: Exception
    ) -> None:
        now = self._now()
        status = getattr(exc, "status", 500)
        if isinstance(exc, MissionControlError) and status == 400:
            failure_class = "fixture_validation"
            remediation = "Correct the repository-owned synthetic fixture and create a new approval."
        elif isinstance(exc, sqlite3.Error) or (
            isinstance(exc, MissionControlError) and status >= 500
        ):
            failure_class = "local_persistence_failure"
            remediation = "Inspect the local durable store and create a new approval before another manual attempt."
        elif isinstance(exc, MissionControlError):
            failure_class = "bound_input_validation"
            remediation = "Revalidate the bound local input and create a new approval before another manual attempt."
        else:
            failure_class = "internal_failure"
            remediation = "Inspect the local application and create a new approval before another manual attempt."
        with self.store.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM enrichment_runs WHERE enrichment_run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["state"] != "running":
                return
            connection.execute(
                "UPDATE enrichment_runs SET state='failed',completed_at=?,failure_class=?,remediation=?"
                " WHERE enrichment_run_id=?",
                (now, failure_class, remediation, run_id),
            )
            self.store.insert_audit(
                connection, run_id=approval["run_id"], event_type="phase6a_enrichment_failed",
                actor=actor, business_unit=business_unit, correlation_id=run["audit_correlation_id"],
                safe_status=f"{failure_class}:http {status}", recorded_at=now,
            )

    def get_run(self, actor: str, business_unit: str, run_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT er.*, cb.content_hash AS brief_hash, cb.byte_length AS brief_byte_length"
                " FROM enrichment_runs er LEFT JOIN campaign_brief_versions cb ON cb.brief_id=er.brief_id"
                " WHERE er.enrichment_run_id=? AND er.business_unit=?",
                (run_id, business_unit),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise MissionControlError(403, "Business-unit access denied or enrichment run not found.")
        return self._run_row(row)

    def runs(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT er.*, cb.content_hash AS brief_hash, cb.byte_length AS brief_byte_length"
                " FROM enrichment_runs er LEFT JOIN campaign_brief_versions cb ON cb.brief_id=er.brief_id"
                " WHERE er.business_unit=? ORDER BY er.rowid", (business_unit,)
            ).fetchall()
        finally:
            connection.close()
        return [self._run_row(row) for row in rows]

    @staticmethod
    def _validated_source(source: sqlite3.Row) -> dict[str, Any]:
        source_hash, source_length = _digest(source["source_snapshot"])
        if source_hash != source["content_hash"] or source_length != source["byte_length"]:
            raise MissionControlError(409, "A fixture source failed its integrity check.")
        snapshot = _json_object(source["source_snapshot"], "fixture source")
        expected = {
            "source_record_id": source["source_record_id"],
            "enrichment_run_id": source["enrichment_run_id"],
            "source_key": source["source_key"],
            "business_unit": source["business_unit"],
            "source_url": source["source_url"],
            "source_type": source["source_type"],
            "title": source["title"],
            "summary": source["summary"],
            "quote": source["quote"],
            "source_date": source["source_date"],
            "observed_at": source["observed_at"],
            "official_source": bool(source["official_source"]),
        }
        if snapshot != expected:
            raise MissionControlError(409, "A fixture source failed its denormalized integrity check.")
        item = dict(source)
        item["official_source"] = bool(item["official_source"])
        return item

    @staticmethod
    def _validated_link(
        link: sqlite3.Row,
        *,
        brief: sqlite3.Row,
        sources: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        link_hash, link_length = _digest(link["link_snapshot"])
        if link_hash != link["content_hash"] or link_length != link["byte_length"]:
            raise MissionControlError(409, "An evidence link failed its integrity check.")
        snapshot = _json_object(link["link_snapshot"], "evidence link")
        lineage = _json_object(link["source_lineage"], "source lineage")
        expected = {
            "link_id": link["link_id"],
            "brief_id": link["brief_id"],
            "field_path": link["field_path"],
            "source_record_id": link["source_record_id"],
            "basis": link["basis"],
            "confidence_reason": link["confidence_reason"],
            "uncertainty": link["uncertainty"],
            "freshness": link["freshness"],
            "currentness": link["currentness"],
            "quote": link["quote"],
            "source_lineage": lineage,
        }
        if snapshot != expected or link["brief_id"] != brief["brief_id"]:
            raise MissionControlError(409, "An evidence link failed its denormalized integrity check.")
        source_id = link["source_record_id"]
        if source_id is None:
            if link["basis"] != "explicit_unknown" or lineage != {
                "source_date": None, "source_id": None, "url": None
            }:
                raise MissionControlError(409, "An evidence link failed its source binding check.")
        else:
            source = sources.get(source_id)
            if source is None or (
                source["enrichment_run_id"] != brief["enrichment_run_id"]
                or source["business_unit"] != brief["business_unit"]
                or lineage != {
                    "source_id": source["source_key"],
                    "url": source["source_url"],
                    "source_date": source["source_date"],
                }
                or link["quote"] != source["quote"]
            ):
                raise MissionControlError(409, "An evidence link failed its source binding check.")
        return dict(link)

    def _validated_brief_details(
        self, connection: sqlite3.Connection, brief: sqlite3.Row
    ) -> dict[str, Any]:
        brief_hash, brief_length = _digest(brief["brief_snapshot"])
        if brief_hash != brief["content_hash"] or brief_length != brief["byte_length"]:
            raise MissionControlError(409, "The campaign brief failed its integrity check.")
        snapshot = _json_object(brief["brief_snapshot"], "campaign brief")
        if (
            snapshot.get("business_unit") != brief["business_unit"]
            or snapshot.get("global_identity_id") != brief["global_identity_id"]
            or snapshot.get("result_id") != brief["result_id"]
            or snapshot.get("approval_event_id") != brief["approval_event_id"]
            or snapshot.get("brief_schema") != brief["brief_schema"]
            or snapshot.get("research_state") != brief["research_state"]
        ):
            raise MissionControlError(409, "The campaign brief binding failed its integrity check.")
        source_rows = connection.execute(
            "SELECT * FROM enrichment_sources WHERE enrichment_run_id=? AND business_unit=? ORDER BY rowid",
            (brief["enrichment_run_id"], brief["business_unit"]),
        ).fetchall()
        link_rows = connection.execute(
            "SELECT * FROM brief_evidence_links WHERE brief_id=? ORDER BY rowid",
            (brief["brief_id"],),
        ).fetchall()
        manifest = connection.execute(
            "SELECT * FROM brief_integrity_manifests WHERE brief_id=? AND enrichment_run_id=?"
            " AND business_unit=?",
            (brief["brief_id"], brief["enrichment_run_id"], brief["business_unit"]),
        ).fetchone()
        if manifest is None:
            raise MissionControlError(409, "The brief integrity manifest is missing.")
        manifest_hash, manifest_length = _digest(manifest["manifest_snapshot"])
        manifest_snapshot = _json_object(manifest["manifest_snapshot"], "brief integrity manifest")
        current_manifest = {
            "manifest_id": manifest["manifest_id"],
            "brief_id": brief["brief_id"],
            "enrichment_run_id": brief["enrichment_run_id"],
            "business_unit": brief["business_unit"],
            "brief_hash": brief["content_hash"],
            "brief_byte_length": brief["byte_length"],
            "sources": [
                {
                    "source_record_id": source["source_record_id"],
                    "source_key": source["source_key"],
                    "content_hash": source["content_hash"],
                    "byte_length": source["byte_length"],
                }
                for source in source_rows
            ],
            "evidence_links": [
                {
                    "link_id": link["link_id"],
                    "field_path": link["field_path"],
                    "source_record_id": link["source_record_id"],
                    "content_hash": link["content_hash"],
                    "byte_length": link["byte_length"],
                }
                for link in link_rows
            ],
        }
        if (
            manifest_hash != manifest["content_hash"]
            or manifest_length != manifest["byte_length"]
            or manifest_snapshot != current_manifest
        ):
            raise MissionControlError(409, "The brief integrity manifest failed its completeness check.")
        source_items = [self._validated_source(source) for source in source_rows]
        source_map = {source["source_record_id"]: source for source in source_items}
        link_items = [
            self._validated_link(link, brief=brief, sources=source_map) for link in link_rows
        ]
        result = dict(brief)
        result.pop("brief_snapshot")
        result["snapshot"] = snapshot
        result["sources"] = source_items
        result["evidence_links"] = link_items
        return result

    def sources(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            briefs = connection.execute(
                "SELECT * FROM campaign_brief_versions WHERE business_unit=? ORDER BY rowid",
                (business_unit,),
            ).fetchall()
            expected_source_ids = {
                source["source_record_id"]
                for brief in briefs
                for source in self._validated_brief_details(connection, brief)["sources"]
            }
            rows = connection.execute(
                "SELECT * FROM enrichment_sources WHERE business_unit=? ORDER BY rowid", (business_unit,)
            ).fetchall()
            result = [self._validated_source(row) for row in rows]
            if {source["source_record_id"] for source in result} != expected_source_ids:
                raise MissionControlError(409, "The brief integrity manifest failed its completeness check.")
        finally:
            connection.close()
        return result

    def get_brief(self, actor: str, business_unit: str, brief_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM campaign_brief_versions WHERE brief_id=? AND business_unit=?",
                (brief_id, business_unit),
            ).fetchone()
            if row is not None:
                result = self._validated_brief_details(connection, row)
        finally:
            connection.close()
        if row is None:
            raise MissionControlError(403, "Business-unit access denied or campaign brief not found.")
        return result

    def _review_context(
        self, connection: sqlite3.Connection, brief_id: str, business_unit: str
    ) -> tuple[sqlite3.Row, list[str]]:
        brief = connection.execute(
            "SELECT cb.*,er.initiating_actor,er.business_unit AS enrichment_business_unit,"
            " pae.run_id AS worker_run_id,pae.proposer_actor AS worker_proposer_actor,"
            " pae.business_unit AS approval_business_unit,rt.business_unit AS task_business_unit,"
            " rt.allowed_reviewers"
            " FROM campaign_brief_versions cb JOIN enrichment_runs er"
            " ON er.enrichment_run_id=cb.enrichment_run_id JOIN prospect_approval_events pae"
            " ON pae.approval_event_id=cb.approval_event_id"
            " JOIN review_tasks rt ON rt.task_id=pae.task_id"
            " WHERE cb.brief_id=? AND cb.business_unit=?", (brief_id, business_unit),
        ).fetchone()
        if brief is None:
            raise MissionControlError(403, "Business-unit access denied or campaign brief not found.")
        if not all((
            brief["initiating_actor"] == brief["worker_proposer_actor"],
            brief["enrichment_business_unit"] == business_unit,
            brief["approval_business_unit"] == business_unit,
            brief["task_business_unit"] == business_unit,
        )):
            raise MissionControlError(409, "The research-review provenance binding is invalid.")
        approval = connection.execute(
            "SELECT * FROM prospect_approval_events WHERE approval_event_id=?",
            (brief["approval_event_id"],),
        ).fetchone()
        if approval is None:
            raise MissionControlError(409, "The research-review approval binding is invalid.")
        self._approval_integrity(
            connection, approval, business_unit, require_current_projection=False
        )
        try:
            allowed_reviewers = json.loads(brief["allowed_reviewers"])
        except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
            raise MissionControlError(409, "The stored reviewer scope is invalid.") from exc
        if (
            not isinstance(allowed_reviewers, list)
            or not allowed_reviewers
            or not all(
                isinstance(reviewer, str)
                and reviewer in ACTOR_SCOPE
                and business_unit in ACTOR_SCOPE[reviewer]
                for reviewer in allowed_reviewers
            )
        ):
            raise MissionControlError(409, "The stored reviewer scope is invalid.")
        return brief, allowed_reviewers

    @staticmethod
    def _validate_brief_review_event(
        row: sqlite3.Row, brief: sqlite3.Row, allowed_reviewers: list[str]
    ) -> None:
        _validate_event_integrity(row, _BRIEF_REVIEW_EVENT_FIELDS, "brief review event")
        effective = _parse_time(row["effective_at"], "brief review effective time")
        recorded = _parse_time(row["recorded_at"], "brief review recorded time")
        try:
            _safe_text(row["reason"], "Stored brief review reason")
        except MissionControlError as exc:
            raise MissionControlError(409, "The brief review metadata failed its integrity check.") from exc
        if (
            row["effective_at"] != _format_time(effective)
            or row["recorded_at"] != _format_time(recorded)
            or effective != recorded
            or row["brief_id"] != brief["brief_id"]
            or row["brief_hash"] != brief["content_hash"]
            or row["brief_byte_length"] != brief["byte_length"]
            or row["business_unit"] != brief["business_unit"]
            or row["proposer_actor"] != brief["worker_proposer_actor"]
            or row["reviewer_actor"] not in allowed_reviewers
            or row["reviewer_actor"] == row["proposer_actor"]
            or row["decision"] not in _REVIEW_DECISIONS
            or row["authority_granted"] != "research_quality_only"
        ):
            raise MissionControlError(409, "The brief review event failed its integrity check.")

    def _validated_brief_reviews(
        self, connection: sqlite3.Connection, brief: sqlite3.Row, allowed_reviewers: list[str]
    ) -> list[sqlite3.Row]:
        rows = connection.execute(
            "SELECT * FROM brief_review_events WHERE brief_id=? ORDER BY rowid",
            (brief["brief_id"],),
        ).fetchall()
        for row in rows:
            self._validate_brief_review_event(row, brief, allowed_reviewers)
        if not rows:
            return []
        by_id = {row["review_event_id"]: row for row in rows}
        roots = [row for row in rows if row["supersedes_id"] is None]
        children: dict[str, sqlite3.Row] = {}
        for row in rows:
            parent_id = row["supersedes_id"]
            if parent_id is None:
                continue
            if parent_id not in by_id or parent_id in children:
                raise MissionControlError(409, "The brief review supersession chain failed its integrity check.")
            children[parent_id] = row
        if len(roots) != 1:
            raise MissionControlError(409, "The brief review supersession chain failed its integrity check.")
        ordered = [roots[0]]
        while ordered[-1]["review_event_id"] in children:
            ordered.append(children[ordered[-1]["review_event_id"]])
            if len(ordered) > len(rows):
                raise MissionControlError(409, "The brief review supersession chain failed its integrity check.")
        if len(ordered) != len(rows):
            raise MissionControlError(409, "The brief review supersession chain failed its integrity check.")
        return ordered

    def review_brief(
        self,
        actor: str,
        business_unit: str,
        brief_id: str,
        *,
        decision: str,
        reason: str,
        expected_leaf_id: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        if decision not in _REVIEW_DECISIONS:
            raise MissionControlError(400, "Unknown research-quality review decision.")
        reason = _safe_text(reason, "Review reason")
        now = self._now()
        with self.store.transaction() as connection:
            brief, allowed_reviewers = self._review_context(connection, brief_id, business_unit)
            if actor == brief["worker_proposer_actor"]:
                raise MissionControlError(403, "The proposing actor cannot self-review this research brief.")
            if actor not in allowed_reviewers:
                raise MissionControlError(403, "Actor is not an allowed reviewer for this research brief.")
            details = self._validated_brief_details(connection, brief)
            if decision == "accepted":
                latest_version = connection.execute(
                    "SELECT MAX(version) FROM campaign_brief_versions WHERE brief_family_id=?"
                    " AND business_unit=?",
                    (brief["brief_family_id"], business_unit),
                ).fetchone()[0]
                if brief["version"] != latest_version:
                    raise MissionControlError(409, "Only the latest brief version may be accepted.")
                research = details["snapshot"].get("research")
                unresolved = details["snapshot"].get("unresolved_questions")
                if brief["research_state"] != "ready_for_research_quality_review":
                    raise MissionControlError(409, "The brief is not ready for research-quality acceptance.")
                if (
                    not isinstance(research, dict) or set(research) != set(REQUIRED_BRIEF_FIELDS)
                    or unresolved != []
                    or any(
                        not isinstance(value, dict) or value.get("conflict_state") != "none"
                        for value in research.values()
                    )
                ):
                    raise MissionControlError(409, "The brief has an unresolved conflict entry.")
            reviews = self._validated_brief_reviews(connection, brief, allowed_reviewers)
            leaf = reviews[-1] if reviews else None
            expected = expected_leaf_id or None
            if (leaf is None and expected is not None) or (leaf is not None and leaf["review_event_id"] != expected):
                raise MissionControlError(409, "The expected brief review is not the current leaf.")
            event_id = self.store.next_id(connection, "breview")
            correlation_id = self.store.next_id(connection, "bcorr")
            event = {
                "review_event_id": event_id,
                "brief_id": brief_id,
                "brief_hash": brief["content_hash"],
                "brief_byte_length": brief["byte_length"],
                "business_unit": business_unit,
                "decision": decision,
                "proposer_actor": brief["worker_proposer_actor"],
                "reviewer_actor": actor,
                "reason": reason,
                "effective_at": now,
                "recorded_at": now,
                "supersedes_id": expected,
                "authority_granted": "research_quality_only",
                "audit_correlation_id": correlation_id,
            }
            event_raw = canonical_json(event, limit=16_384, label="Brief review event")
            event_hash, event_length = _digest(event_raw)
            connection.execute(
                f"INSERT INTO brief_review_events ({','.join(_BRIEF_REVIEW_EVENT_FIELDS)},"
                "event_snapshot,content_hash,byte_length) VALUES ("
                f"{','.join('?' for _ in range(len(_BRIEF_REVIEW_EVENT_FIELDS) + 3))})",
                tuple(event[field] for field in _BRIEF_REVIEW_EVENT_FIELDS)
                + (event_raw, event_hash, event_length),
            )
            self.store.insert_audit(
                connection, run_id=brief["worker_run_id"], event_type="phase6a_brief_review_recorded",
                actor=actor, business_unit=business_unit, correlation_id=correlation_id,
                safe_status=f"{decision}:{event_id}", recorded_at=now,
            )
        return next(
            item for item in self.brief_reviews(actor, business_unit, brief_id)
            if item["review_event_id"] == event_id
        )

    def brief_reviews(
        self, actor: str, business_unit: str, brief_id: str
    ) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            brief, allowed_reviewers = self._review_context(connection, brief_id, business_unit)
            self._validated_brief_details(connection, brief)
            rows = self._validated_brief_reviews(connection, brief, allowed_reviewers)
        finally:
            connection.close()
        return [dict(row) for row in rows]
