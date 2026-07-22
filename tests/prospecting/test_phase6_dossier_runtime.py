"""Phase 6 durable dossier runtime, authority, race, and release tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "apps" / "prospecting-mission-control"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from mission_control.agent import RegisteredAgentService  # noqa: E402
from mission_control.application import (  # noqa: E402
    CounterIds,
    FixtureRepository,
    MissionControl,
    MissionControlError,
)
from mission_control.dossier import (  # noqa: E402
    ACCOUNT_IDENTITY_VERSION,
    DossierService,
    derive_account_id,
)
from mission_control.enrichment import EnrichmentService  # noqa: E402
from mission_control.store import (  # noqa: E402
    DOSSIER_SCHEMA_VERSION,
    PHASE6_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SqliteStore,
)
from mission_control.web import WebApplication  # noqa: E402

PHASE3_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase3" / "prospects.json"
BRIEF_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "brief-fixtures.json"
DOSSIER_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "dossier-fixtures.json"
HISTORY_FIXTURE = ROOT / "fixtures" / "prospecting" / "phase6" / "dossier-history.json"
FIXED = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.value = FIXED

    def __call__(self) -> datetime:
        return self.value


class Fault:
    def __init__(self) -> None:
        self.armed: str | None = None

    def __call__(self, label: str) -> None:
        if self.armed == label:
            self.armed = None
            raise sqlite3.OperationalError("synthetic storage fault")


class PauseFault:
    def __init__(self) -> None:
        self.armed: str | None = None
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, label: str) -> None:
        if self.armed == label:
            self.entered.set()
            if not self.release.wait(5):
                raise sqlite3.OperationalError("synthetic pause timed out")


class ObservedRLock:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.entered = threading.Event()

    def __enter__(self):
        self.lock.acquire()
        self.entered.set()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.lock.release()
        return False


class Env:
    def __init__(self, directory: Path, *, fault: Fault | None = None):
        self.clock = Clock()
        self.path = directory / "phase6-dossier.sqlite3"
        self.repository = FixtureRepository(
            clock=self.clock,
            next_id=CounterIds(),
            fixture_path=PHASE3_FIXTURE,
        )
        self.store = SqliteStore(self.path, fault_hook=fault)
        self.agent = RegisteredAgentService(self.repository, self.store, clock=self.clock)
        self.enrichment = EnrichmentService(
            self.agent, fixture_path=BRIEF_FIXTURE, clock=self.clock
        )
        self.dossier = DossierService(
            self.enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=self.clock,
        )

    def search(self, *, unit: str = "unreal-media-group", key: str = "phase6-search"):
        include = ["product_photography", "product_video"] if unit == "unreal-media-group" else None
        exclude = ["ugc_ad"] if unit == "unreal-media-group" else None
        return self.dossier.create_search(
            "noah", unit, include_any=include, exclude=exclude, idempotency_key=key
        )[0]

    @staticmethod
    def selected(search: dict, result_id: str | None = None) -> dict:
        return next(
            item for item in search["results"]
            if item["selected"] and (result_id is None or item["result_id"] == result_id)
        )

    def approve(self, search: dict, *, unit: str = "unreal-media-group", result_id: str | None = None):
        result = self.selected(search, result_id)
        reviewer = "rob" if unit == "unreal-media-group" else "dan"
        return self.dossier.record_approval(
            reviewer,
            unit,
            search["search_id"],
            result["result_id"],
            decision="approved",
            reason="Exact synthetic dossier research is approved for independent review.",
        )

    def candidate(self, *, unit: str = "unreal-media-group", suffix: str = "one"):
        search = self.search(unit=unit, key=f"search-{suffix}-{unit}")
        preferred = "result-umg-orbit" if unit == "unreal-media-group" else "result-talent-lumen"
        approval = self.approve(search, unit=unit, result_id=preferred)
        return self.dossier.start_dossier(
            "noah", unit, approval["approval_event_id"], f"run-{suffix}-{unit}"
        )[0]


class Phase6DossierRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.directory = Path(self._temporary.name)

    @staticmethod
    def schema_version(path: Path) -> int:
        connection = sqlite3.connect(path)
        try:
            return int(connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0])
        finally:
            connection.close()

    @staticmethod
    def populated_phase6a(path: Path):
        clock = Clock()
        repository = FixtureRepository(
            clock=clock, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE
        )
        store = SqliteStore(path)
        agent = RegisteredAgentService(repository, store, clock=clock)
        enrichment = EnrichmentService(
            agent, fixture_path=BRIEF_FIXTURE, clock=clock
        )
        control = MissionControl(repository)
        config = control.campaign_config({
            "business_unit": "unreal-media-group",
            "campaign_name": "Synthetic populated v3 preservation",
            "discovery_scope": "open",
            "verticals": "",
            "target_prospect_count": "20",
            "minimum_qualification_score": "72",
            "cooldown_days": "120",
            "maximum_evidence_age_days": "180",
            "geography": "United States",
            "rights_territory": "",
            "talent_categories": "",
        })
        campaign = repository.add_campaign("noah", config)
        run, _ = agent.create_manual_run(
            "noah", campaign["family_id"], campaign["version"], "populated-v3-worker"
        )
        agent.execute_run("noah", run["run_id"])
        detail = agent.run_detail("noah", run["run_id"])
        result = next(
            item for item in detail["output"]["result_snapshot"]
            if item.get("queue") == "new" and not item.get("effective_suppressed")
        )
        approval = enrichment.record_approval(
            "rob", "unreal-media-group", detail["review_task"]["task_id"],
            result["prospect_id"], decision="approved",
            reason="Synthetic populated v3 preservation approval.",
        )
        enrichment.start_enrichment(
            "noah", "unreal-media-group", approval["approval_event_id"], "populated-v3-enrichment"
        )
        return repository, store, agent, enrichment, clock

    def test_schema_activation_isolated_v2_v3_v4_and_valid_reopen(self) -> None:
        path = self.directory / "activation.sqlite3"
        store = SqliteStore(path)
        self.assertEqual(self.schema_version(path), SCHEMA_VERSION)
        repository = FixtureRepository(clock=lambda: FIXED, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE)
        agent = RegisteredAgentService(repository, store, clock=lambda: FIXED)
        enrichment = EnrichmentService(agent, fixture_path=BRIEF_FIXTURE, clock=lambda: FIXED)
        self.assertEqual(self.schema_version(path), PHASE6_SCHEMA_VERSION)
        with self.assertRaisesRegex(MissionControlError, "prerequisite"):
            SqliteStore(self.directory / "v2-only.sqlite3").initialize_phase6_dossier()
        dossier = DossierService(
            enrichment, fixture_path=DOSSIER_FIXTURE, history_path=HISTORY_FIXTURE, clock=lambda: FIXED
        )
        self.assertEqual(self.schema_version(path), DOSSIER_SCHEMA_VERSION)
        search, _ = dossier.create_search(
            "noah", "unreal-media-group", include_any=["product_video"], exclude=["ugc_ad"],
            idempotency_key="reopen-proof",
        )
        before = json.dumps(search, sort_keys=True, separators=(",", ":"))
        database_before_reopen = path.read_bytes()
        reopened_store = SqliteStore(path)
        reopened_agent = RegisteredAgentService(repository, reopened_store, clock=lambda: FIXED)
        reopened_enrichment = EnrichmentService(
            reopened_agent, fixture_path=BRIEF_FIXTURE, clock=lambda: FIXED
        )
        reopened = DossierService(
            reopened_enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=lambda: FIXED,
        )
        after = json.dumps(
            reopened.get_search("noah", "unreal-media-group", search["search_id"]),
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(before, after)
        self.assertEqual(database_before_reopen, path.read_bytes())

        connection = sqlite3.connect(path)
        try:
            connection.execute("DROP TRIGGER dossier_candidates_no_update")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "incomplete schema-v4"):
            SqliteStore(path)

        wrong_index = self.directory / "wrong-index" / "state.sqlite3"
        wrong_index.parent.mkdir()
        repository = FixtureRepository(
            clock=lambda: FIXED, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE
        )
        wrong_store = SqliteStore(wrong_index)
        wrong_agent = RegisteredAgentService(repository, wrong_store, clock=lambda: FIXED)
        wrong_enrichment = EnrichmentService(
            wrong_agent, fixture_path=BRIEF_FIXTURE, clock=lambda: FIXED
        )
        DossierService(
            wrong_enrichment, fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE, clock=lambda: FIXED,
        )
        connection = sqlite3.connect(wrong_index)
        try:
            connection.execute("DROP INDEX dossier_approval_root_idx")
            connection.execute(
                "CREATE INDEX dossier_approval_root_idx "
                "ON dossier_approval_events(result_record_id)"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "incomplete schema-v4"):
            SqliteStore(wrong_index)

    def test_v3_to_v4_upgrade_rolls_back_and_preserves_existing_bytes(self) -> None:
        fault = Fault()
        path = self.directory / "rollback.sqlite3"
        repository = FixtureRepository(clock=lambda: FIXED, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE)
        store = SqliteStore(path, fault_hook=fault)
        agent = RegisteredAgentService(repository, store, clock=lambda: FIXED)
        enrichment = EnrichmentService(agent, fixture_path=BRIEF_FIXTURE, clock=lambda: FIXED)
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO id_counters(prefix,value) VALUES('preserved',73)"
            )
        fault.armed = "initialize_phase6_dossier"
        with self.assertRaises(MissionControlError):
            DossierService(
                enrichment, fixture_path=DOSSIER_FIXTURE, history_path=HISTORY_FIXTURE, clock=lambda: FIXED
            )
        self.assertEqual(self.schema_version(path), PHASE6_SCHEMA_VERSION)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                connection.execute("SELECT value FROM id_counters WHERE prefix='preserved'").fetchone()[0],
                73,
            )
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dossier_searches'"
            ).fetchone())
        finally:
            connection.close()

    def test_populated_v3_to_v4_upgrade_preserves_all_phase6a_rows_exactly(self) -> None:
        path = self.directory / "populated-upgrade.sqlite3"
        _repository, _store, _agent, enrichment, clock = self.populated_phase6a(path)
        tables = (
            "prospect_approval_events",
            "enrichment_runs",
            "enrichment_sources",
            "campaign_brief_versions",
            "brief_evidence_links",
            "brief_integrity_manifests",
        )

        def snapshots() -> dict[str, list[tuple]]:
            connection = sqlite3.connect(path)
            try:
                return {
                    table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                    for table in tables
                }
            finally:
                connection.close()

        before = snapshots()
        self.assertTrue(all(before[table] for table in tables))
        DossierService(
            enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=clock,
        )
        self.assertEqual(self.schema_version(path), DOSSIER_SCHEMA_VERSION)
        self.assertEqual(before, snapshots())

    def test_integrity_foreign_keys_and_immutable_runtime_records(self) -> None:
        env = Env(self.directory)
        candidate = env.candidate()
        outcome, _ = env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Exact candidate accepted.", idempotency_key="accept-immutable",
        )
        self.assertIsNotNone(outcome["package"])
        connection = sqlite3.connect(env.path)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE lead_intelligence_packages SET content_hash='bad' WHERE package_id=?",
                    (outcome["package"]["package_id"],),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE dossier_runs SET result_id='tampered' WHERE dossier_run_id=?",
                    (candidate["dossier_run_id"],),
                )
        finally:
            connection.close()

    def test_self_consistent_snapshot_rewrites_fail_closed_on_read(self) -> None:
        approval_env = Env(self.directory / "approval-rewrite")
        approval_search = approval_env.search()
        approval = approval_env.approve(
            approval_search, result_id="result-umg-orbit"
        )
        connection = sqlite3.connect(approval_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_approval_events_no_update")
            raw = connection.execute(
                "SELECT event_snapshot FROM dossier_approval_events "
                "WHERE approval_event_id=?",
                (approval["approval_event_id"],),
            ).fetchone()[0]
            snapshot = json.loads(raw)
            snapshot.pop("scope")
            rewritten = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            encoded = rewritten.encode("utf-8")
            connection.execute(
                "UPDATE dossier_approval_events "
                "SET event_snapshot=?,content_hash=?,byte_length=? "
                "WHERE approval_event_id=?",
                (
                    rewritten,
                    hashlib.sha256(encoded).hexdigest(),
                    len(encoded),
                    approval["approval_event_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            approval_env.dossier.get_approval(
                "rob", "unreal-media-group", approval["approval_event_id"]
            )

        candidate_env = Env(self.directory / "candidate-rewrite")
        candidate = candidate_env.candidate()
        connection = sqlite3.connect(candidate_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_candidates_no_update")
            snapshot = json.loads(connection.execute(
                "SELECT dossier_snapshot FROM dossier_candidates "
                "WHERE candidate_version_id=?",
                (candidate["candidate_version_id"],),
            ).fetchone()[0])
            snapshot["dossier_id"] = "dossier-self-consistent-rewrite"
            rewritten = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            encoded = rewritten.encode("utf-8")
            connection.execute(
                "UPDATE dossier_candidates "
                "SET dossier_snapshot=?,content_hash=?,byte_length=? "
                "WHERE candidate_version_id=?",
                (
                    rewritten,
                    hashlib.sha256(encoded).hexdigest(),
                    len(encoded),
                    candidate["candidate_version_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            candidate_env.dossier.get_candidate(
                "noah", "unreal-media-group", candidate["candidate_version_id"]
            )

        link_env = Env(self.directory / "candidate-link-rewrite")
        linked_candidate = link_env.candidate()
        connection = sqlite3.connect(link_env.path)
        connection.row_factory = sqlite3.Row
        try:
            original_search = dict(connection.execute(
                "SELECT * FROM dossier_searches WHERE search_id=?",
                (linked_candidate["search_id"],),
            ).fetchone())
            cloned_search = dict(original_search)
            cloned_search["search_id"] = "dsearch-umg-cloned"
            cloned_search["idempotency_key"] = "cloned-search-key"
            connection.execute(
                f"INSERT INTO dossier_searches ({','.join(cloned_search)}) VALUES "
                f"({','.join('?' for _ in cloned_search)})",
                tuple(cloned_search.values()),
            )
            original_result = dict(connection.execute(
                "SELECT * FROM dossier_results WHERE result_record_id=("
                "SELECT result_record_id FROM dossier_runs WHERE dossier_run_id=?)",
                (linked_candidate["dossier_run_id"],),
            ).fetchone())
            cloned_result = dict(original_result)
            cloned_result["result_record_id"] = "dresult-umg-cloned"
            cloned_result["search_id"] = cloned_search["search_id"]
            connection.execute(
                f"INSERT INTO dossier_results ({','.join(cloned_result)}) VALUES "
                f"({','.join('?' for _ in cloned_result)})",
                tuple(cloned_result.values()),
            )
            connection.execute("DROP TRIGGER dossier_runs_identity_no_update")
            connection.execute(
                "UPDATE dossier_runs SET result_record_id=? WHERE dossier_run_id=?",
                (cloned_result["result_record_id"], linked_candidate["dossier_run_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            link_env.dossier.get_candidate(
                "noah", "unreal-media-group", linked_candidate["candidate_version_id"]
            )

        review_env = Env(self.directory / "review-rewrite")
        review_candidate = review_env.candidate()
        review, _ = review_env.dossier.review_candidate(
            "rob",
            "unreal-media-group",
            review_candidate["candidate_version_id"],
            decision="accepted",
            reason="Exact candidate accepted before integrity rewrite.",
            idempotency_key="review-rewrite",
        )
        connection = sqlite3.connect(review_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_review_events_no_update")
            review_id = review["review"]["review_event_id"]
            raw = connection.execute(
                "SELECT event_snapshot FROM dossier_review_events "
                "WHERE review_event_id=?",
                (review_id,),
            ).fetchone()[0]
            snapshot = json.loads(raw)
            snapshot.pop("decision")
            rewritten = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            encoded = rewritten.encode("utf-8")
            connection.execute(
                "UPDATE dossier_review_events "
                "SET event_snapshot=?,content_hash=?,byte_length=? "
                "WHERE review_event_id=?",
                (
                    rewritten,
                    hashlib.sha256(encoded).hexdigest(),
                    len(encoded),
                    review_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            review_env.dossier.review_for_candidate(
                "rob", "unreal-media-group", review_candidate["candidate_version_id"]
            )

    def test_stored_idempotency_fingerprints_are_recomputed_before_replay(self) -> None:
        def fingerprint(value: dict) -> str:
            return hashlib.sha256(json.dumps(
                value, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()

        search_env = Env(self.directory / "search-fingerprint")
        search = search_env.search(key="fingerprint-search")
        changed_request = search_env.dossier._template_request("unreal-media-group")
        changed_request["opportunity_filter"] = {"include_any": ["product_video"]}
        changed_request_hash = hashlib.sha256(json.dumps(
            changed_request, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        changed_search_fingerprint = fingerprint({
            "actor": "noah",
            "business_unit": "unreal-media-group",
            "request_hash": changed_request_hash,
        })
        connection = sqlite3.connect(search_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_searches_no_update")
            connection.execute(
                "UPDATE dossier_searches SET request_fingerprint=? WHERE search_id=?",
                (changed_search_fingerprint, search["search_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            search_env.dossier.create_search(
                "noah",
                "unreal-media-group",
                include_any=["product_video"],
                exclude=None,
                idempotency_key="fingerprint-search",
            )

        run_env = Env(self.directory / "run-fingerprint")
        first_search = run_env.search(key="run-fingerprint-first")
        first_approval = run_env.approve(
            first_search, result_id="result-umg-orbit"
        )
        first_run, _ = run_env.dossier.claim_dossier(
            "noah",
            "unreal-media-group",
            first_approval["approval_event_id"],
            "fingerprint-run",
        )
        second_approval = run_env.approve(
            first_search, result_id="result-umg-reengage"
        )
        changed_run_fingerprint = fingerprint({
            "actor": "noah",
            "approval_event_id": second_approval["approval_event_id"],
            "business_unit": "unreal-media-group",
        })
        connection = sqlite3.connect(run_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_runs_identity_no_update")
            connection.execute(
                "UPDATE dossier_runs SET request_fingerprint=? WHERE dossier_run_id=?",
                (changed_run_fingerprint, first_run["dossier_run_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            run_env.dossier.claim_dossier(
                "noah",
                "unreal-media-group",
                second_approval["approval_event_id"],
                "fingerprint-run",
            )

        review_env = Env(self.directory / "review-fingerprint")
        candidate = review_env.candidate()
        accepted, _ = review_env.dossier.review_candidate(
            "rob",
            "unreal-media-group",
            candidate["candidate_version_id"],
            decision="accepted",
            reason="Original accepted input.",
            idempotency_key="fingerprint-review",
        )
        changed_reason = "Different rejected input."
        changed_review_fingerprint = fingerprint({
            "actor": "rob",
            "business_unit": "unreal-media-group",
            "candidate_id": candidate["candidate_version_id"],
            "decision": "rejected",
            "reason": changed_reason,
        })
        review_id = accepted["review"]["review_event_id"]
        connection = sqlite3.connect(review_env.path)
        try:
            connection.execute("DROP TRIGGER dossier_review_events_no_update")
            row = connection.execute(
                "SELECT event_snapshot FROM dossier_review_events "
                "WHERE review_event_id=?",
                (review_id,),
            ).fetchone()
            event = json.loads(row[0])
            event["request_fingerprint"] = changed_review_fingerprint
            raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
            encoded = raw.encode("utf-8")
            connection.execute(
                "UPDATE dossier_review_events SET request_fingerprint=?,"
                "event_snapshot=?,content_hash=?,byte_length=? WHERE review_event_id=?",
                (
                    changed_review_fingerprint,
                    raw,
                    hashlib.sha256(encoded).hexdigest(),
                    len(encoded),
                    review_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(MissionControlError, "integrity"):
            review_env.dossier.review_candidate(
                "rob",
                "unreal-media-group",
                candidate["candidate_version_id"],
                decision="rejected",
                reason=changed_reason,
                idempotency_key="fingerprint-review",
            )

    def test_operational_filter_history_protection_and_cap_order(self) -> None:
        env = Env(self.directory)
        search = env.search()
        decisions = {item["result_id"]: item["decision"] for item in search["results"]}
        self.assertEqual(decisions["result-umg-orbit"]["filter_state"], "matched")
        self.assertTrue(decisions["result-umg-orbit"]["selected"])
        self.assertEqual(decisions["result-umg-ugc"]["filter_state"], "excluded")
        self.assertFalse(decisions["result-umg-ugc"]["selected"])
        self.assertEqual(
            decisions["result-umg-duplicate"]["filter_state"],
            "not_evaluated_history_protection",
        )
        self.assertEqual(
            decisions["result-umg-reengage"]["history_classification"]["status"],
            "existing_new_trigger",
        )
        self.assertRegex(search["history_hash"], r"^[0-9a-f]{64}$")

    def test_search_idempotency_and_business_unit_isolation(self) -> None:
        env = Env(self.directory)
        search = env.search(key="same-search")
        replay, created = env.dossier.create_search(
            "noah", "unreal-media-group",
            include_any=["product_photography", "product_video"], exclude=["ugc_ad"],
            idempotency_key="same-search",
        )
        self.assertFalse(created)
        self.assertEqual(replay, search)
        with self.assertRaisesRegex(MissionControlError, "different search input"):
            env.dossier.create_search(
                "noah", "unreal-media-group", include_any=["product_video"], exclude=None,
                idempotency_key="same-search",
            )
        with self.assertRaisesRegex(MissionControlError, "Business-unit") as raised:
            env.dossier.get_search("dan", "unreal-talent", search["search_id"])
        self.assertNotIn(search["search_id"], str(raised.exception))

    def test_idempotency_keys_are_scoped_to_each_business_unit(self) -> None:
        env = Env(self.directory)
        umg = env.search(unit="unreal-media-group", key="shared-search")
        talent = env.search(unit="unreal-talent", key="shared-search")
        self.assertNotEqual(umg["search_id"], talent["search_id"])
        self.assertEqual(umg["search_id"], "dsearch-umg-0001")
        self.assertEqual(talent["search_id"], "dsearch-talent-0001")

        umg_approval = env.approve(
            umg, unit="unreal-media-group", result_id="result-umg-orbit"
        )
        talent_approval = env.approve(
            talent, unit="unreal-talent", result_id="result-talent-lumen"
        )
        umg_candidate, _ = env.dossier.start_dossier(
            "noah", "unreal-media-group", umg_approval["approval_event_id"], "shared-run"
        )
        talent_candidate, _ = env.dossier.start_dossier(
            "noah", "unreal-talent", talent_approval["approval_event_id"], "shared-run"
        )
        umg_release, _ = env.dossier.review_candidate(
            "rob", "unreal-media-group", umg_candidate["candidate_version_id"],
            decision="accepted", reason="Independent UMG acceptance.",
            idempotency_key="shared-review",
        )
        talent_release, _ = env.dossier.review_candidate(
            "dan", "unreal-talent", talent_candidate["candidate_version_id"],
            decision="accepted", reason="Independent Talent acceptance.",
            idempotency_key="shared-review",
        )
        self.assertNotEqual(
            umg_release["package"]["package_id"], talent_release["package"]["package_id"]
        )

    def test_shared_global_account_does_not_leak_cross_unit_version_count(self) -> None:
        fixture = json.loads(DOSSIER_FIXTURE.read_text(encoding="utf-8"))
        talent_candidate = next(
            item for item in fixture["candidates"]
            if item["result_id"] == "result-talent-lumen"
        )
        talent_candidate.update({
            "global_identity_id": "global-orbit",
            "account_id": "account-orbit",
            "domain": "orbit.example",
        })
        talent_dossier = next(
            item for item in fixture["dossiers"]
            if item["approved_result"]["result_id"] == "result-talent-lumen"
        )
        talent_dossier["approved_result"].update({
            "global_identity_id": "global-orbit",
            "account_id": "account-orbit",
            "canonical_domain": "orbit.example",
        })
        next(
            item for item in talent_dossier["entities"]
            if item["node_type"] == "organization"
        )["attributes"]["canonical_domain"] = "orbit.example"
        fixture_path = self.directory / "shared-account-fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

        def create_candidate(
            dossier: DossierService, unit: str, result_id: str, reviewer: str
        ) -> dict:
            search, _ = dossier.create_search(
                "noah",
                unit,
                include_any=["product_photography", "product_video"]
                if unit == "unreal-media-group" else None,
                exclude=["ugc_ad"] if unit == "unreal-media-group" else None,
                idempotency_key=f"shared-account-search-{unit}",
            )
            approval = dossier.record_approval(
                reviewer,
                unit,
                search["search_id"],
                result_id,
                decision="approved",
                reason="Independent exact shared-account proof.",
            )
            return dossier.start_dossier(
                "noah",
                unit,
                approval["approval_event_id"],
                f"shared-account-run-{unit}",
            )[0]

        units = {
            "unreal-media-group": ("result-umg-orbit", "rob"),
            "unreal-talent": ("result-talent-lumen", "dan"),
        }
        for order in (
            ("unreal-media-group", "unreal-talent"),
            ("unreal-talent", "unreal-media-group"),
        ):
            with self.subTest(order=order):
                clock = Clock()
                state_path = self.directory / f"shared-account-{order[0]}.sqlite3"
                repository = FixtureRepository(
                    clock=clock, next_id=CounterIds(), fixture_path=PHASE3_FIXTURE
                )
                store = SqliteStore(state_path)
                agent = RegisteredAgentService(repository, store, clock=clock)
                enrichment = EnrichmentService(
                    agent, fixture_path=BRIEF_FIXTURE, clock=clock
                )
                dossier = DossierService(
                    enrichment,
                    fixture_path=fixture_path,
                    history_path=HISTORY_FIXTURE,
                    clock=clock,
                )
                candidates = {
                    unit: create_candidate(dossier, unit, *units[unit])
                    for unit in order
                }
                umg = candidates["unreal-media-group"]
                talent = candidates["unreal-talent"]
                self.assertEqual(umg["account_id"], talent["account_id"])
                self.assertEqual(
                    umg["dossier_family_id"], talent["dossier_family_id"]
                )
                self.assertEqual((umg["version"], talent["version"]), (1, 1))
                releases = {}
                for unit in order:
                    releases[unit], _ = dossier.review_candidate(
                        units[unit][1],
                        unit,
                        candidates[unit]["candidate_version_id"],
                        decision="accepted",
                        reason="Independent exact shared-account release.",
                        idempotency_key=f"shared-account-review-{unit}",
                    )
                self.assertNotEqual(
                    releases["unreal-media-group"]["package"]["package_id"],
                    releases["unreal-talent"]["package"]["package_id"],
                )

    def test_approval_rejects_wrong_source_authority_and_propagates_exact_hash(self) -> None:
        env = Env(self.directory)
        search = env.search()
        expected = env.dossier._synthetic_source_plan(
            "result-umg-orbit", "unreal-media-group"
        )
        wrong_plans = []
        for field, value in (
            ("business_unit", "unreal-talent"),
            ("result_id", "result-talent-lumen"),
            ("sources", []),
        ):
            plan = json.loads(json.dumps(expected))
            plan[field] = value
            wrong_plans.append(plan)
        for index, plan in enumerate(wrong_plans):
            with self.subTest(index=index), self.assertRaisesRegex(
                MissionControlError, "does not exactly match"
            ):
                env.dossier.record_approval(
                    "rob", "unreal-media-group", search["search_id"], "result-umg-orbit",
                    decision="approved", reason="Wrong authority must not persist.",
                    source_plan=plan,
                )
        connection = sqlite3.connect(env.path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM dossier_approval_events").fetchone()[0], 0
            )
        finally:
            connection.close()

        approval = env.approve(search, result_id="result-umg-orbit")
        approved_result = env.selected(search, "result-umg-orbit")
        result_bytes = json.dumps(
            approved_result, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(
            (approval["result_hash"], approval["result_byte_length"]),
            (hashlib.sha256(result_bytes).hexdigest(), len(result_bytes)),
        )
        candidate, _ = env.dossier.start_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "source-bound-run"
        )
        release, _ = env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Exact source-bound candidate accepted.",
            idempotency_key="source-bound-review",
        )
        connection = sqlite3.connect(env.path)
        connection.row_factory = sqlite3.Row
        try:
            final = connection.execute(
                "SELECT source_plan_hash FROM customer_dossier_versions"
            ).fetchone()
            package = connection.execute(
                "SELECT source_plan_hash FROM lead_intelligence_packages"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            {
                approval["source_plan_hash"],
                candidate["source_plan_hash"],
                final["source_plan_hash"],
                package["source_plan_hash"],
                release["package"]["source_plan_hash"],
            },
            {approval["source_plan_hash"]},
        )

    def test_account_identity_is_derived_and_fixture_values_are_not_authority(self) -> None:
        env = Env(self.directory)
        search = env.search()
        result = env.selected(search, "result-umg-orbit")
        payload = json.dumps(
            {
                "canonical_domain": result["canonical_domain"],
                "global_identity_id": result["global_identity_id"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = ACCOUNT_IDENTITY_VERSION + "-" + hashlib.sha256(payload).hexdigest()[:24]
        self.assertEqual(result["account_id"], expected)
        self.assertNotEqual(result["account_id"], result["candidate"].get("fixture_account_id", "account-orbit"))
        self.assertEqual(
            derive_account_id(result["canonical_domain"], result["global_identity_id"]), expected
        )

    def test_revoke_first_denies_claim_and_concurrent_supersession_has_one_winner(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        revoked = env.dossier.record_approval(
            "rob", "unreal-media-group", search["search_id"], "result-umg-orbit",
            decision="revoked", reason="Approval withdrawn before claim.",
            expected_leaf_id=approval["approval_event_id"],
        )
        with self.assertRaisesRegex(MissionControlError, "current leaf"):
            env.dossier.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "revoked-claim"
            )
        self.assertEqual(env.dossier.get_approval(
            "rob", "unreal-media-group", revoked["approval_event_id"]
        )["decision"], "revoked")

        fresh = Env(self.directory / "concurrent")
        search = fresh.search()
        approval = fresh.approve(search, result_id="result-umg-orbit")
        barrier = threading.Barrier(3)
        outcomes: list[str] = []

        def supersede(decision: str) -> None:
            barrier.wait()
            try:
                fresh.dossier.record_approval(
                    "rob", "unreal-media-group", search["search_id"], "result-umg-orbit",
                    decision=decision, reason=f"Concurrent {decision} decision.",
                    expected_leaf_id=approval["approval_event_id"],
                )
                outcomes.append("won")
            except MissionControlError as exc:
                outcomes.append(f"lost:{exc.status}")

        threads = [threading.Thread(target=supersede, args=(value,)) for value in ("revoked", "invalidated")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["lost:409", "won"])

    def test_claim_first_can_finish_after_nonretroactive_revocation_and_no_second_claim(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        run, created = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "claim-first"
        )
        self.assertTrue(created)
        env.dossier.record_approval(
            "rob", "unreal-media-group", search["search_id"], "result-umg-orbit",
            decision="revoked", reason="Non-retroactive revocation after committed claim.",
            expected_leaf_id=approval["approval_event_id"],
        )
        candidate = env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(candidate["dossier"]["review_state"], "pending_research_quality_review")
        with self.assertRaisesRegex(MissionControlError, "already consumed|current leaf"):
            env.dossier.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "claim-second"
            )

    def test_expiry_and_claim_idempotency_conflict(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.dossier.record_approval(
            "rob", "unreal-media-group", search["search_id"], "result-umg-orbit",
            decision="approved", reason="Approval at exact seven-day boundary.",
            effective_at=FIXED - timedelta(days=7),
        )
        with self.assertRaisesRegex(MissionControlError, "expired"):
            env.dossier.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "expired-run"
            )

        fresh = Env(self.directory / "idempotency")
        search = fresh.search()
        approval = fresh.approve(search, result_id="result-umg-orbit")
        first, _ = fresh.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "same-run"
        )
        replay, created = fresh.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "same-run"
        )
        self.assertFalse(created)
        self.assertEqual(first, replay)

    def test_cancel_first_blocks_candidate_and_complete_first_blocks_cancel(self) -> None:
        env = Env(self.directory)
        search = env.search(key="cancel-search")
        approval = env.approve(search, result_id="result-umg-orbit")
        run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "cancel-run"
        )
        env.dossier.cancel_dossier("noah", "unreal-media-group", run["dossier_run_id"])
        with self.assertRaisesRegex(MissionControlError, "cancelled"):
            env.dossier.complete_dossier("noah", "unreal-media-group", run["dossier_run_id"])
        self.assertEqual(env.dossier.candidates("noah", "unreal-media-group"), [])

        complete = Env(self.directory / "complete")
        candidate = complete.candidate()
        with self.assertRaisesRegex(MissionControlError, "terminal"):
            complete.dossier.cancel_dossier(
                "noah", "unreal-media-group", candidate["dossier_run_id"]
            )

    def test_cancel_and_unrelated_claim_use_one_lock_order_without_busy_failure(self) -> None:
        original_lock = DossierService._active_lock
        observed = ObservedRLock()
        DossierService._active_lock = observed
        self.addCleanup(setattr, DossierService, "_active_lock", original_lock)
        pause = PauseFault()
        env = Env(self.directory, fault=pause)
        umg_search = env.search(unit="unreal-media-group", key="lock-order-umg")
        umg_approval = env.approve(
            umg_search, unit="unreal-media-group", result_id="result-umg-orbit"
        )
        umg_run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", umg_approval["approval_event_id"], "lock-order-umg-run"
        )
        talent_search = env.search(unit="unreal-talent", key="lock-order-talent")
        talent_approval = env.approve(
            talent_search, unit="unreal-talent", result_id="result-talent-lumen"
        )
        observed.entered.clear()
        pause.armed = "insert_audit"
        outcomes: dict[str, str] = {}

        def cancel() -> None:
            try:
                outcomes["cancel"] = env.dossier.cancel_dossier(
                    "noah", "unreal-media-group", umg_run["dossier_run_id"]
                )["state"]
            except MissionControlError as exc:
                outcomes["cancel"] = f"error:{exc.status}"

        def claim() -> None:
            try:
                outcomes["claim"] = env.dossier.claim_dossier(
                    "noah", "unreal-talent", talent_approval["approval_event_id"],
                    "lock-order-talent-run",
                )[0]["state"]
            except MissionControlError as exc:
                outcomes["claim"] = f"error:{exc.status}"

        cancel_thread = threading.Thread(target=cancel)
        claim_thread = threading.Thread(target=claim)
        cancel_thread.start()
        self.assertTrue(pause.entered.wait(2))
        claim_thread.start()
        self.assertTrue(observed.entered.wait(2))
        pause.release.set()
        cancel_thread.join(5)
        claim_thread.join(5)
        self.assertFalse(cancel_thread.is_alive())
        self.assertFalse(claim_thread.is_alive())
        self.assertEqual(outcomes, {"cancel": "cancelled", "claim": "running"})
        connection = sqlite3.connect(env.path)
        try:
            talent_run = connection.execute(
                "SELECT dossier_run_id FROM dossier_runs "
                "WHERE business_unit='unreal-talent'"
            ).fetchone()[0]
        finally:
            connection.close()
        env.dossier.cancel_dossier("noah", "unreal-talent", talent_run)

    def test_live_claim_survives_second_service_and_interrupted_claim_fails_closed(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "interrupted"
        )
        restarted_store = SqliteStore(env.path)
        restarted_agent = RegisteredAgentService(env.repository, restarted_store, clock=env.clock)
        restarted_enrichment = EnrichmentService(
            restarted_agent, fixture_path=BRIEF_FIXTURE, clock=env.clock
        )
        restarted = DossierService(
            restarted_enrichment,
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(restarted.recovered_run_ids, [])
        replay, created = restarted.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "interrupted"
        )
        self.assertFalse(created)
        self.assertEqual(replay["state"], "running")

        # Simulate process termination by removing the process-local active
        # registration while leaving the committed running claim intact.
        env.dossier._unregister_active(run["dossier_run_id"])
        restarted._unregister_active(run["dossier_run_id"])
        after_exit = DossierService(
            EnrichmentService(
                RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
                fixture_path=BRIEF_FIXTURE,
                clock=env.clock,
            ),
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(after_exit.recovered_run_ids, [run["dossier_run_id"]])
        with self.assertRaisesRegex(MissionControlError, "terminal"):
            after_exit.complete_dossier("noah", "unreal-media-group", run["dossier_run_id"])

    def test_durable_history_prevents_rediscovery_and_reuses_no_trigger(self) -> None:
        env = Env(self.directory)
        first = env.search(key="history-first")
        self.assertTrue(env.selected(first, "result-umg-orbit")["selected"])
        second = env.search(key="history-second")
        by_result = {item["result_id"]: item["decision"] for item in second["results"]}
        self.assertEqual(
            by_result["result-umg-orbit"]["history_classification"]["status"],
            "existing_no_new_trigger",
        )
        self.assertFalse(by_result["result-umg-orbit"]["selected"])
        self.assertEqual(
            by_result["result-umg-reengage"]["history_classification"]["status"],
            "existing_no_new_trigger",
        )
        self.assertFalse(by_result["result-umg-reengage"]["selected"])
        self.assertNotEqual(first["history_hash"], second["history_hash"])

    def test_claimed_template_uses_actual_runtime_cutoff_and_marks_stale_claims(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "stale-claim"
        )
        env.clock.value = FIXED + timedelta(days=400)
        candidate = env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(
            candidate["dossier"]["research_cutoff"],
            env.clock.value.isoformat().replace("+00:00", "Z"),
        )
        claim_states = {
            claim["freshness_state"]
            for category in candidate["dossier"]["categories"]
            for claim in category["claims"]
        }
        self.assertEqual(claim_states, {"stale"})
        released, _ = env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Staleness is explicit in this exact version.",
            idempotency_key="stale-review",
        )
        self.assertEqual(released["package"]["package"]["research_cutoff"], candidate["dossier"]["research_cutoff"])

    def test_stored_approval_survives_fixture_evolution_but_execution_requires_new_approval(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        changed_path = self.directory / "dossier-fixtures-v2.json"
        changed = json.loads(DOSSIER_FIXTURE.read_text(encoding="utf-8"))
        orbit = next(
            item for item in changed["dossiers"]
            if item["approved_result"]["result_id"] == "result-umg-orbit"
        )
        orbit["evidence_inventory"][0]["source_url"] = "https://orbit.example/about-v2"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        evolved = DossierService(
            EnrichmentService(
                RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
                fixture_path=BRIEF_FIXTURE,
                clock=env.clock,
            ),
            fixture_path=changed_path,
            history_path=HISTORY_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(
            evolved.get_approval(
                "rob", "unreal-media-group", approval["approval_event_id"]
            )["source_plan_hash"],
            approval["source_plan_hash"],
        )
        with self.assertRaisesRegex(MissionControlError, "source plan changed"):
            evolved.claim_dossier(
                "noah", "unreal-media-group", approval["approval_event_id"], "evolved-plan"
            )

    def test_synchronous_candidate_failure_terminalizes_claim_without_artifact(self) -> None:
        fault = Fault()
        env = Env(self.directory, fault=fault)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "failed-candidate"
        )
        fault.armed = "insert_dossier_candidate"
        with self.assertRaises(MissionControlError):
            env.dossier.complete_dossier(
                "noah", "unreal-media-group", run["dossier_run_id"]
            )
        connection = sqlite3.connect(env.path)
        try:
            run = connection.execute(
                "SELECT state,failure_class FROM dossier_runs WHERE idempotency_key='failed-candidate'"
            ).fetchone()
            self.assertEqual(run, ("failed", "candidate_creation_failed"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dossier_candidates").fetchone()[0], 0)
        finally:
            connection.close()

    def test_rejected_completion_cannot_unregister_or_recover_another_actors_claim(self) -> None:
        env = Env(self.directory)
        search = env.search()
        approval = env.approve(search, result_id="result-umg-orbit")
        run, _ = env.dossier.claim_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "owned-claim"
        )
        with self.assertRaisesRegex(MissionControlError, "initiating actor"):
            env.dossier.complete_dossier(
                "rob", "unreal-media-group", run["dossier_run_id"]
            )
        concurrent = DossierService(
            EnrichmentService(
                RegisteredAgentService(env.repository, SqliteStore(env.path), clock=env.clock),
                fixture_path=BRIEF_FIXTURE,
                clock=env.clock,
            ),
            fixture_path=DOSSIER_FIXTURE,
            history_path=HISTORY_FIXTURE,
            clock=env.clock,
        )
        self.assertEqual(concurrent.recovered_run_ids, [])
        candidate = env.dossier.complete_dossier(
            "noah", "unreal-media-group", run["dossier_run_id"]
        )
        self.assertEqual(candidate["dossier_run_id"], run["dossier_run_id"])

    def test_no_package_before_acceptance_and_all_eleven_categories_for_both_units(self) -> None:
        env = Env(self.directory)
        umg = env.candidate(unit="unreal-media-group", suffix="umg")
        talent = env.candidate(unit="unreal-talent", suffix="talent")
        self.assertEqual(len(umg["dossier"]["categories"]), 11)
        self.assertEqual(len(talent["dossier"]["categories"]), 11)
        self.assertEqual(env.dossier.packages("noah", "unreal-media-group"), [])
        rejected, _ = env.dossier.review_candidate(
            "rob", "unreal-media-group", umg["candidate_version_id"],
            decision="changes_requested", reason="More evidence is required.",
            idempotency_key="needs-changes",
        )
        self.assertIsNone(rejected["package"])
        self.assertEqual(env.dossier.packages("noah", "unreal-media-group"), [])

    def test_terminal_review_replay_conflict_and_atomic_inert_release(self) -> None:
        env = Env(self.directory)
        candidate = env.candidate()
        released, created = env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Exact immutable candidate accepted.",
            idempotency_key="terminal-review",
        )
        self.assertTrue(created)
        replay, created = env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="accepted", reason="Exact immutable candidate accepted.",
            idempotency_key="terminal-review",
        )
        self.assertFalse(created)
        self.assertEqual(replay, released)
        authority = released["package"]["package"]["authority"]
        self.assertTrue(authority["local_data_handoff_only"])
        self.assertFalse(any(value for key, value in authority.items() if key != "local_data_handoff_only"))
        with self.assertRaisesRegex(MissionControlError, "different input"):
            env.dossier.review_candidate(
                "rob", "unreal-media-group", candidate["candidate_version_id"],
                decision="rejected", reason="Changed input.", idempotency_key="terminal-review",
            )
        with self.assertRaisesRegex(MissionControlError, "terminal review"):
            env.dossier.review_candidate(
                "rob", "unreal-media-group", candidate["candidate_version_id"],
                decision="rejected", reason="Second terminal decision.", idempotency_key="other-review",
            )

    def test_concurrent_accept_and_reject_has_one_terminal_winner(self) -> None:
        env = Env(self.directory)
        candidate = env.candidate()
        barrier = threading.Barrier(3)
        outcomes: list[str] = []

        def review(decision: str) -> None:
            barrier.wait()
            try:
                env.dossier.review_candidate(
                    "rob", "unreal-media-group", candidate["candidate_version_id"],
                    decision=decision, reason=f"Concurrent {decision} review.",
                    idempotency_key=f"review-{decision}",
                )
                outcomes.append(f"won:{decision}")
            except MissionControlError as exc:
                outcomes.append(f"lost:{exc.status}")

        threads = [threading.Thread(target=review, args=(value,)) for value in ("accepted", "rejected")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(len([item for item in outcomes if item.startswith("won:")]), 1)
        self.assertEqual(len([item for item in outcomes if item == "lost:409"]), 1)
        self.assertLessEqual(len(env.dossier.packages("noah", "unreal-media-group")), 1)

    def test_concurrent_accepted_reviews_release_exactly_one_package(self) -> None:
        env = Env(self.directory)
        candidate = env.candidate()
        barrier = threading.Barrier(3)
        outcomes: list[str] = []

        def accept(suffix: str) -> None:
            barrier.wait()
            try:
                env.dossier.review_candidate(
                    "rob", "unreal-media-group", candidate["candidate_version_id"],
                    decision="accepted", reason=f"Concurrent exact acceptance {suffix}.",
                    idempotency_key=f"accept-{suffix}",
                )
                outcomes.append("won")
            except MissionControlError as exc:
                outcomes.append(f"lost:{exc.status}")

        threads = [threading.Thread(target=accept, args=(suffix,)) for suffix in ("one", "two")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["lost:409", "won"])
        self.assertEqual(len(env.dossier.packages("noah", "unreal-media-group")), 1)

    def test_release_faults_roll_back_review_dossier_package_and_audit(self) -> None:
        for label in (
            "insert_dossier_review",
            "insert_final_dossier",
            "insert_lead_package",
            "insert_dossier_release_audit",
        ):
            with self.subTest(label=label):
                fault = Fault()
                env = Env(self.directory / label, fault=fault)
                candidate = env.candidate(suffix=label)
                fault.armed = label
                with self.assertRaises(MissionControlError):
                    env.dossier.review_candidate(
                        "rob", "unreal-media-group", candidate["candidate_version_id"],
                        decision="accepted", reason="Fault-injected acceptance.",
                        idempotency_key=f"review-{label}",
                    )
                connection = sqlite3.connect(env.path)
                try:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM dossier_review_events").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM customer_dossier_versions").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM lead_intelligence_packages").fetchone()[0], 0)
                finally:
                    connection.close()

    def test_web_flow_exposes_filter_authority_review_and_inert_package(self) -> None:
        env = Env(self.directory)
        app = WebApplication(
            MissionControl(env.repository),
            csrf_token="phase6-csrf",
            agent_service=env.agent,
            enrichment_service=env.enrichment,
            dossier_service=env.dossier,
        )
        status, body = app.get(
            "/phase6-dossiers",
            {"actor": ["noah"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("product_photography, product_video", body)
        self.assertIn("ugc_ad", body)
        self.assertIn("@media(max-width:640px)", body)
        self.assertIn("button:focus", body)
        status, search_body = app.post(
            "/phase6-searches",
            {
                "csrf_token": "phase6-csrf",
                "actor": "noah",
                "business_unit": "unreal-media-group",
                "include_any": "product_photography, product_video",
                "exclude": "ugc_ad",
                "idempotency_key": "web-search",
            },
        )
        self.assertEqual(status, 200)
        search = env.dossier.searches("noah", "unreal-media-group")[0]
        self.assertIn("History fingerprint", search_body)
        status, approval_body = app.post(
            "/phase6-dossier-approvals",
            {
                "csrf_token": "phase6-csrf",
                "actor": "rob",
                "business_unit": "unreal-media-group",
                "search_id": search["search_id"],
                "result_id": "result-umg-orbit",
                "decision": "approved",
                "reason": "Exact web-selected result approved for dossier research.",
            },
        )
        self.assertEqual(status, 201)
        self.assertIn("Exact dossier authority event recorded", approval_body)
        approval = env.dossier.approvals("noah", "unreal-media-group")[0]
        status, candidate_body = app.post(
            "/phase6-dossier-runs",
            {
                "csrf_token": "phase6-csrf",
                "actor": "noah",
                "business_unit": "unreal-media-group",
                "approval_event_id": approval["approval_event_id"],
                "idempotency_key": "web-dossier",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("Eleven-category coverage", candidate_body)
        candidate = env.dossier.candidates("noah", "unreal-media-group")[0]
        status, accepted_body = app.post(
            f"/phase6-dossier-candidates/{candidate['candidate_version_id']}/review",
            {
                "csrf_token": "phase6-csrf",
                "actor": "rob",
                "business_unit": "unreal-media-group",
                "decision": "accepted",
                "reason": "Exact web dossier accepted after independent review.",
                "idempotency_key": "web-review",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("Every downstream action authority is false", accepted_body)
        status, package_body = app.get(
            "/phase6-packages",
            {"actor": ["rob"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn('&quot;outreach&quot;: false', package_body)

    def test_web_hides_review_form_after_nonaccepted_terminal_decision(self) -> None:
        env = Env(self.directory)
        candidate = env.candidate()
        env.dossier.review_candidate(
            "rob", "unreal-media-group", candidate["candidate_version_id"],
            decision="changes_requested", reason="This exact version needs more evidence.",
            idempotency_key="terminal-no-release",
        )
        app = WebApplication(
            MissionControl(env.repository),
            csrf_token="phase6-csrf",
            agent_service=env.agent,
            enrichment_service=env.enrichment,
            dossier_service=env.dossier,
        )
        status, body = app.get(
            f"/phase6-dossier-candidates/{candidate['candidate_version_id']}",
            {"actor": ["rob"], "business_unit": ["unreal-media-group"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("Terminal review recorded", body)
        self.assertIn("changes_requested", body)
        self.assertNotIn("Record one terminal review", body)

    def test_every_selected_synthetic_result_can_create_a_gapped_candidate(self) -> None:
        env = Env(self.directory)
        search = env.search()
        generic = env.selected(search, "result-umg-reengage")
        approval = env.approve(search, result_id=generic["result_id"])
        candidate, _ = env.dossier.start_dossier(
            "noah", "unreal-media-group", approval["approval_event_id"], "generic-dossier"
        )
        self.assertEqual(len(candidate["dossier"]["categories"]), 11)
        self.assertTrue(any(
            category["coverage_state"] != "complete"
            and category.get("gap_explanation")
            for category in candidate["dossier"]["categories"]
        ))


if __name__ == "__main__":
    unittest.main()
