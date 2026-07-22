"""Deterministic, socket-free tests for the bounded Phase 6 public reader."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "apps" / "prospecting-mission-control"
CORE = ROOT / "shared" / "prospecting-core" / "scripts"
for path in (APP_ROOT, CORE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mission_control.public_reader import (  # noqa: E402
    USER_AGENT,
    PublicReader,
    _ReadFailure,
    _default_resolver,
    _parse_robots,
)
from validate_phase6_real_contract import (  # noqa: E402
    load_real_source_manifest,
    validate_real_research_bundle,
)

MANIFEST = ROOT / "shared" / "prospecting-core" / "manifests" / "phase6-live-proof-source-plans.json"
FIXED = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
PUBLIC_IP = "93.184.216.34"


class Monotonic:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, hostname: str, port: int, timeout: float):
        self.calls.append((hostname, port, timeout))
        return [PUBLIC_IP]


class Transport:
    def __init__(self, overrides=None) -> None:
        self.overrides = overrides or {}
        self.calls: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.calls.append(request)
        override = self.overrides.get(request["url"])
        if callable(override):
            return override(request)
        if isinstance(override, list):
            value = override.pop(0)
            return value(request) if callable(value) else value
        if override is not None:
            return override
        if request["url"].endswith("/robots.txt"):
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain; charset=utf-8"
        else:
            body = (
                b"<html><body><main><p>Official public product evidence describes the company "
                b"and its current catalog. The official page also explains its current business "
                b"offering and product portfolio.</p></main></body></html>"
            )
            content_type = "text/html; charset=utf-8"
        return response(request, body=body, content_type=content_type)


def response(
    request: dict,
    *,
    status: int = 200,
    body: bytes = b"",
    content_type: str = "text/html",
    headers: dict[str, str] | None = None,
    peer_ip: str = PUBLIC_IP,
    tls_verified: bool = True,
    tls_hostname: str | None = None,
) -> dict:
    response_headers = {"Content-Type": content_type}
    response_headers.update(headers or {})
    return {
        "status": status,
        "headers": response_headers,
        "body": body,
        "peer_ip": peer_ip,
        "tls_verified": tls_verified,
        "tls_hostname": request["hostname"] if tls_hostname is None else tls_hostname,
    }


class Phase6PublicReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_real_source_manifest(MANIFEST)
        cls.plan = cls.manifest["plans"][0]

    def reader(self, *, resolver=None, transport=None, monotonic=None) -> PublicReader:
        return PublicReader(
            [copy.deepcopy(self.plan)],
            resolver=resolver or Resolver(),
            transport=transport or Transport(),
            monotonic=monotonic or Monotonic(),
            utc_now=lambda: FIXED,
        )

    def test_success_returns_exact_validated_bundle_without_raw_content(self) -> None:
        first = self.plan["sources"][0]["url"]

        def source(request: dict) -> dict:
            body = (
                b"<html><head><style>private style</style><script>doNotExecute()</script></head>"
                b"<body><main><p>Official launch details describe the company and its current product catalog. "
                b"The official page also explains its current business offering. "
                b"press@example.org +1 (212) 555-0100</p></main></body></html>"
            )
            return response(
                request,
                body=body,
                headers={"Last-Modified": "Tue, 21 Jul 2026 10:00:00 GMT"},
            )

        transport = Transport({first: source})
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])

        self.assertEqual(validate_real_research_bundle(bundle, self.plan), bundle)
        record = bundle["sources"][0]
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["source_date"], "2026-07-21")
        self.assertEqual(record["date_state"], "published_date")
        self.assertIn("[redacted]", record["summary"])
        self.assertNotIn("doNotExecute", record["summary"])
        serialized = json.dumps(bundle)
        self.assertNotIn("press@example.org", serialized)
        self.assertNotIn("555-0100", serialized)
        self.assertNotIn("<html>", serialized)
        self.assertNotIn("body", record)
        self.assertNotIn("extracted_text", record)
        self.assertEqual(
            [item["requested_url"] for item in bundle["sources"]],
            [item["url"] for item in self.plan["sources"]],
        )

    def test_redirect_is_followed_only_to_an_exact_planned_url(self) -> None:
        first, second = [source["url"] for source in self.plan["sources"][:2]]

        def redirect(request: dict) -> dict:
            return response(request, status=302, headers={"Location": second})

        transport = Transport({first: redirect})
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["final_url"], second)
        self.assertEqual(record["redirect_chain"], [second])

    def test_unlisted_redirect_is_rejected_before_dns_or_transport(self) -> None:
        first = self.plan["sources"][0]["url"]

        def redirect(request: dict) -> dict:
            return response(request, status=302, headers={"Location": "https://evil.invalid/collect"})

        transport = Transport({first: redirect})
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual((record["status"], record["safe_reason_code"]), ("failed", "redirect_rejected"))
        self.assertFalse(any(call["url"].startswith("https://evil.invalid") for call in transport.calls))
        self.assertEqual(record["summary"], "")
        self.assertIsNone(record["body_sha256"])

    def test_every_dns_answer_must_be_public(self) -> None:
        calls: dict[str, int] = {}

        def resolver(hostname: str, port: int, timeout: float):
            del port, timeout
            calls[hostname] = calls.get(hostname, 0) + 1
            return [PUBLIC_IP] if calls[hostname] == 1 else [PUBLIC_IP, "10.0.0.7"]

        bundle = self.reader(resolver=resolver).read_plan(self.plan["source_plan_id"])
        self.assertTrue(bundle["sources"])
        self.assertTrue(all(record["safe_reason_code"] == "dns_rejected" for record in bundle["sources"]))

    def test_multicast_dns_answer_is_not_treated_as_public(self) -> None:
        calls: dict[str, int] = {}

        def resolver(hostname: str, port: int, timeout: float):
            del port, timeout
            calls[hostname] = calls.get(hostname, 0) + 1
            return [PUBLIC_IP] if calls[hostname] == 1 else ["224.0.0.1"]

        bundle = self.reader(resolver=resolver).read_plan(self.plan["source_plan_id"])
        self.assertTrue(all(record["safe_reason_code"] == "dns_rejected" for record in bundle["sources"]))

    def test_ipv4_mapped_ipv6_answer_is_rejected(self) -> None:
        calls: dict[str, int] = {}

        def resolver(hostname: str, port: int, timeout: float):
            del port, timeout
            calls[hostname] = calls.get(hostname, 0) + 1
            return [PUBLIC_IP] if calls[hostname] == 1 else ["::ffff:5db8:d822"]

        bundle = self.reader(resolver=resolver).read_plan(self.plan["source_plan_id"])
        self.assertTrue(all(record["safe_reason_code"] == "dns_rejected" for record in bundle["sources"]))

    def test_peer_and_tls_hostname_are_bound_to_the_validated_request(self) -> None:
        first = self.plan["sources"][0]["url"]
        transport = Transport({first: lambda request: response(request, peer_ip="8.8.8.8")})
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        self.assertEqual(bundle["sources"][0]["safe_reason_code"], "tls_rejected")

        transport = Transport({first: lambda request: response(request, tls_hostname="other.example.org")})
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        self.assertEqual(bundle["sources"][0]["safe_reason_code"], "tls_rejected")

    def test_robots_denial_blocks_source_request(self) -> None:
        first = self.plan["sources"][0]["url"]
        robots = self.plan["robots_policy_urls"][0]
        transport = Transport({
            robots: lambda request: response(
                request,
                body=b"User-agent: *\nDisallow: /\n",
                content_type="text/plain",
            )
        })
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        self.assertEqual(bundle["sources"][0]["safe_reason_code"], "robots_denied")
        self.assertFalse(any(call["url"] == first for call in transport.calls))

    def test_robots_uses_longest_matching_path_rule(self) -> None:
        root = self.plan["sources"][1]["url"]
        about = self.plan["sources"][2]["url"]
        robots = self.plan["robots_policy_urls"][1]
        transport = Transport({
            robots: lambda request: response(
                request,
                body=b"User-agent: *\nAllow: /\nDisallow: /about/\n",
                content_type="text/plain",
            )
        })
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        records = {record["requested_url"]: record for record in bundle["sources"]}
        self.assertEqual(records[root]["status"], "success")
        self.assertEqual(records[about]["safe_reason_code"], "robots_denied")
        self.assertFalse(any(call["url"] == about for call in transport.calls))

    def test_robots_wildcard_and_end_anchor_rules_are_enforced(self) -> None:
        root = self.plan["sources"][1]["url"]
        about = self.plan["sources"][2]["url"]
        robots = self.plan["robots_policy_urls"][1]
        cases = {
            "wildcard": (
                b"User-agent: *\nDisallow: /*\n",
                {root: "robots_denied", about: "robots_denied"},
            ),
            "end_anchor_allow_tie": (
                b"User-agent: *\nDisallow: /$\nAllow: /$\nDisallow: /about/$\n",
                {root: "ok", about: "robots_denied"},
            ),
        }
        for name, (body, expected) in cases.items():
            with self.subTest(name=name):
                transport = Transport({
                    robots: lambda request, body=body: response(
                        request, body=body, content_type="text/plain"
                    )
                })
                bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
                records = {record["requested_url"]: record for record in bundle["sources"]}
                self.assertEqual(records[root]["safe_reason_code"], expected[root])
                self.assertEqual(records[about]["safe_reason_code"], expected[about])

    def test_robots_specific_and_duplicate_groups_are_combined(self) -> None:
        root = self.plan["sources"][1]["url"]
        about = self.plan["sources"][2]["url"]
        products = self.plan["sources"][3]["url"]
        robots = self.plan["robots_policy_urls"][1]
        product = USER_AGENT.split("/", 1)[0].encode()
        body = (
            b"User-agent: *\nDisallow: /\n"
            b"User-agent: " + product + b"\nAllow: /\nDisallow: /about/\n"
            b"User-agent: " + product + b"\nDisallow: /products/\n"
        )
        transport = Transport({
            robots: lambda request: response(request, body=body, content_type="text/plain")
        })
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        records = {record["requested_url"]: record for record in bundle["sources"]}
        self.assertEqual(records[root]["safe_reason_code"], "ok")
        self.assertEqual(records[about]["safe_reason_code"], "robots_denied")
        self.assertEqual(records[products]["safe_reason_code"], "robots_denied")

    def test_robots_rate_directives_fail_closed_before_source_reads(self) -> None:
        robots = self.plan["robots_policy_urls"][1]
        source_urls = {
            source["url"]
            for source in self.plan["sources"]
            if source["url"].startswith("https://www.celsius.com/")
        }
        for directive in (b"Crawl-delay: 2", b"Request-rate: 1/10"):
            with self.subTest(directive=directive):
                product = USER_AGENT.split("/", 1)[0].encode()
                body = b"User-agent: " + product + b"\nAllow: /\n" + directive + b"\n"
                transport = Transport({
                    robots: lambda request, body=body: response(
                        request, body=body, content_type="text/plain"
                    )
                })
                bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
                matching = [
                    record for record in bundle["sources"]
                    if record["requested_url"] in source_urls
                ]
                self.assertTrue(matching)
                self.assertTrue(all(record["safe_reason_code"] == "robots_unavailable" for record in matching))
                self.assertFalse(any(call["url"] in source_urls for call in transport.calls))

    def test_robots_repeated_stars_are_collapsed_and_input_is_bounded(self) -> None:
        policy = _parse_robots(
            "https://public.example/robots.txt",
            "User-agent: *\nDisallow: /" + ("*" * 64) + "never$\n",
        )
        self.assertEqual(policy.rules[0][1], "/*never")
        self.assertTrue(policy.can_fetch(USER_AGENT, "https://public.example/products/" + ("x" * 2_000)))
        with self.assertRaises(_ReadFailure):
            _parse_robots(
                "https://public.example/robots.txt",
                "User-agent: *\nDisallow: /" + ("x" * 4_097) + "\n",
            )

    def test_robots_failure_is_safe_and_does_not_expose_transport_details(self) -> None:
        robots = self.plan["robots_policy_urls"][0]

        def fail(_request: dict) -> dict:
            raise OSError("secret internal endpoint detail")

        bundle = self.reader(transport=Transport({robots: fail})).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "robots_unavailable")
        self.assertNotIn("secret internal", json.dumps(record))

    def test_malformed_or_directiveless_robots_fails_closed(self) -> None:
        robots = self.plan["robots_policy_urls"][0]
        for body in (b"not-a-directive\n", b"Sitemap: https://public.example/sitemap.xml\n"):
            with self.subTest(body=body):
                bundle = self.reader(transport=Transport({
                    robots: lambda request, body=body: response(
                        request, body=body, content_type="text/plain"
                    )
                })).read_plan(self.plan["source_plan_id"])
                self.assertEqual(bundle["sources"][0]["safe_reason_code"], "robots_unavailable")

    def test_request_deadline_uses_monotonic_time(self) -> None:
        clock = Monotonic()
        first = self.plan["sources"][0]["url"]

        def late(request: dict) -> dict:
            clock.value = request["deadline"] + 0.01
            return response(request, body=b"late public body")

        bundle = self.reader(
            transport=Transport({first: late}), monotonic=clock
        ).read_plan(self.plan["source_plan_id"])
        self.assertEqual(bundle["sources"][0]["safe_reason_code"], "deadline")

    def test_content_type_encoding_length_and_utf8_fail_closed(self) -> None:
        first = self.plan["sources"][0]["url"]
        cases = {
            "type": lambda request: response(request, body=b"{}", content_type="application/json"),
            "encoding": lambda request: response(
                request,
                body=b"compressed",
                headers={"Content-Encoding": "gzip"},
            ),
            "length": lambda request: response(
                request,
                body=b"short",
                headers={"Content-Length": str(self.plan["budgets"]["max_body_bytes"] + 1)},
            ),
            "utf8": lambda request: response(request, body=b"\xff"),
            "attachment": lambda request: response(
                request,
                body=b"download",
                headers={"Content-Disposition": "attachment; filename=public.html"},
            ),
            "ambiguous_length": lambda request: response(
                request,
                body=b"short",
                headers={"Content-Length": "5", "Transfer-Encoding": "chunked"},
            ),
        }
        for name, override in cases.items():
            with self.subTest(name=name):
                bundle = self.reader(transport=Transport({first: override})).read_plan(self.plan["source_plan_id"])
                record = bundle["sources"][0]
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["safe_reason_code"], "body_limit" if name == "length" else "content_rejected")
                self.assertEqual(record["body_byte_length"], 0)
                self.assertEqual(record["summary"], "")

    def test_extracted_text_limit_is_enforced_before_persistence(self) -> None:
        first = self.plan["sources"][0]["url"]
        oversized = b"a" * (self.plan["budgets"]["max_extracted_text_bytes"] + 1)
        bundle = self.reader(
            transport=Transport({
                first: lambda request: response(request, body=b"<main><p>" + oversized + b"</p></main>")
            })
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "body_limit")
        self.assertEqual(record["extracted_text_byte_length"], 0)

    def test_credential_like_visible_text_is_rejected_not_persisted(self) -> None:
        first = self.plan["sources"][0]["url"]
        transport = Transport({
            first: lambda request: response(
                request, body=b"<main><p>api_key=super-secret-material</p></main>"
            )
        })
        bundle = self.reader(transport=transport).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "privacy_rejected")
        self.assertNotIn("super-secret-material", json.dumps(record))

    def test_zero_width_credential_like_visible_text_is_rejected_not_persisted(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            "<main><p>api\u200b_key=super-secret-material. "
            "The official source describes the company and its current product catalog.</p></main>"
        ).encode()
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "privacy_rejected")
        self.assertNotIn("super-secret-material", json.dumps(record))

    def test_default_ignorable_credential_like_visible_text_is_rejected(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            "<main><p>api\u034f_key=super-secret-material. "
            "The official source describes the company and its current product catalog.</p></main>"
        ).encode()
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "privacy_rejected")
        self.assertNotIn("super-secret-material", json.dumps(record))

    def test_cross_script_credential_like_visible_text_is_rejected(self) -> None:
        first = self.plan["sources"][0]["url"]
        for credential in (
            "api.key=super-secret-material",
            "api_kеy=super-secret-material",
            "api_kЕy=super-secret-material",
            "АPI_KEY=super-secret-material",
        ):
            with self.subTest(credential=credential):
                body = (
                    f"<main><p>{credential}. The official source describes the company and "
                    "its current product catalog.</p></main>"
                ).encode()
                bundle = self.reader(
                    transport=Transport({first: lambda request, body=body: response(request, body=body)})
                ).read_plan(self.plan["source_plan_id"])
                record = bundle["sources"][0]
                self.assertEqual(record["safe_reason_code"], "privacy_rejected")
                self.assertNotIn("super-secret-material", json.dumps(record))

    def test_spaced_unicode_contact_routes_fail_closed(self) -> None:
        first = self.plan["sources"][0]["url"]
        for contact in (
            "press @ пример.рф",
            "press (at) пример (dot) рф",
            "press at пример dot рф",
        ):
            with self.subTest(contact=contact):
                body = (
                    f"<main><p>{contact}. The official source describes the company and its current "
                    "product catalog. Its public page also explains the current business offering.</p></main>"
                ).encode()
                bundle = self.reader(
                    transport=Transport({first: lambda request, body=body: response(request, body=body)})
                ).read_plan(self.plan["source_plan_id"])
                record = bundle["sources"][0]
                self.assertEqual(record["safe_reason_code"], "privacy_rejected")
                self.assertNotIn("press", json.dumps(record, ensure_ascii=False))

    def test_contact_data_split_across_html_nodes_is_scrubbed(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            b"<main><p>press<span>@</span>example<span>.</span>org and 212<span>-</span>555<span>-</span>0100. "
            b"The official source describes the company and its current product catalog. "
            b"Its public page also explains the current business offering.</p></main>"
        )
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["status"], "success")
        self.assertNotIn("press", record["summary"])
        self.assertNotIn("0100", record["summary"])
        self.assertIn("[redacted]", record["summary"])

    def test_unicode_obfuscated_contact_data_is_scrubbed_before_persistence(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            "<main><p>press＠example．org and press@example。org and "
            "press@пример.рф and press@exаmple.com and "
            "press\u200b@\u200bexample\u200b.\u200borg and "
            "212\u200b-\u200b555\u200b-\u200b0100. "
            "The official source describes the company and its current product catalog. "
            "Its public page also explains the current business offering.</p></main>"
        ).encode()
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["status"], "success")
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("press", serialized)
        self.assertNotIn("0100", serialized)
        self.assertNotIn("\u200b", record["summary"])
        self.assertGreaterEqual(record["summary"].count("[redacted]"), 6)

    def test_combining_mark_contact_data_fails_closed_without_partial_redaction(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            "<main><p>press@exa\u0301mple.com. "
            "The official source describes the company and its current product catalog. "
            "Its public page also explains the current business offering.</p></main>"
        ).encode()
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "privacy_rejected")
        self.assertNotIn("press", json.dumps(record, ensure_ascii=False))

    def test_obfuscated_contact_routes_and_bare_urls_are_scrubbed(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            b"<main><p>press [at] example [dot] org; 212.555.0100; "
            b"www.unapproved.invalid/contact; unapproved.invalid/contact; "
            b"https://unapproved.invalid/contact; example.com; official evidence.</p>"
            b"<p>The official evidence describes the company and its current product catalog. "
            b"Its public page also explains the current business offering.</p></main>"
        )
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["status"], "success")
        self.assertIn("official evidence", record["summary"])
        serialized = json.dumps(record)
        self.assertNotIn("press [at]", serialized)
        self.assertNotIn("212.555.0100", serialized)
        self.assertNotIn("www.unapproved.invalid", serialized)
        self.assertNotIn("unapproved.invalid", serialized)
        self.assertNotIn("example.com", serialized)
        self.assertGreaterEqual(record["summary"].count("[redacted]"), 6)

    def test_temporary_password_visible_text_is_rejected(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = b"<main><p>Temporary password: hunter2</p></main>"
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual(record["safe_reason_code"], "privacy_rejected")
        self.assertNotIn("hunter2", json.dumps(record))

    def test_hidden_and_malformed_executable_text_is_not_extracted(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = (
            b"<main><p>Visible official evidence describes the company and its current product catalog.</p>"
            b"<div hidden>hidden-marker-one</div>"
            b"<div aria-hidden='true'>hidden-marker-two</div>"
            b"<div style='display: none'>hidden-marker-three</div>"
            b"<script>hidden-marker-four</style>hidden-marker-five</script>"
            b"<p>The official page also explains its current business offering and operations.</p></main>"
        )
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        summary = bundle["sources"][0]["summary"]
        self.assertIn("Visible official evidence", summary)
        self.assertIn("current business offering", summary)
        self.assertNotIn("hidden-marker", summary)

    def test_summary_prefers_main_research_content_over_navigation_chrome(self) -> None:
        first = self.plan["sources"][0]["url"]
        navigation = " ".join(["navigation-marker menu search catalog"] * 80)
        body = (
            "<html><body>"
            f"<header><nav>{navigation}</nav></header>"
            "<main><h1>Official company profile</h1>"
            "<p>The company designs and manufactures outdoor products in Miami. "
            "Its official page also explains the current business offering and operations.</p></main>"
            "<footer>footer-navigation-marker</footer>"
            "</body></html>"
        ).encode()
        bundle = self.reader(
            transport=Transport({first: lambda request: response(request, body=body)})
        ).read_plan(self.plan["source_plan_id"])
        summary = bundle["sources"][0]["summary"]
        self.assertIn("designs and manufactures outdoor products", summary)
        self.assertNotIn("navigation-marker", summary)
        self.assertNotIn("footer-navigation-marker", summary)

    def test_navigation_only_text_fails_closed_without_retained_metadata(self) -> None:
        first = self.plan["sources"][0]["url"]
        cases = {
            "semantic-nav": "<nav>Menu Search Catalog Collections</nav>",
            "aria-nav": "<div role='navigation'>Menu Search Catalog Collections</div>",
            "generic-menu-labels": "<div>" + ("Menu Search Catalog Collections " * 40) + "</div>",
            "punctuated-title-case-menu": (
                "<div>Shop New Arrivals, Products, Collections, Gifts, Stores, About Us.</div>"
            ),
            "punctuated-lowercase-menu": (
                "<div>menu search catalog collections featured new arrivals offers.</div>"
            ),
            "padded-title-case-menu": (
                "<main><div>Shop New Arrivals and Explore Products for all our favorite styles.</div></main>"
            ),
            "padded-lowercase-menu": (
                "<div role='main'>shop new arrivals and explore products for all our favorite styles.</div>"
            ),
            "drawer-menu-prose": (
                "<article><div class='drawer-menu'>Open the menu to explore new arrivals, bestselling "
                "rings, necklaces, bracelets, and gifts.</div></article>"
            ),
            "malformed-preferred-boundary": (
                "<main><article></main><div>Open the menu to explore new arrivals, bestselling "
                "rings, necklaces, bracelets, and gifts.</div>"
            ),
            "direct-main-padded-prose": (
                "<main>Shop New Arrivals and Explore Products for all our favorite styles. "
                "Browse featured collections and discover more gifts for favorite moments.</main>"
            ),
            "paragraph-navigation-prose": (
                "<main><p>Shop New Arrivals and Explore Products for all our favorite styles. "
                "Browse featured collections and discover more gifts for favorite moments.</p></main>"
            ),
        }
        for label, html in cases.items():
            with self.subTest(label=label):
                bundle = self.reader(
                    transport=Transport({
                        first: lambda request, body=html.encode(): response(request, body=body)
                    })
                ).read_plan(self.plan["source_plan_id"])
                record = bundle["sources"][0]
                self.assertEqual((record["status"], record["safe_reason_code"]), ("failed", "content_rejected"))
                self.assertEqual(record["summary"], "")
                self.assertIsNone(record["body_sha256"])
                self.assertEqual(record["body_byte_length"], 0)
                self.assertIsNone(record["extracted_text_sha256"])
                self.assertEqual(record["extracted_text_byte_length"], 0)

    def test_plain_text_source_content_cannot_become_research_evidence(self) -> None:
        first = self.plan["sources"][0]["url"]
        body = b"Open the menu to explore new arrivals, bestselling rings, necklaces, bracelets, and gifts."
        bundle = self.reader(
            transport=Transport({
                first: lambda request: response(request, body=body, content_type="text/plain")
            })
        ).read_plan(self.plan["source_plan_id"])
        record = bundle["sources"][0]
        self.assertEqual((record["status"], record["safe_reason_code"]), ("failed", "content_rejected"))
        self.assertEqual(record["summary"], "")

    def test_request_contract_carries_only_bounded_exact_destination_data(self) -> None:
        resolver = Resolver()
        transport = Transport()
        self.reader(resolver=resolver, transport=transport).read_plan(self.plan["source_plan_id"])
        self.assertTrue(resolver.calls)
        self.assertTrue(all(port == 443 and 0 < timeout <= 5 for _, port, timeout in resolver.calls))
        self.assertTrue(all(call["url"] in {
            *self.plan["robots_policy_urls"],
            *(source["url"] for source in self.plan["sources"]),
        } for call in transport.calls))
        self.assertTrue(all(call["target"].startswith("/") for call in transport.calls))
        self.assertTrue(all(call["approved_ips"] == (PUBLIC_IP,) for call in transport.calls))

    def test_unknown_plan_never_resolves_or_connects(self) -> None:
        resolver = Resolver()
        transport = Transport()
        with self.assertRaisesRegex(ValueError, "Unknown exact"):
            self.reader(resolver=resolver, transport=transport).read_plan("not-authorized")
        self.assertEqual(resolver.calls, [])
        self.assertEqual(transport.calls, [])

    def test_default_resolver_timeout_terminates_and_joins_worker(self) -> None:
        class Connection:
            def __init__(self, readable: bool) -> None:
                self.readable = readable
                self.closed = False

            def poll(self, timeout: float) -> bool:
                self.assert_timeout = timeout
                return False

            def close(self) -> None:
                self.closed = True

        class Process:
            def __init__(self) -> None:
                self.started = False
                self.terminated = False
                self.killed = False
                self.joined = False

            def start(self) -> None:
                self.started = True

            def is_alive(self) -> bool:
                return self.started and not self.killed

            def terminate(self) -> None:
                self.terminated = True

            def join(self, timeout: float) -> None:
                self.assert_join_timeout = timeout
                self.joined = True

            def kill(self) -> None:
                self.killed = True

            def close(self) -> None:
                self.closed = True

        receiver, sender = Connection(True), Connection(False)
        process = Process()

        class Context:
            @staticmethod
            def Pipe(*, duplex: bool):
                self.assertFalse(duplex)
                return receiver, sender

            @staticmethod
            def Process(**kwargs):
                self.assertEqual(kwargs["name"], "phase6-public-dns")
                return process

        with mock.patch("mission_control.public_reader.multiprocessing.get_context", return_value=Context()):
            with self.assertRaises(_ReadFailure):
                _default_resolver("public.example", 443, 0.01)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(process.joined)
        self.assertTrue(receiver.closed)
        self.assertTrue(sender.closed)

    def test_committed_manifest_object_is_the_supported_plan_interface(self) -> None:
        bundle = PublicReader(
            copy.deepcopy(self.manifest),
            resolver=Resolver(),
            transport=Transport(),
            monotonic=Monotonic(),
            utc_now=lambda: FIXED,
        ).read_plan(self.manifest["plans"][1]["source_plan_id"])
        self.assertEqual(bundle["source_plan_hash"], self.manifest["plans"][1]["source_plan_hash"])


if __name__ == "__main__":
    unittest.main()
