"""Loopback HTTP surface for the synthetic Phase 3 review application."""

from __future__ import annotations

import argparse
import hmac
import html
import json
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from pathlib import Path

from .application import ACTOR_SCOPE, BUSINESS_UNITS, MissionControl, MissionControlError, safe_evidence_url, state_domain, utc_now
from .agent import AGENT_ID, RegisteredAgentService, default_state_path
from .dossier import DossierService
from .enrichment import EnrichmentService
from .shadow import SCHEDULE_MANAGER, ShadowLoopService, ShadowScheduler
from .store import SqliteStore

MAX_BODY = 32_768
MAX_FIELDS = 40
IDENTIFIER = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str, *, actor: str, business_unit: str, status: str = "") -> str:
    query = urlencode({"actor": actor, "business_unit": business_unit})
    status_html = f'<p class="status" role="status" aria-live="polite">{e(status)}</p>' if status else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · Prospecting Mission Control</title>
<style>
:root{{--ink:#17202a;--muted:#566573;--paper:#fbfcfc;--panel:#fff;--line:#ccd1d1;--accent:#174a7e;--warn:#7d3c00}}
*{{box-sizing:border-box}} body{{margin:0;font:16px/1.5 system-ui,sans-serif;color:var(--ink);background:var(--paper)}}
header,main,footer{{max-width:1120px;margin:auto;padding:1rem}} header{{border-bottom:1px solid var(--line)}}
nav{{display:flex;flex-wrap:wrap;gap:.8rem}} a{{color:var(--accent)}} a:focus,button:focus,input:focus,select:focus,textarea:focus{{outline:3px solid #f5b041;outline-offset:2px}}
.notice,.status,.error{{padding:.75rem;border:1px solid var(--line);background:var(--panel)}} .error{{border-color:#b03a2e}} .status{{border-color:#1e8449}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}} .card{{padding:1rem;border:1px solid var(--line);background:var(--panel)}}
label{{display:block;font-weight:650;margin-top:.7rem}} input,select,textarea{{width:100%;max-width:42rem;padding:.55rem;border:1px solid #7b7d7d;background:white}} input[type=checkbox]{{width:auto}}
button{{margin-top:1rem;padding:.65rem 1rem;border:0;background:var(--accent);color:white;font-weight:700;cursor:pointer}} button[disabled]{{opacity:.55;cursor:not-allowed}}
table{{width:100%;border-collapse:collapse;background:var(--panel)}} th,td{{padding:.55rem;text-align:left;border:1px solid var(--line);vertical-align:top}} pre{{white-space:pre-wrap;overflow-wrap:anywhere}} dd,code,td{{overflow-wrap:anywhere}}
.tag{{display:inline-block;padding:.1rem .4rem;border:1px solid var(--line);margin:.1rem}} .warning{{color:var(--warn);font-weight:700}}
@media(max-width:640px){{table,thead,tbody,tr,th,td{{display:block}} thead{{position:absolute;left:-9999px}} td{{border-top:0}} nav{{flex-direction:column}}}}
</style></head><body>
<header><p><strong>Prospecting Manual Mission Control</strong></p>
<p class="notice"><strong>Local governed review surface.</strong> Synthetic fixtures remain the default path; the separately labeled Phase 6 live proof is limited to its two exact public source plans. Actor selection simulates policy and never supplies human acceptance. No creative, likeness, outreach, external-write, deployment, or downstream-agent authority exists.</p>
<nav aria-label="Primary"><a href="/campaigns?{query}">Campaigns</a><a href="/runs?{query}">Run history</a><a href="/worker-runs?{query}">Worker runs</a><a href="/review-tasks?{query}">Review tasks</a><a href="/prospects?{query}&amp;queue=new">Prospect queues</a><a href="/registry?{query}">Agent registry</a><a href="/shadow-schedules?{query}">Shadow schedules</a><a href="/phase6-approvals?{query}">Fixture approvals</a><a href="/phase6-enrichments?{query}">Fixture briefs</a><a href="/phase6-dossiers?{query}">Customer dossiers</a><a href="/phase6-packages?{query}">Local packages</a></nav>
<form method="get" action="/campaigns"><label for="scope_actor">Local review actor (not authentication)</label><select id="scope_actor" name="actor">{''.join(f'<option value="{e(name)}"{" selected" if name == actor else ""}>{e(name)}</option>' for name in ACTOR_SCOPE)}</select><label for="scope_unit">Business-unit scope</label><select id="scope_unit" name="business_unit">{''.join(f'<option value="{e(unit)}"{" selected" if unit == business_unit else ""}>{e(unit)}</option>' for unit in sorted(BUSINESS_UNITS))}</select><button type="submit">Change local review scope</button></form>
<p>Review actor: <strong>{e(actor)}</strong> · Business unit: <strong>{e(business_unit)}</strong></p></header>
<main id="main"><h1>{e(title)}</h1>{status_html}{body}</main>
<footer><small>Local registered worker · governed fixture or exact bounded public research · durable local state · no credentials, private contacts, creative, likeness, outreach, external writes, deployment, or downstream invocation.</small></footer></body></html>"""


class WebApplication:
    def __init__(
        self,
        mission_control: MissionControl | None = None,
        csrf_token: str | None = None,
        agent_service: RegisteredAgentService | None = None,
        state_path: Path | None = None,
        shadow_service: ShadowLoopService | None = None,
        enrichment_service: EnrichmentService | None = None,
        dossier_service: DossierService | None = None,
    ):
        self.control = mission_control or MissionControl()
        self.csrf_token = csrf_token or secrets.token_urlsafe(32)
        self.agent = agent_service or RegisteredAgentService(
            self.control.repository,
            SqliteStore(state_path or default_state_path()),
            clock=getattr(self.control.repository, "clock", utc_now),
        )
        self.shadow = shadow_service or ShadowLoopService(
            self.agent.store,
            self.agent,
            clock=getattr(self.control.repository, "clock", utc_now),
        )
        self.enrichment = enrichment_service or EnrichmentService(
            self.agent,
            fixture_path=Path(__file__).resolve().parents[3]
            / "fixtures" / "prospecting" / "phase6" / "brief-fixtures.json",
            clock=getattr(self.control.repository, "clock", utc_now),
        )
        self.dossier = dossier_service or DossierService(
            self.enrichment,
            fixture_path=Path(__file__).resolve().parents[3]
            / "fixtures" / "prospecting" / "phase6" / "dossier-fixtures.json",
            history_path=Path(__file__).resolve().parents[3]
            / "fixtures" / "prospecting" / "phase6" / "dossier-history.json",
            real_manifest_path=Path(__file__).resolve().parents[3]
            / "shared" / "prospecting-core" / "manifests" / "phase6-live-proof-source-plans.json",
            clock=getattr(self.control.repository, "clock", utc_now),
        )

    @staticmethod
    def context(query: dict[str, list[str]]) -> tuple[str, str]:
        actor = query.get("actor", ["noah"])[0]
        business_unit = query.get("business_unit", ["unreal-media-group"])[0]
        if actor not in ACTOR_SCOPE:
            raise MissionControlError(403, "Unknown local review actor.")
        if business_unit not in BUSINESS_UNITS:
            raise MissionControlError(400, "Unknown business unit.")
        if business_unit not in ACTOR_SCOPE[actor]:
            raise MissionControlError(403, "Business-unit access denied.")
        return actor, business_unit

    def hidden(self, actor: str, business_unit: str) -> str:
        return f'<input type="hidden" name="csrf_token" value="{e(self.csrf_token)}"><input type="hidden" name="actor" value="{e(actor)}"><input type="hidden" name="business_unit" value="{e(business_unit)}">'

    def get(self, path: str, query: dict[str, list[str]]) -> tuple[int, str]:
        actor, business_unit = self.context(query)
        parts = [part for part in path.split("/") if part]
        if path in {"/", "/campaigns"}:
            return 200, self.campaigns(actor, business_unit)
        if path == "/campaigns/new":
            return 200, self.campaign_form(actor, business_unit)
        if len(parts) == 3 and parts[0] == "campaigns" and IDENTIFIER.fullmatch(parts[1]) and parts[2].isdigit():
            return 200, self.campaign_detail(actor, business_unit, parts[1], int(parts[2]))
        if path == "/runs":
            return 200, self.runs(actor, business_unit)
        if len(parts) == 2 and parts[0] == "runs" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.run_detail(actor, business_unit, parts[1])
        if path == "/prospects":
            queue = query.get("queue", ["new"])[0]
            if queue not in {"new", "duplicate_reengagement", "rejections"}:
                raise MissionControlError(400, "Unknown prospect queue.")
            return 200, self.prospects(actor, business_unit, queue)
        if len(parts) == 2 and parts[0] == "prospects" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.prospect_detail(actor, business_unit, parts[1])
        if path == "/registry":
            return 200, self.registry(actor, business_unit)
        if len(parts) == 2 and parts[0] == "registry" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.registry_detail(actor, business_unit, parts[1])
        if path == "/worker-runs":
            return 200, self.worker_runs(actor, business_unit)
        if len(parts) == 2 and parts[0] == "worker-runs" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.worker_run_detail(actor, business_unit, parts[1])
        if path == "/review-tasks":
            return 200, self.review_tasks(actor, business_unit)
        if len(parts) == 2 and parts[0] == "review-tasks" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.review_task_detail(actor, business_unit, parts[1])
        if path == "/phase6-approvals":
            return 200, self.phase6_approvals(actor, business_unit)
        if len(parts) == 2 and parts[0] == "phase6-approvals" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.phase6_approval_detail(actor, business_unit, parts[1])
        if path == "/phase6-enrichments":
            return 200, self.phase6_enrichments(actor, business_unit)
        if len(parts) == 2 and parts[0] == "phase6-enrichments" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.phase6_enrichment_detail(actor, business_unit, parts[1])
        if len(parts) == 2 and parts[0] == "phase6-briefs" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.phase6_brief_detail(actor, business_unit, parts[1])
        if path == "/phase6-dossiers":
            return 200, self.phase6_dossiers(actor, business_unit)
        if len(parts) == 2 and parts[0] == "phase6-searches" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.phase6_search_detail(actor, business_unit, parts[1])
        if len(parts) == 2 and parts[0] == "phase6-dossier-candidates" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.phase6_dossier_candidate_detail(actor, business_unit, parts[1])
        if path == "/phase6-packages":
            return 200, self.phase6_packages(actor, business_unit)
        if path == "/shadow-schedules":
            return 200, self.shadow_schedules(actor, business_unit)
        if path == "/shadow-schedules/new":
            return 200, self.shadow_schedule_form(actor, business_unit)
        if len(parts) == 2 and parts[0] == "shadow-schedules" and IDENTIFIER.fullmatch(parts[1]):
            return 200, self.shadow_schedule_detail(actor, business_unit, parts[1])
        raise MissionControlError(404, "Page not found.")

    def post(self, path: str, form: dict[str, str]) -> tuple[int, str]:
        if not hmac.compare_digest(form.get("csrf_token", ""), self.csrf_token):
            raise MissionControlError(403, "CSRF validation failed.")
        actor = form.get("actor", "")
        business_unit = form.get("business_unit", "")
        self.context({"actor": [actor], "business_unit": [business_unit]})
        parts = [part for part in path.split("/") if part]
        if path == "/campaigns":
            try:
                config = self.control.campaign_config(form)
            except MissionControlError as exc:
                return exc.status, self.campaign_form(
                    actor, business_unit, config=form, family_id=form.get("family_id", ""), error=exc.message
                )
            item = self.control.repository.add_campaign(actor, config, form.get("family_id") or None)
            return 201, self.campaign_detail(actor, business_unit, item["family_id"], item["version"], "Immutable campaign version created.")
        if len(parts) == 4 and parts[0] == "campaigns" and IDENTIFIER.fullmatch(parts[1]) and parts[2].isdigit() and parts[3] == "run":
            campaign = self.control.repository.get_campaign(actor, parts[1], int(parts[2]))
            if campaign["business_unit"] != business_unit:
                raise MissionControlError(403, "Business-unit access denied.")
            run, created = self.agent.create_manual_run(actor, parts[1], int(parts[2]), form.get("idempotency_key", ""))
            if run["state"] == "queued":
                run = self.agent.execute_run(actor, run["run_id"])
            message = (
                "Registered-agent manual run executed."
                if created
                else "Idempotent replay: the existing logical run was returned without duplicate execution."
            )
            return 200, self.worker_run_detail(actor, business_unit, run["run_id"], message)
        if len(parts) == 3 and parts[0] == "worker-runs" and IDENTIFIER.fullmatch(parts[1]) and parts[2] in {"cancel", "retry"}:
            run = self.agent.get_run(actor, parts[1])
            if run["business_unit"] != business_unit:
                raise MissionControlError(403, "Business-unit access denied.")
            if parts[2] == "cancel":
                self.agent.cancel_run(actor, parts[1])
                message = "Cancellation recorded for the worker run."
            else:
                self.agent.retry_run(actor, parts[1])
                message = "Manual retry attempt executed under the same logical run."
            return 200, self.worker_run_detail(actor, business_unit, parts[1], message)
        if len(parts) == 3 and parts[0] == "prospects" and IDENTIFIER.fullmatch(parts[1]) and parts[2] == "actions":
            self.control.repository.act(actor, business_unit, parts[1], form.get("action", ""), form)
            return 200, self.prospect_detail(actor, business_unit, parts[1], "Append-only governed event recorded.")
        if path == "/phase6-approvals":
            expected = form.get("expected_leaf_id", "") or None
            approval = self.enrichment.record_approval(
                actor, business_unit, form.get("task_id", ""), form.get("result_id", ""),
                decision=form.get("decision", ""), reason=form.get("reason", ""),
                expected_leaf_id=expected,
            )
            return 201, self.phase6_approval_detail(
                actor, business_unit, approval["approval_event_id"],
                f"durable approval event recorded: {approval['reason']}",
            )
        if path == "/phase6-enrichments":
            run, created = self.enrichment.start_enrichment(
                actor, business_unit, form.get("approval_event_id", ""),
                form.get("idempotency_key", ""),
            )
            status = (
                "Synthetic fixture brief completed; this is not official-site research."
                if created else
                "Idempotent replay returned the existing synthetic fixture enrichment."
            )
            return 200, self.phase6_enrichment_detail(
                actor, business_unit, run["enrichment_run_id"], status,
            )
        if len(parts) == 3 and parts[0] == "phase6-briefs" and IDENTIFIER.fullmatch(parts[1]) and parts[2] == "review":
            self.enrichment.review_brief(
                actor, business_unit, parts[1], decision=form.get("decision", ""),
                reason=form.get("reason", ""), expected_leaf_id=form.get("expected_leaf_id", "") or None,
            )
            return 200, self.phase6_brief_detail(
                actor, business_unit, parts[1],
                "Research-quality review recorded. It grants no generation or execution authority.",
            )
        if path == "/phase6-searches":
            parse_values = lambda key: [item.strip() for item in form.get(key, "").split(",") if item.strip()]
            search, created = self.dossier.create_search(
                actor,
                business_unit,
                include_any=parse_values("include_any") or None,
                exclude=parse_values("exclude") or None,
                idempotency_key=form.get("idempotency_key", ""),
            )
            status = (
                "History-first opportunity filtering completed and was recorded durably."
                if created else
                "Idempotent replay returned the existing durable search."
            )
            return 200, self.phase6_search_detail(
                actor, business_unit, search["search_id"], status
            )
        if path == "/phase6-real-searches":
            search, created = self.dossier.create_real_search(
                actor,
                business_unit,
                idempotency_key=form.get("idempotency_key", ""),
            )
            status = (
                "The exact real-proof target set was classified against durable history. No public read occurred."
                if created else
                "Idempotent replay returned the existing exact real-proof search without a public read."
            )
            return 200, self.phase6_search_detail(
                actor, business_unit, search["search_id"], status
            )
        if path == "/phase6-real-goal-approvals":
            approval = self.dossier.record_real_goal_approval(
                actor,
                business_unit,
                form.get("search_id", ""),
                form.get("result_id", ""),
                decision=form.get("decision", ""),
                reason=form.get("reason", ""),
                expected_leaf_id=form.get("expected_leaf_id", "") or None,
            )
            return 201, self.phase6_search_detail(
                actor,
                business_unit,
                approval["search_id"],
                f"The user's exact goal-authority event was recorded as {approval['decision']}.",
            )
        if path == "/phase6-dossier-approvals":
            approval = self.dossier.record_approval(
                actor,
                business_unit,
                form.get("search_id", ""),
                form.get("result_id", ""),
                decision=form.get("decision", ""),
                reason=form.get("reason", ""),
                expected_leaf_id=form.get("expected_leaf_id", "") or None,
            )
            return 201, self.phase6_search_detail(
                actor,
                business_unit,
                approval["search_id"],
                f"Exact dossier authority event recorded: {approval['decision']}.",
            )
        if path == "/phase6-dossier-runs":
            candidate, created = self.dossier.start_dossier(
                actor,
                business_unit,
                form.get("approval_event_id", ""),
                form.get("idempotency_key", ""),
            )
            status = (
                "Dossier candidate created for independent research-quality review."
                if created else
                "Idempotent replay returned the existing dossier candidate."
            )
            return 200, self.phase6_dossier_candidate_detail(
                actor, business_unit, candidate["candidate_version_id"], status
            )
        if len(parts) == 3 and parts[0] == "phase6-dossier-runs" and IDENTIFIER.fullmatch(parts[1]) and parts[2] == "cancel":
            run = self.dossier.cancel_dossier(actor, business_unit, parts[1])
            return 200, self.phase6_dossiers(
                actor, business_unit, f"Dossier run {run['dossier_run_id']} cancelled before completion."
            )
        if len(parts) == 3 and parts[0] == "phase6-dossier-candidates" and IDENTIFIER.fullmatch(parts[1]) and parts[2] == "review":
            outcome, created = self.dossier.review_candidate(
                actor,
                business_unit,
                parts[1],
                decision=form.get("decision", ""),
                reason=form.get("reason", ""),
                idempotency_key=form.get("idempotency_key", ""),
            )
            status = (
                "Terminal review recorded atomically. An accepted version released one inert local package."
                if created and outcome["review"]["decision"] == "accepted" else
                "Terminal research-quality review recorded; no downstream action authority was granted."
                if created else
                "Idempotent replay returned the existing terminal review."
            )
            return 200, self.phase6_dossier_candidate_detail(
                actor, business_unit, parts[1], status
            )
        if path == "/shadow-schedules":
            refs = []
            for raw in form.get("campaign_refs", "").split(","):
                raw = raw.strip()
                if not raw:
                    continue
                family_id, separator, version = raw.partition(":")
                if not separator or not IDENTIFIER.fullmatch(family_id) or not version.isdigit():
                    raise MissionControlError(400, "Campaign versions must use family_id:version format.")
                refs.append((family_id, int(version)))
            schedule = self.shadow.create_schedule(
                actor=actor,
                business_unit=business_unit,
                name=form.get("name", ""),
                campaign_refs=refs,
                weekday=int(form.get("weekday", "")),
                utc_hour=int(form.get("utc_hour", "")),
                utc_minute=int(form.get("utc_minute", "")),
                prospect_cap=int(form.get("prospect_cap", "")),
                open_discovery_every=int(form.get("open_discovery_every", "0")),
            )
            return 201, self.shadow_schedule_detail(
                actor, business_unit, schedule["schedule_id"],
                "Synthetic local shadow schedule created disabled by default.",
            )
        if len(parts) == 3 and parts[0] == "shadow-schedules" and IDENTIFIER.fullmatch(parts[1]) and parts[2] in {"enable", "pause", "resume", "disable"}:
            schedule = self.shadow.get_schedule(actor, parts[1])
            if schedule["business_unit"] != business_unit:
                raise MissionControlError(403, "Business-unit access denied.")
            try:
                row_version = int(form.get("row_version", ""))
            except ValueError as exc:
                raise MissionControlError(400, "A valid schedule row version is required.") from exc
            transition = getattr(self.shadow, parts[2])
            transition(actor, parts[1], row_version)
            return 200, self.shadow_schedule_detail(
                actor, business_unit, parts[1], f"Shadow schedule {parts[2]} action recorded atomically.",
            )
        raise MissionControlError(404, "Action not found.")

    def campaigns(self, actor: str, business_unit: str) -> str:
        items = [item for item in self.control.repository.campaigns(actor) if item["business_unit"] == business_unit]
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(f'<tr><td>{e(item["configuration"]["campaign_name"])}</td><td>{item["version"]}</td><td>{e(item["configuration"]["discovery_scope"])}</td><td><a href="/campaigns/{e(item["family_id"])}/{item["version"]}?{query}">Open</a></td></tr>' for item in items)
        body = f'<p><a href="/campaigns/new?{query}">Create a campaign</a></p>'
        body += '<p class="notice">No campaigns exist for this business unit. Create one to start a manual fixture run.</p>' if not rows else f'<table><thead><tr><th>Campaign</th><th>Version</th><th>Scope</th><th>View</th></tr></thead><tbody>{rows}</tbody></table>'
        return page("Campaigns", body, actor=actor, business_unit=business_unit)

    def campaign_form(self, actor: str, business_unit: str, *, config: dict[str, Any] | None = None, family_id: str = "", error: str = "") -> str:
        config = config or {}
        selected = lambda value, current: " selected" if value == current else ""
        def list_text(key: str) -> str:
            value = config.get(key, [])
            return value if isinstance(value, str) else ", ".join(value)

        geography_value = config.get("geography", "")
        if isinstance(geography_value, dict):
            geography_value = ", ".join(geography_value.get("countries", []))
        lowered_error = error.casefold()
        field_aliases = {
            "campaign_name": ("campaign_name", "campaign name"),
            "discovery_scope": ("discovery_scope", "discovery scope"),
            "verticals": ("vertical", "filtered discovery"),
            "target_prospect_count": ("target_prospect_count", "target prospect"),
            "minimum_qualification_score": ("minimum_qualification_score", "minimum qualification"),
            "cooldown_days": ("cooldown_days", "cooldown"),
            "maximum_evidence_age_days": ("maximum_evidence_age_days", "evidence age"),
            "rights_territory": ("rights_territory", "rights territory"),
        }
        error_field = next((key for key, aliases in field_aliases.items() if error and any(alias in lowered_error for alias in aliases)), "campaign_name" if error else "")
        def invalid(key: str) -> str:
            return f' aria-invalid="true" aria-describedby="{key}_error"' if error_field == key else ""

        def field_error(key: str) -> str:
            return f'<p id="{key}_error" class="error">{e(error)}</p>' if error_field == key else ""

        described_by = "campaign-help campaign-errors" if error else "campaign-help"
        error_summary = f'<p id="campaign-errors" class="error" role="alert">Campaign validation failed. {e(error)}</p>' if error else ""
        body = f"""<form method="post" action="/campaigns" aria-describedby="{described_by}">
{self.hidden(actor, business_unit)}<input type="hidden" name="family_id" value="{e(family_id)}">
<p id="campaign-help">Every submission creates an immutable version and is validated by the frozen Phase 1 validator.</p>{error_summary}
<label for="campaign_name">Campaign name</label><input id="campaign_name" name="campaign_name" maxlength="120" required value="{e(config.get('campaign_name',''))}"{invalid('campaign_name')}>{field_error('campaign_name')}
<label for="business_unit_display">Business unit</label><input id="business_unit_display" value="{e(business_unit)}" disabled>
<label for="discovery_scope">Discovery scope</label><select id="discovery_scope" name="discovery_scope"{invalid('discovery_scope')}><option value="open"{selected('open',config.get('discovery_scope','open'))}>Open discovery</option><option value="filtered"{selected('filtered',config.get('discovery_scope'))}>Filtered / vertical</option></select>{field_error('discovery_scope')}
<label for="verticals">Verticals (comma separated; required for filtered)</label><input id="verticals" name="verticals" maxlength="500" value="{e(list_text('verticals'))}"{invalid('verticals')}>{field_error('verticals')}
<label for="target_prospect_count">Target prospect count</label><input id="target_prospect_count" name="target_prospect_count" type="number" min="1" max="100" required value="{e(config.get('target_prospect_count',20))}"{invalid('target_prospect_count')}>{field_error('target_prospect_count')}
<label for="minimum_qualification_score">Minimum qualification score</label><input id="minimum_qualification_score" name="minimum_qualification_score" type="number" min="0" max="100" step="0.1" required value="{e(config.get('minimum_qualification_score',72 if business_unit=='unreal-media-group' else 78))}"{invalid('minimum_qualification_score')}>{field_error('minimum_qualification_score')}
<label><input name="reengagement_enabled" type="checkbox"{' checked' if config.get('reengagement_enabled') in {True, 'on'} else ''}> Include governed re-engagement review</label>
<label for="cooldown_days">Cooldown days</label><input id="cooldown_days" name="cooldown_days" type="number" min="0" max="3650" required value="{e(config.get('cooldown_days',120 if business_unit=='unreal-media-group' else 180))}"{invalid('cooldown_days')}>{field_error('cooldown_days')}
<label for="maximum_evidence_age_days">Maximum evidence age (days)</label><input id="maximum_evidence_age_days" name="maximum_evidence_age_days" type="number" min="1" max="3650" required value="{e(config.get('maximum_evidence_age_days',180))}"{invalid('maximum_evidence_age_days')}>{field_error('maximum_evidence_age_days')}
<label for="geography">Countries (comma separated)</label><input id="geography" name="geography" maxlength="500" value="{e(geography_value)}">
<label for="rights_territory">Talent rights territory (required for Talent)</label><input id="rights_territory" name="rights_territory" maxlength="500" value="{e(list_text('rights_territory'))}"{invalid('rights_territory')}>{field_error('rights_territory')}
<label for="talent_categories">Talent categories / archetypes (never named talent)</label><input id="talent_categories" name="talent_categories" maxlength="500" value="{e(list_text('talent_categories'))}">
<button type="submit">Create immutable campaign version</button></form>"""
        return page("New campaign" if not family_id else "New campaign version", body, actor=actor, business_unit=business_unit)

    def campaign_detail(self, actor: str, business_unit: str, family_id: str, version: int, status: str = "") -> str:
        item = self.control.repository.get_campaign(actor, family_id, version)
        if item["business_unit"] != business_unit:
            raise MissionControlError(403, "Business-unit access denied.")
        config = item["configuration"]
        key = f"{family_id}-v{version}-{item['configuration_hash'][:12]}"
        body = f"""<dl><dt>Family</dt><dd>{e(family_id)}</dd><dt>Version</dt><dd>{version}</dd><dt>Configuration hash</dt><dd><code>{e(item['configuration_hash'])}</code></dd><dt>Scope</dt><dd>{e(config['discovery_scope'])}</dd><dt>Verticals</dt><dd>{e(', '.join(config['verticals']) or 'Open discovery')}</dd></dl>
<details><summary>Full validated Phase 1 configuration</summary><pre>{e(json.dumps(config,indent=2,sort_keys=True))}</pre></details>
<form method="post" action="/campaigns/{e(family_id)}/{version}/run">{self.hidden(actor,business_unit)}<label for="idempotency_key">Idempotency key</label><input id="idempotency_key" name="idempotency_key" maxlength="100" required value="{e(key)}"><button type="submit">Start fixture dry run</button></form>
<h2>Create a new immutable version</h2>{self.campaign_form(actor,business_unit,config=config,family_id=family_id).split('<main id="main"><h1>New campaign version</h1>',1)[-1].split('</main>',1)[0]}"""
        return page(config["campaign_name"], body, actor=actor, business_unit=business_unit, status=status)

    def runs(self, actor: str, business_unit: str) -> str:
        items = [item for item in self.control.repository.runs(actor) if item["business_unit"] == business_unit]
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(f'<tr><td><a href="/runs/{e(item["run_id"])}?{query}">{e(item["run_id"])}</a></td><td>{e(item["status"])}</td><td>{len(item["output_ids"])}</td><td>${item["estimated_cost_usd"]}</td><td>{e(item["stop_reason"])}</td></tr>' for item in items)
        body = '<p class="notice">No manual runs have been recorded.</p>' if not rows else f'<table><thead><tr><th>Run</th><th>Status</th><th>Outputs</th><th>Cost</th><th>Stop reason</th></tr></thead><tbody>{rows}</tbody></table>'
        return page("Run history", body, actor=actor, business_unit=business_unit)

    def run_detail(self, actor: str, business_unit: str, run_id: str, status: str = "") -> str:
        run = self.control.repository.get_run(actor, run_id)
        if run["business_unit"] != business_unit:
            raise MissionControlError(403, "Business-unit access denied.")
        fields = ["run_id","campaign_family_id","campaign_version","business_unit","initiating_actor","input_schema_version","configuration_hash","skill_name","skill_version","fixture_source_ids","output_ids","started_at","completed_at","status","estimated_cost_usd","errors","stop_reason","idempotency_key","fixture_adapter"]
        body = '<dl>' + ''.join(f'<dt>{e(key.replace("_"," ").title())}</dt><dd>{e(run[key])}</dd>' for key in fields) + '</dl>'
        query = urlencode({"actor": actor, "business_unit": business_unit})
        body += f'<p><a href="/prospects?{query}&amp;queue=new">Review run outputs in prospect queues</a></p>'
        return page(f"Run {run_id}", body, actor=actor, business_unit=business_unit, status=status)

    def prospects(self, actor: str, business_unit: str, queue: str) -> str:
        items = [item for item in self.control.repository.prospects(actor,business_unit) if item["queue"] == queue]
        query_base = {"actor": actor, "business_unit": business_unit}
        nav = ' · '.join(f'<a href="/prospects?{urlencode(query_base)}&amp;queue={name}">{label}</a>' for name,label in [("new","New"),("duplicate_reengagement","Duplicates & re-engagement"),("rejections","Rejections")])
        rows = "".join(f'<tr><td><a href="/prospects/{e(item["prospect_id"])}?{urlencode(query_base)}">{e(item["account_name"])}</a></td><td>{item["score"]}</td><td>{e(item["effective_duplicate_state"])}</td><td>{e(item["effective_rejection"] or "eligible review")}</td></tr>' for item in items)
        body = f'<p>{nav}</p>' + ('<p class="notice">This queue is empty.</p>' if not rows else f'<table><thead><tr><th>Account</th><th>Score</th><th>Identity state</th><th>Disposition</th></tr></thead><tbody>{rows}</tbody></table>')
        return page(f"Prospect queue: {queue.replace('_',' ')}", body, actor=actor, business_unit=business_unit)

    def prospect_detail(self, actor: str, business_unit: str, prospect_id: str, status: str = "") -> str:
        item = self.control.repository.get_prospect(actor,business_unit,prospect_id)
        evidence = "".join(f'<li><span class="tag">{e(entry["freshness"])}</span> {e(entry["source"])} · {e(entry["observed_at"])} · {e(entry["basis"])} · ' + (f'<a href="{e(entry["url"])}" rel="noreferrer">credential-free source</a>' if safe_evidence_url(entry["url"]) else '<span class="warning">unsafe URL omitted</span>') + '</li>' for entry in item["evidence"])
        history = "".join(f'<tr><td>{e(event["event_id"])}</td><td>{e(event["kind"])}</td><td>{e(event["actor"])}</td><td>{e(event["effective_at"])}</td><td>{e(event["supersedes_id"] or "")}</td><td>{e(json.dumps(event["value"],sort_keys=True))}</td></tr>' for event in item["review_history"])
        match_options = ''.join(f'<option value="{e(match)}">{e(match)}</option>' for match in item.get("matched_identity_ids",[]))
        hidden = self.hidden(actor,business_unit)
        supersedes = lambda kind: f'<input type="hidden" name="supersedes_id" value="{e(item["current_review"].get(state_domain(kind), {}).get("event_id", ""))}">'  # noqa: E731
        body = f"""<dl><dt>Global synthetic identity</dt><dd>{e(item['global_identity_id'])}</dd><dt>Account</dt><dd>{e(item['account_name'])} · {e(item['domain'])}</dd><dt>Business unit</dt><dd>{e(item['business_unit'])}</dd><dt>Relationship</dt><dd>{e(item['relationship'])}</dd><dt>Score</dt><dd>{item['score']} · {e(json.dumps(item['score_dimensions'],sort_keys=True))}</dd><dt>Queue</dt><dd>{e(item['queue'])}</dd><dt>Identity classification</dt><dd>{e(item['effective_duplicate_state'])} · source classification {e(item['duplicate_state'])} · matches {e(item.get('matched_identity_ids',[]))}</dd><dt>Identity resolution</dt><dd>{e(item['effective_matched_identity_id'] or 'unresolved')}</dd><dt>Rejection</dt><dd>{e(item['effective_rejection'] or 'none')}</dd><dt>Uncertainty</dt><dd>{e(item['uncertainty'])}</dd><dt>Suppression</dt><dd>{e(item['suppressed'])}</dd><dt>Cooldown</dt><dd>{e(item['cooldown_until'] or 'none')}</dd><dt>Rights</dt><dd>{e(item['rights_state'])}</dd><dt>Brand safety</dt><dd>{e(item['brand_safety_state'])}</dd><dt>Talent archetype</dt><dd>{e(item.get('talent_archetype','not applicable'))}</dd></dl>
<h2>Evidence</h2><ul>{evidence or '<li class="warning">Missing evidence</li>'}</ul>
<h2>Governed review actions</h2><p>Repeating a governed action appends a correction that explicitly supersedes its current event. History is never deleted.</p><div class="grid">
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}{supersedes('resolve_identity')}<input type="hidden" name="action" value="resolve_identity"><label for="matched_identity_id">Exact synthetic identity match</label><select id="matched_identity_id" name="matched_identity_id">{match_options}</select><button type="submit"{' disabled' if not match_options else ''}>Record identity resolution</button></form>
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}{supersedes('suppress')}<input type="hidden" name="action" value="suppress"><label for="suppress_reason">Suppression reason</label><input id="suppress_reason" name="reason" maxlength="500" required><button type="submit">Append suppression</button></form>
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}{supersedes('approve_deeper_research')}<input type="hidden" name="action" value="approve_deeper_research"><p>Approval requires an eligible record and a reviewer different from the proposing actor.</p><button type="submit">Approve for deeper research</button></form>
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}{supersedes('reject')}<input type="hidden" name="action" value="reject"><label for="reject_reason">Rejection reason</label><input id="reject_reason" name="reason" maxlength="500" required><button type="submit">Append rejection</button></form>
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}{supersedes('assign')}<input type="hidden" name="action" value="assign"><label for="assignee">Assign fixture reviewer</label><select id="assignee" name="assignee">{''.join(f'<option value="{e(name)}">{e(name)}</option>' for name in ACTOR_SCOPE if business_unit in ACTOR_SCOPE[name])}</select><button type="submit">Append assignment</button></form>
<form class="card" method="post" action="/prospects/{e(prospect_id)}/actions">{hidden}<input type="hidden" name="action" value="note"><label for="note_text">Review note</label><textarea id="note_text" name="text" maxlength="2000" required></textarea><button type="submit">Append note</button></form></div>
<h2>Current governed projection</h2><p>Decision: <strong>{e(item['current_decision'])}</strong> · Effective suppression: <strong>{e(item['effective_suppressed'])}</strong> · Assignment: <strong>{e(item['effective_assignment'] or 'unassigned')}</strong></p>
<h2>Append-only review history</h2>{'<p class="notice">No human review events yet.</p>' if not history else f'<table><thead><tr><th>ID</th><th>Kind</th><th>Actor</th><th>Effective</th><th>Supersedes</th><th>Value</th></tr></thead><tbody>{history}</tbody></table>'}"""
        return page(item["account_name"],body,actor=actor,business_unit=business_unit,status=status)

    def registry(self, actor: str, business_unit: str) -> str:
        registration = self.agent.registration()
        query = urlencode({"actor": actor, "business_unit": business_unit})
        body = f"""<p class="notice">One local agent is registered for manual synthetic execution. Phase 5 may submit one bounded occurrence from an explicitly enabled local weekly schedule. The worker itself has no credentials, scheduler, generic recurring loop, external network, creative, likeness, approval, or outreach capability.</p>
<table><thead><tr><th>Agent</th><th>Version</th><th>Registration state</th><th>Trigger</th><th>Detail</th></tr></thead><tbody>
<tr><td>{e(registration['agent_id'])}</td><td>{e(registration['agent_contract_version'])}</td><td>{e(registration['registration_state'])}</td><td>{e(registration['trigger'])}</td><td><a href="/registry/{e(registration['agent_id'])}?{query}">Capabilities</a></td></tr>
</tbody></table>"""
        return page("Agent registry", body, actor=actor, business_unit=business_unit)

    def registry_detail(self, actor: str, business_unit: str, agent_id: str) -> str:
        if agent_id != AGENT_ID:
            raise MissionControlError(404, "Registered agent not found.")
        registration = self.agent.registration()
        bindings = "".join(
            f'<tr><td>{e(unit)}</td><td>{e(binding["skill_id"])}</td><td>{e(binding["skill_version"])}</td><td><code>{e(binding["skill_integrity_sha256"][:16])}…</code></td></tr>'
            for unit, binding in registration["skill_bindings"].items()
        )
        fields = [
            "agent_id", "agent_contract_version", "worker_runtime_version", "registration_state",
            "input_schema", "output_schema", "trigger", "scheduled_shadow_trigger", "max_concurrency", "timeout_seconds",
            "cost_cap_usd", "max_attempts", "cancellation", "durable_store_adapter",
            "credentials", "scheduler", "recurring_loop", "network", "creative", "likeness", "outreach",
        ]
        body = f"""<p class="notice">Manual, fixture-only, locally registered worker. Business-unit skill routing is server-owned; a client can never select a skill, executable, or version.</p>
<dl>{''.join(f'<dt>{e(key.replace("_", " ").title())}</dt><dd>{e(registration[key])}</dd>' for key in fields)}</dl>
<h2>Server-owned business-unit skill bindings</h2>
<table><thead><tr><th>Business unit</th><th>Skill</th><th>Exact version</th><th>Integrity manifest</th></tr></thead><tbody>{bindings}</tbody></table>
<p>Direct runs start only from a campaign detail page through the manual human form. A separately governed, explicitly enabled Phase 5 schedule may submit one weekly shadow occurrence through the same bounded service.</p>"""
        return page(f"Registered agent {agent_id}", body, actor=actor, business_unit=business_unit)

    def worker_runs(self, actor: str, business_unit: str) -> str:
        items = self.agent.list_runs(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td><a href="/worker-runs/{e(item["run_id"])}?{query}">{e(item["run_id"])}</a></td>'
            f'<td>{e(self._run_state_label(item))}</td><td>{item["attempt_count"]}/{item["max_attempts"]}</td>'
            f'<td>${item["cost_cap_usd"]}</td><td>{e(item["failure_class"] or "none")}</td></tr>'
            for item in items
        )
        body = (
            '<p class="notice">No registered-agent runs exist for this business unit. Start one from a campaign detail page.</p>'
            if not rows
            else f'<table><thead><tr><th>Logical run</th><th>State</th><th>Attempts</th><th>Cost cap</th><th>Failure class</th></tr></thead><tbody>{rows}</tbody></table>'
        )
        return page("Worker runs", body, actor=actor, business_unit=business_unit)

    @staticmethod
    def _durable_results_section(output: dict[str, Any] | None) -> str:
        """Read-only, business-unit-scoped review projection rendered from the
        durable validated result snapshot. It stays inspectable after a process
        restart, unlike the Phase 3 process-local queues."""
        if output is None:
            return ""

        def disposition(item: dict[str, Any]) -> Any:
            if item.get("effective_suppressed") is True:
                return "suppressed"
            decision = item.get("current_decision")
            if decision in {"rejected", "approved_for_deeper_research"}:
                return decision
            return item.get("effective_rejection") or decision or "eligible review"

        rows = "".join(
            f'<tr><td>{e(item.get("prospect_id", ""))}</td><td>{e(item.get("account_name", ""))}</td>'
            f'<td>{e(item.get("domain", ""))}</td><td>{e(item.get("score", ""))}</td>'
            f'<td>{e(item.get("queue", ""))}</td><td>{e(disposition(item))}</td>'
            f'<td>{e(item.get("uncertainty", ""))}</td></tr>'
            for item in output["result_snapshot"]
            if isinstance(item, dict)
        )
        table = (
            '<p class="notice">This run recorded a visible empty result set.</p>'
            if not rows
            else f'<table><thead><tr><th>Result</th><th>Account</th><th>Domain</th><th>Score</th><th>Queue</th><th>Disposition</th><th>Uncertainty</th></tr></thead><tbody>{rows}</tbody></table>'
        )
        return f"""<h2>Durable validated result snapshot</h2>
<p class="notice">Read-only durable review data; it remains inspectable after an application restart. Governed review actions (identity resolution, suppression, deeper-research approval, rejection, assignment, notes) operate on the process-local Phase 3 prospect queue populated by the server process that executed the run; after a restart those actions are unavailable for this run's results, and this snapshot remains the review record.</p>
{table}"""

    @staticmethod
    def _run_state_label(run: dict[str, Any]) -> str:
        if run["state"] == "succeeded" and run.get("review_task_id"):
            return "succeeded · review pending"
        if run["state"] in {"failed_retryable", "timed_out"} and run["attempt_count"] >= run["max_attempts"]:
            return f'{run["state"]} · retry budget exhausted'
        return run["state"]

    def worker_run_detail(self, actor: str, business_unit: str, run_id: str, status: str = "") -> str:
        run = self.agent.run_detail(actor, run_id)
        if run["business_unit"] != business_unit:
            raise MissionControlError(403, "Business-unit access denied.")
        query = urlencode({"actor": actor, "business_unit": business_unit})
        fields = [
            "run_id", "agent_id", "agent_version", "campaign_family_id", "campaign_version",
            "business_unit", "initiating_actor", "skill_id", "skill_version", "skill_integrity",
            "configuration_hash", "idempotency_key", "state", "attempt_count", "max_attempts",
            "retry_budget_remaining", "timeout_seconds", "cost_cap_usd", "actual_cost_usd",
            "cancel_requested", "created_at", "started_at", "completed_at", "failure_class",
            "retryable", "remediation",
        ]
        body = f'<p><span class="tag">{e(self._run_state_label(run))}</span></p><dl>' + "".join(
            f'<dt>{e(key.replace("_", " ").title())}</dt><dd>{e(run[key] if run[key] is not None else "none")}</dd>'
            for key in fields
        ) + "</dl>"
        attempts = "".join(
            f'<tr><td>{item["attempt_number"]}</td><td>{e(item["started_at"])}</td><td>{e(item["completed_at"] or "in progress")}</td>'
            f'<td>{e(item["terminal_state"] or "running")}</td><td>{e(item["outcome"] or "pending")}</td>'
            f'<td>{e(item["worker_version"])}</td><td>{e(item["estimated_cost_usd"] if item["estimated_cost_usd"] is not None else "none")}</td></tr>'
            for item in run["attempts"]
        )
        body += "<h2>Append-only attempt history</h2>" + (
            '<p class="notice">No attempt has started.</p>'
            if not attempts
            else f'<table><thead><tr><th>#</th><th>Started</th><th>Completed</th><th>Terminal state</th><th>Outcome</th><th>Worker version</th><th>Cost</th></tr></thead><tbody>{attempts}</tbody></table>'
        )
        output = run["output"]
        if output:
            body += f"""<h2>Output manifest</h2><dl>
<dt>Output Id</dt><dd>{e(output['output_id'])}</dd>
<dt>Output Schema</dt><dd>{e(output['output_schema'])}</dd>
<dt>Content Hash</dt><dd><code>{e(output['content_hash'])}</code></dd>
<dt>Byte Length</dt><dd>{output['byte_length']}</dd>
<dt>Fixture Source Ids</dt><dd>{e(', '.join(output['fixture_source_ids']))}</dd>
<dt>Result Ids</dt><dd>{e(', '.join(output['result_ids']) or 'none (visible empty result)')}</dd>
<dt>Estimated Cost Usd</dt><dd>{output['estimated_cost_usd']}</dd>
<dt>Errors</dt><dd>{e('; '.join(output['errors']) or 'none')}</dd>
<dt>Stop Reason</dt><dd>{e(output['stop_reason'])}</dd>
</dl>{self._durable_results_section(output)}<p><a href="/prospects?{query}&amp;queue=new">Open the process-local prospect queues (populated only by runs executed in this server process)</a></p>"""
        if run["review_task"]:
            body += f'<h2>Human review task</h2><p><a href="/review-tasks/{e(run["review_task"]["task_id"])}?{query}">{e(run["review_task"]["task_id"])}</a> · state: {e(run["review_task"]["state"])} · the worker cannot approve or complete it.</p>'
        hidden = self.hidden(actor, business_unit)
        if run["cancel_allowed"]:
            body += f'<form method="post" action="/worker-runs/{e(run_id)}/cancel">{hidden}<button type="submit">Cancel this run</button></form>'
        if run["retry_allowed"]:
            body += f'<form method="post" action="/worker-runs/{e(run_id)}/retry">{hidden}<button type="submit">Retry manually (attempt {run["attempt_count"] + 1} of {run["max_attempts"]})</button></form>'
        elif run["state"] in {"failed_retryable", "timed_out"}:
            if run["retry_block_reason"] == "scheduled_run":
                body += '<p class="warning">Scheduled shadow runs are not manually retryable; the linked occurrence owns final settlement.</p>'
            else:
                body += '<p class="warning">The retry budget is exhausted; no further attempts are allowed.</p>'
        audits = "".join(
            f'<tr><td>{e(item["audit_id"])}</td><td>{e(item["event_type"])}</td><td>{e(item["actor"])}</td><td>{e(item["safe_status"])}</td><td>{e(item["recorded_at"])}</td></tr>'
            for item in run["audit_events"]
        )
        body += "<h2>Bounded audit history</h2>" + (
            '<p class="notice">No audit events.</p>'
            if not audits
            else f'<table><thead><tr><th>ID</th><th>Event</th><th>Actor</th><th>Safe status</th><th>Recorded</th></tr></thead><tbody>{audits}</tbody></table>'
        )
        return page(f"Worker run {run_id}", body, actor=actor, business_unit=business_unit, status=status)

    def review_tasks(self, actor: str, business_unit: str) -> str:
        items = self.agent.review_tasks(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td><a href="/review-tasks/{e(item["task_id"])}?{query}">{e(item["task_id"])}</a></td>'
            f'<td>{e(item["run_id"])}</td><td>{e(item["state"])}</td><td>{e(item["created_at"])}</td></tr>'
            for item in items
        )
        body = (
            '<p class="notice">No human review tasks exist for this business unit.</p>'
            if not rows
            else f'<table><thead><tr><th>Task</th><th>Logical run</th><th>State</th><th>Created</th></tr></thead><tbody>{rows}</tbody></table>'
        )
        return page("Human review tasks", body, actor=actor, business_unit=business_unit)

    def review_task_detail(self, actor: str, business_unit: str, task_id: str) -> str:
        task = self.agent.get_review_task(actor, task_id)
        if task["business_unit"] != business_unit:
            raise MissionControlError(403, "Business-unit access denied.")
        output = self.agent.run_detail(actor, task["run_id"])["output"]
        query = urlencode({"actor": actor, "business_unit": business_unit})
        body = f"""<dl>
<dt>Task Id</dt><dd>{e(task['task_id'])}</dd>
<dt>Logical Run</dt><dd><a href="/worker-runs/{e(task['run_id'])}?{query}">{e(task['run_id'])}</a></dd>
<dt>Output Reference</dt><dd>{e(task['output_id'])}</dd>
<dt>Business Unit</dt><dd>{e(task['business_unit'])}</dd>
<dt>State</dt><dd>{e(task['state'])}</dd>
<dt>Allowed Human Reviewers</dt><dd>{e(', '.join(task['allowed_reviewers']))}</dd>
<dt>Created</dt><dd>{e(task['created_at'])}</dd>
</dl>
<p class="notice">The worker created this task and cannot approve, complete, or impersonate the human reviewer. No automatic approval or standing permission exists.</p>
{self._durable_results_section(output)}
<p><a href="/prospects?{query}&amp;queue=new">Open the process-local prospect queues (populated only by runs executed in this server process)</a></p>"""
        return page(f"Review task {task_id}", body, actor=actor, business_unit=business_unit)

    def phase6_approvals(self, actor: str, business_unit: str) -> str:
        items = self.enrichment.approvals(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td><a href="/phase6-approvals/{e(item["approval_event_id"])}?{query}">{e(item["approval_event_id"])}</a></td>'
            f'<td>{e(item["task_id"])}</td><td>{e(item["result_id"])}</td><td>{e(item["decision"])}</td>'
            f'<td>{e(item["effective_at"])}</td><td>{e(item["expires_at"])}</td>'
            f'<td>{e("actionable" if item["proposer_actor"] == actor and item["is_current_leaf"] and item["valid_now"] and not item["consumed"] else "current (bound proposer only)" if item["is_current_leaf"] and item["valid_now"] and not item["consumed"] else "history only (" + item["validity_status"] + ")")}</td></tr>'
            for item in items
        )
        table = (
            f'<table><thead><tr><th>Event</th><th>Review task</th><th>Result</th><th>Decision</th><th>Effective</th><th>Expires</th><th>Use</th></tr></thead><tbody>{rows}</tbody></table>'
            if rows else '<p class="notice">No durable approvals exist for this scope.</p>'
        )
        body = f"""<p class="notice"><strong>Phase 6A fixture policy simulation.</strong> Actor selection is not authentication. This append-only event is authority only for one local synthetic fixture enrichment; it grants no external research, generation, likeness, outreach, or deployment authority.</p>
{table}<h2>Record a durable result-bound decision</h2>
<form method="post" action="/phase6-approvals">{self.hidden(actor, business_unit)}
<label for="task_id">Pending review task ID</label><input id="task_id" name="task_id" required maxlength="100">
<label for="result_id">Exact result ID</label><input id="result_id" name="result_id" required maxlength="100">
<label for="decision">Decision</label><select id="decision" name="decision"><option value="approved">Approve fixture enrichment</option><option value="rejected">Reject</option><option value="revoked">Revoke</option><option value="invalidated">Invalidate</option></select>
<label for="expected_leaf_id">Current leaf event ID (for supersession)</label><input id="expected_leaf_id" name="expected_leaf_id" maxlength="100">
<label for="reason">Reason</label><textarea id="reason" name="reason" required maxlength="512"></textarea>
<button type="submit">Record append-only decision</button></form>"""
        return page("Phase 6A fixture approvals", body, actor=actor, business_unit=business_unit)

    def phase6_approval_detail(
        self, actor: str, business_unit: str, approval_event_id: str, status: str = ""
    ) -> str:
        item = self.enrichment.get_approval(actor, business_unit, approval_event_id)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        fields = (
            "approval_event_id", "decision", "scope", "task_id", "run_id", "output_id", "result_id",
            "global_identity_id", "proposer_actor", "reviewer_actor", "reason", "effective_at",
            "recorded_at", "expires_at", "supersedes_id", "result_hash", "protection_hash",
            "configuration_hash",
        )
        body = '<p class="notice">This exact durable binding grants only local synthetic fixture enrichment authority.</p><dl>'
        body += "".join(
            f'<dt>{e(name.replace("_", " ").title())}</dt><dd>{e(item[name] or "None")}</dd>'
            for name in fields
        )
        body += f'</dl><p><a href="/phase6-approvals?{query}">Back to fixture approvals</a></p>'
        return page(
            f"Fixture approval {approval_event_id}", body, actor=actor,
            business_unit=business_unit, status=status,
        )

    def phase6_enrichments(self, actor: str, business_unit: str) -> str:
        runs = self.enrichment.runs(actor, business_unit)
        approvals = self.enrichment.approvals(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td><a href="/phase6-enrichments/{e(item["enrichment_run_id"])}?{query}">{e(item["enrichment_run_id"])}</a></td>'
            f'<td>{e(item["result_id"])}</td><td>{e(item["state"])}</td><td>$0</td><td>{e(item["brief_id"] or "None")}</td></tr>'
            for item in runs
        )
        run_table = (
            f'<table><thead><tr><th>Enrichment</th><th>Result</th><th>State</th><th>Cost</th><th>Brief</th></tr></thead><tbody>{rows}</tbody></table>'
            if rows else '<p class="notice">No synthetic fixture enrichment has run.</p>'
        )
        forms = "".join(
            f'<form class="card" method="post" action="/phase6-enrichments">{self.hidden(actor, business_unit)}'
            f'<input type="hidden" name="approval_event_id" value="{e(item["approval_event_id"])}">'
            f'<p>Approved result: <strong>{e(item["result_id"])}</strong></p>'
            f'<label for="key-{e(item["approval_event_id"])}">Idempotency key</label>'
            f'<input id="key-{e(item["approval_event_id"])}" name="idempotency_key" required maxlength="100">'
            f'<button type="submit">Run one bounded fixture enrichment</button></form>'
            for item in approvals
            if item["proposer_actor"] == actor
            and item["is_current_leaf"] and item["valid_now"] and not item["consumed"]
        )
        body = f"""<p class="notice"><strong>Synthetic fixture only.</strong> This one-shot action reads repository-owned .example metadata, costs $0, performs no official-site research, and creates no prompt, asset, likeness, message, schedule, or external record.</p>
{run_table}<h2>Start from a durable approved leaf</h2><div class="grid">{forms or '<p class="notice">No approved event is available.</p>'}</div>"""
        return page("Phase 6A fixture enrichments", body, actor=actor, business_unit=business_unit)

    def phase6_enrichment_detail(
        self, actor: str, business_unit: str, run_id: str, status: str = ""
    ) -> str:
        item = self.enrichment.get_run(actor, business_unit, run_id)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        brief = (
            f'<a href="/phase6-briefs/{e(item["brief_id"])}?{query}">{e(item["brief_id"])}</a>'
            if item["brief_id"] else "Not created"
        )
        body = f"""<p class="notice">One bounded zero-cost fixture attempt. A successful brief grants no downstream execution authority.</p>
<dl><dt>Run</dt><dd>{e(item['enrichment_run_id'])}</dd><dt>State</dt><dd>{e(item['state'])}</dd>
<dt>Approval</dt><dd>{e(item['approval_event_id'])}</dd><dt>Result</dt><dd>{e(item['result_id'])}</dd>
<dt>Global identity</dt><dd>{e(item['global_identity_id'])}</dd><dt>Cost</dt><dd>$0 exactly</dd>
<dt>Brief</dt><dd>{brief}</dd><dt>Failure class</dt><dd>{e(item['failure_class'] or 'None')}</dd>
<dt>Remediation</dt><dd>{e(item['remediation'] or 'None')}</dd></dl>"""
        return page(
            f"Fixture enrichment {run_id}", body, actor=actor,
            business_unit=business_unit, status=status,
        )

    def phase6_brief_detail(
        self, actor: str, business_unit: str, brief_id: str, status: str = ""
    ) -> str:
        item = self.enrichment.get_brief(actor, business_unit, brief_id)
        snapshot = item["snapshot"]
        reviews = self.enrichment.brief_reviews(actor, business_unit, brief_id)
        research_rows = "".join(
            f'<tr><th scope="row">{e(name)}</th><td><pre>{e(json.dumps(value["value"], indent=2, sort_keys=True))}</pre></td>'
            f'<td>{e(value["basis"])}</td><td>{e(value["currentness"])}</td>'
            f'<td>{e(value["confidence_reason"])}</td><td>{e(value["uncertainty"])}</td>'
            f'<td><pre>{e(json.dumps(value["source_lineage"], indent=2, sort_keys=True))}</pre></td></tr>'
            for name, value in snapshot["research"].items()
        )
        source_rows = "".join(
            f'<tr><td>{e(source["title"])}</td><td><code>{e(source["source_url"])}</code></td>'
            f'<td>{e(source["summary"])}</td><td>{e(source["quote"])}</td><td>{e(source["source_date"] or "Unknown")}</td></tr>'
            for source in item["sources"]
        )
        review_rows = "".join(
            f'<tr><td>{e(review["review_event_id"])}</td><td>{e(review["decision"])}</td>'
            f'<td>{e(review["reviewer_actor"])}</td><td>{e(review["reason"])}</td>'
            f'<td>{e(review["recorded_at"])}</td><td>{e(review["authority_granted"])}</td></tr>'
            for review in reviews
        )
        current_review = reviews[-1]["review_event_id"] if reviews else ""
        unit_name = "umg" if business_unit == "unreal-media-group" else "talent"
        run = self.enrichment.get_run(actor, business_unit, item["enrichment_run_id"])
        if actor == run["initiating_actor"]:
            review_controls = (
                '<p class="notice"><strong>Read-only for the proposing actor.</strong> '
                'Separation of duty requires a different allowed reviewer; no review form is shown.</p>'
            )
        else:
            review_controls = f"""<form method="post" action="/phase6-briefs/{e(brief_id)}/review">{self.hidden(actor, business_unit)}
<label for="brief-decision">Decision</label><select id="brief-decision" name="decision"><option value="accepted">Accept research quality</option><option value="changes_requested">Request changes</option><option value="rejected">Reject</option></select>
<label for="brief-reason">Reason</label><textarea id="brief-reason" name="reason" required maxlength="512"></textarea>
<input type="hidden" name="expected_leaf_id" value="{e(current_review)}"><button type="submit">Record research-quality review</button></form>"""
        body = f"""<p class="notice"><strong>Research brief, not production authority.</strong> Synthetic fixture metadata only; no official-site research, page body, generation, likeness, outreach, or clearance claim.</p>
<dl><dt>Brief</dt><dd>{e(brief_id)}</dd><dt>Version</dt><dd>{item['version']}</dd><dt>Research state</dt><dd>{e(item['research_state'])}</dd>
<dt>Global identity</dt><dd>{e(item['global_identity_id'])}</dd><dt>Human review required</dt><dd>Yes</dd><dt>Integrity</dt><dd>{e(item['content_hash'])}</dd></dl>
<h2>Research fields</h2><table><thead><tr><th>Field</th><th>Value</th><th>Basis</th><th>Currentness</th><th>Confidence</th><th>Uncertainty</th><th>Source lineage</th></tr></thead><tbody>{research_rows}</tbody></table>
<h2>{e(unit_name.upper())} brief fields</h2><pre>{e(json.dumps(snapshot[unit_name], indent=2, sort_keys=True))}</pre>
<h2>Limitations and unresolved questions</h2><pre>{e(json.dumps({"limitations": snapshot["limitations"], "unresolved_questions": snapshot["unresolved_questions"], "business_unit_value_lineage": snapshot["business_unit_value_lineage"]}, indent=2, sort_keys=True))}</pre>
<h2>Bounded source metadata</h2><table><thead><tr><th>Fixture source</th><th>.example URL (display only)</th><th>Summary</th><th>Short quote</th><th>Source date</th></tr></thead><tbody>{source_rows}</tbody></table>
<h2>Append-only review history</h2>{f'<table><thead><tr><th>Event</th><th>Decision</th><th>Reviewer</th><th>Reason</th><th>Recorded</th><th>Authority</th></tr></thead><tbody>{review_rows}</tbody></table>' if review_rows else '<p class="notice">No research-quality review exists.</p>'}
<h2>Research-quality review only</h2>{review_controls}"""
        return page(f"Campaign brief {brief_id}", body, actor=actor, business_unit=business_unit, status=status)

    def phase6_dossiers(
        self, actor: str, business_unit: str, status: str = ""
    ) -> str:
        searches = self.dossier.searches(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td><a href="/phase6-searches/{e(item["search_id"])}?{query}">{e(item["search_id"])}</a></td>'
            f'<td>{e(item["request"].get("opportunity_filter") or "No opportunity filter")}</td>'
            f'<td>{sum(1 for result in item["results"] if result["selected"])}</td>'
            f'<td>{e(item["history_hash"])}</td><td>{e(item["created_at"])}</td></tr>'
            for item in searches
        )
        default_include = "product_photography, product_video" if business_unit == "unreal-media-group" else ""
        default_exclude = "ugc_ad" if business_unit == "unreal-media-group" else ""
        real_form = ""
        if business_unit == "unreal-media-group":
            if not self.dossier.real_execution_available():
                real_form = """<h2>Exact public-business proof</h2>
<p class="notice">No executable real-proof route is configured. Previously authorized attempts remain durable and cannot be retried. A new versioned route requires new exact target and source-plan authorization.</p>"""
            else:
                real_form = f"""<h2>Exact authorized public-business proof</h2>
<p class="notice">This route evaluates only its exact repository-authorized target set against durable history using the fixed product-photo/video include and UGC-ad exclude filter. It performs no public read until the exact result receives its separately recorded goal authority.</p>
<form method="post" action="/phase6-real-searches">{self.hidden(actor, business_unit)}
<label for="phase6-real-search-key">Real-proof search idempotency key</label><input id="phase6-real-search-key" name="idempotency_key" maxlength="100" required>
<button type="submit">Evaluate the exact authorized target set</button></form>"""
        body = f"""<p class="notice"><strong>Durable history-first Phase 6 search.</strong> History, identity, suppression, relationship, cooldown, and re-engagement classification runs before opportunity filtering, qualification, and target capping. Search intent is not evidence of demand.</p>
<form method="post" action="/phase6-searches" aria-describedby="phase6-filter-help">{self.hidden(actor, business_unit)}
<p id="phase6-filter-help">Controlled values: product_photography, product_video, ugc_ad. Leave both fields blank for the unfiltered route.</p>
<label for="phase6-include">Include any opportunity types</label><input id="phase6-include" name="include_any" maxlength="200" value="{e(default_include)}">
<label for="phase6-exclude">Exclude opportunity types</label><input id="phase6-exclude" name="exclude" maxlength="200" value="{e(default_exclude)}">
<label for="phase6-search-key">Idempotency key</label><input id="phase6-search-key" name="idempotency_key" maxlength="100" required>
<button type="submit">Run governed local fixture search</button></form>{real_form}
<h2>Durable searches</h2>{f'<table><thead><tr><th>Search</th><th>Filter</th><th>Selected</th><th>History fingerprint</th><th>Created</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<p class="notice">No Phase 6 dossier search has run for this business unit.</p>'}"""
        return page("Customer dossier search", body, actor=actor, business_unit=business_unit, status=status)

    def phase6_search_detail(
        self, actor: str, business_unit: str, search_id: str, status: str = ""
    ) -> str:
        search = self.dossier.get_search(actor, business_unit, search_id)
        approvals = self.dossier.approvals(actor, business_unit)
        children = {item["supersedes_id"] for item in approvals if item["supersedes_id"]}
        leaf_by_result = {
            item["result_record_id"]: item
            for item in approvals
            if item["approval_event_id"] not in children
        }
        rows = []
        for item in search["results"]:
            decision = item["decision"]
            leaf = leaf_by_result.get(item["result_record_id"])
            controls = ""
            is_real = item["candidate"].get("synthetic") is False
            real_ready = bool(
                is_real
                and item["selected"]
                and actor == search["initiating_actor"]
                and self.dossier.real_goal_authority_ready(
                    actor, business_unit, search_id, item["result_id"]
                )
            )
            if (
                real_ready
                and leaf
                and leaf["valid_now"]
                and not leaf["consumed"]
            ):
                controls = f"""<form method="post" action="/phase6-dossier-runs">{self.hidden(actor, business_unit)}
<input type="hidden" name="approval_event_id" value="{e(leaf['approval_event_id'])}">
<label for="dossier-key-{e(item['result_record_id'])}">Dossier idempotency key</label><input id="dossier-key-{e(item['result_record_id'])}" name="idempotency_key" maxlength="100" required>
<button type="submit">Run the exact bounded public read</button></form>"""
            elif real_ready:
                expected = leaf["approval_event_id"] if leaf else ""
                label = (
                    "Bind one infrastructure-recovery authority"
                    if self.dossier.real_goal_authority_requires_recovery(
                        actor, business_unit, search_id, item["result_id"]
                    )
                    else "Bind the existing exact user-goal authority"
                )
                controls = f"""<form method="post" action="/phase6-real-goal-approvals">{self.hidden(actor, business_unit)}
<input type="hidden" name="search_id" value="{e(search_id)}"><input type="hidden" name="result_id" value="{e(item['result_id'])}">
<input type="hidden" name="expected_leaf_id" value="{e(expected)}"><input type="hidden" name="decision" value="approved">
<input type="hidden" name="reason" value="The user's exact Phase 6 goal authorizes this bounded public read.">
<button type="submit">{label}</button></form>"""
            elif is_real and item["selected"] and leaf and leaf["consumed"] and actor == search["initiating_actor"]:
                controls = '<p class="notice">This exact target attempt has been consumed and cannot be reauthorized. Refresh to inspect its durable running or terminal state.</p>'
            elif is_real and item["selected"] and leaf and actor == search["initiating_actor"]:
                controls = '<p class="notice">The current exact goal-authority leaf is not executable or target order currently blocks it.</p>'
            elif is_real and item["selected"] and actor == search["initiating_actor"]:
                controls = '<p class="notice">Waiting for every earlier eligible real-proof target to become terminal.</p>'
            elif is_real and item["selected"]:
                controls = '<p class="notice">Read-only. Simulated local actors cannot supply or replace the user-goal authority.</p>'
            elif item["selected"] and actor != search["initiating_actor"]:
                expected = leaf["approval_event_id"] if leaf else ""
                controls = f"""<form method="post" action="/phase6-dossier-approvals">{self.hidden(actor, business_unit)}
<input type="hidden" name="search_id" value="{e(search_id)}"><input type="hidden" name="result_id" value="{e(item['result_id'])}">
<input type="hidden" name="expected_leaf_id" value="{e(expected)}">
<label for="decision-{e(item['result_record_id'])}">Authority decision</label><select id="decision-{e(item['result_record_id'])}" name="decision"><option value="approved">Approve exact dossier research</option><option value="rejected">Reject</option><option value="revoked">Revoke</option><option value="invalidated">Invalidate</option></select>
<label for="reason-{e(item['result_record_id'])}">Reason</label><textarea id="reason-{e(item['result_record_id'])}" name="reason" maxlength="2000" required></textarea><button type="submit">Record exact authority event</button></form>"""
            elif (
                item["selected"]
                and leaf
                and leaf["valid_now"]
                and not leaf["consumed"]
                and actor == search["initiating_actor"]
            ):
                controls = f"""<form method="post" action="/phase6-dossier-runs">{self.hidden(actor, business_unit)}
<input type="hidden" name="approval_event_id" value="{e(leaf['approval_event_id'])}">
<label for="dossier-key-{e(item['result_record_id'])}">Dossier idempotency key</label><input id="dossier-key-{e(item['result_record_id'])}" name="idempotency_key" maxlength="100" required>
<button type="submit">Create pending dossier candidate</button></form>"""
            elif item["selected"] and leaf and leaf["consumed"] and actor == search["initiating_actor"]:
                controls = '<p class="notice">This exact approval has already been consumed. A new research attempt requires a new approval and candidate version.</p>'
            elif item["selected"] and leaf and actor == search["initiating_actor"]:
                controls = '<p class="notice">The current authority leaf is not executable; it may be rejected, revoked, invalidated, or expired.</p>'
            elif item["selected"] and actor == search["initiating_actor"]:
                controls = '<p class="notice">A separate allowed reviewer must approve this exact selected result before research.</p>'
            rows.append(
                f'<tr><td>{e(item["candidate"]["company_name"])}</td><td>{e(item["result_id"])}</td>'
                f'<td>{e(item["account_id"])}</td><td>{e(decision["history_classification"]["status"])}</td>'
                f'<td>{e(", ".join(decision["matched_opportunities"]))}</td><td>{e(decision["filter_state"])}</td>'
                f'<td>{e(decision["qualification_state"])}</td><td>{e(item["selected"])}</td>'
                f'<td>{e(leaf["decision"] if leaf else "No authority")}</td><td>{controls}</td></tr>'
            )
        body = f"""<p class="notice">This immutable search is bound to a validated history snapshot before filtering. Changing the filter never makes an existing identity new.</p>
<dl><dt>Search</dt><dd>{e(search_id)}</dd><dt>Initiating actor</dt><dd>{e(search['initiating_actor'])}</dd><dt>History fingerprint</dt><dd>{e(search['history_hash'])}</dd><dt>Opportunity filter</dt><dd>{e(search['request'].get('opportunity_filter') or 'Not requested')}</dd></dl>
<table><thead><tr><th>Company</th><th>Result</th><th>Derived account</th><th>History</th><th>Opportunities</th><th>Filter</th><th>Qualification</th><th>Selected</th><th>Authority</th><th>Governed control</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"""
        return page(f"Phase 6 search {search_id}", body, actor=actor, business_unit=business_unit, status=status)

    def phase6_dossier_candidate_detail(
        self, actor: str, business_unit: str, candidate_id: str, status: str = ""
    ) -> str:
        item = self.dossier.get_candidate(actor, business_unit, candidate_id)
        dossier = item["dossier"]
        is_real = dossier.get("synthetic") is False
        coverage_rows = "".join(
            f'<tr><td>{e(category["category"])}</td><td>{e(category["coverage_state"])}</td>'
            f'<td>{e(category.get("gap_explanation", ""))}</td><td>{len(category["claims"])}</td></tr>'
            for category in dossier["categories"]
        )
        review = self.dossier.review_for_candidate(actor, business_unit, candidate_id)
        if review and review["package"]:
            package = review["package"]
            controls = (
                f'<p class="status" role="status">Accepted and released as immutable local package '
                f'<strong>{e(package["package_id"])}</strong>. Every downstream action authority is false.</p>'
            )
        elif review:
            controls = (
                f'<p class="notice"><strong>Terminal review recorded:</strong> '
                f'{e(review["review"]["decision"])} by {e(review["review"]["reviewer_actor"])}. '
                'This exact candidate cannot receive another review or release.</p>'
            )
        elif is_real:
            controls = (
                '<p class="notice"><strong>Pending genuine human review.</strong> '
                'This loopback UI has no authority to accept, request changes, or reject a real dossier. '
                'Use the exact hash-and-length approval packet and record only the user’s explicit decision.</p>'
            )
        elif actor == item["proposer_actor"]:
            controls = '<p class="notice"><strong>Read-only for the proposer.</strong> A different allowed reviewer must make the one terminal exact-version decision.</p>'
        else:
            controls = f"""<form method="post" action="/phase6-dossier-candidates/{e(candidate_id)}/review">{self.hidden(actor, business_unit)}
<label for="dossier-review-decision">Terminal decision</label><select id="dossier-review-decision" name="decision"><option value="accepted">Accept exact research version</option><option value="changes_requested">Request changes</option><option value="rejected">Reject</option></select>
<label for="dossier-review-reason">Reason</label><textarea id="dossier-review-reason" name="reason" maxlength="2000" required></textarea>
<label for="dossier-review-key">Review idempotency key</label><input id="dossier-review-key" name="idempotency_key" maxlength="100" required>
<button type="submit">Record one terminal review</button></form>"""
        real_packet = ""
        if is_real:
            conflicts = sum(
                1 for evidence in dossier["evidence_inventory"]
                if evidence["conflict_state"] == "conflicted"
            )
            evidence_review = dossier.get("automated_evidence_review")
            review_packet = (
                f"<dt>Automated evidence review</dt><dd>{e(evidence_review['state'])}</dd>"
                f"<dt>Verified claims</dt><dd>{evidence_review['claim_count']}</dd>"
                f"<dt>Explicit gaps</dt><dd>{evidence_review['gap_count']}</dd>"
                f"<dt>Reviewed payload hash</dt><dd>{e(evidence_review['reviewed_payload_hash'])}</dd>"
                if evidence_review is not None else
                "<dt>Automated evidence review</dt><dd>Legacy inventory-only candidate</dd>"
            )
            real_packet = f"""<h2>Exact human approval packet</h2>
<dl><dt>Source-plan hash</dt><dd>{e(dossier['source_plan_hash'])}</dd><dt>Claim-projection hash</dt><dd>{e(dossier.get('claim_projection_hash', 'Not available for legacy candidate'))}</dd>{review_packet}<dt>Conflicts</dt><dd>{conflicts}</dd><dt>Qualification</dt><dd>{e(dossier['qualification']['state'])} ({e(dossier['qualification']['basis'])})</dd></dl>
<h3>Downstream authority</h3><pre>{e(json.dumps(dossier['authority'], indent=2, sort_keys=True))}</pre>"""
        body = f"""<p class="notice"><strong>Exact immutable dossier candidate.</strong> It is local data only and cannot generate, contact, send, write externally, invoke an agent, or deploy.</p>
<dl><dt>Candidate version</dt><dd>{e(candidate_id)}</dd><dt>Dossier</dt><dd>{e(dossier['dossier_id'])} v{dossier['version']}</dd><dt>Result</dt><dd>{e(item['result_id'])}</dd><dt>Derived account</dt><dd>{e(item['account_id'])}</dd><dt>Content hash</dt><dd>{e(item['content_hash'])}</dd><dt>Byte length</dt><dd>{item['byte_length']}</dd><dt>State</dt><dd>{e(dossier['review_state'])}</dd></dl>{real_packet}
<h2>Eleven-category coverage</h2><table><thead><tr><th>Category</th><th>Coverage</th><th>Explicit gap</th><th>Claims</th></tr></thead><tbody>{coverage_rows}</tbody></table>
<h2>Evidence inventory</h2><pre>{e(json.dumps(dossier['evidence_inventory'], indent=2, sort_keys=True))}</pre>
<h2>Complete graph-ready candidate</h2><pre>{e(json.dumps(dossier, indent=2, sort_keys=True))}</pre>
<h2>Independent exact-version review</h2>{controls}"""
        return page(f"Dossier candidate {candidate_id}", body, actor=actor, business_unit=business_unit, status=status)

    def phase6_packages(self, actor: str, business_unit: str) -> str:
        packages = self.dossier.packages(actor, business_unit)
        rows = "".join(
            f'<tr><td>{e(item["package_id"])}</td><td>{e(item["package"]["dossier_id"])}</td>'
            f'<td>{e(item["content_hash"])}</td><td>{item["byte_length"]}</td>'
            f'<td><pre>{e(json.dumps(item["package"]["authority"], sort_keys=True))}</pre></td></tr>'
            for item in packages
        )
        body = f"""<p class="notice">Immutable, deterministic, graph-ready local data packages. They contain structured nodes, edges, claims, provenance, history, coverage, and no executable downstream authority.</p>
{f'<table><thead><tr><th>Package</th><th>Dossier</th><th>Stored hash</th><th>Bytes</th><th>Authority</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<p class="notice">No package exists. A package is created only inside an accepted terminal-review transaction.</p>'}"""
        return page("Local lead-intelligence packages", body, actor=actor, business_unit=business_unit)

    def shadow_schedules(self, actor: str, business_unit: str) -> str:
        items = self.shadow.schedules(actor, business_unit)
        query = urlencode({"actor": actor, "business_unit": business_unit})
        rows = "".join(
            f'<tr><td>{e(item["name"])}</td><td>{e(item["state"])}</td>'
            f'<td>{e(item["next_due_at"] or "Not enabled")}</td><td>{e(item["health"])}</td>'
            f'<td>{e("Not available" if item.get("duplicate_rate") is None else item["duplicate_rate"])}</td>'
            f'<td><a href="/shadow-schedules/{e(item["schedule_id"])}?{query}">Inspect</a></td></tr>'
            for item in items
        )
        body = f"""<p class="notice">Phase 5 schedules are disabled by default and remain local, synthetic,
fixture-only, weekly UTC, zero-cost, and shadow-only. Creating a schedule never enables it.</p>"""
        if actor == SCHEDULE_MANAGER:
            body += f'<p><a href="/shadow-schedules/new?{query}">Configure a disabled shadow schedule</a></p>'
        else:
            body += '<p class="notice">This is read-only schedule state. Only Noah may configure or control a shadow schedule.</p>'
        body += (
            '<p class="notice">No shadow schedules exist for this business unit.</p>'
            if not rows
            else '<table><thead><tr><th>Schedule</th><th>State</th><th>Next UTC due</th><th>Health</th><th>Duplicate rate</th><th>View</th></tr></thead>'
                 f'<tbody>{rows}</tbody></table>'
        )
        alerts = self.shadow.alerts(actor, business_unit)
        if alerts:
            alert_rows = "".join(
                f'<tr><td>{e(item["created_at"])}</td><td>{e(item["failure_class"])}</td><td>{e(item["safe_message"])}</td></tr>'
                for item in alerts
            )
            body += f'<h2>Local alerts</h2><table><thead><tr><th>Time</th><th>Class</th><th>Safe message</th></tr></thead><tbody>{alert_rows}</tbody></table>'
        else:
            body += '<h2>Local alerts</h2><p class="notice">No local shadow-loop alerts exist.</p>'
        return page("Shadow schedules", body, actor=actor, business_unit=business_unit)

    def shadow_schedule_form(self, actor: str, business_unit: str) -> str:
        self.shadow.schedules(actor, business_unit)
        if actor != SCHEDULE_MANAGER:
            return page(
                "Shadow schedule access",
                '<p class="notice">This is read-only schedule state. Only Noah may configure or control a shadow schedule.</p>',
                actor=actor,
                business_unit=business_unit,
            )
        campaigns = [
            item for item in self.control.repository.campaigns(actor)
            if item["business_unit"] == business_unit
        ]
        choices = ", ".join(f'{item["family_id"]}:{item["version"]}' for item in campaigns)
        body = f"""<p class="notice">Configuration is immutable and disabled by default. Use approved campaign
versions from this business unit. The cadence is one bounded weekly UTC boundary, not generic cron.</p>
<form method="post" action="/shadow-schedules">
{self.hidden(actor, business_unit)}
<label for="schedule_name">Schedule name</label><input id="schedule_name" name="name" maxlength="120" required>
<label for="campaign_refs">Campaign rotation (comma-separated family_id:version)</label>
<input id="campaign_refs" name="campaign_refs" maxlength="1200" required aria-describedby="campaign_choices">
<p id="campaign_choices">Available immutable versions: {e(choices or 'None. Create a campaign first.')}</p>
<label for="weekday">UTC weekday (Monday 0 through Sunday 6)</label><input id="weekday" name="weekday" type="number" min="0" max="6" required value="0">
<label for="utc_hour">UTC hour</label><input id="utc_hour" name="utc_hour" type="number" min="0" max="23" required value="9">
<label for="utc_minute">UTC minute</label><input id="utc_minute" name="utc_minute" type="number" min="0" max="59" required value="0">
<label for="prospect_cap">Prospect cap</label><input id="prospect_cap" name="prospect_cap" type="number" min="1" max="15" required value="5">
<label for="open_discovery_every">Open-discovery week interval (0 disables)</label><input id="open_discovery_every" name="open_discovery_every" type="number" min="0" max="52" required value="0">
<button type="submit">Create disabled shadow schedule</button></form>"""
        return page("Configure shadow schedule", body, actor=actor, business_unit=business_unit)

    def shadow_schedule_detail(
        self, actor: str, business_unit: str, schedule_id: str, status: str = ""
    ) -> str:
        item = self.shadow.schedule_detail(actor, schedule_id)
        if item["business_unit"] != business_unit:
            raise MissionControlError(403, "Business-unit access denied.")
        query = urlencode({"actor": actor, "business_unit": business_unit})
        if actor == SCHEDULE_MANAGER:
            allowed = {
                "disabled": ("enable",),
                "enabled": ("pause", "disable"),
                "paused": ("resume", "disable"),
            }[item["state"]]
            controls = "".join(
                f'<form method="post" action="/shadow-schedules/{e(schedule_id)}/{action}">{self.hidden(actor, business_unit)}'
                f'<input type="hidden" name="row_version" value="{item["row_version"]}"><button type="submit">{e(action.title())} schedule</button></form>'
                for action in allowed
            )
        else:
            controls = '<p class="notice">This is read-only schedule state. Only Noah may control a shadow schedule.</p>'
        campaign_rows = "".join(
            f'<tr><td>{row["position"]}</td><td>{e(row["campaign_family_id"])}</td><td>{row["campaign_version"]}</td><td>{e(row["discovery_scope"])}</td></tr>'
            for row in item["campaigns"]
        )
        occurrence_rows = "".join(
            f'<tr><td>{e(row["scheduled_for"])}</td><td>{e(row["state"])}</td><td>{e(row["worker_run_id"] or "None")}</td><td>{e(row["stop_reason"] or "None")}</td><td>{e("Not available" if row["duplicate_rate"] is None else row["duplicate_rate"])}</td></tr>'
            for row in item["occurrences"]
        )
        body = f"""<p class="notice">Synthetic local shadow schedule. It can discover fixture candidates only;
every successful output remains pending human review. It cannot approve, enrich, generate, contact, or send.</p>
<dl><dt>Schedule</dt><dd>{e(item['name'])}</dd><dt>Business unit</dt><dd>{e(item['business_unit'])}</dd>
<dt>State</dt><dd>{e(item['state'])}</dd><dt>UTC cadence</dt><dd>weekday {item['weekday']} at {item['utc_hour']:02d}:{item['utc_minute']:02d} UTC</dd>
<dt>Next due</dt><dd>{e(item['next_due_at'] or 'Not enabled')}</dd><dt>Health</dt><dd>{e(item['health'])}</dd>
<dt>Last stop reason</dt><dd>{e(item['last_stop_reason'] or 'None')}</dd><dt>Prospect cap</dt><dd>{item['prospect_cap']}</dd>
<dt>Cost cap</dt><dd>$0 exactly</dd><dt>Duplicate rate</dt><dd>{e('Not available' if item.get('duplicate_rate') is None else item['duplicate_rate'])}</dd></dl>
<h2>Controls</h2>{controls}
<p class="notice">Pause or disable prevents an unclaimed occurrence. An occurrence whose claim already committed finishes under its immutable snapshot and remains shadow-only.</p>
<h2>Immutable campaign rotation</h2><table><thead><tr><th>Position</th><th>Family</th><th>Version</th><th>Scope</th></tr></thead><tbody>{campaign_rows}</tbody></table>
<h2>Occurrences</h2>"""
        body += (
            f'<table><thead><tr><th>Scheduled UTC</th><th>State</th><th>Run</th><th>Stop reason</th><th>Duplicate rate</th></tr></thead><tbody>{occurrence_rows}</tbody></table>'
            if occurrence_rows else '<p class="notice">No occurrence has been claimed.</p>'
        )
        body += f'<p><a href="/shadow-schedules?{query}">Back to shadow schedules and local alerts</a></p>'
        return page(f"Shadow schedule {schedule_id}", body, actor=actor, business_unit=business_unit, status=status)


def make_handler(app: WebApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ProspectingMissionControl/3"
        sys_version = ""

        def _validate_envelope(self) -> None:
            if len(self.path) > 4_096:
                raise MissionControlError(400, "Request target exceeds the local safety limit.")
            host = self.headers.get("Host", "").split(":", 1)[0].casefold()
            if host not in {"127.0.0.1", "localhost"}:
                raise MissionControlError(403, "Only a loopback Host is accepted.")

        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _error(
            self,
            exc: MissionControlError,
            context: tuple[str, str] | None = None,
        ) -> None:
            actor, unit = context or ("noah", "unreal-media-group")
            if context is None:
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=MAX_FIELDS)
                    actor, unit = app.context(query)
                except Exception:
                    pass
            self._send(exc.status, page(HTTPStatus(exc.status).phrase, f'<p class="error" role="alert">{e(exc.message)}</p>', actor=actor, business_unit=unit))

        def do_GET(self) -> None:
            try:
                self._validate_envelope()
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=MAX_FIELDS)
                status, body = app.get(parsed.path, query)
                self._send(status, body)
            except MissionControlError as exc:
                self._error(exc)
            except (ValueError, UnicodeError):
                self._error(MissionControlError(400, "Malformed request."))
            except Exception:
                self._error(MissionControlError(500, "The local review request failed safely."))

        def do_POST(self) -> None:
            error_context: tuple[str, str] | None = None
            try:
                self._validate_envelope()
                if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    raise MissionControlError(400, "Mutating forms require application/x-www-form-urlencoded content.")
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise MissionControlError(400, "Content-Length is required.")
                length = int(raw_length)
                if length < 0 or length > MAX_BODY:
                    raise MissionControlError(400, "Request body exceeds the local safety limit.")
                body = self.rfile.read(length).decode("utf-8", errors="strict")
                parsed_form = parse_qs(body, keep_blank_values=True, max_num_fields=MAX_FIELDS, strict_parsing=True)
                if any(len(values) != 1 for values in parsed_form.values()):
                    raise MissionControlError(400, "Repeated form fields are not allowed.")
                form = {key: values[0] for key, values in parsed_form.items()}
                try:
                    error_context = app.context({
                        "actor": [form.get("actor", "")],
                        "business_unit": [form.get("business_unit", "")],
                    })
                except MissionControlError:
                    pass
                status, output = app.post(urlsplit(self.path).path, form)
                self._send(status, output)
            except (ValueError, UnicodeError):
                self._error(MissionControlError(400, "Malformed form submission."), error_context)
            except MissionControlError as exc:
                self._error(exc, error_context)
            except Exception:
                self._error(
                    MissionControlError(500, "The governed action failed without a partial result."),
                    error_context,
                )

        def do_PUT(self) -> None: self._error(MissionControlError(405, "HTTP method not allowed."))
        def do_DELETE(self) -> None: self._error(MissionControlError(405, "HTTP method not allowed."))
        def do_PATCH(self) -> None: self._error(MissionControlError(405, "HTTP method not allowed."))
        def do_HEAD(self) -> None: self._error(MissionControlError(405, "HTTP method not allowed."))
        def do_OPTIONS(self) -> None: self._error(MissionControlError(405, "HTTP method not allowed."))

        def log_message(self, format: str, *args: Any) -> None:
            # Deliberately omit paths, query values, form bodies, and fixture content.
            print(f"mission-control status={args[1] if len(args) > 1 else 'unknown'}")

    return Handler


class LoopbackServer(ThreadingHTTPServer):
    """Loopback-only server with one bounded thread per request.

    Threads exist so a human can inspect or cancel a running bounded attempt;
    daemon_threads stays False so server_close joins every request thread.
    The separately owned Phase 5 weekly scheduler is also non-daemon and is
    explicitly stopped and joined by run_server.
    """

    daemon_threads = False


def build_server(app: WebApplication | None = None, port: int = 8765) -> HTTPServer:
    return LoopbackServer(("127.0.0.1", port), make_handler(app or WebApplication()))


def run_server() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic Prospecting Mission Control on loopback only.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help="Operator-selected local SQLite state file for durable worker runs"
        " (default: the gitignored apps/prospecting-mission-control/local_state directory).",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Port must be from 1024 to 65535.")
    app = WebApplication(state_path=args.state_path)
    server = build_server(app, port=args.port)
    scheduler = ShadowScheduler(app.shadow)
    scheduler.start()
    print(f"Synthetic Prospecting Mission Control: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        scheduler.join(timeout=5)
        server.server_close()
        if scheduler.is_alive():
            raise RuntimeError("The local shadow scheduler did not stop cleanly.")


if __name__ == "__main__":
    run_server()
