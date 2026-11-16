from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from l03_domain.rewrite import (
    apply_body_rules_report,
    apply_header_rules,
    diff_body,
    validate_rules,
)
from l04_repo import orm
from l05_service.llm.gateway import build_headers, json_dict

# Headers that describe the transport framing, not application data. A rule
# may not override them or the response would be truncated or mis-framed.
UNSAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "content-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


@dataclass(slots=True)
class PreparedRequest:
    """The request after header/body rules, before the gateway façade fields."""

    headers: dict[str, str]
    body: dict[str, Any]
    applied: bool = False
    skipped: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    header_changes: list[str] = field(default_factory=list)
    rule_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def diff(self) -> dict[str, Any]:
        return {
            "request_headers": self.header_changes,
            "request_body": self.changes,
        }


@dataclass(slots=True)
class PreparedResponse:
    """The upstream response after header/body rules.

    ``body`` is always safe to send: on any rule failure the original payload
    and headers are kept, ``skipped`` is ``"error"`` and ``error`` explains why.
    For a streaming response only headers are rewritten and ``skipped`` is
    ``"stream"``.
    """

    headers: dict[str, str]
    body: dict[str, Any]
    applied: bool = False
    skipped: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    header_changes: list[str] = field(default_factory=list)
    error: str = ""
    rule_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def diff(self) -> dict[str, Any]:
        return {
            "response_headers": self.header_changes,
            "response_body": self.changes,
        }


