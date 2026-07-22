"""Durable Phase 6 dossier runtime and local graph-ready release boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .application import ACTOR_SCOPE, BUSINESS_UNITS, MissionControlError
from .enrichment import EnrichmentService
from .store import MAX_SNAPSHOT_BYTES, SqliteStore, canonical_json

# application.py installs the shared deterministic core on sys.path.
from common import ValidationError  # noqa: E402
from classify_duplicate import classify_duplicate  # noqa: E402
from normalize_domain import normalize_domain  # noqa: E402
from validate_phase6b_contract import (  # noqa: E402
    CATEGORIES,
    ContractConflict,
    SOURCE_KINDS,
    build_lead_intelligence_package,
    filter_candidates,
    load_json_strict,
    validate_customer_dossier,
    validate_fixture_bundle,
    validate_lead_intelligence_package,
    validate_search_request,
)
from validate_phase6_real_contract import (  # noqa: E402
    build_real_customer_dossier,
    build_real_lead_intelligence_package,
    build_real_result_projection,
    derive_real_result_id,
    load_real_source_manifest,
    validate_real_customer_dossier,
    validate_real_lead_intelligence_package,
    validate_real_research_bundle,
    validate_real_result_projection,
    validate_real_source_plan,
)

ACCOUNT_IDENTITY_VERSION = "account-v1"
DOSSIER_APPROVAL_SCOPE = "phase6_dossier_research"
REAL_GOAL_AUTHORITY = "user-goal-authority"
REAL_HUMAN_REVIEWER = "user-human-reviewer"
REAL_NO_RETRY_FAILURES = {"candidate_creation_failed", "source_read_failed_no_retry"}
APPROVAL_LIFETIME = timedelta(days=7)
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
TERMINAL_REVIEW_DECISIONS = {"accepted", "changes_requested", "rejected"}
APPROVAL_DECISIONS = {"approved", "rejected", "revoked", "invalidated"}
BUSINESS_UNIT_ID_TAGS = {
    "unreal-media-group": "umg",
    "unreal-talent": "talent",
}
DOSSIER_APPROVAL_EVENT_FIELDS = (
    "approval_event_id",
    "result_record_id",
    "search_id",
    "result_id",
    "global_identity_id",
    "account_id",
    "account_identity_version",
    "canonical_domain",
    "business_unit",
    "result_hash",
    "result_byte_length",
    "history_hash",
    "request_hash",
    "source_plan_snapshot",
    "source_plan_hash",
    "source_plan_byte_length",
    "scope",
    "decision",
    "proposer_actor",
    "reviewer_actor",
    "reason",
    "effective_at",
    "recorded_at",
    "expires_at",
    "supersedes_id",
    "audit_correlation_id",
)
DOSSIER_REVIEW_EVENT_FIELDS = (
    "review_event_id",
    "candidate_version_id",
    "idempotency_key",
    "request_fingerprint",
    "candidate_hash",
    "candidate_byte_length",
    "business_unit",
    "decision",
    "proposer_actor",
    "reviewer_actor",
    "reason",
    "recorded_at",
    "audit_correlation_id",
)


def _canonical(value: Any, *, label: str, limit: int = MAX_SNAPSHOT_BYTES) -> tuple[str, str, int]:
    raw = canonical_json(value, limit=limit, label=label)
    encoded = raw.encode("utf-8")
    return raw, hashlib.sha256(encoded).hexdigest(), len(encoded)


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MissionControlError(500, "The Phase 6 clock must return a timezone-aware value.")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise MissionControlError(409, f"The stored {label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise MissionControlError(409, f"The stored {label} is invalid.")
    parsed = parsed.astimezone(timezone.utc)
    if value != _format_time(parsed):
        raise MissionControlError(409, f"The stored {label} is not canonical UTC.")
    return parsed


def _bounded_text(value: Any, label: str, *, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MissionControlError(400, f"{label} must be non-empty and at most {maximum} characters.")
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise MissionControlError(400, f"{label} contains unsupported control characters.")
    return value.strip()


def _idempotency(value: str) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY.fullmatch(value):
        raise MissionControlError(400, "A bounded alphanumeric idempotency key is required.")
    return value


def derive_account_id(canonical_domain: str, global_identity_id: str) -> str:
    """Derive the only authoritative account identity used by Phase 6."""
    if not isinstance(canonical_domain, str) or normalize_domain(canonical_domain) != canonical_domain:
        raise MissionControlError(409, "The canonical domain is not normalized.")
    if not isinstance(global_identity_id, str) or not IDENTIFIER.fullmatch(global_identity_id):
        raise MissionControlError(409, "The global identity is invalid.")
    payload = json.dumps(
        {"canonical_domain": canonical_domain, "global_identity_id": global_identity_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{ACCOUNT_IDENTITY_VERSION}-{hashlib.sha256(payload).hexdigest()[:24]}"


class DossierService:
    """History-first search, exact approval, candidate review, and inert release."""

    _active_lock = threading.RLock()
    _active_runs: dict[str, set[str]] = {}

    def __init__(
        self,
        enrichment: EnrichmentService,
        *,
        fixture_path: Path,
        history_path: Path,
        clock: Callable[[], datetime],
        real_manifest_path: Path | None = None,
        public_reader: Any | None = None,
    ):
        self.enrichment = enrichment
        self.store: SqliteStore = enrichment.store
        self.clock = clock
        self.fixture_path = Path(fixture_path)
        self.history_path = Path(history_path)
        self.real_manifest_path = Path(real_manifest_path) if real_manifest_path else None
        self.public_reader = public_reader
        self._active_key = str(self.store.path.resolve())
        self.store.initialize_phase6_dossier()
        try:
            self.fixture_bundle = load_json_strict(self.fixture_path, "Phase 6 dossier fixture")
            self.history_bundle = load_json_strict(self.history_path, "Phase 6 dossier history")
            validate_fixture_bundle(self.fixture_bundle, self.history_bundle)
            self.real_manifest = (
                load_real_source_manifest(self.real_manifest_path)
                if self.real_manifest_path is not None
                else None
            )
        except ValidationError as exc:
            raise MissionControlError(500, "The repository-owned Phase 6 dossier fixtures are invalid.") from exc
        self.real_plans = {
            item["source_plan_id"]: copy.deepcopy(item)
            for item in (self.real_manifest or {}).get("plans", [])
        }
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

    def _next_id(
        self, connection: sqlite3.Connection, prefix: str, business_unit: str
    ) -> str:
        return self.store.next_id(
            connection, f"{prefix}-{BUSINESS_UNIT_ID_TAGS[business_unit]}"
        )

    @staticmethod
    def _authorize(actor: str, business_unit: str) -> None:
        if actor not in ACTOR_SCOPE:
            raise MissionControlError(403, "Actor is not allowed in this local review surface.")
        if business_unit not in BUSINESS_UNITS:
            raise MissionControlError(400, "Unknown business unit.")
        if business_unit not in ACTOR_SCOPE[actor]:
            raise MissionControlError(403, "Business-unit access denied.")

    def _recover_interrupted(self) -> list[str]:
        now = self._now()
        recovered: list[str] = []
        with self._active_lock:
            active = self._active_snapshot()
            with self.store.transaction() as connection:
                rows = connection.execute(
                    "SELECT dossier_run_id,business_unit,initiating_actor,audit_correlation_id "
                    "FROM dossier_runs WHERE state='running' ORDER BY dossier_run_id"
                ).fetchall()
                for row in rows:
                    if row["dossier_run_id"] in active:
                        continue
                    connection.execute(
                        "UPDATE dossier_runs SET state='failed',completed_at=?,failure_class=?,remediation=? "
                        "WHERE dossier_run_id=? AND state='running'",
                        (
                            now,
                            "interrupted_execution_recovered",
                            "Create a new exact approval before another research attempt.",
                            row["dossier_run_id"],
                        ),
                    )
                    self.store.insert_audit(
                        connection,
                        run_id=row["dossier_run_id"],
                        event_type="phase6_dossier_interrupted_run_recovered",
                        actor="startup_recovery",
                        business_unit=row["business_unit"],
                        correlation_id=row["audit_correlation_id"],
                        safe_status="failed_closed",
                        recorded_at=now,
                    )
                    recovered.append(row["dossier_run_id"])
        return recovered

    def _template_request(self, business_unit: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(next(
                item for item in self.fixture_bundle["search_requests"]
                if item["business_unit"] == business_unit
            ))
        except StopIteration as exc:
            raise MissionControlError(500, "The business unit has no repository-owned Phase 6 search route.") from exc

    def _history(
        self,
        connection: sqlite3.Connection,
        business_unit: str,
        *,
        generated_at: str,
        include_real: bool = False,
    ) -> dict[str, Any]:
        """Project prior durable search outcomes into the next immutable history snapshot."""
        history = copy.deepcopy(self.history_bundle["histories"][business_unit])
        by_domain = {
            record["canonical_domain"]: record for record in history["prospects"]
        }
        history_ids = {record["id"] for record in history["prospects"]}
        rows = connection.execute(
            "SELECT result.*,search.request_snapshot,search.request_hash,search.request_byte_length "
            "FROM dossier_results result JOIN dossier_searches search "
            "ON search.search_id=result.search_id AND search.business_unit=result.business_unit "
            "WHERE result.business_unit=? ORDER BY result.created_at,result.result_record_id",
            (business_unit,),
        ).fetchall()
        for row in rows:
            request_raw = row["request_snapshot"]
            request_encoded = request_raw.encode("utf-8")
            if (
                hashlib.sha256(request_encoded).hexdigest() != row["request_hash"]
                or len(request_encoded) != row["request_byte_length"]
            ):
                raise MissionControlError(409, "The durable search request failed its integrity check.")
            request = self._decode(request_raw, "search request")
            if request.get("synthetic") is False:
                if not include_real:
                    continue
                self._require_exact_real_search_request(request, business_unit)
            result = self._result_row(row)
            candidate = result["candidate"]
            decision = result["decision"]
            domain = result["canonical_domain"]
            record = by_domain.get(domain)
            if record is None:
                history_id = "history-runtime-" + result["global_identity_id"]
                if history_id in history_ids:
                    raise MissionControlError(409, "The durable history identity is ambiguous.")
                record = {
                    "id": history_id,
                    "canonical_company_name": candidate["company_name"],
                    "canonical_domain": domain,
                    "domains": [domain],
                    "alternate_names": [],
                    "social_handles": copy.deepcopy(candidate.get("social_handles") or {}),
                    "previous_discovery_dates": [],
                    "previous_campaign_appearances": [],
                    "previous_scores": [],
                    "previous_decisions": [],
                    "outreach_status": "none",
                    "client_status": "not_client",
                    "partner_status": "not_partner",
                    "suppressed": False,
                    "prior_signals": [],
                    "prior_rejection_reasons": [],
                }
                history["prospects"].append(record)
                by_domain[domain] = record
                history_ids.add(history_id)
            discovered = row["created_at"][:10]
            campaign_name = request["campaign"]["campaign_name"]
            score = candidate["qualification_score"]
            outcome = (
                f"{decision['history_classification']['status']}:"
                f"{decision['filter_state']}:{decision['qualification_state']}:"
                f"{'selected' if decision['selected'] else 'not_selected'}"
            )
            for key, value in (
                ("previous_discovery_dates", discovered),
                ("previous_campaign_appearances", campaign_name),
                ("previous_scores", score),
                ("previous_decisions", outcome),
            ):
                if value not in record[key]:
                    record[key].append(value)
            trigger = candidate.get("new_trigger")
            if isinstance(trigger, dict):
                signal = {
                    "signal_type": "reengagement_trigger",
                    "description": trigger["reason"],
                    "source_url": trigger["source_url"],
                    "source_date": trigger["source_date"],
                }
                if signal not in record["prior_signals"]:
                    record["prior_signals"].append(signal)
        history["generated_at"] = generated_at
        return history

    def _candidates(self, business_unit: str) -> list[dict[str, Any]]:
        values = [
            copy.deepcopy(item) for item in self.fixture_bundle["candidates"]
            if item["business_unit"] == business_unit
        ]
        for item in values:
            item["account_id"] = derive_account_id(item["domain"], item["global_identity_id"])
        return values

    def _template_dossier(self, result_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(next(
            (
                item for item in self.fixture_bundle["dossiers"]
                if item["approved_result"]["result_id"] == result_id
            ),
            None,
        ))

    def _candidate_fixture(self, result_id: str, business_unit: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(next(
                item for item in self.fixture_bundle["candidates"]
                if item["result_id"] == result_id and item["business_unit"] == business_unit
            ))
        except StopIteration as exc:
            raise MissionControlError(409, "The selected result has no bounded candidate fixture.") from exc

    @staticmethod
    def _decode(raw: str, label: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError, UnicodeError) as exc:
            raise MissionControlError(409, f"The stored {label} failed its integrity check.") from exc
        if not isinstance(value, dict):
            raise MissionControlError(409, f"The stored {label} failed its integrity check.")
        return value

    @staticmethod
    def _check_snapshot(row: sqlite3.Row, prefix: str, label: str) -> dict[str, Any]:
        raw = row[f"{prefix}_snapshot"]
        encoded = raw.encode("utf-8")
        if (
            hashlib.sha256(encoded).hexdigest() != row[f"{prefix}_hash"]
            or len(encoded) != row[f"{prefix}_byte_length"]
        ):
            raise MissionControlError(409, f"The stored {label} failed its integrity check.")
        return DossierService._decode(raw, label)

    def _real_plan_for_id(self, source_plan_id: str) -> dict[str, Any]:
        plan = self.real_plans.get(source_plan_id)
        if plan is None:
            raise MissionControlError(409, "The exact authorized real source plan is unavailable.")
        try:
            validate_real_source_plan(plan)
        except ValidationError as exc:
            raise MissionControlError(409, "The exact authorized real source plan is invalid.") from exc
        return copy.deepcopy(plan)

    def _real_plan_for_result(self, result_id: str, business_unit: str) -> dict[str, Any]:
        matches = [
            plan for plan in self.real_plans.values()
            if plan["business_unit"] == business_unit and derive_real_result_id(plan) == result_id
        ]
        if len(matches) != 1:
            raise MissionControlError(409, "The real result does not resolve to one exact authorized source plan.")
        return copy.deepcopy(matches[0])

    def _real_search_request(self, business_unit: str) -> dict[str, Any]:
        plans = [
            copy.deepcopy(plan) for plan in self.real_plans.values()
            if plan["business_unit"] == business_unit
        ]
        if business_unit != "unreal-media-group" or len(plans) != 2:
            raise MissionControlError(409, "No exact real-proof search route is authorized for this business unit.")
        filters = {json.dumps(plan["opportunity_filter"], sort_keys=True) for plan in plans}
        if len(filters) != 1:
            raise MissionControlError(409, "The exact real-proof filters are inconsistent.")
        return {
            "contract_version": 2,
            "synthetic": False,
            "request_id": "search-real-live-proof-v1",
            "idempotency_identity": "phase6-real:search:umg:live-proof-v1",
            "business_unit": business_unit,
            "campaign": {
                "business_unit": business_unit,
                "campaign_name": "Authorized South Florida Product Creative Live Proof",
                "reengagement_enabled": False,
                "cooldown_days": 120,
                "maximum_evidence_age_days": 365,
            },
            "opportunity_filter": copy.deepcopy(plans[0]["opportunity_filter"]),
            "source_plan_ids": [plan["source_plan_id"] for plan in plans],
        }

    def _require_exact_real_search_request(
        self, request: dict[str, Any], business_unit: str
    ) -> None:
        if request != self._real_search_request(business_unit):
            raise MissionControlError(409, "The exact real-proof search request changed.")

    def _prior_real_target_blocker(
        self,
        connection: sqlite3.Connection,
        search_id: str,
        source_plan_id: str,
        *,
        now: datetime,
    ) -> str | None:
        search = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=?", (search_id,)
        ).fetchone()
        if search is None:
            raise MissionControlError(409, "The exact real-proof search is unavailable.")
        request = self._check_snapshot(search, "request", "search request")
        self._require_exact_real_search_request(request, search["business_unit"])
        plan_ids = request.get("source_plan_ids")
        if request.get("synthetic") is not False or not isinstance(plan_ids, list):
            raise MissionControlError(409, "The exact real-proof search ordering is invalid.")
        try:
            target_index = plan_ids.index(source_plan_id)
        except ValueError as exc:
            raise MissionControlError(409, "The real source plan is not in its exact search order.") from exc
        for prior_plan_id in plan_ids[:target_index]:
            prior_plan = self._real_plan_for_id(prior_plan_id)
            prior_result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, derive_real_result_id(prior_plan), search["business_unit"]),
            ).fetchone()
            if prior_result is None:
                raise MissionControlError(409, "An earlier exact real-proof result is unavailable.")
            projected = self._result_row(prior_result)
            if not projected["selected"]:
                continue
            active_run = connection.execute(
                "SELECT dossier_run_id FROM dossier_runs WHERE result_record_id=? AND state='running'",
                (prior_result["result_record_id"],),
            ).fetchone()
            if active_run is not None:
                return "An earlier eligible real-proof target is still running."
            leaf = self._approval_leaf(connection, prior_result["result_record_id"])
            if leaf is None:
                return "An earlier eligible real-proof target must become terminal before this target."
            self._approval_integrity(connection, leaf, require_leaf=True)
            run = connection.execute(
                "SELECT * FROM dossier_runs WHERE approval_event_id=?",
                (leaf["approval_event_id"],),
            ).fetchone()
            if run is not None:
                if not self._verified_run_transition(connection, run):
                    return "An earlier eligible real-proof target has inconsistent transition evidence."
                state = self._run_row(run)["state"]
                if state == "running":
                    return "An earlier eligible real-proof target is still running."
                continue
            if leaf["decision"] in {"rejected", "revoked", "invalidated"}:
                continue
            if now >= _parse_time(leaf["expires_at"], "approval expiry"):
                continue
            return "An earlier eligible real-proof target remains executable and must run first."
        return None

    def _later_real_target_progressed(
        self,
        connection: sqlite3.Connection,
        search_id: str,
        source_plan_id: str,
    ) -> bool:
        search = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=?", (search_id,)
        ).fetchone()
        if search is None:
            raise MissionControlError(409, "The exact real-proof search is unavailable.")
        request = self._check_snapshot(search, "request", "search request")
        self._require_exact_real_search_request(request, search["business_unit"])
        plan_ids = request["source_plan_ids"]
        try:
            target_index = plan_ids.index(source_plan_id)
        except ValueError as exc:
            raise MissionControlError(409, "The real source plan is not in its exact search order.") from exc
        for later_plan_id in plan_ids[target_index + 1:]:
            later_plan = self._real_plan_for_id(later_plan_id)
            later_result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, derive_real_result_id(later_plan), search["business_unit"]),
            ).fetchone()
            if later_result is None:
                raise MissionControlError(409, "A later exact real-proof result is unavailable.")
            projected = self._result_row(later_result)
            if not projected["selected"]:
                continue
            progressed = connection.execute(
                "SELECT 1 FROM dossier_approval_events WHERE result_record_id=? LIMIT 1",
                (later_result["result_record_id"],),
            ).fetchone()
            if progressed is not None:
                return True
        return False

    @staticmethod
    def _real_result_has_no_retry_failure(
        connection: sqlite3.Connection, result_record_id: str
    ) -> bool:
        return connection.execute(
            "SELECT 1 FROM dossier_runs WHERE result_record_id=? AND state='failed' "
            "AND failure_class IN (?,?) LIMIT 1",
            (result_record_id, *sorted(REAL_NO_RETRY_FAILURES)),
        ).fetchone() is not None

    @staticmethod
    def _verified_run_transition(
        connection: sqlite3.Connection, run: sqlite3.Row
    ) -> bool:
        """Bind a mutable run projection to its append-only transition evidence."""
        try:
            DossierService._run_row(run)
        except MissionControlError:
            return False
        events = connection.execute(
            "SELECT * FROM audit_events WHERE run_id=? ORDER BY rowid",
            (run["dossier_run_id"],),
        ).fetchall()
        if not events:
            return False
        claimed = events[0]
        claimed_valid = bool(
            claimed["event_type"] == "phase6_dossier_claimed"
            and claimed["actor"] == run["initiating_actor"]
            and claimed["business_unit"] == run["business_unit"]
            and claimed["correlation_id"] == run["audit_correlation_id"]
            and claimed["safe_status"] == "running"
            and claimed["recorded_at"] == run["created_at"]
        )
        if not claimed_valid:
            return False
        if run["state"] == "running":
            return len(events) == 1
        if len(events) < 2:
            return False
        terminal = events[1]
        common_terminal = bool(
            terminal["business_unit"] == run["business_unit"]
            and terminal["correlation_id"] == run["audit_correlation_id"]
            and terminal["recorded_at"] == run["completed_at"]
        )
        if run["state"] == "cancelled":
            return bool(
                len(events) == 2
                and common_terminal
                and terminal["event_type"] == "phase6_dossier_cancelled"
                and terminal["actor"] == run["initiating_actor"]
                and terminal["safe_status"] == "cancelled"
            )
        if run["state"] == "failed":
            if run["failure_class"] == "interrupted_execution_recovered":
                return bool(
                    len(events) == 2
                    and common_terminal
                    and terminal["event_type"] == "phase6_dossier_interrupted_run_recovered"
                    and terminal["actor"] == "startup_recovery"
                    and terminal["safe_status"] == "failed_closed"
                )
            return bool(
                len(events) == 2
                and common_terminal
                and terminal["event_type"] == "phase6_dossier_failed"
                and terminal["actor"] == run["initiating_actor"]
                and terminal["safe_status"] == "failed"
            )
        candidate = connection.execute(
            "SELECT 1 FROM dossier_candidates WHERE candidate_version_id=? "
            "AND dossier_run_id=? AND approval_event_id=? AND business_unit=?",
            (
                run["candidate_version_id"],
                run["dossier_run_id"],
                run["approval_event_id"],
                run["business_unit"],
            ),
        ).fetchone()
        return bool(
            candidate is not None
            and common_terminal
            and terminal["event_type"] == "phase6_dossier_candidate_created"
            and terminal["actor"] == run["initiating_actor"]
            and terminal["safe_status"] == "pending_research_quality_review"
            and all(
                event["event_type"] == "phase6_dossier_terminal_review_recorded"
                for event in events[2:]
            )
            and len(events) <= 3
        )

    @staticmethod
    def _verified_interrupted_run(
        connection: sqlite3.Connection, run: sqlite3.Row
    ) -> bool:
        """Verify startup recovery and absence of any prior target artifact."""
        return bool(
            DossierService._verified_run_transition(connection, run)
            and run["state"] == "failed"
            and run["failure_class"] == "interrupted_execution_recovered"
            and run["candidate_version_id"] is None
            and connection.execute(
                "SELECT 1 FROM dossier_candidates WHERE result_id=? AND business_unit=? LIMIT 1",
                (run["result_id"], run["business_unit"]),
            ).fetchone() is None
        )

    @staticmethod
    def _real_attempt_allowed(
        connection: sqlite3.Connection,
        result_record_id: str,
        *,
        for_new_authority: bool,
        approval_event_id: str | None = None,
        decision: str | None = None,
    ) -> bool:
        runs = connection.execute(
            "SELECT rowid AS run_rowid,* FROM dossier_runs "
            "WHERE result_record_id=? ORDER BY rowid",
            (result_record_id,),
        ).fetchall()
        if any(
            not DossierService._verified_run_transition(connection, run)
            for run in runs
        ):
            return False
        failed = [run for run in runs if run["state"] == "failed"]
        interrupted = [
            run for run in runs
            if run["failure_class"] == "interrupted_execution_recovered"
        ]
        if failed and (len(failed) != 1 or failed != interrupted):
            return False
        if not failed:
            return True
        if len(interrupted) != 1 or not DossierService._verified_interrupted_run(
            connection, interrupted[0]
        ):
            return False
        if runs[0]["run_rowid"] != interrupted[0]["run_rowid"]:
            return False
        original_approval = connection.execute(
            "SELECT rowid FROM dossier_approval_events WHERE approval_event_id=?",
            (interrupted[0]["approval_event_id"],),
        ).fetchone()
        if original_approval is None:
            return False
        recovery_approvals = connection.execute(
            "SELECT * FROM dossier_approval_events WHERE result_record_id=? "
            "AND rowid>? AND decision='approved' ORDER BY rowid",
            (result_record_id, original_approval["rowid"]),
        ).fetchall()
        recovery_runs = [
            run for run in runs
            if run["run_rowid"] > interrupted[0]["run_rowid"]
        ]
        if for_new_authority:
            return decision != "approved" or not recovery_approvals and not recovery_runs
        return bool(
            approval_event_id
            and len(recovery_approvals) == 1
            and recovery_approvals[0]["approval_event_id"] == approval_event_id
            and not recovery_runs
        )

    @staticmethod
    def _real_recovery_authority_pending(
        connection: sqlite3.Connection, result_record_id: str
    ) -> bool:
        runs = connection.execute(
            "SELECT rowid AS run_rowid,* FROM dossier_runs "
            "WHERE result_record_id=? ORDER BY rowid",
            (result_record_id,),
        ).fetchall()
        interrupted = [
            run for run in runs
            if run["failure_class"] == "interrupted_execution_recovered"
        ]
        if len(interrupted) != 1 or not DossierService._verified_interrupted_run(
            connection, interrupted[0]
        ):
            return False
        return DossierService._real_attempt_allowed(
            connection,
            result_record_id,
            for_new_authority=True,
            decision="approved",
        )

    def _real_infrastructure_recovery_ready(
        self,
        connection: sqlite3.Connection,
        search_id: str,
        source_plan_id: str,
        *,
        for_new_authority: bool,
    ) -> bool:
        """Permit one earlier retry only after an interrupted run and terminal alternatives."""
        search = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=?", (search_id,)
        ).fetchone()
        if search is None:
            return False
        request = self._check_snapshot(search, "request", "search request")
        self._require_exact_real_search_request(request, search["business_unit"])
        plan_ids = request["source_plan_ids"]
        try:
            target_index = plan_ids.index(source_plan_id)
        except ValueError:
            return False
        target_plan = self._real_plan_for_id(source_plan_id)
        target_result = connection.execute(
            "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
            (search_id, derive_real_result_id(target_plan), search["business_unit"]),
        ).fetchone()
        if target_result is None:
            return False
        target_runs = connection.execute(
            "SELECT rowid AS run_rowid,* FROM dossier_runs "
            "WHERE result_record_id=? ORDER BY rowid",
            (target_result["result_record_id"],),
        ).fetchall()
        if any(
            not self._verified_run_transition(connection, run)
            for run in target_runs
        ):
            return False
        interrupted = [
            run for run in target_runs
            if run["failure_class"] == "interrupted_execution_recovered"
        ]
        if len(interrupted) != 1 or not self._verified_interrupted_run(
            connection, interrupted[0]
        ):
            return False
        if target_runs[0]["run_rowid"] != interrupted[0]["run_rowid"]:
            return False
        original_approval_id = interrupted[0]["approval_event_id"]
        original_approval = connection.execute(
            "SELECT rowid FROM dossier_approval_events WHERE approval_event_id=?",
            (original_approval_id,),
        ).fetchone()
        if original_approval is None:
            return False
        recovery_approvals = connection.execute(
            "SELECT * FROM dossier_approval_events WHERE result_record_id=? "
            "AND rowid>? AND decision='approved' ORDER BY rowid",
            (target_result["result_record_id"], original_approval["rowid"]),
        ).fetchall()
        recovery_runs = [
            run for run in target_runs
            if run["run_rowid"] > interrupted[0]["run_rowid"]
        ]
        if for_new_authority:
            if recovery_approvals or recovery_runs:
                return False
        elif (
            len(recovery_approvals) != 1
            or recovery_approvals[0]["decision"] != "approved"
            or len(recovery_runs) > 1
            or (
                recovery_runs
                and recovery_runs[0]["approval_event_id"]
                != recovery_approvals[0]["approval_event_id"]
            )
        ):
            return False
        for later_plan_id in plan_ids[target_index + 1:]:
            later_plan = self._real_plan_for_id(later_plan_id)
            later_result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, derive_real_result_id(later_plan), search["business_unit"]),
            ).fetchone()
            if later_result is None:
                return False
            projected = self._result_row(later_result)
            if not projected["selected"]:
                continue
            if connection.execute(
                "SELECT 1 FROM dossier_candidates WHERE result_id=? AND business_unit=? LIMIT 1",
                (later_result["result_id"], search["business_unit"]),
            ).fetchone() is not None:
                return False
            later_approvals = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE result_record_id=?",
                (later_result["result_record_id"],),
            ).fetchall()
            if not later_approvals:
                return False
            for approval in later_approvals:
                run = connection.execute(
                    "SELECT * FROM dossier_runs WHERE approval_event_id=?",
                    (approval["approval_event_id"],),
                ).fetchone()
                if run is not None:
                    if not self._verified_run_transition(connection, run):
                        return False
                    if self._run_row(run)["state"] not in {"failed", "cancelled"}:
                        return False
                if approval["decision"] == "approved" and run is None:
                    return False
        return True

    def _require_no_later_real_target_progress(
        self,
        connection: sqlite3.Connection,
        search_id: str,
        source_plan_id: str,
        *,
        for_new_authority: bool = False,
    ) -> None:
        if (
            self._later_real_target_progressed(connection, search_id, source_plan_id)
            and not self._real_infrastructure_recovery_ready(
                connection,
                search_id,
                source_plan_id,
                for_new_authority=for_new_authority,
            )
        ):
            raise MissionControlError(
                409,
                "A later eligible real-proof target already entered its authority flow; earlier targets cannot be reopened.",
            )

    def _require_prior_real_targets_terminal(
        self,
        connection: sqlite3.Connection,
        search_id: str,
        source_plan_id: str,
        *,
        now: datetime,
    ) -> None:
        blocker = self._prior_real_target_blocker(
            connection, search_id, source_plan_id, now=now
        )
        if blocker is not None:
            raise MissionControlError(409, blocker)

    def real_goal_authority_ready(
        self,
        actor: str,
        business_unit: str,
        search_id: str,
        result_id: str,
    ) -> bool:
        """Return whether target ordering permits binding this exact real result now."""
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            search = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
                (search_id, business_unit),
            ).fetchone()
            result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, result_id, business_unit),
            ).fetchone()
            if search is None or result is None:
                raise MissionControlError(403, "Business-unit access denied or real result not found.")
            if search["initiating_actor"] != actor:
                return False
            projected = self._result_row(result)
            if projected["candidate"].get("synthetic") is not False or not projected["selected"]:
                return False
            if self._real_result_has_no_retry_failure(
                connection, result["result_record_id"]
            ):
                return False
            leaf = self._approval_leaf(connection, result["result_record_id"])
            claim_ready = False
            if leaf is not None:
                consumed = connection.execute(
                    "SELECT 1 FROM dossier_runs WHERE approval_event_id=?",
                    (leaf["approval_event_id"],),
                ).fetchone() is not None
                now = self._now_dt()
                claim_ready = bool(
                    leaf["decision"] == "approved"
                    and not consumed
                    and _parse_time(leaf["recorded_at"], "approval recorded time") <= now
                    and _parse_time(leaf["effective_at"], "approval effective time") <= now
                    and now < _parse_time(leaf["expires_at"], "approval expiry")
                    and self._real_attempt_allowed(
                        connection,
                        result["result_record_id"],
                        for_new_authority=False,
                        approval_event_id=leaf["approval_event_id"],
                    )
                )
            authority_ready = self._real_attempt_allowed(
                connection,
                result["result_record_id"],
                for_new_authority=True,
                decision="approved",
            )
            plan = self._real_plan_for_result(result_id, business_unit)
            later_progressed = self._later_real_target_progressed(
                connection, search_id, plan["source_plan_id"]
            )
            return self._prior_real_target_blocker(
                connection, search_id, plan["source_plan_id"], now=self._now_dt()
            ) is None and (claim_ready or authority_ready) and (
                not later_progressed
                or (
                    authority_ready
                    and self._real_infrastructure_recovery_ready(
                        connection,
                        search_id,
                        plan["source_plan_id"],
                        for_new_authority=True,
                    )
                )
                or (
                    claim_ready
                    and self._real_infrastructure_recovery_ready(
                        connection,
                        search_id,
                        plan["source_plan_id"],
                        for_new_authority=False,
                    )
                )
            )
        finally:
            connection.close()

    def real_goal_authority_requires_recovery(
        self,
        actor: str,
        business_unit: str,
        search_id: str,
        result_id: str,
    ) -> bool:
        """Return whether the next executable authority is the one recovery."""
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, result_id, business_unit),
            ).fetchone()
            return bool(
                result is not None
                and self._real_recovery_authority_pending(
                    connection, result["result_record_id"]
                )
            )
        finally:
            connection.close()

    def create_search(
        self,
        actor: str,
        business_unit: str,
        *,
        include_any: list[str] | None,
        exclude: list[str] | None,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        self._authorize(actor, business_unit)
        key = _idempotency(idempotency_key)
        request = self._template_request(business_unit)
        if include_any or exclude:
            request["opportunity_filter"] = {}
            if include_any:
                request["opportunity_filter"]["include_any"] = list(include_any)
            if exclude:
                request["opportunity_filter"]["exclude"] = list(exclude)
        else:
            request.pop("opportunity_filter", None)
        try:
            validate_search_request(request)
            candidates = self._candidates(business_unit)
        except ValidationError as exc:
            raise MissionControlError(400, str(exc)) from exc
        request_raw, request_hash, request_length = _canonical(request, label="Phase 6 search request")
        fingerprint = _fingerprint({
            "actor": actor,
            "business_unit": business_unit,
            "request_hash": request_hash,
        })
        now_dt = self._now_dt()
        now = _format_time(now_dt)
        by_result = {item["result_id"]: item for item in candidates}
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM dossier_searches WHERE idempotency_key=? AND business_unit=?",
                (key, business_unit),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise MissionControlError(409, "Idempotency key was already used with different search input.")
                return self._search_row(connection, existing), False
            try:
                history = self._history(connection, business_unit, generated_at=now)
                evaluation = filter_candidates(
                    request,
                    candidates,
                    history,
                    today=now_dt.date(),
                )
            except ValidationError as exc:
                raise MissionControlError(400, str(exc)) from exc
            history_raw, history_hash, history_length = _canonical(
                history, label="Phase 6 history snapshot"
            )
            evaluation_raw, evaluation_hash, evaluation_length = _canonical(
                evaluation, label="Phase 6 search evaluation"
            )
            search_id = self._next_id(connection, "dsearch", business_unit)
            connection.execute(
                "INSERT INTO dossier_searches (search_id,idempotency_key,request_fingerprint,business_unit,"
                "initiating_actor,request_snapshot,request_hash,request_byte_length,history_snapshot,history_hash,"
                "history_byte_length,evaluation_snapshot,evaluation_hash,evaluation_byte_length,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    search_id, key, fingerprint, business_unit, actor, request_raw, request_hash,
                    request_length, history_raw, history_hash, history_length, evaluation_raw,
                    evaluation_hash, evaluation_length, now,
                ),
            )
            for decision in evaluation["decisions"]:
                candidate = by_result[decision["result_id"]]
                candidate_raw, candidate_hash, candidate_length = _canonical(
                    candidate, label="Phase 6 candidate"
                )
                decision_raw, decision_hash, decision_length = _canonical(
                    decision, label="Phase 6 search decision"
                )
                result_record_id = self._next_id(connection, "dresult", business_unit)
                connection.execute(
                    "INSERT INTO dossier_results (result_record_id,search_id,result_id,global_identity_id,"
                    "account_id,account_identity_version,canonical_domain,business_unit,candidate_snapshot,"
                    "candidate_hash,candidate_byte_length,decision_snapshot,decision_hash,decision_byte_length,"
                    "selected,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        result_record_id, search_id, decision["result_id"], decision["global_identity_id"],
                        decision["account_id"], ACCOUNT_IDENTITY_VERSION, decision["canonical_domain"],
                        business_unit, candidate_raw, candidate_hash, candidate_length, decision_raw,
                        decision_hash, decision_length, int(decision["selected"]), now,
                    ),
                )
            self.store.insert_audit(
                connection,
                run_id=search_id,
                event_type="phase6_history_first_search_recorded",
                actor=actor,
                business_unit=business_unit,
                correlation_id=self._next_id(connection, "dcorr", business_unit),
                safe_status="completed",
                recorded_at=now,
            )
            row = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=?", (search_id,)
            ).fetchone()
            return self._search_row(connection, row), True

    def create_real_search(
        self,
        actor: str,
        business_unit: str,
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Evaluate only the exact authorized alternatives through durable history."""
        self._authorize(actor, business_unit)
        key = _idempotency(idempotency_key)
        request = self._real_search_request(business_unit)
        request_raw, request_hash, request_length = _canonical(
            request, label="Phase 6 real search request"
        )
        fingerprint = _fingerprint({
            "actor": actor,
            "business_unit": business_unit,
            "request_hash": request_hash,
        })
        now_dt = self._now_dt()
        now = _format_time(now_dt)
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM dossier_searches WHERE idempotency_key=? AND business_unit=?",
                (key, business_unit),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise MissionControlError(409, "Idempotency key was already used with different search input.")
                return self._search_row(connection, existing), False
            try:
                history = self._history(
                    connection,
                    business_unit,
                    generated_at=now,
                    include_real=True,
                )
                candidates: list[dict[str, Any]] = []
                decisions: list[dict[str, Any]] = []
                for source_plan_id in request["source_plan_ids"]:
                    plan = self._real_plan_for_id(source_plan_id)
                    classification = classify_duplicate(
                        {
                            "company_name": plan["organization_name"],
                            "domain": plan["canonical_domain"],
                            "company_type": "brand",
                        },
                        history,
                        request["campaign"],
                        today=now_dt.date(),
                    )
                    candidate, decision = build_real_result_projection(plan, classification)
                    candidates.append(candidate)
                    decisions.append(decision)
            except ValidationError as exc:
                raise MissionControlError(400, str(exc)) from exc
            evaluation = {
                "contract_version": 2,
                "synthetic": False,
                "business_unit": business_unit,
                "source_plan_ids": list(request["source_plan_ids"]),
                "decisions": decisions,
            }
            history_raw, history_hash, history_length = _canonical(
                history, label="Phase 6 real history snapshot"
            )
            evaluation_raw, evaluation_hash, evaluation_length = _canonical(
                evaluation, label="Phase 6 real search evaluation"
            )
            search_id = self._next_id(connection, "dsearch", business_unit)
            connection.execute(
                "INSERT INTO dossier_searches (search_id,idempotency_key,request_fingerprint,business_unit,"
                "initiating_actor,request_snapshot,request_hash,request_byte_length,history_snapshot,history_hash,"
                "history_byte_length,evaluation_snapshot,evaluation_hash,evaluation_byte_length,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    search_id, key, fingerprint, business_unit, actor, request_raw, request_hash,
                    request_length, history_raw, history_hash, history_length, evaluation_raw,
                    evaluation_hash, evaluation_length, now,
                ),
            )
            for candidate, decision in zip(candidates, decisions, strict=True):
                candidate_raw, candidate_hash, candidate_length = _canonical(
                    candidate, label="Phase 6 real candidate"
                )
                decision_raw, decision_hash, decision_length = _canonical(
                    decision, label="Phase 6 real search decision"
                )
                result_record_id = self._next_id(connection, "dresult", business_unit)
                connection.execute(
                    "INSERT INTO dossier_results (result_record_id,search_id,result_id,global_identity_id,"
                    "account_id,account_identity_version,canonical_domain,business_unit,candidate_snapshot,"
                    "candidate_hash,candidate_byte_length,decision_snapshot,decision_hash,decision_byte_length,"
                    "selected,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        result_record_id, search_id, decision["result_id"], decision["global_identity_id"],
                        decision["account_id"], ACCOUNT_IDENTITY_VERSION, decision["canonical_domain"],
                        business_unit, candidate_raw, candidate_hash, candidate_length, decision_raw,
                        decision_hash, decision_length, int(decision["selected"]), now,
                    ),
                )
            self.store.insert_audit(
                connection,
                run_id=search_id,
                event_type="phase6_real_history_first_search_recorded",
                actor=actor,
                business_unit=business_unit,
                correlation_id=self._next_id(connection, "dcorr", business_unit),
                safe_status="completed",
                recorded_at=now,
            )
            row = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=?", (search_id,)
            ).fetchone()
            return self._search_row(connection, row), True

    def _search_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        request = self._check_snapshot(row, "request", "search request")
        history = self._check_snapshot(row, "history", "history snapshot")
        evaluation = self._check_snapshot(row, "evaluation", "search evaluation")
        results = connection.execute(
            "SELECT * FROM dossier_results WHERE search_id=? ORDER BY result_id", (row["search_id"],)
        ).fetchall()
        items = [self._result_row(item) for item in results]
        if request.get("synthetic") is False:
            self._require_exact_real_search_request(request, row["business_unit"])
            order = {
                source_plan_id: index
                for index, source_plan_id in enumerate(request.get("source_plan_ids", []))
            }
            items.sort(key=lambda item: order.get(item["candidate"].get("source_plan_id"), len(order)))
            if (
                evaluation.get("contract_version") != 2
                or evaluation.get("synthetic") is not False
                or evaluation.get("source_plan_ids") != request["source_plan_ids"]
                or [item["candidate"].get("source_plan_id") for item in items]
                != request["source_plan_ids"]
            ):
                raise MissionControlError(409, "The exact real search projection changed.")
        stored_decisions = sorted(
            (item["decision"] for item in items), key=lambda item: item["result_id"]
        )
        evaluated_decisions = sorted(
            evaluation.get("decisions", []), key=lambda item: item.get("result_id", "")
        )
        expected_fingerprint = _fingerprint({
            "actor": row["initiating_actor"],
            "business_unit": row["business_unit"],
            "request_hash": row["request_hash"],
        })
        if (
            hashlib.sha256(_canonical(history, label="history snapshot")[0].encode("utf-8")).hexdigest()
            != row["history_hash"]
            or row["request_fingerprint"] != expected_fingerprint
            or stored_decisions != evaluated_decisions
            or evaluation.get("business_unit") != row["business_unit"]
        ):
            raise MissionControlError(409, "The stored search projection failed its integrity check.")
        return {
            "search_id": row["search_id"],
            "business_unit": row["business_unit"],
            "initiating_actor": row["initiating_actor"],
            "idempotency_key": row["idempotency_key"],
            "request": request,
            "history_hash": row["history_hash"],
            "evaluation": evaluation,
            "results": items,
            "created_at": row["created_at"],
        }

    def _result_row(self, row: sqlite3.Row) -> dict[str, Any]:
        candidate = self._check_snapshot(row, "candidate", "candidate")
        decision = self._check_snapshot(row, "decision", "search decision")
        expected = derive_account_id(row["canonical_domain"], row["global_identity_id"])
        if candidate.get("synthetic") is False:
            plan = self._real_plan_for_id(candidate.get("source_plan_id"))
            try:
                validate_real_result_projection(candidate, decision, plan)
            except ValidationError as exc:
                raise MissionControlError(409, "The stored real result failed contract validation.") from exc
        if (
            row["account_identity_version"] != ACCOUNT_IDENTITY_VERSION
            or row["account_id"] != expected
            or candidate.get("account_id") != expected
            or decision.get("account_id") != expected
            or decision.get("selected") is not bool(row["selected"])
            or candidate.get("result_id") != row["result_id"]
            or decision.get("result_id") != row["result_id"]
            or candidate.get("global_identity_id") != row["global_identity_id"]
            or decision.get("global_identity_id") != row["global_identity_id"]
            or candidate.get("domain") != row["canonical_domain"]
            or decision.get("canonical_domain") != row["canonical_domain"]
            or candidate.get("business_unit") != row["business_unit"]
            or decision.get("business_unit") != row["business_unit"]
        ):
            raise MissionControlError(409, "The stored result account binding failed its integrity check.")
        return {
            "result_record_id": row["result_record_id"],
            "search_id": row["search_id"],
            "result_id": row["result_id"],
            "global_identity_id": row["global_identity_id"],
            "account_id": row["account_id"],
            "canonical_domain": row["canonical_domain"],
            "business_unit": row["business_unit"],
            "candidate_hash": row["candidate_hash"],
            "candidate_byte_length": row["candidate_byte_length"],
            "candidate": candidate,
            "decision": decision,
            "selected": bool(row["selected"]),
            "created_at": row["created_at"],
        }

    def get_search(self, actor: str, business_unit: str, search_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
                (search_id, business_unit),
            ).fetchone()
            if row is None:
                raise MissionControlError(403, "Business-unit access denied or Phase 6 search not found.")
            return self._search_row(connection, row)
        finally:
            connection.close()

    def searches(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM dossier_searches WHERE business_unit=? ORDER BY search_id",
                (business_unit,),
            ).fetchall()
            return [self._search_row(connection, row) for row in rows]
        finally:
            connection.close()

    def _synthetic_source_plan(self, result_id: str, business_unit: str) -> dict[str, Any]:
        dossier = self._template_dossier(result_id)
        if dossier is not None:
            sources = [
                {"url": item["source_url"], "source_class": item["source_kind"]}
                for item in dossier["evidence_inventory"]
            ]
        else:
            sources = [
                {"url": item["source_url"], "source_class": "official_site"}
                for item in self._candidate_fixture(result_id, business_unit)["evidence"]
            ]
        return {
            "contract_version": 1,
            "plan_id": f"phase6-synthetic-{business_unit}-v1",
            "synthetic": True,
            "business_unit": business_unit,
            "result_id": result_id,
            "sources": sources,
        }

    @staticmethod
    def _validate_stored_source_plan(
        source_plan: dict[str, Any], result_id: str, business_unit: str
    ) -> None:
        if source_plan.get("synthetic") is False:
            try:
                validate_real_source_plan(source_plan)
            except ValidationError as exc:
                raise MissionControlError(409, "The stored real source plan is invalid.") from exc
            if (
                source_plan["business_unit"] != business_unit
                or derive_real_result_id(source_plan) != result_id
            ):
                raise MissionControlError(409, "The stored real source plan target is invalid.")
            return
        if set(source_plan) != {
            "contract_version", "plan_id", "synthetic", "business_unit", "result_id", "sources"
        }:
            raise MissionControlError(409, "The stored source plan has an invalid shape.")
        if (
            type(source_plan["contract_version"]) is not int
            or source_plan["contract_version"] != 1
            or source_plan["plan_id"] != f"phase6-synthetic-{business_unit}-v1"
            or source_plan["synthetic"] is not True
            or source_plan["business_unit"] != business_unit
            or source_plan["result_id"] != result_id
            or not isinstance(source_plan["sources"], list)
            or not 1 <= len(source_plan["sources"]) <= 100
        ):
            raise MissionControlError(409, "The stored source plan target is invalid.")
        seen: set[tuple[str, str]] = set()
        for source in source_plan["sources"]:
            if not isinstance(source, dict) or set(source) != {"url", "source_class"}:
                raise MissionControlError(409, "The stored source plan source is invalid.")
            url = source["url"]
            source_class = source["source_class"]
            if not isinstance(url, str) or not isinstance(source_class, str):
                raise MissionControlError(409, "The stored source plan source is invalid.")
            parsed = urlsplit(url)
            try:
                port = parsed.port
            except ValueError as exc:
                raise MissionControlError(409, "The stored synthetic source plan is unsafe.") from exc
            if (
                parsed.scheme != "https"
                or parsed.hostname is None
                or not parsed.hostname.endswith(".example")
                or port is not None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or "%" in url
                or any(character.isspace() or ord(character) < 32 for character in url)
                or any(character in url for character in '<>"\'\\')
                or source_class not in SOURCE_KINDS
            ):
                raise MissionControlError(409, "The stored synthetic source plan is unsafe.")
            identity = (url, source_class)
            if identity in seen:
                raise MissionControlError(409, "The stored source plan contains duplicate sources.")
            seen.add(identity)

    def _require_current_source_plan(self, approval: sqlite3.Row) -> dict[str, Any]:
        approved = self._decode(approval["source_plan_snapshot"], "source plan")
        current = (
            self._real_plan_for_result(approval["result_id"], approval["business_unit"])
            if approved.get("synthetic") is False
            else self._synthetic_source_plan(approval["result_id"], approval["business_unit"])
        )
        if approved != current:
            raise MissionControlError(
                409,
                "The repository-owned source plan changed after approval; create a new exact approval.",
            )
        return approved

    def _generic_dossier(
        self,
        result: dict[str, Any],
        search_request: dict[str, Any],
        *,
        research_cutoff: str,
    ) -> dict[str, Any]:
        candidate = result["candidate"]
        evidence = [
            {
                "evidence_id": item["evidence_id"],
                "source_url": item["source_url"],
                "source_kind": "official_site",
                "source_date": item["source_date"],
                "observed_at": item["observed_at"],
                "original_source": True,
                "summary": "Repository-owned synthetic candidate evidence.",
                "conflict_state": "none",
            }
            for item in candidate["evidence"]
        ]
        if not evidence:
            raise MissionControlError(409, "The selected result has no bounded evidence.")
        node_id = "organization-" + hashlib.sha256(result["account_id"].encode("utf-8")).hexdigest()[:20]
        evidence_ref = evidence[0]["evidence_id"]
        source_date = evidence[0]["source_date"]
        observed_at = evidence[0]["observed_at"]

        def claim(category: str, suffix: str, typed_value: dict[str, Any], confidence: str) -> dict[str, Any]:
            return {
                "claim_id": f"claim-{suffix}-{result['result_id']}",
                "category": category,
                "subject_node_id": node_id,
                "typed_value": typed_value,
                "basis": "observed",
                "evidence_refs": [evidence_ref],
                "confidence_reason": confidence,
                "uncertainty": "Bounded synthetic fixture evidence only.",
                "source_date": source_date,
                "observed_at": observed_at,
                "freshness_state": "current",
            }

        complete = {
            "identity_and_relationships": claim(
                "identity_and_relationships", "identity",
                {"type": "string", "value": f"{candidate['company_name']} uses {candidate['domain']}."},
                "The exact synthetic candidate record binds the company name and domain.",
            ),
            "company_and_commercial_context": claim(
                "company_and_commercial_context", "commercial",
                {"type": "string", "value": f"Synthetic {candidate['company_type']} candidate."},
                "The bounded candidate record supplies the company type.",
            ),
            "operations_and_digital_footprint": claim(
                "operations_and_digital_footprint", "operations",
                {"type": "url", "value": evidence[0]["source_url"]},
                "The synthetic evidence record supplies one digital route.",
            ),
            "opportunity_and_fit": claim(
                "opportunity_and_fit", "opportunity",
                {"type": "string_list", "value": sorted(item["kind"] for item in candidate["opportunities"])},
                "Controlled opportunity values are directly bound to candidate evidence.",
            ),
            "governance_and_history": claim(
                "governance_and_history", "governance",
                {"type": "string", "value": result["decision"]["history_classification"]["status"]},
                "The durable history-first classifier produced this exact state.",
            ),
            "evidence_coverage": claim(
                "evidence_coverage", "coverage",
                {"type": "number", "value": len(evidence)},
                "The immutable evidence inventory provides the recorded count.",
            ),
        }
        categories = []
        for category in CATEGORIES:
            if category in complete:
                categories.append({"category": category, "coverage_state": "complete", "claims": [complete[category]]})
            else:
                categories.append({
                    "category": category,
                    "coverage_state": "not_found" if category == "public_people_and_contact_paths" else "unknown",
                    "gap_explanation": "The bounded synthetic candidate record does not establish this category; no value was guessed.",
                    "claims": [],
                })
        return {
            "schema_version": 1,
            "synthetic": True,
            "dossier_id": "placeholder",
            "version": 1,
            "idempotency_identity": "placeholder",
            "search_request_id": search_request["request_id"],
            "business_unit": candidate["business_unit"],
            "approved_result": {},
            "research_cutoff": research_cutoff,
            "maximum_evidence_age_days": search_request["campaign"]["maximum_evidence_age_days"],
            "history_fingerprint": "",
            "duplicate_history": copy.deepcopy(result["decision"]["history_classification"]),
            "evidence_inventory": evidence,
            "categories": categories,
            "entities": [{
                "node_id": node_id,
                "node_type": "organization",
                "label": candidate["company_name"],
                "business_unit": candidate["business_unit"],
                "attributes": {"canonical_domain": candidate["domain"]},
            }],
            "relationships": [],
            "review_state": "pending_research_quality_review",
            "release_state": "not_released",
        }

    @staticmethod
    def _apply_runtime_freshness(
        dossier: dict[str, Any], search_request: dict[str, Any], research_cutoff: str
    ) -> None:
        """Bind runtime copies to their actual cutoff and recompute every claim state."""
        cutoff = _parse_time(research_cutoff, "research cutoff")
        maximum_age = search_request["campaign"]["maximum_evidence_age_days"]
        dossier["research_cutoff"] = research_cutoff
        dossier["maximum_evidence_age_days"] = maximum_age
        evidence = {item["evidence_id"]: item for item in dossier["evidence_inventory"]}
        for category in dossier["categories"]:
            for claim in category["claims"]:
                referenced = [evidence[item] for item in claim["evidence_refs"]]
                conflicted = any(item["conflict_state"] == "conflicted" for item in referenced)
                stale = any(
                    (cutoff.date() - datetime.fromisoformat(item["source_date"]).date()).days
                    > maximum_age
                    for item in referenced
                )
                claim["freshness_state"] = (
                    "conflicted" if conflicted else "stale" if stale else "current"
                )

    def _approval_leaf(self, connection: sqlite3.Connection, result_record_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT parent.* FROM dossier_approval_events parent "
            "LEFT JOIN dossier_approval_events child ON child.supersedes_id=parent.approval_event_id "
            "WHERE parent.result_record_id=? AND child.approval_event_id IS NULL "
            "ORDER BY parent.recorded_at DESC LIMIT 1",
            (result_record_id,),
        ).fetchone()

    def _approval_integrity(
        self, connection: sqlite3.Connection, row: sqlite3.Row, *, require_leaf: bool
    ) -> tuple[dict[str, Any], sqlite3.Row]:
        raw = row["event_snapshot"]
        encoded = raw.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != row["content_hash"] or len(encoded) != row["byte_length"]:
            raise MissionControlError(409, "The durable dossier approval failed its integrity check.")
        event = self._decode(raw, "dossier approval")
        if set(event) != set(DOSSIER_APPROVAL_EVENT_FIELDS):
            raise MissionControlError(409, "The durable dossier approval failed its integrity check.")
        expected_event = {key: row[key] for key in DOSSIER_APPROVAL_EVENT_FIELDS}
        if event != expected_event:
            raise MissionControlError(409, "The durable dossier approval snapshot is inconsistent.")
        result = connection.execute(
            "SELECT * FROM dossier_results WHERE result_record_id=? AND business_unit=?",
            (row["result_record_id"], row["business_unit"]),
        ).fetchone()
        if result is None:
            raise MissionControlError(409, "The durable dossier approval target is unavailable.")
        projected = self._result_row(result)
        _result_raw, result_hash, result_length = _canonical(
            projected, label="approved dossier result"
        )
        search = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
            (row["search_id"], row["business_unit"]),
        ).fetchone()
        if search is None:
            raise MissionControlError(409, "The durable dossier approval search is unavailable.")
        plan_encoded = row["source_plan_snapshot"].encode("utf-8")
        source_plan = self._decode(row["source_plan_snapshot"], "source plan")
        plan_raw, plan_snapshot_hash, plan_snapshot_length = _canonical(
            source_plan, label="stored source plan", limit=64_000
        )
        is_real = source_plan.get("synthetic") is False
        reviewer_valid = (
            row["reviewer_actor"] == REAL_GOAL_AUTHORITY
            if is_real
            else (
                row["reviewer_actor"] in ACTOR_SCOPE
                and row["business_unit"] in ACTOR_SCOPE.get(row["reviewer_actor"], set())
                and row["reviewer_actor"] != row["proposer_actor"]
            )
        )
        plan_hash_valid = (
            row["source_plan_hash"] == source_plan.get("source_plan_hash")
            if is_real
            else row["source_plan_hash"] == plan_snapshot_hash
        )
        expected_account = derive_account_id(row["canonical_domain"], row["global_identity_id"])
        effective = _parse_time(row["effective_at"], "approval effective time")
        recorded = _parse_time(row["recorded_at"], "approval recorded time")
        expires = _parse_time(row["expires_at"], "approval expiry")
        parent = None
        if row["supersedes_id"] is not None:
            parent = connection.execute(
                "SELECT result_record_id,business_unit FROM dossier_approval_events "
                "WHERE approval_event_id=? AND business_unit=?",
                (row["supersedes_id"], row["business_unit"]),
            ).fetchone()
        self._validate_stored_source_plan(source_plan, row["result_id"], row["business_unit"])
        if not all((
            projected["selected"],
            result_hash == row["result_hash"],
            result_length == row["result_byte_length"],
            search["history_hash"] == row["history_hash"],
            search["request_hash"] == row["request_hash"],
            row["account_identity_version"] == ACCOUNT_IDENTITY_VERSION,
            row["account_id"] == expected_account == projected["account_id"],
            row["source_plan_snapshot"] == plan_raw,
            len(plan_encoded) == plan_snapshot_length == row["source_plan_byte_length"],
            plan_hash_valid,
            row["scope"] == DOSSIER_APPROVAL_SCOPE,
            row["decision"] in APPROVAL_DECISIONS,
            row["proposer_actor"] in ACTOR_SCOPE,
            row["business_unit"] in ACTOR_SCOPE.get(row["proposer_actor"], set()),
            reviewer_valid,
            isinstance(row["reason"], str) and 0 < len(row["reason"].strip()) <= 2_000,
            effective <= recorded,
            expires == effective + APPROVAL_LIFETIME,
            parent is None or (
                parent["result_record_id"] == row["result_record_id"]
                and parent["business_unit"] == row["business_unit"]
            ),
        )):
            raise MissionControlError(409, "The durable dossier approval binding failed its integrity check.")
        if require_leaf:
            leaf = self._approval_leaf(connection, row["result_record_id"])
            if leaf is None or leaf["approval_event_id"] != row["approval_event_id"]:
                raise MissionControlError(409, "The dossier approval is not the current leaf.")
        return projected, search

    def record_approval(
        self,
        actor: str,
        business_unit: str,
        search_id: str,
        result_id: str,
        *,
        decision: str,
        reason: str,
        expected_leaf_id: str | None = None,
        effective_at: datetime | None = None,
        source_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        if decision not in APPROVAL_DECISIONS:
            raise MissionControlError(400, "Unknown dossier approval decision.")
        reason = _bounded_text(reason, "Approval reason")
        recorded_dt = self._now_dt()
        effective_dt = _utc(effective_at) if effective_at is not None else recorded_dt
        if effective_dt > recorded_dt:
            raise MissionControlError(400, "Approval effective time cannot be in the future.")
        recorded = _format_time(recorded_dt)
        effective = _format_time(effective_dt)
        expires = _format_time(effective_dt + APPROVAL_LIFETIME)
        with self.store.transaction() as connection:
            result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, result_id, business_unit),
            ).fetchone()
            if result is None:
                raise MissionControlError(403, "Business-unit access denied or selected result not found.")
            projected = self._result_row(result)
            if not projected["selected"]:
                raise MissionControlError(409, "Only an exact selected result can receive dossier approval.")
            _result_raw, result_hash, result_length = _canonical(
                projected, label="approved dossier result"
            )
            search = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
                (search_id, business_unit),
            ).fetchone()
            if search is None:
                raise MissionControlError(403, "Business-unit access denied or Phase 6 search not found.")
            proposer = search["initiating_actor"]
            if actor == proposer:
                raise MissionControlError(403, "The proposer cannot self-approve dossier research.")
            leaf = self._approval_leaf(connection, result["result_record_id"])
            expected = expected_leaf_id or None
            if (leaf is None and expected is not None) or (
                leaf is not None and expected != leaf["approval_event_id"]
            ):
                raise MissionControlError(409, "The expected dossier approval is not the current leaf.")
            expected_plan = self._synthetic_source_plan(result_id, business_unit)
            if source_plan is not None and source_plan != expected_plan:
                raise MissionControlError(
                    400,
                    "The source plan does not exactly match the selected synthetic result and business unit.",
                )
            plan = expected_plan
            plan_raw, plan_hash, plan_length = _canonical(plan, label="Phase 6 source plan", limit=64_000)
            event_id = self._next_id(connection, "dapproval", business_unit)
            correlation_id = self._next_id(connection, "dcorr", business_unit)
            event = {
                "approval_event_id": event_id,
                "result_record_id": result["result_record_id"],
                "search_id": search_id,
                "result_id": result_id,
                "global_identity_id": result["global_identity_id"],
                "account_id": derive_account_id(result["canonical_domain"], result["global_identity_id"]),
                "account_identity_version": ACCOUNT_IDENTITY_VERSION,
                "canonical_domain": result["canonical_domain"],
                "business_unit": business_unit,
                "result_hash": result_hash,
                "result_byte_length": result_length,
                "history_hash": search["history_hash"],
                "request_hash": search["request_hash"],
                "source_plan_snapshot": plan_raw,
                "source_plan_hash": plan_hash,
                "source_plan_byte_length": plan_length,
                "scope": DOSSIER_APPROVAL_SCOPE,
                "decision": decision,
                "proposer_actor": proposer,
                "reviewer_actor": actor,
                "reason": reason,
                "effective_at": effective,
                "recorded_at": recorded,
                "expires_at": expires,
                "supersedes_id": leaf["approval_event_id"] if leaf else None,
                "audit_correlation_id": correlation_id,
            }
            raw, content_hash, byte_length = _canonical(event, label="Dossier approval event", limit=64_000)
            try:
                connection.execute(
                    "INSERT INTO dossier_approval_events ("
                    + ",".join(event)
                    + ",event_snapshot,content_hash,byte_length) VALUES ("
                    + ",".join("?" for _ in range(len(event) + 3))
                    + ")",
                    (*event.values(), raw, content_hash, byte_length),
                )
            except sqlite3.IntegrityError as exc:
                raise MissionControlError(409, "The dossier approval leaf changed concurrently.") from exc
            self.store.insert_audit(
                connection,
                run_id=search_id,
                event_type="phase6_dossier_approval_recorded",
                actor=actor,
                business_unit=business_unit,
                correlation_id=correlation_id,
                safe_status=decision,
                recorded_at=recorded,
            )
            row = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=?", (event_id,)
            ).fetchone()
            self._approval_integrity(connection, row, require_leaf=True)
            return self._approval_row(connection, row)

    def record_real_goal_approval(
        self,
        actor: str,
        business_unit: str,
        search_id: str,
        result_id: str,
        *,
        decision: str,
        reason: str,
        expected_leaf_id: str | None = None,
        effective_at: datetime | None = None,
        source_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record the user's exact goal authority without impersonating a local actor."""
        self._authorize(actor, business_unit)
        if decision not in APPROVAL_DECISIONS:
            raise MissionControlError(400, "Unknown dossier approval decision.")
        reason = _bounded_text(reason, "Approval reason")
        recorded_dt = self._now_dt()
        effective_dt = _utc(effective_at) if effective_at is not None else recorded_dt
        if effective_dt > recorded_dt:
            raise MissionControlError(400, "Approval effective time cannot be in the future.")
        recorded = _format_time(recorded_dt)
        effective = _format_time(effective_dt)
        expires = _format_time(effective_dt + APPROVAL_LIFETIME)
        with self.store.transaction() as connection:
            result = connection.execute(
                "SELECT * FROM dossier_results WHERE search_id=? AND result_id=? AND business_unit=?",
                (search_id, result_id, business_unit),
            ).fetchone()
            if result is None:
                raise MissionControlError(403, "Business-unit access denied or selected result not found.")
            projected = self._result_row(result)
            if projected["candidate"].get("synthetic") is not False or not projected["selected"]:
                raise MissionControlError(409, "Only an exact selected real result can receive goal authority.")
            search = connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
                (search_id, business_unit),
            ).fetchone()
            if search is None:
                raise MissionControlError(403, "Business-unit access denied or Phase 6 search not found.")
            request = self._check_snapshot(search, "request", "search request")
            self._require_exact_real_search_request(request, business_unit)
            if request.get("synthetic") is not False or search["initiating_actor"] != actor:
                raise MissionControlError(403, "Only the bound proposer may bind the user's exact goal authority.")
            plan = self._real_plan_for_result(result_id, business_unit)
            if source_plan is not None and source_plan != plan:
                raise MissionControlError(400, "The source plan does not exactly match the authorized real result.")
            if plan["source_plan_id"] not in request.get("source_plan_ids", []):
                raise MissionControlError(409, "The real result source plan is not bound to its search request.")
            try:
                validate_real_result_projection(projected["candidate"], projected["decision"], plan)
            except ValidationError as exc:
                raise MissionControlError(409, "The selected real result failed exact-plan validation.") from exc
            if self._real_result_has_no_retry_failure(
                connection, result["result_record_id"]
            ):
                raise MissionControlError(
                    409,
                    "The exact public-source attempt failed terminally and cannot be retried.",
                )
            if not self._real_attempt_allowed(
                connection,
                result["result_record_id"],
                for_new_authority=True,
                decision=decision,
            ):
                raise MissionControlError(
                    409,
                    "The bounded real-proof attempt or its one recovery has already been used.",
                )
            self._require_prior_real_targets_terminal(
                connection, search_id, plan["source_plan_id"], now=recorded_dt
            )
            self._require_no_later_real_target_progress(
                connection,
                search_id,
                plan["source_plan_id"],
                for_new_authority=True,
            )
            leaf = self._approval_leaf(connection, result["result_record_id"])
            expected = expected_leaf_id or None
            if (leaf is None and expected is not None) or (
                leaf is not None and expected != leaf["approval_event_id"]
            ):
                raise MissionControlError(409, "The expected dossier approval is not the current leaf.")
            _result_raw, result_hash, result_length = _canonical(
                projected, label="approved real dossier result"
            )
            plan_raw, _plan_snapshot_hash, plan_length = _canonical(
                plan, label="Phase 6 real source plan", limit=64_000
            )
            event_id = self._next_id(connection, "dapproval", business_unit)
            correlation_id = self._next_id(connection, "dcorr", business_unit)
            event = {
                "approval_event_id": event_id,
                "result_record_id": result["result_record_id"],
                "search_id": search_id,
                "result_id": result_id,
                "global_identity_id": result["global_identity_id"],
                "account_id": derive_account_id(result["canonical_domain"], result["global_identity_id"]),
                "account_identity_version": ACCOUNT_IDENTITY_VERSION,
                "canonical_domain": result["canonical_domain"],
                "business_unit": business_unit,
                "result_hash": result_hash,
                "result_byte_length": result_length,
                "history_hash": search["history_hash"],
                "request_hash": search["request_hash"],
                "source_plan_snapshot": plan_raw,
                "source_plan_hash": plan["source_plan_hash"],
                "source_plan_byte_length": plan_length,
                "scope": DOSSIER_APPROVAL_SCOPE,
                "decision": decision,
                "proposer_actor": actor,
                "reviewer_actor": REAL_GOAL_AUTHORITY,
                "reason": reason,
                "effective_at": effective,
                "recorded_at": recorded,
                "expires_at": expires,
                "supersedes_id": leaf["approval_event_id"] if leaf else None,
                "audit_correlation_id": correlation_id,
            }
            raw, content_hash, byte_length = _canonical(
                event, label="Real dossier goal-authority event", limit=64_000
            )
            try:
                connection.execute(
                    "INSERT INTO dossier_approval_events ("
                    + ",".join(event)
                    + ",event_snapshot,content_hash,byte_length) VALUES ("
                    + ",".join("?" for _ in range(len(event) + 3))
                    + ")",
                    (*event.values(), raw, content_hash, byte_length),
                )
            except sqlite3.IntegrityError as exc:
                raise MissionControlError(409, "The dossier approval leaf changed concurrently.") from exc
            self.store.insert_audit(
                connection,
                run_id=search_id,
                event_type="phase6_real_goal_authority_recorded",
                actor=REAL_GOAL_AUTHORITY,
                business_unit=business_unit,
                correlation_id=correlation_id,
                safe_status=decision,
                recorded_at=recorded,
            )
            row = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=?", (event_id,)
            ).fetchone()
            self._approval_integrity(connection, row, require_leaf=True)
            return self._approval_row(connection, row)

    def _approval_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        item = dict(row)
        item.pop("event_snapshot")
        item["source_plan"] = json.loads(item.pop("source_plan_snapshot"))
        leaf = self._approval_leaf(connection, row["result_record_id"])
        consumed = connection.execute(
            "SELECT 1 FROM dossier_runs WHERE approval_event_id=?",
            (row["approval_event_id"],),
        ).fetchone() is not None
        now = self._now_dt()
        item["is_current_leaf"] = (
            leaf is not None and leaf["approval_event_id"] == row["approval_event_id"]
        )
        item["consumed"] = consumed
        item["valid_now"] = bool(
            item["is_current_leaf"]
            and not consumed
            and row["decision"] == "approved"
            and _parse_time(row["recorded_at"], "approval recorded time") <= now
            and _parse_time(row["effective_at"], "approval effective time") <= now
            and now < _parse_time(row["expires_at"], "approval expiry")
        )
        return item

    def get_approval(self, actor: str, business_unit: str, approval_event_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (approval_event_id, business_unit),
            ).fetchone()
            if row is None:
                raise MissionControlError(403, "Business-unit access denied or dossier approval not found.")
            self._approval_integrity(connection, row, require_leaf=False)
            return self._approval_row(connection, row)
        finally:
            connection.close()

    def approvals(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE business_unit=? ORDER BY rowid",
                (business_unit,),
            ).fetchall()
            items = []
            for row in rows:
                self._approval_integrity(connection, row, require_leaf=False)
                items.append(self._approval_row(connection, row))
            return items
        finally:
            connection.close()

    def claim_dossier(
        self,
        actor: str,
        business_unit: str,
        approval_event_id: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        self._authorize(actor, business_unit)
        key = _idempotency(idempotency_key)
        fingerprint = _fingerprint({
            "actor": actor,
            "approval_event_id": approval_event_id,
            "business_unit": business_unit,
        })
        now_dt = self._now_dt()
        now = _format_time(now_dt)
        with self._active_lock, self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM dossier_runs WHERE idempotency_key=? AND business_unit=?",
                (key, business_unit),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise MissionControlError(409, "Idempotency key was already used with different dossier input.")
                existing_approval = connection.execute(
                    "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                    (existing["approval_event_id"], business_unit),
                ).fetchone()
                if existing_approval is None:
                    raise MissionControlError(409, "The stored dossier run lost its approval binding.")
                self._approval_integrity(connection, existing_approval, require_leaf=False)
                existing_plan = self._require_current_source_plan(existing_approval)
                if (
                    existing_plan.get("synthetic") is False
                    and not self._verified_run_transition(connection, existing)
                ):
                    raise MissionControlError(
                        409, "The stored real dossier run has inconsistent transition evidence."
                    )
                if existing_plan.get("synthetic") is False and existing["state"] == "running":
                    self._require_prior_real_targets_terminal(
                        connection,
                        existing_approval["search_id"],
                        existing_plan["source_plan_id"],
                        now=now_dt,
                    )
                    self._require_no_later_real_target_progress(
                        connection,
                        existing_approval["search_id"],
                        existing_plan["source_plan_id"],
                    )
                if existing["state"] == "running":
                    self._register_active(existing["dossier_run_id"])
                return self._run_row(existing), False
            approval = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (approval_event_id, business_unit),
            ).fetchone()
            if approval is None:
                raise MissionControlError(403, "Business-unit access denied or dossier approval not found.")
            result, _search = self._approval_integrity(connection, approval, require_leaf=True)
            plan = self._require_current_source_plan(approval)
            if plan.get("synthetic") is False:
                if self._real_result_has_no_retry_failure(
                    connection, approval["result_record_id"]
                ):
                    raise MissionControlError(
                        409,
                        "The exact public-source attempt failed terminally and cannot be retried.",
                    )
                if not self._real_attempt_allowed(
                    connection,
                    approval["result_record_id"],
                    for_new_authority=False,
                    approval_event_id=approval["approval_event_id"],
                ):
                    raise MissionControlError(
                        409,
                        "The bounded real-proof attempt or its one recovery has already been used.",
                    )
                self._require_prior_real_targets_terminal(
                    connection,
                    approval["search_id"],
                    plan["source_plan_id"],
                    now=now_dt,
                )
                self._require_no_later_real_target_progress(
                    connection, approval["search_id"], plan["source_plan_id"]
                )
            if approval["decision"] != "approved":
                raise MissionControlError(409, "The current dossier approval is not approved.")
            if actor != approval["proposer_actor"]:
                raise MissionControlError(403, "Only the bound proposer may start dossier research.")
            if _parse_time(approval["recorded_at"], "approval recorded time") > now_dt:
                raise MissionControlError(409, "The dossier approval recorded time is in the future.")
            if now_dt < _parse_time(approval["effective_at"], "approval effective time"):
                raise MissionControlError(409, "The dossier approval is not effective yet.")
            if now_dt >= _parse_time(approval["expires_at"], "approval expiry"):
                raise MissionControlError(409, "The dossier approval has expired.")
            consumed = connection.execute(
                "SELECT dossier_run_id FROM dossier_runs WHERE approval_event_id=?", (approval_event_id,)
            ).fetchone()
            if consumed is not None:
                raise MissionControlError(409, "The dossier approval was already consumed.")
            account_id = derive_account_id(approval["canonical_domain"], approval["global_identity_id"])
            if account_id != approval["account_id"] or account_id != result["account_id"]:
                raise MissionControlError(409, "The dossier account binding failed at claim.")
            run_id = self._next_id(connection, "drun", business_unit)
            correlation_id = self._next_id(connection, "dcorr", business_unit)
            try:
                connection.execute(
                    "INSERT INTO dossier_runs (dossier_run_id,approval_event_id,idempotency_key,request_fingerprint,"
                    "business_unit,result_record_id,result_id,global_identity_id,account_id,account_identity_version,"
                    "canonical_domain,initiating_actor,state,cancel_requested,created_at,audit_correlation_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'running',0,?,?)",
                    (
                        run_id, approval_event_id, key, fingerprint, business_unit,
                        approval["result_record_id"], approval["result_id"], approval["global_identity_id"],
                        account_id, ACCOUNT_IDENTITY_VERSION, approval["canonical_domain"], actor, now,
                        correlation_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise MissionControlError(409, "The dossier approval was claimed concurrently.") from exc
            self.store.insert_audit(
                connection,
                run_id=run_id,
                event_type="phase6_dossier_claimed",
                actor=actor,
                business_unit=business_unit,
                correlation_id=correlation_id,
                safe_status="running",
                recorded_at=now,
            )
            row = connection.execute("SELECT * FROM dossier_runs WHERE dossier_run_id=?", (run_id,)).fetchone()
            self._register_active(run_id)
            return self._run_row(row), True

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        expected_fingerprint = _fingerprint({
            "actor": row["initiating_actor"],
            "approval_event_id": row["approval_event_id"],
            "business_unit": row["business_unit"],
        })
        expected_account = derive_account_id(
            row["canonical_domain"], row["global_identity_id"]
        )
        if (
            row["request_fingerprint"] != expected_fingerprint
            or row["account_identity_version"] != ACCOUNT_IDENTITY_VERSION
            or row["account_id"] != expected_account
            or row["initiating_actor"] not in ACTOR_SCOPE
            or row["business_unit"] not in ACTOR_SCOPE.get(row["initiating_actor"], set())
            or row["state"] not in {"running", "succeeded", "failed", "cancelled"}
            or bool(row["cancel_requested"]) != (row["state"] == "cancelled")
            or (row["completed_at"] is None) != (row["state"] == "running")
            or (row["candidate_version_id"] is not None) != (row["state"] == "succeeded")
            or (row["state"] == "failed") != (
                isinstance(row["failure_class"], str)
                and bool(row["failure_class"].strip())
                and isinstance(row["remediation"], str)
                and bool(row["remediation"].strip())
            )
        ):
            raise MissionControlError(409, "The stored dossier run failed its integrity check.")
        item = dict(row)
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item

    def cancel_dossier(self, actor: str, business_unit: str, run_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        now = self._now()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?", (run_id, business_unit)
            ).fetchone()
            if row is None:
                raise MissionControlError(403, "Business-unit access denied or dossier run not found.")
            if actor != row["initiating_actor"]:
                raise MissionControlError(403, "Only the initiating actor may cancel this dossier run.")
            if row["state"] == "cancelled":
                result = self._run_row(row)
            elif row["state"] != "running":
                raise MissionControlError(409, "The dossier run is already terminal.")
            else:
                connection.execute(
                    "UPDATE dossier_runs SET state='cancelled',cancel_requested=1,completed_at=? "
                    "WHERE dossier_run_id=? AND state='running'",
                    (now, run_id),
                )
                self.store.insert_audit(
                    connection,
                    run_id=run_id,
                    event_type="phase6_dossier_cancelled",
                    actor=actor,
                    business_unit=business_unit,
                    correlation_id=row["audit_correlation_id"],
                    safe_status="cancelled",
                    recorded_at=now,
                )
                result = self._run_row(connection.execute(
                    "SELECT * FROM dossier_runs WHERE dossier_run_id=?", (run_id,)
                ).fetchone())
        self._unregister_active(run_id)
        return result

    def _real_bundle_for_run(
        self, actor: str, business_unit: str, run_id: str
    ) -> dict[str, Any] | None:
        """Resolve a claimed exact plan, close SQLite, then perform the bounded read."""
        connection = self.store._connect()
        try:
            run = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?",
                (run_id, business_unit),
            ).fetchone()
            if run is None:
                raise MissionControlError(403, "Business-unit access denied or dossier run not found.")
            if run["initiating_actor"] != actor:
                raise MissionControlError(403, "Only the initiating actor may complete this dossier run.")
            approval = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (run["approval_event_id"], business_unit),
            ).fetchone()
            if approval is None:
                raise MissionControlError(409, "The dossier approval is unavailable.")
            self._approval_integrity(connection, approval, require_leaf=False)
            plan = self._require_current_source_plan(approval)
            if plan.get("synthetic") is not False:
                return None
            if not self._verified_run_transition(connection, run):
                raise MissionControlError(
                    409, "The stored real dossier run has inconsistent transition evidence."
                )
            if run["state"] == "succeeded":
                return None
            expected_account = derive_account_id(run["canonical_domain"], run["global_identity_id"])
            if (
                run["state"] != "running"
                or run["cancel_requested"]
                or run["account_id"] != expected_account
                or approval["account_id"] != expected_account
                or run["result_id"] != derive_real_result_id(plan)
                or approval["decision"] != "approved"
            ):
                raise MissionControlError(409, "The claimed real dossier run is not executable.")
            self._require_prior_real_targets_terminal(
                connection,
                approval["search_id"],
                plan["source_plan_id"],
                now=self._now_dt(),
            )
            self._require_no_later_real_target_progress(
                connection, approval["search_id"], plan["source_plan_id"]
            )
            plan_id = plan["source_plan_id"]
        finally:
            connection.close()
        reader = self.public_reader
        if reader is None:
            try:
                from .public_reader import PublicReader

                reader = PublicReader(list(self.real_plans.values()))
            except (ImportError, TypeError, ValueError) as exc:
                raise MissionControlError(500, "The bounded public reader is unavailable.") from exc
            self.public_reader = reader
        try:
            bundle = reader.read_plan(plan_id)
            return validate_real_research_bundle(bundle, plan)
        except ValidationError as exc:
            raise MissionControlError(409, "The bounded public research result failed its contract.") from exc

    def _candidate_payload(
        self,
        connection: sqlite3.Connection,
        run: sqlite3.Row,
        approval: sqlite3.Row,
        version: int,
        research_cutoff: str,
        research_bundle: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        result_row = connection.execute(
            "SELECT * FROM dossier_results WHERE result_record_id=? AND business_unit=?",
            (run["result_record_id"], run["business_unit"]),
        ).fetchone()
        if result_row is None:
            raise MissionControlError(409, "The dossier result is unavailable.")
        result = self._result_row(result_row)
        search_row = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
            (result["search_id"], run["business_unit"]),
        ).fetchone()
        if search_row is None:
            raise MissionControlError(409, "The dossier search is unavailable.")
        history = self._check_snapshot(search_row, "history", "history snapshot")
        search_request = self._check_snapshot(search_row, "request", "search request")
        source_plan = self._decode(approval["source_plan_snapshot"], "source plan")
        if source_plan.get("synthetic") is False:
            if research_bundle is None:
                raise MissionControlError(409, "The real dossier candidate has no bounded research bundle.")
            approved_result = copy.deepcopy(result["decision"])
            try:
                dossier = build_real_customer_dossier(
                    plan=source_plan,
                    research_bundle=research_bundle,
                    history=history,
                    approved_result=approved_result,
                    approval_id=approval["approval_event_id"],
                    search_request_id=search_request["request_id"],
                    history_fingerprint=search_row["history_hash"],
                    research_cutoff=research_bundle["completed_at"],
                    maximum_evidence_age_days=search_request["campaign"]["maximum_evidence_age_days"],
                    version=version,
                )
                validate_real_customer_dossier(
                    dossier,
                    history=history,
                    approved_result=approved_result,
                    source_plan=source_plan,
                )
            except ValidationError as exc:
                raise MissionControlError(409, "The real dossier candidate failed its contract.") from exc
            return dossier, history, approved_result
        dossier = self._template_dossier(run["result_id"])
        if dossier is None:
            dossier = self._generic_dossier(
                result,
                search_request,
                research_cutoff=research_cutoff,
            )
        self._apply_runtime_freshness(dossier, search_request, research_cutoff)
        family_id = "dossier-" + hashlib.sha256(run["account_id"].encode("utf-8")).hexdigest()[:24]
        dossier.update({
            "dossier_id": family_id,
            "version": version,
            "idempotency_identity": f"phase6-runtime:{run['business_unit']}:{run['result_id']}:{version}",
            "search_request_id": search_request["request_id"],
            "business_unit": run["business_unit"],
            "approved_result": {
                "approval_id": approval["approval_event_id"],
                "result_id": run["result_id"],
                "global_identity_id": run["global_identity_id"],
                "account_id": derive_account_id(run["canonical_domain"], run["global_identity_id"]),
                "canonical_domain": run["canonical_domain"],
            },
            "history_fingerprint": search_row["history_hash"],
            "duplicate_history": copy.deepcopy(result["decision"]["history_classification"]),
            "review_state": "pending_research_quality_review",
            "release_state": "not_released",
        })
        approved_result = copy.deepcopy(result["decision"])
        approved_result.update({
            "selected": True,
            "business_unit": run["business_unit"],
            "account_id": dossier["approved_result"]["account_id"],
            "canonical_domain": run["canonical_domain"],
        })
        try:
            validate_customer_dossier(dossier, history=history, approved_result=approved_result)
        except ValidationError as exc:
            raise MissionControlError(409, "The dossier candidate failed the frozen contract.") from exc
        return dossier, history, approved_result

    def _complete_dossier(
        self,
        actor: str,
        business_unit: str,
        run_id: str,
        *,
        research_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        now = self._now()
        with self.store.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?", (run_id, business_unit)
            ).fetchone()
            if run is None:
                raise MissionControlError(403, "Business-unit access denied or dossier run not found.")
            if actor != run["initiating_actor"]:
                raise MissionControlError(403, "Only the initiating actor may complete this dossier run.")
            if run["state"] == "succeeded":
                candidate_row = connection.execute(
                    "SELECT * FROM dossier_candidates WHERE candidate_version_id=? AND business_unit=?",
                    (run["candidate_version_id"], business_unit),
                ).fetchone()
                if candidate_row is None:
                    raise MissionControlError(409, "The completed dossier candidate is unavailable.")
                return self._candidate_row(connection, candidate_row)
            if run["state"] == "cancelled" or run["cancel_requested"]:
                raise MissionControlError(409, "The dossier run was cancelled before completion.")
            if run["state"] != "running":
                raise MissionControlError(409, "The dossier run is already terminal.")
            approval = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (run["approval_event_id"], business_unit),
            ).fetchone()
            if approval is None:
                raise MissionControlError(409, "The dossier approval is unavailable.")
            self._approval_integrity(connection, approval, require_leaf=False)
            source_plan = self._require_current_source_plan(approval)
            if source_plan.get("synthetic") is False:
                if not self._verified_run_transition(connection, run):
                    raise MissionControlError(
                        409, "The stored real dossier run has inconsistent transition evidence."
                    )
                self._require_prior_real_targets_terminal(
                    connection,
                    approval["search_id"],
                    source_plan["source_plan_id"],
                    now=self._now_dt(),
                )
                self._require_no_later_real_target_progress(
                    connection,
                    approval["search_id"],
                    source_plan["source_plan_id"],
                )
            expected_account = derive_account_id(run["canonical_domain"], run["global_identity_id"])
            if (
                run["account_identity_version"] != ACCOUNT_IDENTITY_VERSION
                or run["account_id"] != expected_account
                or approval["account_id"] != expected_account
                or run["approval_event_id"] != approval["approval_event_id"]
                or run["result_record_id"] != approval["result_record_id"]
                or run["result_id"] != approval["result_id"]
                or run["global_identity_id"] != approval["global_identity_id"]
                or run["canonical_domain"] != approval["canonical_domain"]
                or run["business_unit"] != approval["business_unit"]
                or run["initiating_actor"] != approval["proposer_actor"]
            ):
                raise MissionControlError(409, "The dossier account binding failed at candidate creation.")
            family_id = (
                "dossier-real-v2-"
                if source_plan.get("synthetic") is False
                else "dossier-"
            ) + hashlib.sha256(run["account_id"].encode("utf-8")).hexdigest()[:24]
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM dossier_candidates "
                "WHERE dossier_family_id=? AND business_unit=?",
                (family_id, business_unit),
            ).fetchone()["next_version"]
            dossier, _history, _approved_result = self._candidate_payload(
                connection,
                run,
                approval,
                version,
                now,
                research_bundle=research_bundle,
            )
            if dossier.get("dossier_id") != family_id:
                raise MissionControlError(409, "The dossier family derivation changed before persistence.")
            raw, content_hash, byte_length = _canonical(dossier, label="Dossier candidate")
            candidate_id = self._next_id(connection, "dcandidate", business_unit)
            self.store._fault("insert_dossier_candidate")
            connection.execute(
                "INSERT INTO dossier_candidates (candidate_version_id,dossier_run_id,approval_event_id,"
                "dossier_family_id,version,business_unit,result_id,global_identity_id,account_id,"
                "account_identity_version,canonical_domain,search_id,history_hash,source_plan_hash,dossier_snapshot,"
                "content_hash,byte_length,proposer_actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id, run_id, run["approval_event_id"], family_id, version, business_unit,
                    run["result_id"], run["global_identity_id"], expected_account, ACCOUNT_IDENTITY_VERSION,
                    run["canonical_domain"], approval["search_id"], approval["history_hash"],
                    approval["source_plan_hash"], raw,
                    content_hash, byte_length, actor, now,
                ),
            )
            connection.execute(
                "UPDATE dossier_runs SET state='succeeded',completed_at=?,candidate_version_id=? "
                "WHERE dossier_run_id=? AND state='running' AND cancel_requested=0",
                (now, candidate_id, run_id),
            )
            if connection.execute("SELECT changes() AS count").fetchone()["count"] != 1:
                raise MissionControlError(409, "The dossier run changed before completion.")
            self.store.insert_audit(
                connection,
                run_id=run_id,
                event_type="phase6_dossier_candidate_created",
                actor=actor,
                business_unit=business_unit,
                correlation_id=run["audit_correlation_id"],
                safe_status="pending_research_quality_review",
                recorded_at=now,
            )
            row = connection.execute(
                "SELECT * FROM dossier_candidates WHERE candidate_version_id=?", (candidate_id,)
            ).fetchone()
            return self._candidate_row(connection, row)

    def complete_dossier(self, actor: str, business_unit: str, run_id: str) -> dict[str, Any]:
        research_bundle = None
        try:
            research_bundle = self._real_bundle_for_run(actor, business_unit, run_id)
            result = self._complete_dossier(
                actor,
                business_unit,
                run_id,
                research_bundle=research_bundle,
            )
        except Exception:
            failure_class = "candidate_creation_failed"
            remediation = "Create a new exact approval before another research attempt."
            if research_bundle is not None:
                product_sources = [
                    source for source in research_bundle["sources"]
                    if "product" in source["source_class"]
                ]
                if product_sources and not any(
                    source["status"] == "success" for source in product_sources
                ):
                    failure_class = "source_read_failed_no_retry"
                    remediation = (
                        "The exact public-source attempt failed closed and must not be retried."
                    )
            if self._fail_dossier_run(
                actor,
                business_unit,
                run_id,
                failure_class=failure_class,
                remediation=remediation,
            ):
                self._unregister_active(run_id)
            raise
        self._unregister_active(run_id)
        return result

    def start_dossier(
        self, actor: str, business_unit: str, approval_event_id: str, idempotency_key: str
    ) -> tuple[dict[str, Any], bool]:
        run, created = self.claim_dossier(
            actor, business_unit, approval_event_id, idempotency_key
        )
        if run["state"] == "running":
            candidate = self.complete_dossier(actor, business_unit, run["dossier_run_id"])
        elif run["state"] == "succeeded":
            candidate = self.get_candidate(actor, business_unit, run["candidate_version_id"])
        else:
            raise MissionControlError(409, "The dossier run cannot produce a candidate.")
        return candidate, created

    def _fail_dossier_run(
        self,
        actor: str,
        business_unit: str,
        run_id: str,
        *,
        failure_class: str = "candidate_creation_failed",
        remediation: str = "Create a new exact approval before another research attempt.",
    ) -> bool:
        """Terminalize a claimed synchronous run after candidate creation fails."""
        now = self._now()
        try:
            with self.store.transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?",
                    (run_id, business_unit),
                ).fetchone()
                if (
                    row is None
                    or row["initiating_actor"] != actor
                ):
                    return False
                if row["state"] != "running":
                    return True
                connection.execute(
                    "UPDATE dossier_runs SET state='failed',completed_at=?,failure_class=?,remediation=? "
                    "WHERE dossier_run_id=? AND state='running'",
                    (
                        now,
                        failure_class,
                        remediation,
                        run_id,
                    ),
                )
                self.store.insert_audit(
                    connection,
                    run_id=run_id,
                    event_type="phase6_dossier_failed",
                    actor=actor,
                    business_unit=business_unit,
                    correlation_id=row["audit_correlation_id"],
                    safe_status="failed",
                    recorded_at=now,
                )
            return True
        except MissionControlError:
            # Preserve the active registration when cleanup cannot be proven;
            # a later authorized retry can resolve the still-governed claim.
            return False

    def _candidate_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        raw = row["dossier_snapshot"]
        encoded = raw.encode("utf-8")
        dossier = self._decode(raw, "dossier candidate")
        expected = derive_account_id(row["canonical_domain"], row["global_identity_id"])
        run = connection.execute(
            "SELECT * FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?",
            (row["dossier_run_id"], row["business_unit"]),
        ).fetchone()
        approval = connection.execute(
            "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
            (row["approval_event_id"], row["business_unit"]),
        ).fetchone()
        search = connection.execute(
            "SELECT * FROM dossier_searches WHERE search_id=? AND business_unit=?",
            (row["search_id"], row["business_unit"]),
        ).fetchone()
        if run is None or approval is None or search is None:
            raise MissionControlError(409, "The stored dossier candidate provenance is incomplete.")
        self._approval_integrity(connection, approval, require_leaf=False)
        result_row = connection.execute(
            "SELECT * FROM dossier_results WHERE result_record_id=? AND business_unit=?",
            (run["result_record_id"], row["business_unit"]),
        ).fetchone()
        if result_row is None:
            raise MissionControlError(409, "The stored dossier candidate provenance is incomplete.")
        history = self._check_snapshot(search, "history", "history snapshot")
        approved_result = self._result_row(result_row)["decision"]
        approved_result.update({
            "selected": True,
            "business_unit": row["business_unit"],
            "account_id": row["account_id"],
            "canonical_domain": row["canonical_domain"],
        })
        source_plan = self._decode(approval["source_plan_snapshot"], "source plan")
        is_real = dossier.get("synthetic") is False
        try:
            if is_real:
                validate_real_customer_dossier(
                    dossier,
                    history=history,
                    approved_result=approved_result,
                    source_plan=source_plan,
                )
            else:
                validate_customer_dossier(
                    dossier, history=history, approved_result=approved_result
                )
        except ValidationError as exc:
            raise MissionControlError(
                409, "The stored dossier candidate failed contract integrity validation."
            ) from exc
        if (
            hashlib.sha256(encoded).hexdigest() != row["content_hash"]
            or len(encoded) != row["byte_length"]
            or row["account_identity_version"] != ACCOUNT_IDENTITY_VERSION
            or row["account_id"] != expected
            or row["dossier_family_id"] != dossier.get("dossier_id")
            or row["version"] != dossier.get("version")
            or dossier.get("idempotency_identity") != (
                f"phase6-real:{row['business_unit']}:{row['result_id']}:{row['version']}"
                if is_real
                else f"phase6-runtime:{row['business_unit']}:{row['result_id']}:{row['version']}"
            )
            or dossier.get("business_unit") != row["business_unit"]
            or dossier.get("history_fingerprint") != row["history_hash"]
            or dossier.get("review_state") != "pending_research_quality_review"
            or dossier.get("release_state") != "not_released"
            or dossier.get("approved_result", {}).get("approval_id") != row["approval_event_id"]
            or dossier.get("approved_result", {}).get("account_id") != expected
            or dossier.get("approved_result", {}).get("result_id") != row["result_id"]
            or dossier.get("approved_result", {}).get("global_identity_id")
            != row["global_identity_id"]
            or dossier.get("approved_result", {}).get("canonical_domain")
            != row["canonical_domain"]
            or run["dossier_run_id"] != row["dossier_run_id"]
            or run["candidate_version_id"] != row["candidate_version_id"]
            or run["state"] != "succeeded"
            or run["approval_event_id"] != row["approval_event_id"]
            or run["result_record_id"] != approval["result_record_id"]
            or result_row["result_record_id"] != run["result_record_id"]
            or result_row["search_id"] != row["search_id"]
            or result_row["business_unit"] != row["business_unit"]
            or run["result_id"] != row["result_id"]
            or run["global_identity_id"] != row["global_identity_id"]
            or run["account_id"] != row["account_id"]
            or run["canonical_domain"] != row["canonical_domain"]
            or run["initiating_actor"] != row["proposer_actor"]
            or approval["approval_event_id"] != row["approval_event_id"]
            or approval["search_id"] != row["search_id"]
            or approval["result_id"] != row["result_id"]
            or approval["global_identity_id"] != row["global_identity_id"]
            or approval["account_id"] != row["account_id"]
            or approval["canonical_domain"] != row["canonical_domain"]
            or approval["history_hash"] != row["history_hash"]
            or approval["source_plan_hash"] != row["source_plan_hash"]
            or (is_real and dossier.get("source_plan_hash") != row["source_plan_hash"])
            or search["history_hash"] != row["history_hash"]
            or row["proposer_actor"] not in ACTOR_SCOPE
            or row["business_unit"] not in ACTOR_SCOPE.get(row["proposer_actor"], set())
        ):
            raise MissionControlError(409, "The stored dossier candidate failed its integrity check.")
        item = dict(row)
        item.pop("dossier_snapshot")
        item["dossier"] = dossier
        return item

    def get_candidate(self, actor: str, business_unit: str, candidate_id: str) -> dict[str, Any]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM dossier_candidates WHERE candidate_version_id=? AND business_unit=?",
                (candidate_id, business_unit),
            ).fetchone()
            if row is None:
                raise MissionControlError(403, "Business-unit access denied or dossier candidate not found.")
            return self._candidate_row(connection, row)
        finally:
            connection.close()

    def candidates(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM dossier_candidates WHERE business_unit=? ORDER BY candidate_version_id",
                (business_unit,),
            ).fetchall()
            return [self._candidate_row(connection, row) for row in rows]
        finally:
            connection.close()

    def review_candidate(
        self,
        actor: str,
        business_unit: str,
        candidate_id: str,
        *,
        decision: str,
        reason: str,
        idempotency_key: str,
        _human_authority: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if _human_authority:
            if actor != REAL_HUMAN_REVIEWER or business_unit not in BUSINESS_UNITS:
                raise MissionControlError(403, "The explicit human-review authority is invalid.")
        else:
            self._authorize(actor, business_unit)
        if decision not in TERMINAL_REVIEW_DECISIONS:
            raise MissionControlError(400, "Unknown terminal dossier review decision.")
        reason = _bounded_text(reason, "Review reason")
        key = _idempotency(idempotency_key)
        now = self._now()
        fingerprint = _fingerprint({
            "actor": actor,
            "business_unit": business_unit,
            "candidate_id": candidate_id,
            "decision": decision,
            "reason": reason,
        })
        with self.store.transaction() as connection:
            replay = connection.execute(
                "SELECT * FROM dossier_review_events WHERE idempotency_key=? AND business_unit=?",
                (key, business_unit),
            ).fetchone()
            if replay is not None:
                if replay["request_fingerprint"] != fingerprint:
                    raise MissionControlError(409, "Review idempotency key was used with different input.")
                return self._review_projection(connection, replay), False
            candidate_row = connection.execute(
                "SELECT * FROM dossier_candidates WHERE candidate_version_id=? AND business_unit=?",
                (candidate_id, business_unit),
            ).fetchone()
            if candidate_row is None:
                raise MissionControlError(403, "Business-unit access denied or dossier candidate not found.")
            candidate = self._candidate_row(connection, candidate_row)
            is_real = candidate["dossier"].get("synthetic") is False
            if is_real != _human_authority:
                raise MissionControlError(
                    403,
                    "A real dossier requires the explicit human review pathway; simulated local actors have no human authority.",
                )
            if actor == candidate["proposer_actor"]:
                raise MissionControlError(403, "The dossier proposer cannot perform terminal research review.")
            existing = connection.execute(
                "SELECT review_event_id FROM dossier_review_events WHERE candidate_version_id=?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                raise MissionControlError(409, "This exact dossier candidate already has a terminal review.")
            expected_account = derive_account_id(candidate["canonical_domain"], candidate["global_identity_id"])
            approval_row = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (candidate["approval_event_id"], business_unit),
            ).fetchone()
            if approval_row is None:
                raise MissionControlError(409, "The dossier candidate approval is unavailable.")
            self._approval_integrity(connection, approval_row, require_leaf=False)
            if (
                candidate["account_id"] != expected_account
                or candidate["source_plan_hash"] != approval_row["source_plan_hash"]
                or candidate["history_hash"] != approval_row["history_hash"]
                or candidate["result_id"] != approval_row["result_id"]
            ):
                raise MissionControlError(409, "The dossier account binding failed at review.")
            review_id = self._next_id(connection, "dreview", business_unit)
            correlation_id = self._next_id(connection, "dcorr", business_unit)
            event = {
                "review_event_id": review_id,
                "candidate_version_id": candidate_id,
                "idempotency_key": key,
                "request_fingerprint": fingerprint,
                "candidate_hash": candidate["content_hash"],
                "candidate_byte_length": candidate["byte_length"],
                "business_unit": business_unit,
                "decision": decision,
                "proposer_actor": candidate["proposer_actor"],
                "reviewer_actor": actor,
                "reason": reason,
                "recorded_at": now,
                "audit_correlation_id": correlation_id,
            }
            event_raw, event_hash, event_length = _canonical(event, label="Dossier review event", limit=64_000)
            try:
                connection.execute(
                    "INSERT INTO dossier_review_events ("
                    + ",".join(event)
                    + ",event_snapshot,content_hash,byte_length) VALUES ("
                    + ",".join("?" for _ in range(len(event) + 3))
                    + ")",
                    (*event.values(), event_raw, event_hash, event_length),
                )
            except sqlite3.IntegrityError as exc:
                raise MissionControlError(409, "The dossier candidate was reviewed concurrently.") from exc
            self.store._fault("insert_dossier_review")
            if decision == "accepted":
                history_row = connection.execute(
                    "SELECT history_snapshot FROM dossier_searches WHERE search_id=? AND business_unit=?",
                    (candidate["search_id"], business_unit),
                ).fetchone()
                result_row = connection.execute(
                    "SELECT dr.* FROM dossier_results dr JOIN dossier_runs run "
                    "ON run.result_record_id=dr.result_record_id AND run.business_unit=dr.business_unit "
                    "WHERE run.dossier_run_id=? AND dr.business_unit=?",
                    (candidate["dossier_run_id"], business_unit),
                ).fetchone()
                if history_row is None or result_row is None:
                    raise MissionControlError(409, "The accepted dossier provenance is unavailable.")
                history = self._decode(history_row["history_snapshot"], "history snapshot")
                approved_result = self._result_row(result_row)["decision"]
                approved_result.update({"selected": True, "business_unit": business_unit})
                final_dossier = copy.deepcopy(candidate["dossier"])
                final_dossier["review_state"] = "research_quality_accepted"
                final_dossier["release_state"] = "released_local_data_only"
                if final_dossier["approved_result"]["account_id"] != derive_account_id(
                    final_dossier["approved_result"]["canonical_domain"],
                    final_dossier["approved_result"]["global_identity_id"],
                ):
                    raise MissionControlError(409, "The dossier account binding failed at release.")
                try:
                    source_plan = self._decode(
                        approval_row["source_plan_snapshot"], "source plan"
                    )
                    if is_real:
                        validate_real_customer_dossier(
                            final_dossier,
                            history=history,
                            approved_result=approved_result,
                            source_plan=source_plan,
                        )
                    else:
                        validate_customer_dossier(
                            final_dossier, history=history, approved_result=approved_result
                        )
                    prior_rows = connection.execute(
                        "SELECT package_snapshot FROM lead_intelligence_packages "
                        "WHERE business_unit=? ORDER BY package_record_id",
                        (business_unit,),
                    ).fetchall()
                    prior_packages = [self._decode(row["package_snapshot"], "lead package") for row in prior_rows]
                    if is_real:
                        package = build_real_lead_intelligence_package(
                            final_dossier,
                            history=history,
                            approved_result=approved_result,
                            source_plan=source_plan,
                            prior_packages=prior_packages,
                        )
                        validate_real_lead_intelligence_package(
                            package, source_plan=source_plan
                        )
                    else:
                        package = build_lead_intelligence_package(
                            final_dossier,
                            history=history,
                            approved_result=approved_result,
                            prior_packages=prior_packages,
                        )
                        validate_lead_intelligence_package(package)
                except ContractConflict as exc:
                    raise MissionControlError(409, str(exc)) from exc
                except ValidationError as exc:
                    raise MissionControlError(409, "The accepted dossier release failed contract validation.") from exc
                final_raw, final_hash, final_length = _canonical(final_dossier, label="Released dossier")
                package_raw, package_hash, package_length = _canonical(
                    package, label="Lead intelligence package", limit=1_500_000
                )
                dossier_version_id = self._next_id(connection, "dversion", business_unit)
                package_record_id = self._next_id(connection, "dpackage", business_unit)
                connection.execute(
                    "INSERT INTO customer_dossier_versions (dossier_version_id,candidate_version_id,review_event_id,"
                    "dossier_family_id,version,business_unit,source_plan_hash,final_snapshot,content_hash,byte_length,"
                    "released_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dossier_version_id, candidate_id, review_id, candidate["dossier_family_id"],
                        candidate["version"], business_unit, candidate["source_plan_hash"], final_raw,
                        final_hash, final_length, now,
                    ),
                )
                self.store._fault("insert_final_dossier")
                connection.execute(
                    "INSERT INTO lead_intelligence_packages (package_record_id,package_id,dossier_version_id,"
                    "idempotency_identity,request_fingerprint,business_unit,source_plan_hash,package_snapshot,"
                    "content_hash,byte_length,released_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        package_record_id, package["package_id"], dossier_version_id,
                        package["idempotency_identity"], package["canonical_hash"], business_unit,
                        candidate["source_plan_hash"], package_raw, package_hash, package_length, now,
                    ),
                )
                self.store._fault("insert_lead_package")
            self.store.insert_audit(
                connection,
                run_id=candidate["dossier_run_id"],
                event_type="phase6_dossier_terminal_review_recorded",
                actor=actor,
                business_unit=business_unit,
                correlation_id=correlation_id,
                safe_status=decision,
                recorded_at=now,
            )
            self.store._fault("insert_dossier_release_audit")
            row = connection.execute(
                "SELECT * FROM dossier_review_events WHERE review_event_id=?", (review_id,)
            ).fetchone()
            return self._review_projection(connection, row), True

    def record_real_human_review(
        self,
        business_unit: str,
        candidate_id: str,
        *,
        decision: str,
        reason: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Persist only a directly supplied exact-version human decision."""
        return self.review_candidate(
            REAL_HUMAN_REVIEWER,
            business_unit,
            candidate_id,
            decision=decision,
            reason=reason,
            idempotency_key=idempotency_key,
            _human_authority=True,
        )

    def _review_projection(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        raw = row["event_snapshot"]
        encoded = raw.encode("utf-8")
        event = self._decode(raw, "dossier review")
        if set(event) != set(DOSSIER_REVIEW_EVENT_FIELDS):
            raise MissionControlError(409, "The dossier review failed its integrity check.")
        expected_event = {key: row[key] for key in DOSSIER_REVIEW_EVENT_FIELDS}
        expected_fingerprint = _fingerprint({
            "actor": row["reviewer_actor"],
            "business_unit": row["business_unit"],
            "candidate_id": row["candidate_version_id"],
            "decision": row["decision"],
            "reason": row["reason"],
        })
        candidate_row = connection.execute(
            "SELECT * FROM dossier_candidates WHERE candidate_version_id=? AND business_unit=?",
            (row["candidate_version_id"], row["business_unit"]),
        ).fetchone()
        candidate_snapshot = (
            self._decode(candidate_row["dossier_snapshot"], "dossier candidate")
            if candidate_row is not None
            else {}
        )
        reviewer_valid = (
            row["reviewer_actor"] == REAL_HUMAN_REVIEWER
            if candidate_snapshot.get("synthetic") is False
            else (
                row["reviewer_actor"] in ACTOR_SCOPE
                and row["business_unit"] in ACTOR_SCOPE.get(row["reviewer_actor"], set())
            )
        )
        if (
            hashlib.sha256(encoded).hexdigest() != row["content_hash"]
            or len(encoded) != row["byte_length"]
            or event != expected_event
            or row["request_fingerprint"] != expected_fingerprint
            or row["decision"] not in TERMINAL_REVIEW_DECISIONS
            or candidate_row is None
            or candidate_row["content_hash"] != row["candidate_hash"]
            or candidate_row["byte_length"] != row["candidate_byte_length"]
            or candidate_row["proposer_actor"] != row["proposer_actor"]
            or row["reviewer_actor"] == row["proposer_actor"]
            or not reviewer_valid
        ):
            raise MissionControlError(409, "The dossier review failed its integrity check.")
        candidate = self._candidate_row(connection, candidate_row)
        final_row = connection.execute(
            "SELECT * FROM customer_dossier_versions WHERE review_event_id=? AND business_unit=?",
            (row["review_event_id"], row["business_unit"]),
        ).fetchone()
        package_row = None
        if final_row is not None:
            package_row = connection.execute(
                "SELECT * FROM lead_intelligence_packages WHERE dossier_version_id=? AND business_unit=?",
                (final_row["dossier_version_id"], row["business_unit"]),
            ).fetchone()
        if row["decision"] != "accepted":
            if final_row is not None or package_row is not None:
                raise MissionControlError(409, "A non-accepted review cannot have a released artifact.")
            return {"review": event, "package": None}
        if final_row is None or package_row is None:
            raise MissionControlError(409, "The accepted review has an incomplete atomic release.")
        final_raw = final_row["final_snapshot"]
        final_encoded = final_raw.encode("utf-8")
        final_dossier = self._decode(final_raw, "released dossier")
        expected_final = copy.deepcopy(candidate["dossier"])
        expected_final["review_state"] = "research_quality_accepted"
        expected_final["release_state"] = "released_local_data_only"
        if (
            hashlib.sha256(final_encoded).hexdigest() != final_row["content_hash"]
            or len(final_encoded) != final_row["byte_length"]
            or final_dossier != expected_final
            or final_row["candidate_version_id"] != candidate["candidate_version_id"]
            or final_row["dossier_family_id"] != candidate["dossier_family_id"]
            or final_row["version"] != candidate["version"]
            or final_row["source_plan_hash"] != candidate["source_plan_hash"]
            or package_row["source_plan_hash"] != candidate["source_plan_hash"]
        ):
            raise MissionControlError(409, "The accepted dossier release failed its integrity check.")
        package = self._package_row(package_row)
        if (
            package["package"]["dossier_id"] != final_dossier["dossier_id"]
            or package["package"]["dossier_version"] != final_dossier["version"]
            or package["package"]["business_unit"] != row["business_unit"]
        ):
            raise MissionControlError(409, "The lead package release binding failed its integrity check.")
        if final_dossier.get("synthetic") is False:
            history_row = connection.execute(
                "SELECT history_snapshot FROM dossier_searches WHERE search_id=? AND business_unit=?",
                (candidate["search_id"], row["business_unit"]),
            ).fetchone()
            result_row = connection.execute(
                "SELECT * FROM dossier_results WHERE result_record_id=("
                "SELECT result_record_id FROM dossier_runs WHERE dossier_run_id=? AND business_unit=?"
                ") AND business_unit=?",
                (candidate["dossier_run_id"], row["business_unit"], row["business_unit"]),
            ).fetchone()
            approval_row = connection.execute(
                "SELECT * FROM dossier_approval_events WHERE approval_event_id=? AND business_unit=?",
                (candidate["approval_event_id"], row["business_unit"]),
            ).fetchone()
            if history_row is None or result_row is None or approval_row is None:
                raise MissionControlError(409, "The real lead package projection provenance is unavailable.")
            approved_result = self._result_row(result_row)["decision"]
            approved_result.update({"selected": True, "business_unit": row["business_unit"]})
            source_plan = self._decode(approval_row["source_plan_snapshot"], "source plan")
            try:
                expected_package = build_real_lead_intelligence_package(
                    final_dossier,
                    history=self._decode(history_row["history_snapshot"], "history snapshot"),
                    approved_result=approved_result,
                    source_plan=source_plan,
                )
            except ValidationError as exc:
                raise MissionControlError(409, "The real lead package projection failed validation.") from exc
            if package["package"] != expected_package:
                raise MissionControlError(409, "The real lead package is not the canonical dossier projection.")
        return {"review": event, "package": package}

    def _package_row(self, row: sqlite3.Row) -> dict[str, Any]:
        raw = row["package_snapshot"]
        encoded = raw.encode("utf-8")
        package = self._decode(raw, "lead intelligence package")
        if hashlib.sha256(encoded).hexdigest() != row["content_hash"] or len(encoded) != row["byte_length"]:
            raise MissionControlError(409, "The lead intelligence package failed its integrity check.")
        try:
            if package.get("synthetic") is False:
                plan = self._real_plan_for_id(
                    package.get("approved_result", {}).get("source_plan_id")
                )
                validate_real_lead_intelligence_package(package, source_plan=plan)
            else:
                validate_lead_intelligence_package(package)
        except ValidationError as exc:
            raise MissionControlError(409, "The lead intelligence package failed contract validation.") from exc
        expected = derive_account_id(
            package["approved_result"]["canonical_domain"],
            package["approved_result"]["global_identity_id"],
        )
        if (
            package["approved_result"]["account_id"] != expected
            or row["package_id"] != package["package_id"]
            or row["idempotency_identity"] != package["idempotency_identity"]
            or row["request_fingerprint"] != package["canonical_hash"]
            or row["business_unit"] != package["business_unit"]
            or (
                package.get("synthetic") is False
                and row["source_plan_hash"] != package.get("source_plan_hash")
            )
        ):
            raise MissionControlError(409, "The lead package account binding failed its integrity check.")
        if not re.fullmatch(r"[0-9a-f]{64}", row["source_plan_hash"]):
            raise MissionControlError(409, "The lead package source-plan binding failed its integrity check.")
        item = dict(row)
        item.pop("package_snapshot")
        item["package"] = package
        return item

    def packages(self, actor: str, business_unit: str) -> list[dict[str, Any]]:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM lead_intelligence_packages WHERE business_unit=? ORDER BY package_record_id",
                (business_unit,),
            ).fetchall()
            items = []
            for package_row in rows:
                review_row = connection.execute(
                    "SELECT review.* FROM dossier_review_events review "
                    "JOIN customer_dossier_versions final "
                    "ON final.review_event_id=review.review_event_id "
                    "AND final.business_unit=review.business_unit "
                    "WHERE final.dossier_version_id=? AND review.business_unit=?",
                    (package_row["dossier_version_id"], business_unit),
                ).fetchone()
                if review_row is None:
                    raise MissionControlError(409, "The lead package release provenance is unavailable.")
                projected = self._review_projection(connection, review_row)["package"]
                if projected is None or projected["package_record_id"] != package_row["package_record_id"]:
                    raise MissionControlError(409, "The lead package release provenance is inconsistent.")
                items.append(projected)
            return items
        finally:
            connection.close()

    def review_for_candidate(
        self, actor: str, business_unit: str, candidate_id: str
    ) -> dict[str, Any] | None:
        self._authorize(actor, business_unit)
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM dossier_review_events WHERE candidate_version_id=? AND business_unit=?",
                (candidate_id, business_unit),
            ).fetchone()
            return self._review_projection(connection, row) if row is not None else None
        finally:
            connection.close()
