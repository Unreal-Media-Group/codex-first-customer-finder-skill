"""Bounded, exact-plan public HTTPS reader for Phase 6 research."""

from __future__ import annotations

import copy
import hashlib
import http.client
import ipaddress
import multiprocessing
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit

# application.py installs the shared deterministic core on sys.path.
from common import ValidationError  # noqa: E402
from validate_phase6_real_contract import (  # noqa: E402
    REAL_SOURCE_KIND_BY_CLASS,
    is_substantive_real_summary,
    scrub_real_public_text,
    validate_real_research_bundle,
    validate_real_source_manifest,
    validate_real_source_plan,
)

USER_AGENT = "UnrealProspectingPublicReader/1.0"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class _ReadFailure(Exception):
    def __init__(self, reason: str, redirect_chain: Iterable[str] = ()):
        super().__init__(reason)
        self.reason = reason
        self.redirect_chain = list(redirect_chain)


class _VisibleTextParser(HTMLParser):
    _IGNORED = {
        "script", "style", "template", "noscript", "svg", "canvas", "iframe", "object",
        "nav", "header", "footer", "aside", "menu", "dialog", "a", "button", "form",
        "select", "option",
    }
    _IGNORED_ROLES = {
        "navigation", "banner", "contentinfo", "search", "dialog", "menu", "menubar", "toolbar",
    }
    _IGNORED_CONTAINER_HINTS = {
        "breadcrumb", "dialog", "drawer", "footer", "header", "menu", "modal", "nav",
        "navigation", "pagination", "search", "sidebar", "toolbar", "utility",
    }
    _PREFERRED = {"main", "article"}
    _PREFERRED_ROLES = {"main"}
    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.suppressed: list[str] = []
        self.preferred_depth = 0
        self.preferred_boundaries: list[str] = []
        self.paragraph_depth = 0
        self.current_paragraph_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.malformed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        style = "".join((attributes.get("style") or "").casefold().split())
        container_tokens = set(re.findall(
            r"[a-z0-9]+",
            " ".join(
                attributes.get(name) or ""
                for name in ("class", "id", "aria-label")
            ).casefold(),
        ))
        hidden = (
            tag in self._IGNORED
            or (attributes.get("role") or "").casefold() in self._IGNORED_ROLES
            or bool(container_tokens & self._IGNORED_CONTAINER_HINTS)
            or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag not in self._VOID and (self.suppressed or hidden):
            self.suppressed.append(tag)
            return
        if tag in self._PREFERRED or (attributes.get("role") or "").casefold() in self._PREFERRED_ROLES:
            self.preferred_depth += 1
            self.preferred_boundaries.append(tag)
        if tag == "p" and self.preferred_depth:
            if self.paragraph_depth:
                self.malformed = True
            self.paragraph_depth += 1
            self.current_paragraph_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag == "p" and self.paragraph_depth:
            paragraph = " ".join(" ".join(self.current_paragraph_parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
            self.current_paragraph_parts = []
            self.paragraph_depth -= 1
        elif tag == "p":
            self.malformed = True
        if self.preferred_boundaries and tag == self.preferred_boundaries[-1]:
            if self.paragraph_depth:
                self.malformed = True
            self.preferred_boundaries.pop()
            self.preferred_depth -= 1
        elif tag in self.preferred_boundaries or tag in self._PREFERRED:
            self.malformed = True
            self.preferred_boundaries.clear()
            self.preferred_depth = 0

    def handle_data(self, data: str) -> None:
        if not self.suppressed and self.preferred_depth and self.paragraph_depth:
            self.current_paragraph_parts.append(data)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection whose TCP destination is an already-approved address."""

    def __init__(self, request: dict[str, Any]):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(
            request["hostname"],
            443,
            timeout=request["connect_timeout"],
            context=context,
        )
        self.request_data = request

    def connect(self) -> None:
        request = self.request_data
        remaining = request["deadline"] - request["monotonic"]()
        if remaining <= 0:
            raise _ReadFailure("deadline")
        raw = socket.socket(request["family"], socket.SOCK_STREAM)
        try:
            raw.settimeout(min(request["connect_timeout"], remaining))
            raw.connect(request["sockaddr"])
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _resolver_process(sender: Any, hostname: str, port: int) -> None:
    try:
        value = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        sender.send((True, value))
    except Exception:
        sender.send((False, None))
    finally:
        sender.close()


def _default_resolver(hostname: str, port: int, timeout: float) -> list[tuple[Any, ...]]:
    """Resolve in a killable joined process so a stalled resolver cannot leak."""

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_resolver_process,
        args=(sender, hostname, port),
        daemon=True,
        name="phase6-public-dns",
    )
    started = False
    try:
        process.start()
        started = True
        sender.close()
        if not receiver.poll(max(0.001, timeout)):
            raise _ReadFailure("deadline")
        try:
            succeeded, value = receiver.recv()
        except (EOFError, OSError) as exc:
            raise _ReadFailure("dns_rejected") from exc
        if not succeeded:
            raise _ReadFailure("dns_rejected")
        return value
    finally:
        receiver.close()
        sender.close()
        if started:
            if process.is_alive():
                process.terminate()
            join_timeout = max(0.05, min(1.0, timeout))
            process.join(join_timeout)
            if process.is_alive():
                process.kill()
                process.join(join_timeout)
            if not process.is_alive():
                process.close()


def _default_transport(request: dict[str, Any]) -> dict[str, Any]:
    connection = _PinnedHTTPSConnection(request)
    try:
        remaining = request["deadline"] - request["monotonic"]()
        if remaining <= 0:
            raise _ReadFailure("deadline")
        connection.request(
            "GET",
            request["target"],
            headers={
                "Accept": "text/html,text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": USER_AGENT,
            },
        )
        if connection.sock is None:
            raise _ReadFailure("transport_error")
        remaining = request["deadline"] - request["monotonic"]()
        if remaining <= 0:
            raise _ReadFailure("deadline")
        connection.sock.settimeout(min(request["read_timeout"], remaining))
        peer_ip = connection.sock.getpeername()[0]
        response = connection.getresponse()
        headers = response.getheaders()
        header_bytes = len(f"HTTP/1.1 {response.status} {response.reason}\r\n".encode("latin-1"))
        header_bytes += sum(len(name.encode("latin-1")) + len(value.encode("latin-1")) + 4 for name, value in headers)
        if header_bytes > request["max_header_bytes"]:
            raise _ReadFailure("content_rejected")
        body = bytearray()
        while True:
            remaining = request["deadline"] - request["monotonic"]()
            if remaining <= 0:
                raise _ReadFailure("deadline")
            if connection.sock is not None:
                connection.sock.settimeout(min(request["read_timeout"], remaining))
            chunk = response.read(min(65_536, request["max_body_bytes"] + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > request["max_body_bytes"]:
                raise _ReadFailure("body_limit")
        return {
            "status": response.status,
            "headers": headers,
            "body": bytes(body),
            "peer_ip": peer_ip,
            "tls_verified": True,
            "tls_hostname": request["hostname"],
        }
    except _ReadFailure:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise _ReadFailure("deadline") from exc
    except (ssl.CertificateError, ssl.SSLError) as exc:
        raise _ReadFailure("tls_rejected") from exc
    except OSError as exc:
        raise _ReadFailure("transport_error") from exc
    finally:
        connection.close()


def _format_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("The public-reader UTC clock must return an aware datetime.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    port = ":443" if parsed.port == 443 and ":443" in parsed.netloc else ""
    return f"https://{parsed.hostname}{port}"


def _headers(value: Any, maximum: int) -> dict[str, list[str]]:
    items = list(value.items()) if isinstance(value, dict) else value
    if not isinstance(items, (list, tuple)):
        raise _ReadFailure("content_rejected")
    result: dict[str, list[str]] = {}
    size = 0
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise _ReadFailure("content_rejected")
        name, header_value = item
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise _ReadFailure("content_rejected")
        if not _HEADER_NAME.fullmatch(name):
            raise _ReadFailure("content_rejected")
        if any(ord(character) < 32 or ord(character) == 127 for character in header_value):
            raise _ReadFailure("content_rejected")
        try:
            size += len(name.encode("ascii")) + len(header_value.encode("latin-1")) + 4
        except UnicodeEncodeError as exc:
            raise _ReadFailure("content_rejected") from exc
        if size > maximum:
            raise _ReadFailure("content_rejected")
        result.setdefault(name.lower(), []).append(header_value.strip())
    return result


def _public_endpoints(value: Any) -> list[tuple[int, tuple[Any, ...], str]]:
    if not isinstance(value, (list, tuple)) or not value:
        raise _ReadFailure("dns_rejected")
    endpoints: list[tuple[int, tuple[Any, ...], str]] = []
    seen: set[str] = set()
    for answer in value:
        if isinstance(answer, str):
            raw_ip = answer
            family = socket.AF_INET6 if ":" in answer else socket.AF_INET
            sockaddr: tuple[Any, ...] = (answer, 443, 0, 0) if family == socket.AF_INET6 else (answer, 443)
        elif isinstance(answer, tuple) and len(answer) == 5:
            family, _kind, _protocol, _name, raw_sockaddr = answer
            if (
                family not in {socket.AF_INET, socket.AF_INET6}
                or not isinstance(raw_sockaddr, tuple)
                or not raw_sockaddr
            ):
                raise _ReadFailure("dns_rejected")
            raw_ip = raw_sockaddr[0]
            sockaddr = raw_sockaddr
        else:
            raise _ReadFailure("dns_rejected")
        try:
            address = ipaddress.ip_address(raw_ip)
        except (TypeError, ValueError) as exc:
            raise _ReadFailure("dns_rejected") from exc
        if (
            (family == socket.AF_INET and address.version != 4)
            or (family == socket.AF_INET6 and address.version != 6)
            or not address.is_global
            or address.is_link_local
            or address.is_loopback
            or address.is_multicast
            or address.is_private
            or address.is_reserved
            or address.is_unspecified
            or getattr(address, "ipv4_mapped", None) is not None
        ):
            raise _ReadFailure("dns_rejected")
        normalized = address.compressed
        if normalized not in seen:
            seen.add(normalized)
            if family == socket.AF_INET6:
                sockaddr = (normalized, 443, 0, sockaddr[3] if len(sockaddr) > 3 else 0)
            else:
                sockaddr = (normalized, 443)
            endpoints.append((family, sockaddr, normalized))
    if not endpoints:
        raise _ReadFailure("dns_rejected")
    return endpoints


def _scrub_visible_text(value: str) -> str:
    try:
        return scrub_real_public_text(value)
    except ValidationError as exc:
        raise _ReadFailure("privacy_rejected") from exc


_MAX_ROBOTS_LINES = 4_096
_MAX_ROBOTS_GROUPS = 512
_MAX_ROBOTS_RULES = 2_048
_MAX_ROBOTS_AGENTS_PER_GROUP = 64
_MAX_ROBOTS_LINE_BYTES = 4_096
_MAX_ROBOTS_PATTERN_BYTES = 2_048


def _robots_glob_matches(pattern: str, value: str, anchored: bool) -> bool:
    """Match a robots `*` glob by ordered literal chunks, never regex backtracking."""
    if "*" not in pattern:
        return value == pattern if anchored else value.startswith(pattern)
    parts = pattern.split("*")
    position = 0
    first_part = 0
    if not pattern.startswith("*"):
        if not value.startswith(parts[0]):
            return False
        position = len(parts[0])
        first_part = 1
    final_part: str | None = None
    last_part = len(parts)
    if anchored and not pattern.endswith("*"):
        final_part = parts[-1]
        last_part -= 1
    for part in parts[first_part:last_part]:
        if not part:
            continue
        found = value.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    if final_part is None:
        return True
    final_position = len(value) - len(final_part)
    return final_position >= position and value.endswith(final_part)


class _RobotsPolicy:
    def __init__(self, rules: list[tuple[bool, str, int, bool]]) -> None:
        self.rules = rules

    def can_fetch(self, user_agent: str, url: str) -> bool:
        del user_agent
        parsed = urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        matches = [
            rule for rule in self.rules
            if _robots_glob_matches(rule[1], path, rule[3])
        ]
        if not matches:
            return True
        specificity = max(rule[2] for rule in matches)
        return any(allow for allow, _pattern, length, _anchored in matches if length == specificity)


def _robots_pattern(value: str) -> tuple[str, int, bool]:
    if (
        not value
        or len(value.encode("utf-8")) > _MAX_ROBOTS_PATTERN_BYTES
        or (not value.startswith("/") and not value.startswith("*"))
    ):
        raise _ReadFailure("robots_unavailable")
    anchored = value.endswith("$")
    source = value[:-1] if anchored else value
    collapsed: list[str] = []
    for character in source:
        if character == "*" and collapsed and collapsed[-1] == "*":
            continue
        collapsed.append(character)
    pattern = "".join(collapsed)
    return pattern, len(pattern.replace("*", "").encode("utf-8")), anchored


def _parse_robots(url: str, text: str) -> _RobotsPolicy:
    del url
    lines = text.splitlines()
    if len(lines) > _MAX_ROBOTS_LINES:
        raise _ReadFailure("robots_unavailable")
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    has_user_agent = False
    rule_count = 0
    for raw in lines:
        if (
            len(raw.encode("utf-8")) > _MAX_ROBOTS_LINE_BYTES
            or any(ord(character) < 32 and character != "\t" for character in raw)
        ):
            raise _ReadFailure("robots_unavailable")
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise _ReadFailure("robots_unavailable")
        key, value = (part.strip() for part in line.split(":", 1))
        if not re.fullmatch(r"[A-Za-z][A-Za-z-]*", key):
            raise _ReadFailure("robots_unavailable")
        key = key.casefold()
        if key == "user-agent":
            agent = value.casefold()
            if not re.fullmatch(r"\*|[a-z][a-z0-9_-]*", agent):
                raise _ReadFailure("robots_unavailable")
            if current is None or current["directives"]:
                if len(groups) >= _MAX_ROBOTS_GROUPS:
                    raise _ReadFailure("robots_unavailable")
                current = {"agents": [], "rules": [], "directives": False, "rate": False}
                groups.append(current)
            if len(current["agents"]) >= _MAX_ROBOTS_AGENTS_PER_GROUP:
                raise _ReadFailure("robots_unavailable")
            current["agents"].append(agent)
            has_user_agent = True
            continue
        if key == "sitemap":
            continue
        if current is None or not current["agents"]:
            raise _ReadFailure("robots_unavailable")
        current["directives"] = True
        if key in {"crawl-delay", "request-rate"}:
            if not value:
                raise _ReadFailure("robots_unavailable")
            current["rate"] = True
            continue
        if key not in {"allow", "disallow"}:
            raise _ReadFailure("robots_unavailable")
        if not value:
            if key == "allow":
                raise _ReadFailure("robots_unavailable")
            continue
        if rule_count >= _MAX_ROBOTS_RULES:
            raise _ReadFailure("robots_unavailable")
        pattern, specificity, anchored = _robots_pattern(value)
        current["rules"].append((key == "allow", pattern, specificity, anchored))
        rule_count += 1
    if not has_user_agent:
        raise _ReadFailure("robots_unavailable")
    product = USER_AGENT.split("/", 1)[0].casefold()
    matches: list[tuple[int, dict[str, Any]]] = []
    for group in groups:
        lengths = [
            0 if agent == "*" else len(agent)
            for agent in group["agents"]
            if agent == "*" or product.startswith(agent)
        ]
        if lengths:
            matches.append((max(lengths), group))
    if not matches:
        return _RobotsPolicy([])
    longest = max(length for length, _group in matches)
    selected = [group for length, group in matches if length == longest]
    if any(group["rate"] for group in selected):
        raise _ReadFailure("robots_unavailable")
    return _RobotsPolicy([
        rule for group in selected for rule in group["rules"]
    ])


class PublicReader:
    """Read only repository-authorized public sources into a validated safe bundle."""

    def __init__(
        self,
        exact_plans: Iterable[dict[str, Any]] | dict[str, Any],
        *,
        resolver: Callable[[str, int, float], Any] = _default_resolver,
        transport: Callable[[dict[str, Any]], dict[str, Any]] = _default_transport,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if isinstance(exact_plans, dict):
            if "plans" in exact_plans:
                values = validate_real_source_manifest(copy.deepcopy(exact_plans))["plans"]
            else:
                values = list(exact_plans.values())
        else:
            values = exact_plans
        plans = [validate_real_source_plan(copy.deepcopy(plan)) for plan in values]
        self._plans = {plan["source_plan_id"]: plan for plan in plans}
        if len(self._plans) != len(plans):
            raise ValueError("Exact public source plan IDs must be unique.")
        self._resolver = resolver
        self._transport = transport
        self._monotonic = monotonic
        self._utc_now = utc_now

    def read_plan(self, plan_id: str) -> dict[str, Any]:
        if plan_id not in self._plans:
            raise ValueError("Unknown exact public source plan.")
        plan = self._plans[plan_id]
        started_wall = self._wall_now()
        started_mono = self._monotonic()
        state = {
            "budgets": plan["budgets"],
            "run_deadline": started_mono + plan["budgets"]["run_deadline_seconds"],
            "requests": 0,
            "approved_sources": {source["url"] for source in plan["sources"]},
            "approved_robots": set(plan["robots_policy_urls"]),
        }
        robots_by_origin = {_origin(url): url for url in plan["robots_policy_urls"]}
        robots_rules: dict[str, _RobotsPolicy | str] = {}
        for robots_url in plan["robots_policy_urls"]:
            origin = _origin(robots_url)
            try:
                fetched = self._fetch(robots_url, state, state["approved_robots"], robots=True)
                text, _content_type = self._decode(fetched["body"], fetched["headers"], plain_only=True)
                robots_rules[origin] = _parse_robots(robots_url, text)
            except _ReadFailure as exc:
                robots_rules[origin] = "deadline" if exc.reason == "deadline" else "robots_unavailable"

        records: list[dict[str, Any]] = []
        last_observed = started_wall
        for source in plan["sources"]:
            observed = self._wall_now(last_observed)
            last_observed = observed
            robots_url = robots_by_origin[_origin(source["url"])]
            rules = robots_rules.get(_origin(source["url"]), "robots_unavailable")
            try:
                if self._monotonic() >= state["run_deadline"]:
                    raise _ReadFailure("deadline")
                if isinstance(rules, str):
                    raise _ReadFailure(rules)
                if not rules.can_fetch(USER_AGENT, source["url"]):
                    raise _ReadFailure("robots_denied")
                fetched = self._fetch(
                    source["url"],
                    state,
                    state["approved_sources"],
                    robots_rules=robots_rules,
                )
                text, content_type = self._decode(fetched["body"], fetched["headers"])
                extracted = self._extract(text, content_type, plan["budgets"]["max_extracted_text_bytes"])
                if not is_substantive_real_summary(extracted[:1_000]):
                    raise _ReadFailure("content_rejected", fetched["redirect_chain"])
                if self._monotonic() >= state["run_deadline"]:
                    raise _ReadFailure("deadline", fetched["redirect_chain"])
                observed = self._wall_now(last_observed)
                last_observed = observed
                source_date, date_state = self._source_date(fetched["headers"], observed)
                records.append(self._success_record(
                    source, robots_url, fetched, extracted, content_type,
                    observed, source_date, date_state,
                ))
            except _ReadFailure as exc:
                observed = self._wall_now(last_observed)
                last_observed = observed
                records.append(self._failure_record(source, robots_url, observed, exc))

        completed = self._wall_now(last_observed)
        bundle = {
            "bundle_version": 1,
            "source_plan_id": plan["source_plan_id"],
            "source_plan_hash": plan["source_plan_hash"],
            "started_at": _format_time(started_wall),
            "completed_at": _format_time(completed),
            "sources": records,
        }
        validate_real_research_bundle(bundle, plan, require_substantive=True)
        return bundle

    def _wall_now(self, minimum: datetime | None = None) -> datetime:
        value = self._utc_now()
        _format_time(value)
        value = value.astimezone(timezone.utc)
        return minimum if minimum is not None and value < minimum else value

    def _fetch(
        self,
        url: str,
        state: dict[str, Any],
        allowed_urls: set[str],
        *,
        robots: bool = False,
        robots_rules: dict[str, _RobotsPolicy | str] | None = None,
    ) -> dict[str, Any]:
        chain: list[str] = []
        current = url
        while True:
            if current not in allowed_urls:
                raise _ReadFailure("redirect_rejected", chain)
            if robots_rules is not None:
                rules = robots_rules.get(_origin(current), "robots_unavailable")
                if isinstance(rules, str):
                    raise _ReadFailure(rules, chain)
                if not rules.can_fetch(USER_AGENT, current):
                    raise _ReadFailure("robots_denied", chain)
            response = self._request(current, state, robots=robots)
            status = response["status"]
            if status not in _REDIRECT_STATUSES:
                if status in {401, 403}:
                    raise _ReadFailure("access_control", chain)
                if status != 200:
                    raise _ReadFailure("http_status", chain)
                response["final_url"] = current
                response["redirect_chain"] = chain
                return response
            locations = response["headers"].get("location", [])
            if len(locations) != 1 or len(chain) >= state["budgets"]["max_redirects_per_source"]:
                raise _ReadFailure("redirect_rejected", chain)
            target = urljoin(current, locations[0])
            if target not in allowed_urls or target in {url, *chain}:
                raise _ReadFailure("redirect_rejected", chain)
            chain.append(target)
            current = target

    def _request(self, url: str, state: dict[str, Any], *, robots: bool) -> dict[str, Any]:
        budgets = state["budgets"]
        now = self._monotonic()
        if now >= state["run_deadline"] or state["requests"] >= budgets["max_requests_total"]:
            raise _ReadFailure("deadline")
        deadline = min(state["run_deadline"], now + budgets["request_deadline_seconds"])
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise _ReadFailure("deadline")
        try:
            answers = self._resolver(hostname, 443, min(budgets["connect_timeout_seconds"], remaining))
        except _ReadFailure:
            raise
        except Exception as exc:
            raise _ReadFailure("dns_rejected") from exc
        if self._monotonic() >= deadline:
            raise _ReadFailure("deadline")
        endpoints = _public_endpoints(answers)
        family, sockaddr, _approved_ip = endpoints[0]
        state["requests"] += 1
        maximum = budgets["max_robots_bytes"] if robots else budgets["max_body_bytes"]
        request = {
            "url": url,
            "hostname": hostname,
            "target": parsed.path,
            "family": family,
            "sockaddr": sockaddr,
            "approved_ips": tuple(endpoint[2] for endpoint in endpoints),
            "connect_timeout": budgets["connect_timeout_seconds"],
            "read_timeout": budgets["read_timeout_seconds"],
            "deadline": deadline,
            "max_header_bytes": budgets["max_header_bytes"],
            "max_body_bytes": maximum,
            "monotonic": self._monotonic,
        }
        try:
            response = self._transport(request)
        except _ReadFailure:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise _ReadFailure("deadline") from exc
        except Exception as exc:
            raise _ReadFailure("transport_error") from exc
        if self._monotonic() >= deadline:
            raise _ReadFailure("deadline")
        if not isinstance(response, dict):
            raise _ReadFailure("transport_error")
        try:
            status = response["status"]
            body = response["body"]
            peer = ipaddress.ip_address(response["peer_ip"]).compressed
            verified = response["tls_verified"] is True
            tls_hostname = response["tls_hostname"]
            headers = _headers(response["headers"], budgets["max_header_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _ReadFailure("transport_error") from exc
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise _ReadFailure("transport_error")
        if not verified or tls_hostname != hostname or peer not in request["approved_ips"]:
            raise _ReadFailure("tls_rejected")
        if not isinstance(body, bytes):
            raise _ReadFailure("content_rejected")
        if len(body) > maximum:
            raise _ReadFailure("body_limit")
        dispositions = headers.get("content-disposition", [])
        if len(dispositions) > 1 or (
            dispositions and dispositions[0].split(";", 1)[0].strip().casefold() == "attachment"
        ):
            raise _ReadFailure("content_rejected")
        lengths = headers.get("content-length", [])
        if len(lengths) > 1:
            raise _ReadFailure("content_rejected")
        if lengths:
            try:
                content_length = int(lengths[0])
            except ValueError as exc:
                raise _ReadFailure("content_rejected") from exc
            if content_length < 0 or content_length > maximum or content_length != len(body):
                raise _ReadFailure("body_limit" if content_length > maximum else "content_rejected")
        transfers = headers.get("transfer-encoding", [])
        if (
            len(transfers) > 1
            or (transfers and transfers[0].strip().casefold() != "chunked")
            or (transfers and lengths)
        ):
            raise _ReadFailure("content_rejected")
        return {"status": status, "headers": headers, "body": body}

    def _decode(
        self,
        body: bytes,
        headers: dict[str, list[str]],
        *,
        plain_only: bool = False,
    ) -> tuple[str, str]:
        encodings = headers.get("content-encoding", [])
        if len(encodings) > 1 or (encodings and encodings[0].lower() != "identity"):
            raise _ReadFailure("content_rejected")
        types = headers.get("content-type", [])
        if len(types) != 1:
            raise _ReadFailure("content_rejected")
        parts = [part.strip() for part in types[0].split(";")]
        content_type = parts[0].lower()
        allowed = {"text/plain"} if plain_only else {"text/html", "text/plain"}
        if content_type not in allowed:
            raise _ReadFailure("content_rejected")
        parameters: dict[str, str] = {}
        for parameter in parts[1:]:
            if "=" not in parameter:
                raise _ReadFailure("content_rejected")
            key, value = (item.strip() for item in parameter.split("=", 1))
            key = key.lower()
            if not key or key in parameters:
                raise _ReadFailure("content_rejected")
            parameters[key] = value.strip('"').lower()
        charsets = [parameters["charset"]] if "charset" in parameters else []
        if len(charsets) > 1 or (charsets and charsets[0] not in {"utf-8", "utf8", "us-ascii"}):
            raise _ReadFailure("content_rejected")
        try:
            return body.decode("utf-8"), content_type
        except UnicodeDecodeError as exc:
            raise _ReadFailure("content_rejected") from exc

    def _extract(self, text: str, content_type: str, maximum: int) -> str:
        if content_type != "text/html":
            raise _ReadFailure("content_rejected")
        parser = _VisibleTextParser()
        try:
            parser.feed(text)
            parser.close()
        except (UnicodeError, ValueError) as exc:
            raise _ReadFailure("content_rejected") from exc
        if (
            parser.malformed
            or parser.preferred_boundaries
            or parser.paragraph_depth
            or not parser.paragraphs
        ):
            raise _ReadFailure("content_rejected")
        text = " ".join(parser.paragraphs)
        text = " ".join(text.split())
        if len(text.encode("utf-8")) > maximum:
            raise _ReadFailure("body_limit")
        text = _scrub_visible_text(text)
        if not text:
            raise _ReadFailure("content_rejected")
        if len(text.encode("utf-8")) > maximum:
            raise _ReadFailure("body_limit")
        return text

    @staticmethod
    def _source_date(headers: dict[str, list[str]], observed: datetime) -> tuple[str, str]:
        values = headers.get("last-modified", [])
        if len(values) == 1:
            try:
                value = parsedate_to_datetime(values[0])
                if value.tzinfo is not None:
                    value = value.astimezone(timezone.utc)
                    if value.date() <= observed.date():
                        return value.date().isoformat(), "published_date"
            except (TypeError, ValueError, OverflowError):
                pass
        return observed.date().isoformat(), "observed_date_fallback"

    @staticmethod
    def _success_record(
        source: dict[str, Any],
        robots_url: str,
        fetched: dict[str, Any],
        extracted: str,
        content_type: str,
        observed: datetime,
        source_date: str,
        date_state: str,
    ) -> dict[str, Any]:
        body = fetched["body"]
        extracted_bytes = extracted.encode("utf-8")
        return {
            "source_record_version": 1,
            "requested_url": source["url"],
            "final_url": fetched["final_url"],
            "redirect_chain": fetched["redirect_chain"],
            "source_class": source["source_class"],
            "source_kind": REAL_SOURCE_KIND_BY_CLASS[source["source_class"]],
            "robots_url": robots_url,
            "observed_at": _format_time(observed),
            "source_date": source_date,
            "date_state": date_state,
            "status": "success",
            "safe_reason_code": "ok",
            "content_type": content_type,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_byte_length": len(body),
            "extracted_text_sha256": hashlib.sha256(extracted_bytes).hexdigest(),
            "extracted_text_byte_length": len(extracted_bytes),
            "summary": extracted[:1_000],
            "conflict_state": "none",
        }

    @staticmethod
    def _failure_record(
        source: dict[str, Any],
        robots_url: str,
        observed: datetime,
        failure: _ReadFailure,
    ) -> dict[str, Any]:
        return {
            "source_record_version": 1,
            "requested_url": source["url"],
            "final_url": None,
            "redirect_chain": failure.redirect_chain,
            "source_class": source["source_class"],
            "source_kind": REAL_SOURCE_KIND_BY_CLASS[source["source_class"]],
            "robots_url": robots_url,
            "observed_at": _format_time(observed),
            "source_date": None,
            "date_state": "unavailable",
            "status": "failed",
            "safe_reason_code": failure.reason,
            "content_type": None,
            "body_sha256": None,
            "body_byte_length": 0,
            "extracted_text_sha256": None,
            "extracted_text_byte_length": 0,
            "summary": "",
            "conflict_state": "none",
        }