def _header_snapshot(headers: dict[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _safe_response_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in UNSAFE_RESPONSE_HEADERS
    }


def _changed_headers(before: dict[str, str], after: dict[str, str]) -> list[str]:
    before_snapshot = _header_snapshot(before)
    after_snapshot = _header_snapshot(after)
    return sorted(
        name
        for name in set(before_snapshot) | set(after_snapshot)
        if before_snapshot.get(name) != after_snapshot.get(name)
    )


def preview_rules(
    channel: orm.ChannelORM,
    *,
    section: str,
    rules_override: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one rule section against a sample without touching the channel.

    Used by the editor's dry-run panel so authors can see the exact diff that
    the gateway would produce before saving a draft.
    """
    rules = (
        copy.deepcopy(rules_override)
        if rules_override is not None
        else json_dict(channel.rewrite_rules_json)
    )
    warnings = validate_rules(rules)
    sample_body = copy.deepcopy(body) if body is not None else {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.7,
        "stream": False,
    }
    sample_headers = dict(headers) if headers is not None else {
        "content-type": "application/json",
        "x-client": "slipstream",
    }
    requested_model = str(sample_body.get("model") or "preview-model")
    variables = {
        "model": requested_model,
        "original_model": requested_model,
        "upstream_model": requested_model,
    }

    if section.endswith("_headers"):
        before = (
            _safe_response_headers(sample_headers)
            if section == "response_headers"
            else dict(sample_headers)
        )
        after = apply_header_rules(
            before,
            rules.get(section) or [],
            variables,
            sample_body,
            scope="response" if section == "response_headers" else "request",
        )
        before_snapshot = {name.lower(): value for name, value in before.items()}
        after_snapshot = {name.lower(): value for name, value in after.items()}
        diff = [
            {
                "path": name,
                "before": before_snapshot.get(name, "<missing>"),
                "after": after_snapshot.get(name, "<missing>"),
            }
            for name in sorted(set(before_snapshot) | set(after_snapshot))
            if before_snapshot.get(name) != after_snapshot.get(name)
        ]
        return {
            "section": section,
            "before": before,
            "after": after,
            "diff": diff,
            "warnings": warnings,
            "skipped": "",
        }

    after, rule_results = apply_body_rules_report(
        sample_body, rules.get(section) or [], variables
    )
    skipped = "stream" if section == "response_body" and sample_body.get("stream") else ""
    if not skipped and rule_results:
        skipped = str(rule_results[0].get("reason") or "")
    return {
        "section": section,
        "before": sample_body,
        "after": after,
        "diff": [] if skipped == "stream" else diff_body(sample_body, after),
        "warnings": warnings,
        "skipped": skipped,
        "rule_results": rule_results,
    }


def prepare_request(
    channel: orm.ChannelORM,
    body: dict[str, Any],
    *,
    upstream_model: str,
    requested_model: str,
    request_id: str,
    client_headers: dict[str, str] | None = None,
) -> PreparedRequest:
    """Apply the channel's request rules once, before the façade rewrites fields.

    Credential headers are built by :func:`build_headers` and protected by the
    domain layer, so no stored rule can replace the channel key.
    """
    rules = json_dict(channel.rewrite_rules_json)
    variables = {
        "model": requested_model,
        "original_model": requested_model,
        "upstream_model": upstream_model,
    }
    base_headers = build_headers(channel, request_id, client_headers=client_headers)
    request_header_rules = rules.get("request_headers") or []
    request_body_rules = rules.get("request_body") or []
    headers = (
        apply_header_rules(base_headers, request_header_rules, variables, body)
        if request_header_rules
        else base_headers
    )
    rewritten, rule_results = (
        apply_body_rules_report(body, request_body_rules, variables)
        if request_body_rules
        else (body, [])
    )
    # The façade owns these fields: a body rule may edit message content but can
    # never change which model is called or turn a JSON request into an SSE one.
    for guarded in ("model", "stream"):
        if guarded in body:
            rewritten[guarded] = body[guarded]
        else:
            rewritten.pop(guarded, None)

    before = _header_snapshot(base_headers)
    after = _header_snapshot(headers)
    header_changes = sorted(
        name
        for name in set(before) | set(after)
        if before.get(name) != after.get(name)
    )
    # Without body rules the rewrite is the original dict (see above), so the
    # diff is empty by construction and walking both trees would be wasted work.
    changes = diff_body(body, rewritten) if request_body_rules else []
    return PreparedRequest(
        headers=headers,
        body=rewritten,
        applied=bool(header_changes or changes),
        skipped=str(rule_results[0].get("reason") or "") if rule_results else "",
        changes=changes,
        header_changes=header_changes,
        rule_results=rule_results,
    )


def prepare_response(
    channel: orm.ChannelORM,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    upstream_model: str,
    requested_model: str,
    is_stream: bool = False,
) -> PreparedResponse:
    """Apply response rules after a successful upstream call.

    Response bodies are only rewritten for non-stream responses. Streaming
    responses keep their body untouched and report ``rewrite_skipped=stream``.
    Every failure is contained here: the original response wins so a stored
    rule can never break a working request.
    """
    rules = json_dict(channel.rewrite_rules_json)
    variables = {
        "model": upstream_model,
        "upstream_model": upstream_model,
        "original_model": requested_model,
    }
    base_headers = _safe_response_headers(headers)
    response_header_rules = rules.get("response_headers") or []
    response_body_rules = rules.get("response_body") or []
    original_body = copy.deepcopy(body) if response_body_rules else body
    try:
        rewritten_headers = (
            apply_header_rules(
                base_headers,
                response_header_rules,
                variables,
                body,
                scope="response",
            )
            if response_header_rules
            else base_headers
        )
        header_changes = _changed_headers(base_headers, rewritten_headers)
        if is_stream:
            return PreparedResponse(
                headers=_safe_response_headers(rewritten_headers),
                body=body,
                applied=bool(header_changes),
                skipped="stream",
                header_changes=header_changes,
            )
        rewritten, rule_results = (
            apply_body_rules_report(body, response_body_rules, variables)
            if response_body_rules
            else (body, [])
        )
        changes = diff_body(body, rewritten) if response_body_rules else []
        body_skipped = str(rule_results[0].get("reason") or "") if rule_results else ""
        return PreparedResponse(
            headers=_safe_response_headers(rewritten_headers),
            body=rewritten,
            applied=bool(header_changes or changes),
            skipped=body_skipped,
            changes=changes,
            header_changes=header_changes,
            rule_results=rule_results,
        )
    except Exception as error:  # noqa: BLE001 - never break the upstream response
        return PreparedResponse(
            headers=base_headers,
            body=original_body,
            skipped="error",
            error=f"{type(error).__name__}: {error}",
        )


def rewrite_log(
    request: PreparedRequest | None,
    response: PreparedResponse | None,
) -> tuple[bool, str, str]:
    """Build the ``(applied, skipped, diff_json)`` triple stored on a log."""
    applied = bool(
        (request is not None and request.applied)
        or (response is not None and response.applied)
    )
    skipped = response.skipped if response is not None else ""
    if not skipped and request is not None:
        skipped = request.skipped
    diff: dict[str, Any] = {}
    if request is not None and request.applied:
        diff.update(request.diff)
    if response is not None and (response.applied or response.error):
        diff.update(response.diff)
    if response is not None and response.error:
        diff["error"] = response.error
    return applied, skipped, json.dumps(diff, ensure_ascii=False) if diff else "{}"


def diff_json(prepared: PreparedRequest | None) -> str:
    """Backward-compatible request-only diff helper."""
    if prepared is None or not prepared.applied:
        return "{}"
    return json.dumps(prepared.diff, ensure_ascii=False)
