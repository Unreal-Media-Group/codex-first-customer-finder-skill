"""Phase 6A synthetic fixture enrichment and research-quality brief review tests."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "apps" / "prospecting-mission-control"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from mission_control.agent import RegisteredAgentService  # noqa: E402
from mission_control.application import CounterIds, FixtureRepository, MissionControl, MissionControlError  # noqa: E402
from mission_control.enrichment import (  # noqa: E402
    APPROVAL_SCOPE,
    REQUIRED_BRIEF_FIELDS,
    EnrichmentService,
    _APPROVAL_EVENT_FIELDS,
    _BRIEF_REVIEW_EVENT_FIELDS,
    _validate_event_integrity,
)
from mission_control.store import PHASE4_TABLES, PHASE6_SCHEMA_VERSION, SCHEMA_VERSION, SqliteStore  # noqa: E402
from mission_control.web import WebApplication, build_server  # noqa: E402

PHASE3_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase3" / "prospects.json"
PHASE6_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "brief-fixtures.json"
FIXED = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, value: datetime = FIXED):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FaultInjector:
    def __init__(self) -> None:
        self.armed: str | None = None

    def __call__(self, label: str) -> None:
        if self.armed == label:
            self.armed = None
            raise sqlite3.OperationalError("synthetic storage fault")


class Phase6Env:
    def __init__(self, directory: Path, *, unit: str = "unreal-media-group", fault=None):
        self.clock = Clock()
        self.repository = FixtureRepository(
            clock=self.clock, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE
        )
        self.control = MissionControl(self.repository)
        self.path = directory / f"phase6-{unit}.sqlite3"
        self.store = SqliteStore(self.path, fault_hook=fault)
        self.agent = RegisteredAgentService(self.repository, self.store, clock=self.clock)
        self.enrichment = EnrichmentService(
            self.agent, fixture_path=PHASE6_FIXTURE, clock=self.clock
        )
        worker = self.add_worker_run(unit, f"phase6-{unit}-worker")
        self.run = worker["run"]
        self.task = worker["task"]
        self.output = worker["output"]
        self.result = worker["result"]

    def add_worker_run(self, unit: str, idempotency_key: str) -> dict:
        form = {
            "business_unit": unit,
            "campaign_name": f"Synthetic Phase 6A {unit}",
            "discovery_scope": "open" if unit == "unreal-media-group" else "filtered",
            "verticals": "" if unit == "unreal-media-group" else "sports apparel",
            "target_prospect_count": "20",
            "minimum_qualification_score": "72" if unit == "unreal-media-group" else "78",
            "cooldown_days": "120" if unit == "unreal-media-group" else "180",
            "maximum_evidence_age_days": "180",
            "geography": "United States",
            "rights_territory": "" if unit == "unreal-media-group" else "United States",
            "talent_categories": "" if unit == "unreal-media-group" else "athlete archetype",
        }
        config = self.control.campaign_config(form)
        campaign = self.repository.add_campaign("noah", config)
        run, _created = self.agent.create_manual_run(
            "noah", campaign["family_id"], campaign["version"], idempotency_key
        )
        durable_run = self.agent.execute_run("noah", run["run_id"])
        detail = self.agent.run_detail("noah", durable_run["run_id"])
        result = next(
            item for item in detail["output"]["result_snapshot"]
            if item.get("queue") == "new" and not item.get("effective_suppressed")
        )
        return {
            "run": durable_run,
            "task": detail["review_task"],
            "output": detail["output"],
            "result": result,
        }

    def approve(self, actor: str | None = None, **kwargs):
        reviewer = actor or ("rob" if self.task["business_unit"] == "unreal-media-group" else "dan")
        return self.enrichment.record_approval(
            reviewer,
            self.task["business_unit"],
            self.task["task_id"],
            self.result["prospect_id"],
            decision="approved",
            reason="Synthetic fixture enrichment approved for research review.",
            **kwargs,
        )


class Phase6Case(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name)


class ApprovalTests(Phase6Case):
    def test_durable_result_bound_approval_is_the_only_authority(self) -> None:
        env = Phase6Env(self.directory)
        env.repository.act(
            "rob", "unreal-media-group", env.result["prospect_id"],
            "approve_deeper_research", {"reason": "process local only"},
        )
        with self.assertRaisesRegex(MissionControlError, "durable approval"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", "missing-approval", "phase6-local-only"
            )
        with self.assertRaisesRegex(MissionControlError, "protections changed"):
            env.approve()

        env = Phase6Env(self.directory / "durable")
        approval = env.approve()
        self.assertEqual(approval["scope"], APPROVAL_SCOPE)
        with self.assertRaisesRegex(MissionControlError, "bound run proposer"):
            env.enrichment.start_enrichment(
                "rob", "unreal-media-group", approval["approval_event_id"], "wrong-starter"
            )
        self.assertEqual(env.enrichment.runs("rob", "unreal-media-group"), [])
        restarted = EnrichmentService(
            RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
            fixture_path=PHASE6_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(
            restarted.get_approval("rob", "unreal-media-group", approval["approval_event_id"])["result_id"],
            env.result["prospect_id"],
        )

    def test_exact_seven_day_boundary_uses_effective_not_recorded_time(self) -> None:
        env = Phase6Env(self.directory)
        effective = FIXED - timedelta(days=6, seconds=86399)
        approval = env.approve(effective_at=effective)
        self.assertEqual(
            approval["expires_at"],
            (effective + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        )
        env.clock.value = effective + timedelta(days=7) - timedelta(microseconds=1)
        run, created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "before-expiry"
        )
        self.assertTrue(created)
        self.assertEqual(run["state"], "succeeded")

        later = Phase6Env(self.directory / "equal")
        approval = later.approve(effective_at=FIXED - timedelta(days=7))
        with self.assertRaisesRegex(MissionControlError, "expired"):
            later.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "at-expiry"
            )
        later.clock.value += timedelta(microseconds=1)
        with self.assertRaisesRegex(MissionControlError, "expired"):
            later.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "after-expiry"
            )

    def test_binding_scope_identity_protection_and_separation_fail_closed(self) -> None:
        env = Phase6Env(self.directory)
        with self.assertRaisesRegex(MissionControlError, "self-approve"):
            env.approve(actor="noah")
        with self.assertRaisesRegex(MissionControlError, "Actor"):
            env.approve(actor="mallory")
        with self.assertRaisesRegex(MissionControlError, "Business-unit"):
            env.enrichment.record_approval(
                "dan", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
                decision="approved", reason="wrong unit",
            )
        with self.assertRaisesRegex(MissionControlError, "Result"):
            env.enrichment.record_approval(
                "rob", "unreal-media-group", env.task["task_id"], "missing-result",
                decision="approved", reason="wrong result",
            )
        approval = env.approve()
        with env.store.transaction() as connection:
            connection.execute(
                "UPDATE worker_outputs SET byte_length=byte_length+1 WHERE output_id=?",
                (env.output["output_id"],),
            )
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "tampered"
            )

    def test_supersession_revocation_and_fork_protection_are_append_only(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        revoked = env.enrichment.record_approval(
            "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
            decision="revoked", reason="Research authority withdrawn.",
            expected_leaf_id=approval["approval_event_id"],
        )
        with self.assertRaisesRegex(MissionControlError, "current approved"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "revoked"
            )
        with self.assertRaisesRegex(MissionControlError, "current leaf"):
            env.enrichment.record_approval(
                "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
                decision="approved", reason="stale fork",
                expected_leaf_id=approval["approval_event_id"],
            )
        history = env.enrichment.approvals("rob", "unreal-media-group")
        self.assertEqual([item["decision"] for item in history], ["approved", "revoked"])
        self.assertEqual(revoked["supersedes_id"], approval["approval_event_id"])

    def test_concurrent_supersessions_have_one_winner_and_one_conflict(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def supersede(decision: str) -> None:
            barrier.wait()
            try:
                env.enrichment.record_approval(
                    "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
                    decision=decision, reason=f"Concurrent {decision} decision.",
                    expected_leaf_id=approval["approval_event_id"],
                )
                outcomes.append("won")
            except MissionControlError as exc:
                outcomes.append(f"lost:{exc.status}")

        threads = [threading.Thread(target=supersede, args=(decision,)) for decision in ("revoked", "invalidated")]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), ["lost:409", "won"])

    def test_governed_protection_changes_block_approval_and_claim(self) -> None:
        actions = (
            ("suppress", {"reason": "Synthetic suppression after output."}),
            ("approve_deeper_research", {"reason": "Process-local decision only."}),
        )
        for boundary in ("approval", "claim"):
            for index, (action, values) in enumerate(actions):
                with self.subTest(boundary=boundary, action=action):
                    env = Phase6Env(self.directory / f"{boundary}-{index}")
                    approval = env.approve() if boundary == "claim" else None
                    env.repository.act(
                        "rob", "unreal-media-group", env.result["prospect_id"], action, values
                    )
                    if boundary == "approval":
                        with self.assertRaisesRegex(MissionControlError, "protections changed"):
                            env.approve()
                    else:
                        listed = env.enrichment.approvals("rob", "unreal-media-group")[0]
                        self.assertFalse(listed["valid_now"])
                        self.assertEqual(listed["validity_status"], "binding_invalid")
                        with self.assertRaisesRegex(MissionControlError, "protections changed"):
                            env.enrichment.start_enrichment(
                                "noah", "unreal-media-group", approval["approval_event_id"],
                                f"changed-{index}",
                            )

    def test_governed_rejection_blocks_new_output_approval_and_claim_until_superseded(self) -> None:
        before_output = Phase6Env(self.directory / "before-output")
        rejected = before_output.repository.act(
            "rob", "unreal-media-group", before_output.result["prospect_id"], "reject",
            {"reason": "Synthetic governed rejection."},
        )
        second = before_output.add_worker_run("unreal-media-group", "rejected-second-output")
        self.assertEqual(second["result"]["prospect_id"], before_output.result["prospect_id"])
        self.assertEqual(second["result"]["current_decision"], "rejected")
        with self.assertRaisesRegex(MissionControlError, "protections do not permit enrichment"):
            before_output.enrichment.record_approval(
                "rob", "unreal-media-group", second["task"]["task_id"],
                second["result"]["prospect_id"], decision="approved",
                reason="A rejected projection must stay blocked.",
            )

        superseded = before_output.repository.act(
            "rob", "unreal-media-group", before_output.result["prospect_id"],
            "approve_deeper_research", {"supersedes_id": rejected["event_id"]},
        )
        third = before_output.add_worker_run("unreal-media-group", "superseded-third-output")
        self.assertEqual(third["result"]["current_decision"], "approved_for_deeper_research")
        durable = before_output.enrichment.record_approval(
            "rob", "unreal-media-group", third["task"]["task_id"],
            third["result"]["prospect_id"], decision="approved",
            reason="A separate durable approval remains required.",
        )
        self.assertEqual(durable["decision"], "approved")
        self.assertEqual(superseded["supersedes_id"], rejected["event_id"])

        before_claim = Phase6Env(self.directory / "before-claim")
        approval = before_claim.approve()
        before_claim.repository.act(
            "rob", "unreal-media-group", before_claim.result["prospect_id"], "reject",
            {"reason": "Synthetic rejection before claim."},
        )
        with self.assertRaisesRegex(MissionControlError, "protections changed"):
            before_claim.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "rejected-claim"
            )

    def test_approval_event_tamper_is_detected_by_trigger_and_validator(self) -> None:
        # Trusted-local-state boundary: the immutability trigger is the real
        # defense for ordinary application access, and the colocated digest
        # detects an inconsistent or partial copy. Neither claims to authenticate
        # authority against a trusted database owner who disables the trigger and
        # also recomputes the digest.
        env = Phase6Env(self.directory)
        approval = env.approve()
        # 1. Ordinary updates to an authority field are rejected by the trigger.
        for column in ("expires_at", "recorded_at", "effective_at", "reviewer_actor", "reason"):
            with self.subTest(trigger=column):
                with self.assertRaisesRegex(MissionControlError, "rolled back"):
                    with env.store.transaction() as connection:
                        connection.execute(
                            f"UPDATE prospect_approval_events SET {column}='noah'"
                            " WHERE approval_event_id=?",
                            (approval["approval_event_id"],),
                        )
        # 2. The pure integrity validator rejects an inconsistent copied record
        #    whose authority column disagrees with its unchanged snapshot.
        for column, value in (
            ("expires_at", "2099-01-01T00:00:00Z"),
            ("recorded_at", "not-a-time"),
            ("effective_at", "not-a-time"),
            ("reviewer_actor", "noah"),
            ("reason", "x" * 513),
        ):
            with self.subTest(validator=column):
                tampered = dict(approval)
                tampered[column] = value
                with self.assertRaisesRegex(MissionControlError, "integrity") as caught:
                    _validate_event_integrity(
                        tampered, _APPROVAL_EVENT_FIELDS, "durable approval event"
                    )
                self.assertEqual(caught.exception.status, 409)
        # The untampered stored record still validates and remains actionable.
        self.assertEqual(
            env.enrichment.approvals("rob", "unreal-media-group")[0]["validity_status"], "valid"
        )

    def test_rejection_grants_no_authority_and_inconsistent_copy_is_detected(self) -> None:
        env = Phase6Env(self.directory)
        rejected = env.enrichment.record_approval(
            "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
            decision="rejected", reason="The synthetic fixture result is not approved.",
        )
        # The stored digest is a faithful, self-consistent snapshot of the event.
        snapshot = json.loads(rejected["event_snapshot"])
        self.assertEqual(
            set(snapshot), set(rejected) - {"event_snapshot", "content_hash", "byte_length"}
        )
        self.assertEqual(
            hashlib.sha256(rejected["event_snapshot"].encode("utf-8")).hexdigest(),
            rejected["content_hash"],
        )
        self.assertEqual(len(rejected["event_snapshot"].encode("utf-8")), rejected["byte_length"])
        # On the real application path a rejection grants no authority: it is the
        # current leaf, but its decision is not 'approved', so a claim fails closed.
        with self.assertRaisesRegex(MissionControlError, "current approved") as claim:
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", rejected["approval_event_id"], "rejected-start"
            )
        self.assertEqual(claim.exception.status, 409)
        # An inconsistent copy that flips the decision to 'approved' without
        # recomputing the colocated digest is detected by the pure validator.
        forged = dict(rejected)
        forged["decision"] = "approved"
        with self.assertRaisesRegex(MissionControlError, "integrity") as caught:
            _validate_event_integrity(forged, _APPROVAL_EVENT_FIELDS, "durable approval event")
        self.assertEqual(caught.exception.status, 409)

    def test_logical_task_run_output_and_business_unit_graph_is_rechecked(self) -> None:
        for index, unit in enumerate(("unreal-media-group", "unreal-talent")):
            with self.subTest(second_unit=unit):
                env = Phase6Env(self.directory / f"graph-{index}")
                second = env.add_worker_run(unit, f"graph-second-{index}")
                with env.store.transaction() as connection:
                    connection.execute(
                        "DELETE FROM review_tasks WHERE task_id=?", (second["task"]["task_id"],)
                    )
                    connection.execute(
                        "UPDATE review_tasks SET run_id=? WHERE task_id=?",
                        (second["run"]["run_id"], env.task["task_id"]),
                    )
                with self.assertRaisesRegex(MissionControlError, "binding is invalid"):
                    env.approve()

    def test_configuration_hash_is_verified_and_bound_at_approval_and_claim(self) -> None:
        before = Phase6Env(self.directory / "config-before")
        configuration = before.store.get_run(before.run["run_id"])["configuration"]
        configuration["maximum_evidence_age_days"] = 1
        changed = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
        with before.store.transaction() as connection:
            connection.execute(
                "UPDATE worker_runs SET configuration_snapshot=? WHERE run_id=?",
                (changed, before.run["run_id"]),
            )
        with self.assertRaisesRegex(MissionControlError, "configuration binding"):
            before.approve()

        for index, update_hash in enumerate((False, True)):
            with self.subTest(update_hash=update_hash):
                env = Phase6Env(self.directory / f"config-after-{index}")
                approval = env.approve()
                configuration = env.store.get_run(env.run["run_id"])["configuration"]
                configuration["maximum_evidence_age_days"] = 1
                changed = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
                with env.store.transaction() as connection:
                    if update_hash:
                        connection.execute(
                            "UPDATE worker_runs SET configuration_snapshot=?,configuration_hash=? WHERE run_id=?",
                            (changed, hashlib.sha256(changed.encode("utf-8")).hexdigest(), env.run["run_id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE worker_runs SET configuration_snapshot=? WHERE run_id=?",
                            (changed, env.run["run_id"]),
                        )
                listed = env.enrichment.approvals("rob", "unreal-media-group")[0]
                self.assertFalse(listed["valid_now"])
                with self.assertRaisesRegex(MissionControlError, "configuration|approval binding"):
                    env.enrichment.start_enrichment(
                        "noah", "unreal-media-group", approval["approval_event_id"],
                        f"config-{index}",
                    )

    def test_invalidated_review_task_is_not_projected_as_actionable(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        with env.store.transaction() as connection:
            connection.execute(
                "UPDATE review_tasks SET state='invalidated' WHERE task_id=?",
                (env.task["task_id"],),
            )
        listed = env.enrichment.approvals("rob", "unreal-media-group")[0]
        self.assertFalse(listed["valid_now"])
        self.assertEqual(listed["validity_status"], "binding_invalid")
        with self.assertRaisesRegex(MissionControlError, "not pending"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "invalid-task"
            )


class ConcurrencyAndAtomicityTests(Phase6Case):
    def test_concurrent_starts_create_one_logical_run_and_replay(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        barrier = threading.Barrier(2)
        results: list[tuple[dict, bool]] = []
        errors: list[Exception] = []

        def start() -> None:
            try:
                barrier.wait()
                results.append(env.enrichment.start_enrichment(
                    "noah", "unreal-media-group", approval["approval_event_id"], "same-start"
                ))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(created for _run, created in results), [False, True])
        self.assertEqual(len({run["enrichment_run_id"] for run, _created in results}), 1)

    def test_revocation_commit_orders_and_single_use(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        claimed = threading.Event()
        release = threading.Event()

        def gate() -> None:
            claimed.set()
            self.assertTrue(release.wait(timeout=2))

        holder: list[tuple[dict, bool]] = []
        thread = threading.Thread(target=lambda: holder.append(env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "winner", before_finish=gate
        )))
        thread.start()
        self.assertTrue(claimed.wait(timeout=2))
        env.enrichment.record_approval(
            "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
            decision="revoked", reason="Non-retroactive revocation.",
            expected_leaf_id=approval["approval_event_id"],
        )
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder[0][0]["state"], "succeeded")
        replay, created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "winner"
        )
        self.assertFalse(created)
        self.assertEqual(replay["enrichment_run_id"], holder[0][0]["enrichment_run_id"])
        with self.assertRaisesRegex(MissionControlError, "current approved|consumed"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "new-after-revoke"
            )

        first = Phase6Env(self.directory / "revoke-first")
        old = first.approve()
        first.enrichment.record_approval(
            "rob", "unreal-media-group", first.task["task_id"], first.result["prospect_id"],
            decision="revoked", reason="revoked before start", expected_leaf_id=old["approval_event_id"],
        )
        with self.assertRaisesRegex(MissionControlError, "current approved"):
            first.enrichment.start_enrichment(
                "noah", "unreal-media-group", old["approval_event_id"], "loser"
            )
        self.assertEqual(first.enrichment.runs("noah", "unreal-media-group"), [])

    def test_audit_failure_rolls_back_approval_and_run_claim(self) -> None:
        fault = FaultInjector()
        env = Phase6Env(self.directory, fault=fault)
        fault.armed = "insert_audit"
        with self.assertRaisesRegex(MissionControlError, "rolled back"):
            env.approve()
        self.assertEqual(env.enrichment.approvals("rob", "unreal-media-group"), [])
        approval = env.approve()
        fault.armed = "insert_audit"
        with self.assertRaisesRegex(MissionControlError, "rolled back"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "audit-fail"
            )
        self.assertEqual(env.enrichment.runs("noah", "unreal-media-group"), [])

    def test_completion_storage_failure_is_truthful_atomic_correlated_and_single_use(self) -> None:
        fault = FaultInjector()
        env = Phase6Env(self.directory, fault=fault)
        approval = env.approve()
        with self.assertRaisesRegex(MissionControlError, "rolled back"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"],
                "completion-storage-failure",
                before_finish=lambda: setattr(fault, "armed", "insert_audit"),
            )
        run = env.enrichment.runs("noah", "unreal-media-group")[0]
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["failure_class"], "local_persistence_failure")
        self.assertIn("local durable store", run["remediation"])
        connection = env.store._connect()
        try:
            for table in (
                "enrichment_sources", "campaign_brief_versions", "brief_evidence_links",
                "brief_integrity_manifests",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            audits = connection.execute(
                "SELECT event_type,safe_status FROM audit_events WHERE correlation_id=? ORDER BY rowid",
                (run["audit_correlation_id"],),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [row["event_type"] for row in audits],
            ["phase6a_enrichment_claimed", "phase6a_enrichment_failed"],
        )
        self.assertEqual(audits[-1]["safe_status"], "local_persistence_failure:http 500")
        self.assertNotIn("synthetic storage fault", json.dumps([dict(row) for row in audits]))
        replay, created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"],
            "completion-storage-failure",
        )
        self.assertFalse(created)
        self.assertEqual(replay["enrichment_run_id"], run["enrichment_run_id"])
        with self.assertRaisesRegex(MissionControlError, "consumed"):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "no-second-attempt"
            )

    def test_restart_terminalizes_an_interrupted_claim_without_retry(self) -> None:
        class SimulatedProcessExit(BaseException):
            pass

        env = Phase6Env(self.directory)
        approval = env.approve()
        with self.assertRaises(SimulatedProcessExit):
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "interrupted",
                before_finish=lambda: (_ for _ in ()).throw(SimulatedProcessExit()),
            )
        restarted = EnrichmentService(
            RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
            fixture_path=PHASE6_FIXTURE, clock=env.clock,
        )
        self.assertEqual(len(restarted.recovered_run_ids), 1)
        recovered = restarted.runs("noah", "unreal-media-group")[0]
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["failure_class"], "interrupted_execution_recovered")
        with self.assertRaisesRegex(MissionControlError, "consumed"):
            restarted.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "no-retry"
            )

    def test_second_service_does_not_recover_a_live_in_process_owner(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        claimed = threading.Event()
        release = threading.Event()
        holder: list[tuple[dict, bool]] = []
        errors: list[Exception] = []

        def gate() -> None:
            claimed.set()
            self.assertTrue(release.wait(timeout=2))

        def start() -> None:
            try:
                holder.append(env.enrichment.start_enrichment(
                    "noah", "unreal-media-group", approval["approval_event_id"],
                    "live-owner", before_finish=gate,
                ))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=start)
        thread.start()
        self.assertTrue(claimed.wait(timeout=2))
        second = EnrichmentService(
            RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
            fixture_path=PHASE6_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(second.recovered_run_ids, [])
        self.assertEqual(second.runs("noah", "unreal-media-group")[0]["state"], "running")
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(holder[0][0]["state"], "succeeded")

    def test_claim_first_process_protection_change_is_non_retroactive(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        claimed = threading.Event()
        release = threading.Event()
        holder: list[tuple[dict, bool]] = []

        def gate() -> None:
            claimed.set()
            self.assertTrue(release.wait(timeout=2))

        thread = threading.Thread(target=lambda: holder.append(env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"],
            "protection-claim-first", before_finish=gate,
        )))
        thread.start()
        self.assertTrue(claimed.wait(timeout=2))
        env.repository.act(
            "rob", "unreal-media-group", env.result["prospect_id"], "suppress",
            {"reason": "Protection changed only after the atomic claim."},
        )
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder[0][0]["state"], "succeeded")

    def test_unavailable_process_projection_is_visible_history_but_not_actionable(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        empty_repository = FixtureRepository(
            clock=env.clock, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE
        )
        restarted = EnrichmentService(
            RegisteredAgentService(empty_repository, SqliteStore(env.path), clock=env.clock),
            fixture_path=PHASE6_FIXTURE,
            clock=env.clock,
        )
        listed = restarted.approvals("rob", "unreal-media-group")[0]
        self.assertFalse(listed["valid_now"])
        self.assertEqual(listed["validity_status"], "binding_invalid")
        with self.assertRaisesRegex(MissionControlError, "projection is unavailable"):
            restarted.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "restart-blocked"
            )


class FixtureAndBriefTests(Phase6Case):
    def test_fixture_only_sources_and_complete_umg_brief(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "umg-brief"
        )
        brief = env.enrichment.get_brief("noah", "unreal-media-group", run["brief_id"])
        self.assertEqual(set(REQUIRED_BRIEF_FIELDS), set(brief["snapshot"]["research"]))
        self.assertEqual(brief["snapshot"]["research_mode"], "synthetic_fixture_only")
        self.assertFalse(brief["snapshot"]["official_site_research_performed"])
        self.assertTrue(brief["snapshot"]["human_review_required"])
        self.assertIn("umg", brief["snapshot"])
        self.assertNotIn("talent", brief["snapshot"])
        for source in brief["sources"]:
            self.assertTrue(source["source_url"].split("/", 3)[2].endswith(".example"))
            self.assertFalse(source["official_source"])
            self.assertLessEqual(len(source["quote"].split()), 25)
            self.assertLessEqual(len(source["summary"].encode("utf-8")), 512)
        forbidden = {"generation_prompt", "generated_asset", "outreach_text", "contact_data"}
        self.assertTrue(forbidden.isdisjoint(brief["snapshot"]))

    def test_talent_brief_is_archetype_only_and_claims_no_rights(self) -> None:
        env = Phase6Env(self.directory, unit="unreal-talent")
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-talent", approval["approval_event_id"], "talent-brief"
        )
        brief = env.enrichment.get_brief("dan", "unreal-talent", run["brief_id"])["snapshot"]
        talent = brief["talent"]
        self.assertIsNone(talent["named_talent"])
        self.assertFalse(talent["availability_claimed"])
        self.assertFalse(talent["endorsement_claimed"])
        self.assertFalse(talent["clearance_claimed"])
        self.assertFalse(talent["likeness_authorized"])
        self.assertIn("archetype", talent["talent_archetype"].casefold())
        self.assertNotIn("umg", brief)

    def test_fixture_profile_is_bound_to_the_exact_approved_result(self) -> None:
        env = Phase6Env(self.directory)
        ember = next(
            item for item in env.output["result_snapshot"]
            if item["prospect_id"] == "umg-subbrand-ember"
        )
        approval = env.enrichment.record_approval(
            "rob", "unreal-media-group", env.task["task_id"], ember["prospect_id"],
            decision="approved", reason="Approve the distinct synthetic Ember result.",
        )
        with self.assertRaisesRegex(MissionControlError, "fixture target") as caught:
            env.enrichment.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "ember-mismatch"
            )
        self.assertEqual(caught.exception.status, 400)
        connection = env.store._connect()
        try:
            for table in (
                "enrichment_sources", "brief_evidence_links",
                "campaign_brief_versions", "brief_integrity_manifests",
            ):
                self.assertEqual(connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0], 0, table)
        finally:
            connection.close()

    def test_unsafe_fixture_source_is_rejected_before_persistence(self) -> None:
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        cases = [
            "http://user:pass@brand-research.example/private",
            "https://example.com/not-fixture",
            "file:///tmp/source",
            "https://127.0.0.1/private",
            "http://[",
        ]
        for index, url in enumerate(cases):
            with self.subTest(url=url):
                path = self.directory / f"unsafe-{index}.json"
                value = copy.deepcopy(fixture)
                value["profiles"]["unreal-media-group"]["sources"][0]["url"] = url
                path.write_text(json.dumps(value), encoding="utf-8")
                env = Phase6Env(self.directory / f"case-{index}")
                service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
                approval = env.approve()
                with self.assertRaisesRegex(MissionControlError, "fixture (?:source|profile)"):
                    service.start_enrichment(
                        "noah", "unreal-media-group", approval["approval_event_id"], f"unsafe-{index}"
                    )
                self.assertEqual(service.sources("noah", "unreal-media-group"), [])

    def test_recursive_fixture_values_reject_unsafe_or_unbounded_material_before_persistence(self) -> None:
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        cases = (
            ("unreal-media-group", "umg", "person@example.test"),
            ("unreal-media-group", "umg", "password=synthetic"),
            ("unreal-media-group", "umg", "contact_email"),
            ("unreal-talent", "talent", "person@example.test"),
            ("unreal-talent", "talent", "token=synthetic"),
            ("unreal-talent", "talent", "api_key"),
        )
        for index, (unit, section, unsafe_key) in enumerate(cases):
            with self.subTest(unit=unit, key=unsafe_key):
                value = copy.deepcopy(fixture)
                value["profiles"][unit][section][unsafe_key] = "bounded synthetic value"
                path = self.directory / f"unsafe-key-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                env = Phase6Env(self.directory / f"unsafe-key-{index}", unit=unit)
                service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
                approval = env.approve()
                reviewer = "rob" if unit == "unreal-media-group" else "dan"
                with self.assertRaisesRegex(MissionControlError, "contact data|credentials"):
                    service.start_enrichment(
                        "noah", unit, approval["approval_event_id"], f"unsafe-key-{index}"
                    )
                self.assertEqual(service.sources(reviewer, unit), [])
                connection = env.store._connect()
                try:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM campaign_brief_versions"
                    ).fetchone()[0], 0)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM brief_evidence_links"
                    ).fetchone()[0], 0)
                finally:
                    connection.close()

        value = copy.deepcopy(fixture)
        nested = {"bounded": "value"}
        for _ in range(10):
            nested = {"nested": nested}
        value["profiles"]["unreal-media-group"]["umg"]["deep"] = nested
        path = self.directory / "excessive-depth.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        env = Phase6Env(self.directory / "excessive-depth")
        service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
        approval = env.approve()
        with self.assertRaisesRegex(MissionControlError, "nesting depth"):
            service.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "excessive-depth"
            )
        self.assertEqual(service.sources("rob", "unreal-media-group"), [])

    def test_exact_business_unit_schemas_credentials_urls_and_nonfinite_json_fail_before_brief_data(self) -> None:
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        cases = []

        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["umg"]["named_talent"] = "Invented Person"
        cases.append(("unreal-media-group", value, "unknown|business-unit"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["umg"]["talent_archetype"] = "Cross-format"
        cases.append(("unreal-media-group", value, "unknown|business-unit"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["umg"]["authorization"] = "Bearer synthetic-secret"
        cases.append(("unreal-media-group", value, "credentials"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-talent"]["talent"]["accessToken"] = "synthetic-secret"
        cases.append(("unreal-talent", value, "credentials"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-talent"]["talent"]["secretKey"] = "synthetic-secret"
        cases.append(("unreal-talent", value, "credentials"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["umg"]["product_truth"] = (
            "Authorization: Bearer synthetic-secret"
        )
        cases.append(("unreal-media-group", value, "credentials"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["sources"][0]["url"] = (
            "https://brand-research.example/products/token%3Dsynthetic"
        )
        cases.append(("unreal-media-group", value, "URL"))
        value = copy.deepcopy(fixture)
        del value["profiles"]["unreal-media-group"]["umg"]["hero_rationale"]
        cases.append(("unreal-media-group", value, "missing|business-unit"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["umg"]["channels"] = "owned social"
        cases.append(("unreal-media-group", value, "type|business-unit"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-talent"]["talent"]["availability_claimed"] = "false"
        cases.append(("unreal-talent", value, "type|business-unit|claim"))
        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-talent"]["fields"]["hero_product"]["value"] = float("nan")
        cases.append(("unreal-talent", value, "invalid|finite"))

        for index, (unit, value, message) in enumerate(cases):
            with self.subTest(unit=unit, index=index):
                path = self.directory / f"strict-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                env = Phase6Env(self.directory / f"strict-{index}", unit=unit)
                service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
                approval = env.approve()
                with self.assertRaisesRegex(MissionControlError, message):
                    service.start_enrichment(
                        "noah", unit, approval["approval_event_id"], f"strict-{index}"
                    )
                connection = env.store._connect()
                try:
                    for table in (
                        "enrichment_sources", "brief_evidence_links",
                        "campaign_brief_versions", "brief_integrity_manifests",
                    ):
                        self.assertEqual(
                            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0,
                            table,
                        )
                finally:
                    connection.close()

        value = copy.deepcopy(fixture)
        value["profiles"]["unreal-media-group"]["fields"]["brand_voice"]["evidence_ids"] = [{}]
        path = self.directory / "malformed-evidence.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        env = Phase6Env(self.directory / "malformed-evidence")
        service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
        approval = env.approve()
        with self.assertRaisesRegex(MissionControlError, "invalid evidence links"):
            service.start_enrichment(
                "noah", "unreal-media-group", approval["approval_event_id"], "malformed-evidence"
            )
        self.assertEqual(service.sources("rob", "unreal-media-group"), [])

    def test_research_field_types_and_evidence_identifiers_fail_before_brief_data(self) -> None:
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        cases = []
        for unit in ("unreal-media-group", "unreal-talent"):
            value = copy.deepcopy(fixture)
            value["profiles"][unit]["fields"]["hero_product"]["value"] = {
                "unexpected": "object"
            }
            cases.append((unit, value))

            value = copy.deepcopy(fixture)
            value["profiles"][unit]["fields"]["product_extraction"]["value"] = "wrong type"
            cases.append((unit, value))

            value = copy.deepcopy(fixture)
            value["profiles"][unit]["fields"]["hero_product"]["value"] = None
            cases.append((unit, value))

            value = copy.deepcopy(fixture)
            evidence = value["profiles"][unit]["fields"]["hero_product"]["evidence_ids"]
            evidence.append(evidence[0])
            cases.append((unit, value))

            value = copy.deepcopy(fixture)
            value["profiles"][unit]["fields"]["hero_product"]["basis"] = "explicit_unknown"
            cases.append((unit, value))

            value = copy.deepcopy(fixture)
            old_id = value["profiles"][unit]["sources"][0]["source_id"]
            value["profiles"][unit]["sources"][0]["source_id"] = "invalid evidence id"
            for field in value["profiles"][unit]["fields"].values():
                field["evidence_ids"] = [
                    "invalid evidence id" if item == old_id else item
                    for item in field["evidence_ids"]
                ]
            cases.append((unit, value))

        for index, (unit, value) in enumerate(cases):
            with self.subTest(unit=unit, index=index):
                path = self.directory / f"research-type-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                env = Phase6Env(self.directory / f"research-type-{index}", unit=unit)
                service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
                approval = env.approve()
                with self.assertRaises(MissionControlError) as caught:
                    service.start_enrichment(
                        "noah", unit, approval["approval_event_id"], f"research-type-{index}"
                    )
                self.assertEqual(caught.exception.status, 400)
                connection = env.store._connect()
                try:
                    for table in (
                        "enrichment_sources", "brief_evidence_links",
                        "campaign_brief_versions", "brief_integrity_manifests",
                    ):
                        self.assertEqual(connection.execute(
                            f"SELECT COUNT(*) FROM {table}"
                        ).fetchone()[0], 0, table)
                finally:
                    connection.close()

    def test_explicit_unknown_null_value_remains_reviewable_for_both_business_units(self) -> None:
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        for index, unit in enumerate(("unreal-media-group", "unreal-talent")):
            with self.subTest(unit=unit):
                value = copy.deepcopy(fixture)
                field = value["profiles"][unit]["fields"]["hero_product"]
                field.update({"basis": "explicit_unknown", "value": None, "evidence_ids": []})
                path = self.directory / f"explicit-unknown-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                env = Phase6Env(self.directory / f"explicit-unknown-{index}", unit=unit)
                service = EnrichmentService(env.agent, fixture_path=path, clock=env.clock)
                approval = env.approve()
                run, _created = service.start_enrichment(
                    "noah", unit, approval["approval_event_id"], f"explicit-unknown-{index}"
                )
                reviewer = "rob" if unit == "unreal-media-group" else "dan"
                brief = service.get_brief(reviewer, unit, run["brief_id"])
                self.assertEqual(brief["snapshot"]["research"]["hero_product"]["basis"], "explicit_unknown")
                self.assertIsNone(brief["snapshot"]["research"]["hero_product"]["value"])
                review = service.review_brief(
                    reviewer, unit, run["brief_id"], decision="accepted",
                    reason="The explicit unknown is truthful and reviewable.",
                )
                self.assertEqual(review["authority_granted"], "research_quality_only")

    def test_brief_survives_restart_and_immutability_trigger_blocks_writes(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "restart"
        )
        restarted = EnrichmentService(
            RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
            fixture_path=PHASE6_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(
            restarted.get_brief("rob", "unreal-media-group", run["brief_id"])["content_hash"],
            run["brief_hash"],
        )
        # Ordinary updates and deletes are rejected by the immutability triggers,
        # which are the real defense for normal application access.
        for statement in (
            "UPDATE campaign_brief_versions SET byte_length=byte_length+1 WHERE brief_id=?",
            "DELETE FROM campaign_brief_versions WHERE brief_id=?",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MissionControlError, "rolled back"):
                    with restarted.store.transaction() as connection:
                        connection.execute(statement, (run["brief_id"],))
        # The brief remains readable and unchanged after the rejected writes.
        self.assertEqual(
            restarted.get_brief("rob", "unreal-media-group", run["brief_id"])["content_hash"],
            run["brief_hash"],
        )

    def test_sources_links_and_manifests_reject_ordinary_updates_and_deletes(self) -> None:
        # Every immutable Phase 6A artifact table rejects ordinary updates and
        # deletes through its append-only trigger. Detection of an inconsistent
        # copied source/link record is covered separately by the pure validators
        # (test_integrity_validators_reject_inconsistent_copied_records).
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "immutable-artifacts"
        )
        for statement in (
            "UPDATE enrichment_sources SET source_url='https://tampered.example/changed'",
            "DELETE FROM enrichment_sources",
            "UPDATE brief_evidence_links SET freshness='tampered'",
            "DELETE FROM brief_evidence_links",
            "UPDATE brief_integrity_manifests SET business_unit='tampered'",
            "DELETE FROM brief_integrity_manifests",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MissionControlError, "rolled back"):
                    with env.store.transaction() as connection:
                        connection.execute(statement)
        # Every artifact remains readable and unchanged after the rejected writes.
        self.assertTrue(env.enrichment.sources("rob", "unreal-media-group"))
        self.assertEqual(
            env.enrichment.get_brief("rob", "unreal-media-group", run["brief_id"])["brief_id"],
            run["brief_id"],
        )

    def test_integrity_validators_reject_inconsistent_copied_records(self) -> None:
        # Exercise the pure integrity validators directly with inconsistent
        # copied records. The honest property: the colocated digest and the
        # denormalized snapshot detect a copy whose columns disagree with the
        # snapshot. This is accidental/partial-corruption detection, not
        # authentication against a trusted database owner who rewrites a row and
        # recomputes its digest.
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "validators"
        )
        review = env.enrichment.review_brief(
            "rob", "unreal-media-group", run["brief_id"], decision="changes_requested",
            reason="Bounded synthetic review feedback.",
        )
        connection = env.store._connect()
        try:
            source_rows = connection.execute("SELECT * FROM enrichment_sources").fetchall()
            link_rows = connection.execute(
                "SELECT * FROM brief_evidence_links WHERE source_record_id IS NOT NULL"
            ).fetchall()
            brief_row = connection.execute(
                "SELECT * FROM campaign_brief_versions WHERE brief_id=?", (run["brief_id"],)
            ).fetchone()
        finally:
            connection.close()

        # Approval and brief-review events: an authority column that disagrees
        # with the unchanged snapshot is detected.
        forged_approval = dict(approval)
        forged_approval["decision"] = "revoked"
        with self.assertRaisesRegex(MissionControlError, "integrity") as caught:
            _validate_event_integrity(
                forged_approval, _APPROVAL_EVENT_FIELDS, "durable approval event"
            )
        self.assertEqual(caught.exception.status, 409)
        forged_review = dict(review)
        forged_review["decision"] = "accepted"
        forged_review["reviewer_actor"] = "noah"
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            _validate_event_integrity(
                forged_review, _BRIEF_REVIEW_EVENT_FIELDS, "brief review event"
            )

        # Enrichment source: a denormalized column that disagrees with the
        # snapshot fails the denormalized integrity check.
        forged_source = dict(source_rows[0])
        forged_source["source_url"] = "https://tampered.example/changed"
        with self.assertRaisesRegex(MissionControlError, "denormalized integrity") as caught:
            EnrichmentService._validated_source(forged_source)
        self.assertEqual(caught.exception.status, 409)

        # Evidence link: same property, validated against a copy of the real
        # source map and brief row.
        sources_map = {
            row["source_record_id"]: EnrichmentService._validated_source(dict(row))
            for row in source_rows
        }
        forged_link = dict(link_rows[0])
        forged_link["freshness"] = "tampered"
        with self.assertRaisesRegex(MissionControlError, "denormalized integrity"):
            EnrichmentService._validated_link(
                forged_link, brief=dict(brief_row), sources=sources_map
            )

        # An untampered copy of each record still validates.
        _validate_event_integrity(dict(approval), _APPROVAL_EVENT_FIELDS, "durable approval event")
        _validate_event_integrity(dict(review), _BRIEF_REVIEW_EVENT_FIELDS, "brief review event")
        EnrichmentService._validated_source(dict(source_rows[0]))
        EnrichmentService._validated_link(
            dict(link_rows[0]), brief=dict(brief_row), sources=sources_map
        )

    def test_integrity_manifest_completeness_detects_an_appended_record(self) -> None:
        # Appending an extra evidence link is an ordinary INSERT (no immutability
        # trigger blocks inserts), but the exact-set manifest no longer matches
        # the live rows, so the completeness check fails closed before any listing,
        # rendering, or review. This is inconsistent/partial-set detection for
        # ordinary application access, not resistance to a trusted database owner
        # who would also rewrite the manifest.
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "manifest-extra"
        )
        connection = env.store._connect()
        try:
            row = connection.execute(
                "SELECT * FROM brief_evidence_links WHERE brief_id=? ORDER BY rowid LIMIT 1",
                (run["brief_id"],),
            ).fetchone()
            snapshot = json.loads(row["link_snapshot"])
            snapshot["link_id"] = "tampered-extra-link"
            snapshot["field_path"] = "research.extra"
            raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "INSERT INTO brief_evidence_links (link_id,brief_id,field_path,source_record_id,basis,"
                " confidence_reason,uncertainty,freshness,currentness,quote,source_lineage,"
                " link_snapshot,content_hash,byte_length) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot["link_id"], row["brief_id"], snapshot["field_path"],
                    row["source_record_id"], row["basis"], row["confidence_reason"],
                    row["uncertainty"], row["freshness"], row["currentness"], row["quote"],
                    row["source_lineage"], raw, hashlib.sha256(raw.encode()).hexdigest(),
                    len(raw.encode()),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity manifest"):
            env.enrichment.sources("rob", "unreal-media-group")
        with self.assertRaisesRegex(MissionControlError, "integrity manifest"):
            env.enrichment.get_brief("rob", "unreal-media-group", run["brief_id"])
        with self.assertRaisesRegex(MissionControlError, "integrity manifest"):
            env.enrichment.review_brief(
                "rob", "unreal-media-group", run["brief_id"], decision="accepted",
                reason="Incomplete evidence cannot be accepted.",
            )

    def test_shared_identity_brief_families_are_business_unit_qualified(self) -> None:
        env = Phase6Env(self.directory)
        umg_approval = env.approve()
        umg_run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", umg_approval["approval_event_id"], "shared-umg"
        )
        shared_identity = env.result["global_identity_id"]
        candidate = next(
            item for item in env.repository.fixture_candidates
            if item["prospect_id"] == "talent-shared-account"
        )
        candidate["global_identity_id"] = shared_identity
        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        fixture["profiles"]["unreal-talent"]["target"]["global_identity_id"] = shared_identity
        fixture_path = self.directory / "shared-identity-fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        shared_service = EnrichmentService(
            env.agent, fixture_path=fixture_path, clock=env.clock
        )
        talent = env.add_worker_run("unreal-talent", "shared-talent-worker")
        self.assertEqual(talent["result"]["global_identity_id"], shared_identity)
        talent_approval = shared_service.record_approval(
            "dan", "unreal-talent", talent["task"]["task_id"], talent["result"]["prospect_id"],
            decision="approved", reason="Independent synthetic Talent enrichment approval.",
        )
        talent_run, _created = shared_service.start_enrichment(
            "noah", "unreal-talent", talent_approval["approval_event_id"], "shared-talent"
        )
        umg_brief = shared_service.get_brief("rob", "unreal-media-group", umg_run["brief_id"])
        talent_brief = shared_service.get_brief("dan", "unreal-talent", talent_run["brief_id"])
        self.assertNotEqual(umg_brief["brief_family_id"], talent_brief["brief_family_id"])
        self.assertEqual((umg_brief["version"], talent_brief["version"]), (1, 1))

    def test_research_quality_review_is_exact_append_only_and_not_generation_authority(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "review"
        )
        event = env.enrichment.review_brief(
            "rob", "unreal-media-group", run["brief_id"], decision="accepted",
            reason="Synthetic research is complete enough for human planning review.",
        )
        self.assertEqual(event["decision"], "accepted")
        self.assertEqual(event["authority_granted"], "research_quality_only")
        self.assertNotIn("approved_for_generation", json.dumps(event))
        with self.assertRaisesRegex(MissionControlError, "self-review"):
            env.enrichment.review_brief(
                "noah", "unreal-media-group", run["brief_id"], decision="accepted",
                reason="self review denied",
            )
        with env.store.transaction() as connection:
            connection.execute(
                "UPDATE enrichment_runs SET initiating_actor='mallory' WHERE enrichment_run_id=?",
                (run["enrichment_run_id"],),
            )
        with self.assertRaisesRegex(MissionControlError, "provenance binding"):
            env.enrichment.review_brief(
                "noah", "unreal-media-group", run["brief_id"], decision="accepted",
                reason="tampered proposer must not bypass separation",
                expected_leaf_id=event["review_event_id"],
            )

    def test_acceptance_requires_latest_ready_conflict_free_brief_but_other_reviews_remain_recordable(self) -> None:
        env = Phase6Env(self.directory / "versions")
        first_approval = env.approve()
        first, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", first_approval["approval_event_id"], "brief-version-one"
        )
        second_worker = env.add_worker_run("unreal-media-group", "brief-version-two-worker")
        second_approval = env.enrichment.record_approval(
            "rob", "unreal-media-group", second_worker["task"]["task_id"],
            second_worker["result"]["prospect_id"], decision="approved",
            reason="Approve a newer synthetic brief version.",
        )
        second, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", second_approval["approval_event_id"], "brief-version-two"
        )
        self.assertEqual(env.enrichment.get_brief(
            "rob", "unreal-media-group", first["brief_id"]
        )["version"], 1)
        self.assertEqual(env.enrichment.get_brief(
            "rob", "unreal-media-group", second["brief_id"]
        )["version"], 2)
        with self.assertRaisesRegex(MissionControlError, "latest brief version"):
            env.enrichment.review_brief(
                "rob", "unreal-media-group", first["brief_id"], decision="accepted",
                reason="Historical versions cannot be accepted.",
            )
        historical = env.enrichment.review_brief(
            "rob", "unreal-media-group", first["brief_id"], decision="changes_requested",
            reason="Historical review feedback remains append-only.",
        )
        self.assertEqual(historical["decision"], "changes_requested")

        fixture = json.loads(PHASE6_FIXTURE.read_text(encoding="utf-8"))
        fixture["profiles"]["unreal-media-group"]["conflicts"] = ["brand_voice"]
        path = self.directory / "conflicted.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        conflicted = Phase6Env(self.directory / "conflicted")
        service = EnrichmentService(conflicted.agent, fixture_path=path, clock=conflicted.clock)
        approval = conflicted.approve()
        run, _created = service.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "conflicted-brief"
        )
        with self.assertRaisesRegex(MissionControlError, "unresolved conflict|ready"):
            service.review_brief(
                "rob", "unreal-media-group", run["brief_id"], decision="accepted",
                reason="A conflicted brief cannot be accepted.",
            )
        rejected = service.review_brief(
            "rob", "unreal-media-group", run["brief_id"], decision="rejected",
            reason="Conflicted research may be rejected without granting authority.",
        )
        self.assertEqual(rejected["decision"], "rejected")

    def test_approval_and_brief_review_events_reject_update_and_delete(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        revoked = env.enrichment.record_approval(
            "rob", "unreal-media-group", env.task["task_id"], env.result["prospect_id"],
            decision="revoked", reason="Append-only revocation.",
            expected_leaf_id=approval["approval_event_id"],
        )
        for statement, event_id in (
            ("UPDATE prospect_approval_events SET decision='approved' WHERE approval_event_id=?", revoked["approval_event_id"]),
            ("DELETE FROM prospect_approval_events WHERE approval_event_id=?", revoked["approval_event_id"]),
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MissionControlError, "rolled back"):
                    with env.store.transaction() as connection:
                        connection.execute(statement, (event_id,))
        history = env.enrichment.approvals("rob", "unreal-media-group")
        self.assertEqual([item["decision"] for item in history], ["approved", "revoked"])

        brief_env = Phase6Env(self.directory / "brief")
        approval = brief_env.approve()
        run, _created = brief_env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "immutable-review"
        )
        review = brief_env.enrichment.review_brief(
            "rob", "unreal-media-group", run["brief_id"], decision="accepted",
            reason="Synthetic research-quality review.",
        )
        for statement in (
            "UPDATE brief_review_events SET decision='rejected' WHERE review_event_id=?",
            "DELETE FROM brief_review_events WHERE review_event_id=?",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MissionControlError, "rolled back"):
                    with brief_env.store.transaction() as connection:
                        connection.execute(statement, (review["review_event_id"],))
        reviews = brief_env.enrichment.brief_reviews(
            "rob", "unreal-media-group", run["brief_id"]
        )
        self.assertEqual([item["decision"] for item in reviews], ["accepted"])

    def test_brief_review_digest_is_faithful_and_forged_acceptance_copy_is_detected(self) -> None:
        env = Phase6Env(self.directory)
        approval = env.approve()
        run, _created = env.enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "forged-review"
        )
        review = env.enrichment.review_brief(
            "rob", "unreal-media-group", run["brief_id"], decision="rejected",
            reason="The synthetic brief requires correction.",
        )
        # The stored digest is a faithful, self-consistent snapshot of the event.
        snapshot = json.loads(review["event_snapshot"])
        self.assertEqual(
            set(snapshot), set(review) - {"event_snapshot", "content_hash", "byte_length"}
        )
        self.assertEqual(
            hashlib.sha256(review["event_snapshot"].encode("utf-8")).hexdigest(),
            review["content_hash"],
        )
        self.assertEqual(len(review["event_snapshot"].encode("utf-8")), review["byte_length"])
        # An inconsistent copy that promotes the decision to 'accepted' by a
        # different reviewer without recomputing the colocated digest is detected
        # by the pure validator that every read boundary applies.
        forged = dict(review)
        forged["decision"] = "accepted"
        forged["reviewer_actor"] = "noah"
        with self.assertRaisesRegex(MissionControlError, "integrity") as caught:
            _validate_event_integrity(forged, _BRIEF_REVIEW_EVENT_FIELDS, "brief review event")
        self.assertEqual(caught.exception.status, 409)
        # The genuine rejected event remains readable, unchanged, and grants only
        # research-quality authority over the loopback HTTP surface.
        app = WebApplication(
            env.control, csrf_token="phase6-csrf", agent_service=env.agent,
            enrichment_service=env.enrichment,
        )
        server = build_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            host, port = server.server_address
            http_connection = http.client.HTTPConnection(host, port, timeout=2)
            http_connection.request(
                "GET", f'/phase6-briefs/{run["brief_id"]}?actor=rob&business_unit=unreal-media-group'
            )
            response = http_connection.getresponse()
            body = response.read().decode("utf-8")
            http_connection.close()
            self.assertEqual(response.status, 200)
            self.assertNotIn("approved_for_generation", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        reviews = env.enrichment.brief_reviews("rob", "unreal-media-group", run["brief_id"])
        self.assertEqual([item["decision"] for item in reviews], ["rejected"])
        self.assertTrue(all(
            item["authority_granted"] == "research_quality_only" for item in reviews
        ))


class UpgradeAndHttpTests(Phase6Case):
    def test_fresh_v3_and_populated_v2_upgrade_preserve_rows_and_bytes(self) -> None:
        fresh = SqliteStore(self.directory / "fresh.sqlite3")
        connection = fresh._connect()
        try:
            self.assertEqual(connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0], str(SCHEMA_VERSION))
        finally:
            connection.close()
        repository = FixtureRepository(clock=Clock(), next_id=CounterIds(), fixture_path=PHASE3_FIXTURE)
        EnrichmentService(
            RegisteredAgentService(repository, fresh, clock=Clock()),
            fixture_path=PHASE6_FIXTURE, clock=Clock(),
        )
        connection = fresh._connect()
        try:
            self.assertEqual(connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0], str(PHASE6_SCHEMA_VERSION))
            approval_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(prospect_approval_events)")
            }
            review_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(brief_review_events)")
            }
            link_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(brief_evidence_links)")
            }
            self.assertTrue({
                "configuration_hash", "event_snapshot", "content_hash", "byte_length",
            } <= approval_columns)
            self.assertTrue({"event_snapshot", "content_hash", "byte_length"} <= review_columns)
            self.assertTrue({"link_snapshot", "content_hash", "byte_length"} <= link_columns)
            triggers = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            self.assertTrue({
                "prospect_approval_events_no_update", "prospect_approval_events_no_delete",
                "brief_review_events_no_update", "brief_review_events_no_delete",
                "enrichment_sources_no_update", "enrichment_sources_no_delete",
                "campaign_brief_versions_no_update", "campaign_brief_versions_no_delete",
                "brief_evidence_links_no_update", "brief_evidence_links_no_delete",
                "brief_integrity_manifests_no_update", "brief_integrity_manifests_no_delete",
            } <= triggers)
            manifest_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(brief_integrity_manifests)")
            }
            self.assertTrue({
                "brief_id", "enrichment_run_id", "business_unit", "manifest_snapshot",
                "content_hash", "byte_length",
            } <= manifest_columns)
        finally:
            connection.close()

        env = Phase6Env(self.directory / "source")
        source = sqlite3.connect(env.path)
        v2_path = self.directory / "v2.sqlite3"
        target = sqlite3.connect(v2_path)
        for statement in PHASE4_TABLES:
            target.execute(statement)
        target.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','2')")
        phase45 = [row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) if row[0] not in {
            "schema_meta", "prospect_approval_events", "enrichment_runs", "enrichment_sources",
            "campaign_brief_versions", "brief_evidence_links", "brief_integrity_manifests",
            "brief_review_events",
        }]
        for table in phase45:
            schema = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            if table not in {"id_counters", "worker_runs", "worker_attempts", "worker_outputs", "review_tasks", "audit_events"}:
                target.execute(schema)
            cursor = source.execute(f"SELECT * FROM {table} ORDER BY rowid")
            columns = [item[0] for item in cursor.description]
            rows = cursor.fetchall()
            if rows:
                target.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    rows,
                )
        target.commit()
        before = {table: target.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in phase45}
        source.close(); target.close()
        upgraded = SqliteStore(v2_path)
        EnrichmentService(
            RegisteredAgentService(env.repository, upgraded, clock=env.clock),
            fixture_path=PHASE6_FIXTURE, clock=env.clock,
        )
        connection = upgraded._connect()
        try:
            for table in phase45:
                self.assertEqual(
                    [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()],
                    before[table],
                )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0], str(PHASE6_SCHEMA_VERSION))
        finally:
            connection.close()

    def test_directly_built_pre_correction_v3_layout_is_rejected_without_rewrite(self) -> None:
        # Construct the legacy schema-v3 layout DIRECTLY in a temporary database
        # (authority-event tables that predate the integrity columns) instead of
        # dismantling a valid store. Opening it must fail closed and leave the
        # existing rows and schema version untouched.
        path = self.directory / "legacy-v3.sqlite3"
        legacy = sqlite3.connect(path)
        try:
            legacy.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            legacy.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '3')")
            legacy.execute(
                "CREATE TABLE prospect_approval_events ("
                " approval_event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL,"
                " output_id TEXT NOT NULL, result_id TEXT NOT NULL, result_hash TEXT NOT NULL,"
                " result_byte_length INTEGER NOT NULL, output_hash TEXT NOT NULL,"
                " output_byte_length INTEGER NOT NULL, configuration_hash TEXT NOT NULL,"
                " protection_hash TEXT NOT NULL, global_identity_id TEXT NOT NULL,"
                " business_unit TEXT NOT NULL, scope TEXT NOT NULL, decision TEXT NOT NULL,"
                " proposer_actor TEXT NOT NULL, reviewer_actor TEXT NOT NULL, reason TEXT NOT NULL,"
                " effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL, expires_at TEXT NOT NULL,"
                " supersedes_id TEXT, audit_correlation_id TEXT NOT NULL)"
            )
            legacy.execute(
                "CREATE TABLE brief_review_events ("
                " review_event_id TEXT PRIMARY KEY, brief_id TEXT NOT NULL, brief_hash TEXT NOT NULL,"
                " brief_byte_length INTEGER NOT NULL, business_unit TEXT NOT NULL, decision TEXT NOT NULL,"
                " proposer_actor TEXT NOT NULL, reviewer_actor TEXT NOT NULL, reason TEXT NOT NULL,"
                " effective_at TEXT NOT NULL, recorded_at TEXT NOT NULL, supersedes_id TEXT,"
                " authority_granted TEXT NOT NULL, audit_correlation_id TEXT NOT NULL)"
            )
            legacy.commit()
            before = {
                table: legacy.execute(f"PRAGMA table_info({table})").fetchall()
                for table in ("prospect_approval_events", "brief_review_events")
            }
        finally:
            legacy.close()

        with self.assertRaisesRegex(MissionControlError, "predates the final schema-v3") as caught:
            SqliteStore(path)
        self.assertEqual(caught.exception.status, 500)

        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0], "3")
            for table, columns in before.items():
                self.assertEqual(connection.execute(f"PRAGMA table_info({table})").fetchall(), columns)
        finally:
            connection.close()

    def test_http_manual_workflow_security_truth_and_loopback_shutdown(self) -> None:
        env = Phase6Env(self.directory)
        app = WebApplication(
            env.control, csrf_token="phase6-csrf", agent_service=env.agent,
            enrichment_service=env.enrichment,
        )
        status, body = app.get(
            "/phase6-approvals", {"actor": ["rob"], "business_unit": ["unreal-media-group"]}
        )
        self.assertEqual(status, 200)
        self.assertIn("fixture policy simulation", body.casefold())
        self.assertIn("No durable approvals", body)
        with self.assertRaisesRegex(MissionControlError, "CSRF"):
            app.post("/phase6-approvals", {"actor": "rob", "business_unit": "unreal-media-group"})
        status, body = app.post("/phase6-approvals", {
            "csrf_token": "phase6-csrf", "actor": "rob", "business_unit": "unreal-media-group",
            "task_id": env.task["task_id"], "result_id": env.result["prospect_id"],
            "decision": "approved", "reason": "Approve & inspect <synthetic> research.",
            "expected_leaf_id": "",
        })
        self.assertEqual(status, 201)
        self.assertIn("durable approval event", body)
        self.assertIn("&lt;synthetic&gt;", body)
        approval = env.enrichment.approvals("rob", "unreal-media-group")[0]
        status, body = app.get(
            "/phase6-enrichments", {"actor": ["rob"], "business_unit": ["unreal-media-group"]}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Run one bounded fixture enrichment", body)
        status, body = app.get(
            "/phase6-enrichments", {"actor": ["noah"], "business_unit": ["unreal-media-group"]}
        )
        self.assertEqual(status, 200)
        self.assertIn("Run one bounded fixture enrichment", body)
        status, body = app.post("/phase6-enrichments", {
            "csrf_token": "phase6-csrf", "actor": "noah", "business_unit": "unreal-media-group",
            "approval_event_id": approval["approval_event_id"], "idempotency_key": "http-enrich",
        })
        self.assertEqual(status, 200)
        self.assertIn("Synthetic fixture brief completed", body)
        self.assertIn("not official-site research", body.casefold())
        run = env.enrichment.runs("noah", "unreal-media-group")[0]
        status, proposer_body = app.get(
            f'/phase6-briefs/{run["brief_id"]}',
            {"actor": ["noah"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("separation of duty", proposer_body.casefold())
        self.assertNotIn(f'action="/phase6-briefs/{run["brief_id"]}/review"', proposer_body)
        status, reviewer_body = app.get(
            f'/phase6-briefs/{run["brief_id"]}',
            {"actor": ["rob"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn(f'action="/phase6-briefs/{run["brief_id"]}/review"', reviewer_body)
        self.assertIn("Research-quality review only", reviewer_body)
        with self.assertRaisesRegex(MissionControlError, "Business-unit"):
            app.get(
                f'/phase6-approvals/{approval["approval_event_id"]}',
                {"actor": ["dan"], "business_unit": ["unreal-talent"]},
            )

        server = build_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        host, port = server.server_address
        self.assertEqual(host, "127.0.0.1")
        connection = http.client.HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/phase6-enrichments?actor=noah&business_unit=unreal-media-group")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("Content-Security-Policy", response.headers)
        response.read(); connection.close()
        server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_static_surface_has_no_network_dynamic_generation_schedule_or_contact_capability(self) -> None:
        paths = [
            APP_ROOT / "mission_control" / "enrichment.py",
            PHASE6_FIXTURE,
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths).casefold()
        forbidden = (
            "requests", "urllib.request", "http.client", "socket", "subprocess", "eval(", "exec(",
            "generation_prompt", "generated_asset", "send_message", "contact_email", "phone_number",
            "cron", "daemon", "phase7",
        )
        for token in forbidden:
            self.assertNotIn(token, text)
        self.assertEqual(hashlib.sha256(PHASE6_FIXTURE.read_bytes()).hexdigest().__len__(), 64)


if __name__ == "__main__":
    unittest.main()
