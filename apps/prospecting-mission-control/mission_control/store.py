"""Durable local SQLite control-plane store for the Phase 4 registered worker.

The store is repository-local. It initializes additively and idempotently, keeps
every governed multi-record change inside one serialized transaction, and never
uses DROP, TRUNCATE, or delete-and-recreate recovery.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .application import MissionControlError

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 2_000
MAX_CONFIG_BYTES = 16_384
MAX_SNAPSHOT_BYTES = 262_144
RUN_STATES = {
    "queued",
    "running",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "timed_out",
    "cancelled",
}

_TABLES = (
    """CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS id_counters (
        prefix TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS worker_runs (
        run_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        agent_version TEXT NOT NULL,
        campaign_family_id TEXT NOT NULL,
        campaign_version INTEGER NOT NULL,
        business_unit TEXT NOT NULL,
        initiating_actor TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        skill_integrity TEXT NOT NULL,
        configuration_snapshot TEXT NOT NULL,
        configuration_hash TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        request_fingerprint TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN (
            'queued','running','succeeded','failed_retryable',
            'failed_terminal','timed_out','cancelled')),
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL,
        timeout_seconds INTEGER NOT NULL,
        cost_cap_usd INTEGER NOT NULL,
        actual_cost_usd INTEGER,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL,
        failure_class TEXT,
        retryable INTEGER,
        remediation TEXT,
        output_id TEXT,
        review_task_id TEXT,
        row_version INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS worker_attempts (
        attempt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES worker_runs(run_id),
        attempt_number INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        starting_state TEXT NOT NULL,
        terminal_state TEXT,
        worker_version TEXT NOT NULL,
        timeout_seconds INTEGER NOT NULL,
        outcome TEXT,
        failure_class TEXT,
        retryable INTEGER,
        estimated_cost_usd INTEGER,
        output_hash TEXT,
        audit_correlation_id TEXT NOT NULL,
        UNIQUE (run_id, attempt_number)
    )""",
    """CREATE TABLE IF NOT EXISTS worker_outputs (
        output_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE REFERENCES worker_runs(run_id),
        attempt_number INTEGER NOT NULL,
        business_unit TEXT NOT NULL,
        campaign_family_id TEXT NOT NULL,
        campaign_version INTEGER NOT NULL,
        skill_id TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        output_schema TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        byte_length INTEGER NOT NULL,
        fixture_source_ids TEXT NOT NULL,
        result_ids TEXT NOT NULL,
        estimated_cost_usd INTEGER NOT NULL,
        errors TEXT NOT NULL,
        stop_reason TEXT NOT NULL,
        result_snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS review_tasks (
        task_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE REFERENCES worker_runs(run_id),
        output_id TEXT NOT NULL UNIQUE REFERENCES worker_outputs(output_id),
        business_unit TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending_review',
        allowed_reviewers TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        audit_id TEXT PRIMARY KEY,
        run_id TEXT,
        event_type TEXT NOT NULL,
        actor TEXT NOT NULL,
        business_unit TEXT,
        correlation_id TEXT NOT NULL,
        safe_status TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
)


def canonical_json(value: Any, *, limit: int, label: str) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > limit:
        raise MissionControlError(400, f"{label} exceeds the bounded local storage size.")
    return serialized


def decoded_list(raw: str, label: str) -> list[Any]:
    value = json.loads(raw)
    if not isinstance(value, list):
        raise MissionControlError(500, f"Stored {label} has an unexpected shape.")
    return value


class IdempotencyKeyExists(Exception):
    """A concurrent create already persisted this idempotency key.

    Raised instead of a storage error so the service can resolve the race as an
    idempotent replay or a safe conflict, never a 500.
    """


class SqliteStore:
    """Connection-per-operation adapter. All governed writes are transactional."""

    def __init__(self, path: Path, fault_hook: Callable[[str], None] | None = None):
        self.path = Path(path)
        self.fault_hook = fault_hook
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _fault(self, label: str) -> None:
        if self.fault_hook:
            self.fault_hook(label)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        except sqlite3.Error as exc:
            raise MissionControlError(500, "The local durable store is unavailable.") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection

    def _initialize(self) -> None:
        with self.transaction() as connection:
            for statement in _TABLES:
                connection.execute(statement)
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise MissionControlError(500, "The local state file uses a newer schema version.")

    class _Transaction:
        def __init__(self, store: "SqliteStore"):
            self.store = store
            self.connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self.connection = self.store._connect()
            try:
                self.connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                self.connection.close()
                raise MissionControlError(500, "The local durable store is busy or unavailable.") from exc
            return self.connection

        def __exit__(self, exc_type, exc, tb) -> bool:
            assert self.connection is not None
            try:
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()
            finally:
                self.connection.close()
            if exc_type is not None and issubclass(exc_type, sqlite3.Error):
                raise MissionControlError(500, "The durable transaction was rolled back.") from exc
            return False

    def transaction(self) -> "SqliteStore._Transaction":
        return SqliteStore._Transaction(self)

    def next_id(self, connection: sqlite3.Connection, prefix: str) -> str:
        connection.execute(
            "INSERT INTO id_counters (prefix, value) VALUES (?, 0) ON CONFLICT (prefix) DO NOTHING",
            (prefix,),
        )
        connection.execute("UPDATE id_counters SET value = value + 1 WHERE prefix = ?", (prefix,))
        value = connection.execute(
            "SELECT value FROM id_counters WHERE prefix = ?", (prefix,)
        ).fetchone()["value"]
        return f"{prefix}-{value:04d}"

    def insert_audit(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str | None,
        event_type: str,
        actor: str,
        business_unit: str | None,
        correlation_id: str,
        safe_status: str,
        recorded_at: str,
    ) -> str:
        self._fault("insert_audit")
        audit_id = self.next_id(connection, "waudit")
        connection.execute(
            "INSERT INTO audit_events (audit_id, run_id, event_type, actor, business_unit,"
            " correlation_id, safe_status, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
            (audit_id, run_id, event_type, actor, business_unit, correlation_id, safe_status, recorded_at),
        )
        return audit_id

    def record_audit(self, **kwargs: Any) -> str:
        with self.transaction() as connection:
            return self.insert_audit(connection, **kwargs)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        run = dict(row)
        run["configuration"] = json.loads(run.pop("configuration_snapshot"))
        run["cancel_requested"] = bool(run["cancel_requested"])
        run["retryable"] = None if run["retryable"] is None else bool(run["retryable"])
        return run

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        return self._row_to_run(row) if row else None

    def find_run_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM worker_runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        finally:
            connection.close()
        return self._row_to_run(row) if row else None

    def list_runs(self, business_units: set[str]) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM worker_runs ORDER BY run_id"
            ).fetchall()
        finally:
            connection.close()
        return [self._row_to_run(row) for row in rows if row["business_unit"] in business_units]

    def create_run(self, fields: dict[str, Any], *, now: str) -> dict[str, Any]:
        snapshot = canonical_json(fields["configuration"], limit=MAX_CONFIG_BYTES, label="Configuration snapshot")
        with self.transaction() as connection:
            self._fault("insert_run")
            existing = connection.execute(
                "SELECT run_id FROM worker_runs WHERE idempotency_key = ?",
                (fields["idempotency_key"],),
            ).fetchone()
            if existing is not None:
                raise IdempotencyKeyExists(existing["run_id"])
            run_id = self.next_id(connection, "wrun")
            correlation_id = self.next_id(connection, "wcorr")
            statement = (
                "INSERT INTO worker_runs (run_id, agent_id, agent_version, campaign_family_id,"
                " campaign_version, business_unit, initiating_actor, skill_id, skill_version,"
                " skill_integrity, configuration_snapshot, configuration_hash, idempotency_key,"
                " request_fingerprint, state, attempt_count, max_attempts, timeout_seconds,"
                " cost_cap_usd, cancel_requested, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'queued', 0, ?, ?, ?, 0, ?, ?)"
            )
            values = (
                    run_id,
                    fields["agent_id"],
                    fields["agent_version"],
                    fields["campaign_family_id"],
                    fields["campaign_version"],
                    fields["business_unit"],
                    fields["initiating_actor"],
                    fields["skill_id"],
                    fields["skill_version"],
                    fields["skill_integrity"],
                    snapshot,
                    fields["configuration_hash"],
                    fields["idempotency_key"],
                    fields["request_fingerprint"],
                    fields["max_attempts"],
                    fields["timeout_seconds"],
                    fields["cost_cap_usd"],
                    now,
                    now,
                )
            try:
                connection.execute(statement, values)
            except sqlite3.IntegrityError as exc:
                if "idempotency_key" in str(exc):
                    raise IdempotencyKeyExists(fields["idempotency_key"]) from exc
                raise
            self.insert_audit(
                connection,
                run_id=run_id,
                event_type="manual_request_accepted",
                actor=fields["initiating_actor"],
                business_unit=fields["business_unit"],
                correlation_id=correlation_id,
                safe_status="queued",
                recorded_at=now,
            )
        run = self.get_run(run_id)
        assert run is not None
        return run

    def claim_attempt(
        self,
        run_id: str,
        *,
        expected_states: tuple[str, ...],
        actor: str,
        worker_version: str,
        now: str,
        retry_accepted: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically move the run to running and append the next attempt.

        Returns None when another transition already won, so a double claim can
        never start two attempts. Stale terminal-only metadata (completed_at,
        failure class, retryability, remediation) is cleared in the same atomic
        transition; first-start history stays on the logical run's started_at
        and on the earlier append-only attempt rows. When retry_accepted is
        set, the acceptance audit commits inside this same transaction, so it
        can never claim acceptance without a claimed attempt.
        """
        placeholders = ",".join("?" for _ in expected_states)
        with self.transaction() as connection:
            self._fault("claim_attempt")
            cursor = connection.execute(
                f"UPDATE worker_runs SET state = 'running', attempt_count = attempt_count + 1,"
                f" started_at = COALESCE(started_at, ?), completed_at = NULL, updated_at = ?,"
                f" failure_class = NULL, retryable = NULL, remediation = NULL,"
                f" row_version = row_version + 1"
                f" WHERE run_id = ? AND state IN ({placeholders})"
                f" AND cancel_requested = 0 AND attempt_count < max_attempts",
                (now, now, run_id, *expected_states),
            )
            if cursor.rowcount != 1:
                return None
            run = connection.execute(
                "SELECT * FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            attempt_id = self.next_id(connection, "wattempt")
            correlation_id = self.next_id(connection, "wcorr")
            connection.execute(
                "INSERT INTO worker_attempts (attempt_id, run_id, attempt_number, started_at,"
                " starting_state, worker_version, timeout_seconds, audit_correlation_id)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    run_id,
                    run["attempt_count"],
                    now,
                    "running",
                    worker_version,
                    run["timeout_seconds"],
                    correlation_id,
                ),
            )
            if retry_accepted:
                self.insert_audit(
                    connection,
                    run_id=run_id,
                    event_type="retry_accepted",
                    actor=actor,
                    business_unit=run["business_unit"],
                    correlation_id=correlation_id,
                    safe_status=f"attempt {run['attempt_count']} of {run['max_attempts']}",
                    recorded_at=now,
                )
            self.insert_audit(
                connection,
                run_id=run_id,
                event_type="attempt_started",
                actor=actor,
                business_unit=run["business_unit"],
                correlation_id=correlation_id,
                safe_status=f"attempt {run['attempt_count']} of {run['max_attempts']}",
                recorded_at=now,
            )
            return {
                "attempt_id": attempt_id,
                "attempt_number": run["attempt_count"],
                "correlation_id": correlation_id,
            }

    def commit_success(
        self,
        run_id: str,
        *,
        attempt_number: int,
        actor: str,
        correlation_id: str,
        output: dict[str, Any],
        allowed_reviewers: list[str],
        now: str,
    ) -> dict[str, Any] | None:
        """Atomically persist output, attempt completion, run success, the review
        task, and the audit trail. Returns None when cancellation already won.
        """
        snapshot = canonical_json(output["result_snapshot"], limit=MAX_SNAPSHOT_BYTES, label="Result snapshot")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_runs SET state = 'succeeded', completed_at = ?, updated_at = ?,"
                " actual_cost_usd = ?, row_version = row_version + 1"
                " WHERE run_id = ? AND state = 'running' AND cancel_requested = 0",
                (now, now, output["estimated_cost_usd"], run_id),
            )
            if cursor.rowcount != 1:
                return None
            run = connection.execute(
                "SELECT * FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            self._fault("insert_output")
            output_id = self.next_id(connection, "woutput")
            connection.execute(
                "INSERT INTO worker_outputs (output_id, run_id, attempt_number, business_unit,"
                " campaign_family_id, campaign_version, skill_id, skill_version, output_schema,"
                " content_hash, byte_length, fixture_source_ids, result_ids, estimated_cost_usd,"
                " errors, stop_reason, result_snapshot, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    output_id,
                    run_id,
                    attempt_number,
                    run["business_unit"],
                    run["campaign_family_id"],
                    run["campaign_version"],
                    run["skill_id"],
                    run["skill_version"],
                    output["output_schema"],
                    output["content_hash"],
                    output["byte_length"],
                    json.dumps(output["fixture_source_ids"]),
                    json.dumps(output["result_ids"]),
                    output["estimated_cost_usd"],
                    json.dumps(output["errors"]),
                    output["stop_reason"],
                    snapshot,
                    now,
                ),
            )
            connection.execute(
                "UPDATE worker_attempts SET completed_at = ?, terminal_state = 'succeeded',"
                " outcome = 'succeeded', estimated_cost_usd = ?, output_hash = ?, retryable = 0"
                " WHERE run_id = ? AND attempt_number = ?",
                (now, output["estimated_cost_usd"], output["content_hash"], run_id, attempt_number),
            )
            self._fault("insert_review_task")
            task_id = self.next_id(connection, "wreview")
            connection.execute(
                "INSERT INTO review_tasks (task_id, run_id, output_id, business_unit, state,"
                " allowed_reviewers, created_at) VALUES (?,?,?,?, 'pending_review', ?, ?)",
                (task_id, run_id, output_id, run["business_unit"], json.dumps(sorted(allowed_reviewers)), now),
            )
            connection.execute(
                "UPDATE worker_runs SET output_id = ?, review_task_id = ? WHERE run_id = ?",
                (output_id, task_id, run_id),
            )
            for event_type, safe_status in (
                ("attempt_succeeded", f"attempt {attempt_number}"),
                ("output_committed", output["content_hash"][:16]),
                ("review_task_created", task_id),
            ):
                self.insert_audit(
                    connection,
                    run_id=run_id,
                    event_type=event_type,
                    actor=actor,
                    business_unit=run["business_unit"],
                    correlation_id=correlation_id,
                    safe_status=safe_status,
                    recorded_at=now,
                )
            return {"output_id": output_id, "review_task_id": task_id}

    def commit_failure(
        self,
        run_id: str,
        *,
        attempt_number: int,
        terminal_state: str,
        failure_class: str,
        retryable: bool,
        remediation: str,
        event_type: str,
        actor: str,
        correlation_id: str,
        now: str,
        estimated_cost_usd: int | None = None,
    ) -> bool:
        if terminal_state not in RUN_STATES or terminal_state in {"queued", "running", "succeeded"}:
            raise MissionControlError(500, "Unknown terminal worker state.")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_runs SET state = ?, completed_at = ?, updated_at = ?,"
                " failure_class = ?, retryable = ?, remediation = ?,"
                " actual_cost_usd = COALESCE(?, actual_cost_usd), row_version = row_version + 1"
                " WHERE run_id = ? AND state = 'running'",
                (
                    terminal_state,
                    now,
                    now,
                    failure_class,
                    int(retryable),
                    remediation,
                    estimated_cost_usd,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                return False
            run = connection.execute(
                "SELECT * FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            connection.execute(
                "UPDATE worker_attempts SET completed_at = ?, terminal_state = ?, outcome = ?,"
                " failure_class = ?, retryable = ?,"
                " estimated_cost_usd = COALESCE(?, estimated_cost_usd)"
                " WHERE run_id = ? AND attempt_number = ?",
                (
                    now,
                    terminal_state,
                    failure_class,
                    failure_class,
                    int(retryable),
                    estimated_cost_usd,
                    run_id,
                    attempt_number,
                ),
            )
            self.insert_audit(
                connection,
                run_id=run_id,
                event_type=event_type,
                actor=actor,
                business_unit=run["business_unit"],
                correlation_id=correlation_id,
                safe_status=failure_class,
                recorded_at=now,
            )
            return True

    def cancel_queued(self, run_id: str, *, actor: str, now: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_runs SET state = 'cancelled', cancel_requested = 1,"
                " completed_at = ?, updated_at = ?, failure_class = 'cancelled', retryable = 0,"
                " remediation = 'Create a new manual run with a new idempotency key if needed.',"
                " row_version = row_version + 1 WHERE run_id = ? AND state = 'queued'",
                (now, now, run_id),
            )
            if cursor.rowcount != 1:
                return False
            run = connection.execute(
                "SELECT business_unit FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            correlation_id = self.next_id(connection, "wcorr")
            for event_type in ("cancellation_requested", "cancellation_completed"):
                self.insert_audit(
                    connection,
                    run_id=run_id,
                    event_type=event_type,
                    actor=actor,
                    business_unit=run["business_unit"],
                    correlation_id=correlation_id,
                    safe_status="cancelled before execution",
                    recorded_at=now,
                )
            return True

    def request_running_cancellation(self, run_id: str, *, actor: str, now: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_runs SET cancel_requested = 1, updated_at = ?,"
                " row_version = row_version + 1"
                " WHERE run_id = ? AND state = 'running' AND cancel_requested = 0",
                (now, run_id),
            )
            if cursor.rowcount != 1:
                return False
            run = connection.execute(
                "SELECT business_unit FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            self.insert_audit(
                connection,
                run_id=run_id,
                event_type="cancellation_requested",
                actor=actor,
                business_unit=run["business_unit"],
                correlation_id=self.next_id(connection, "wcorr"),
                safe_status="cooperative cancellation requested",
                recorded_at=now,
            )
            return True

    def complete_cancellation(
        self, run_id: str, *, attempt_number: int, actor: str, correlation_id: str, now: str
    ) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_runs SET state = 'cancelled', completed_at = ?, updated_at = ?,"
                " failure_class = 'cancelled', retryable = 0,"
                " remediation = 'Create a new manual run with a new idempotency key if needed.',"
                " row_version = row_version + 1 WHERE run_id = ? AND state = 'running'",
                (now, now, run_id),
            )
            if cursor.rowcount != 1:
                return False
            run = connection.execute(
                "SELECT business_unit FROM worker_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            connection.execute(
                "UPDATE worker_attempts SET completed_at = ?, terminal_state = 'cancelled',"
                " outcome = 'cancelled', failure_class = 'cancelled', retryable = 0"
                " WHERE run_id = ? AND attempt_number = ?",
                (now, run_id, attempt_number),
            )
            self.insert_audit(
                connection,
                run_id=run_id,
                event_type="cancellation_completed",
                actor=actor,
                business_unit=run["business_unit"],
                correlation_id=correlation_id,
                safe_status="cooperative cancellation completed",
                recorded_at=now,
            )
            return True

    def recover_interrupted(self, *, now: str) -> list[str]:
        """Reconcile runs abandoned in running state by a prior process exit."""
        recovered: list[str] = []
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT run_id, attempt_count FROM worker_runs WHERE state = 'running' ORDER BY run_id"
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            with self.transaction() as connection:
                cursor = connection.execute(
                    "UPDATE worker_runs SET state = 'failed_retryable', updated_at = ?,"
                    " cancel_requested = 0,"
                    " failure_class = 'interrupted_execution_recovered', retryable = 1,"
                    " remediation = 'A prior process exit interrupted this attempt."
                    " Review the run and retry manually if the budget remains.',"
                    " row_version = row_version + 1 WHERE run_id = ? AND state = 'running'",
                    (now, row["run_id"]),
                )
                if cursor.rowcount != 1:
                    continue
                run = connection.execute(
                    "SELECT business_unit FROM worker_runs WHERE run_id = ?", (row["run_id"],)
                ).fetchone()
                connection.execute(
                    "UPDATE worker_attempts SET completed_at = ?, terminal_state = 'failed_retryable',"
                    " outcome = 'interrupted', failure_class = 'interrupted_execution_recovered',"
                    " retryable = 1 WHERE run_id = ? AND attempt_number = ? AND completed_at IS NULL",
                    (now, row["run_id"], row["attempt_count"]),
                )
                self.insert_audit(
                    connection,
                    run_id=row["run_id"],
                    event_type="startup_interruption_recovered",
                    actor="system-startup-recovery",
                    business_unit=run["business_unit"],
                    correlation_id=self.next_id(connection, "wcorr"),
                    safe_status="interrupted attempt reconciled; manual retry required",
                    recorded_at=now,
                )
                recovered.append(row["run_id"])
        return recovered

    def attempts_for(self, run_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM worker_attempts WHERE run_id = ? ORDER BY attempt_number", (run_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def get_output(self, run_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM worker_outputs WHERE run_id = ?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return None
        output = dict(row)
        snapshot = output["result_snapshot"]
        if not isinstance(snapshot, str):
            raise MissionControlError(500, "Stored result snapshot failed its integrity check.")
        snapshot_bytes = snapshot.encode("utf-8")
        if (
            type(output["byte_length"]) is not int
            or len(snapshot_bytes) != output["byte_length"]
            or hashlib.sha256(snapshot_bytes).hexdigest() != output["content_hash"]
        ):
            raise MissionControlError(500, "Stored result snapshot failed its integrity check.")
        output["fixture_source_ids"] = decoded_list(output["fixture_source_ids"], "fixture source ids")
        output["result_ids"] = decoded_list(output["result_ids"], "result ids")
        output["errors"] = decoded_list(output["errors"], "errors")
        try:
            output["result_snapshot"] = json.loads(snapshot)
        except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
            raise MissionControlError(500, "Stored result snapshot has an unexpected shape.") from exc
        if not isinstance(output["result_snapshot"], list):
            raise MissionControlError(500, "Stored result snapshot has an unexpected shape.")
        return output

    def list_review_tasks(self, business_units: set[str]) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM review_tasks ORDER BY task_id").fetchall()
        finally:
            connection.close()
        tasks = []
        for row in rows:
            if row["business_unit"] not in business_units:
                continue
            task = dict(row)
            task["allowed_reviewers"] = decoded_list(task["allowed_reviewers"], "allowed reviewers")
            tasks.append(task)
        return tasks

    def get_review_task(self, task_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM review_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return None
        task = dict(row)
        task["allowed_reviewers"] = decoded_list(task["allowed_reviewers"], "allowed reviewers")
        return task

    def audits_for(self, run_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE run_id = ? ORDER BY audit_id", (run_id,)
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def integrity_report(self) -> tuple[str, list[Any]]:
        connection = self._connect()
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()
        return integrity, [tuple(row) for row in foreign_keys]
