"""What an approver reads in Odoo: the To-Do note behind an approval request.

One HTML fragment per approval kind, written for a buyer, not a developer:
the email as it will go out, the order changes as a table, the plan's
totals and exceptions, an escalation's reason and history. Identifiers
that only machines need (draft ids, case ids, steps) never appear; links
to the Outlook draft and to the Control Tower do.
"""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING, Any

from sc_core.i18n import MESSAGES, Language, t

if TYPE_CHECKING:
    from sc_core.graph.approval import ApprovalRequest

_HIDDEN_KEYS = frozenset(
    {
        "step",
        "draft_id",
        "web_link",
        "html_body",
        "run_id",
        "case_id",
        "thread_id",
        "classification",
        "details",
        "history",
        "trace_url",
    }
)
_TAG = re.compile(r"<[^>]+>")


def render_note(
    req: ApprovalRequest, *, language: Language = "en", control_tower_url: str | None = None
) -> str:
    """The activity note for ``req``, in ``language``, with the links a person can open."""
    body = {
        "send_email": _email,
        "po_change": _changes,
        "planning_run": _plan,
        "escalation": _escalation,
    }.get(req.kind, _generic)(req.payload, language)
    return f"<p>{escape(req.summary)}</p>{body}{_links(req, language, control_tower_url)}"


def _email(payload: dict[str, Any], lang: Language) -> str:
    to = ", ".join(str(a) for a in payload.get("to") or [])
    preview = _plain(str(payload.get("html_body") or ""), limit=600)
    attachments = payload.get("attachments") or []
    parts = [
        f"<p><b>{t('note.to', lang)}:</b> {escape(to)}<br/>"
        f"<b>{t('note.subject', lang)}:</b> {escape(str(payload.get('subject') or ''))}</p>"
    ]
    if preview:
        parts.append(f"<blockquote>{escape(preview)}</blockquote>")
    if attachments:
        names = ", ".join(escape(str(a)) for a in attachments)
        parts.append(f"<p><b>{t('note.attachments', lang)}:</b> {names}</p>")
    return "".join(parts)


def _changes(payload: dict[str, Any], lang: Language) -> str:
    changes = payload.get("changes") or []
    if not changes:
        return ""
    rows = []
    for change in changes:
        review = ""
        if change.get("needs_review"):
            reason = escape(str(change.get("review_reason") or ""))
            review = f" <i>({t('note.needs_review', lang)}{': ' + reason if reason else ''})</i>"
        confidence = change.get("confidence")
        pct = f" · {round(float(confidence) * 100)}%" if confidence is not None else ""
        before = change.get("before")
        rows.append(
            "<tr>"
            f"<td>{escape(str(change.get('product') or ''))}</td>"
            f"<td>{_field_label(str(change.get('field')), lang)}</td>"
            f"<td>{escape(str(before if before is not None else '—'))}</td>"
            f"<td><b>{escape(str(change.get('after') or ''))}</b>{review}</td>"
            f"<td>{escape(str(change.get('source') or ''))}{pct}</td>"
            "</tr>"
        )
    head = (
        f"<tr><th>{t('note.product', lang)}</th><th>{t('note.change', lang)}</th>"
        f"<th>{t('note.before', lang)}</th><th>{t('note.after', lang)}</th>"
        f"<th>{t('note.source', lang)}</th></tr>"
    )
    return (
        f'<table class="table table-sm"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _plan(payload: dict[str, Any], lang: Language) -> str:
    totals = payload.get("totals") or {}
    summary = str(payload.get("summary") or "")
    parts = []
    if summary:
        parts.append(f"<p>{escape(summary)}</p>")
    parts.append(
        "<p>"
        + t(
            "note.plan_totals",
            lang,
            lines=int(totals.get("lines", 0)),
            rfqs=int(totals.get("rfq_lines", 0)),
            rules=int(totals.get("rules_changed", 0)),
            exceptions=int(totals.get("exceptions", 0)),
        )
        + "</p>"
    )
    exceptions = payload.get("exceptions") or []
    if exceptions:
        items = "".join(
            f"<li><b>{escape(str(e.get('product_ref') or ''))}</b> · "
            f"{escape(str(e.get('exception') or ''))}"
            f"{' — ' + escape(str(e.get('explanation'))) if e.get('explanation') else ''}</li>"
            for e in exceptions[:15]
        )
        parts.append(f"<p><b>{t('note.exceptions', lang)}:</b></p><ul>{items}</ul>")
    return "".join(parts)


def _escalation(payload: dict[str, Any], lang: Language) -> str:
    parts = []
    if payload.get("reason"):
        parts.append(f"<p>{escape(str(payload['reason']))}</p>")
    history = payload.get("history") or []
    if history:
        items = "".join(f"<li>{escape(str(line))}</li>" for line in history[-8:])
        parts.append(f"<p><b>{t('note.history', lang)}:</b></p><ul>{items}</ul>")
    return "".join(parts)


def _generic(payload: dict[str, Any], lang: Language) -> str:
    del lang
    items = "".join(
        f"<li><b>{escape(str(k))}</b>: {escape(_short(v))}</li>"
        for k, v in payload.items()
        if k not in _HIDDEN_KEYS
    )
    return f"<ul>{items}</ul>" if items else ""


def _links(req: ApprovalRequest, lang: Language, control_tower_url: str | None) -> str:
    links = []
    web_link = req.payload.get("web_link")
    if isinstance(web_link, str) and web_link:
        links.append(f'<a href="{escape(web_link)}">{t("common.open_outlook", lang)}</a>')
    if control_tower_url:
        base = control_tower_url.rstrip("/")
        target = (
            f"{base}/planning/{escape(str(req.payload['run_id']))}"
            if req.kind == "planning_run" and req.payload.get("run_id")
            else base
        )
        links.append(f'<a href="{target}">{t("common.open_control_tower", lang)}</a>')
    return f"<p>{' · '.join(links)}</p>" if links else ""


_BLOCK_END = re.compile(r"</(?:p|div|li|tr|h\d|blockquote)>|<br\s*/?>", re.IGNORECASE)


def _plain(html: str, *, limit: int) -> str:
    """The email as text: block ends become spaces, inline tags vanish."""
    text = re.sub(r"\s+", " ", _TAG.sub("", _BLOCK_END.sub(" ", html))).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _field_label(field: str, lang: Language) -> str:
    key = f"note.field.{field}"
    return t(key, lang) if key in MESSAGES else field.replace("_", " ")


def _short(value: Any, limit: int = 300) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
