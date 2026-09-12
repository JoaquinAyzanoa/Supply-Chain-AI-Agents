"""Signed events from Odoo to the orchestrator.

Odoo's own "Send Webhook Notification" action posts an unsigned payload with
a one-second timeout and no retry, so the agents' side could not verify it.
This helper posts the same JSON envelope every other producer uses
(``sc_core.schema.events.BaseEvent``), signed with the shared events secret
(``X-SC-Signature: sha256=<hex>``), to ``<director_url>/events``.

The request is sent from a post-commit hook: the orchestrator reads the
record by id right away, and it must see the committed state. Delivery is
best effort and never raises; the orchestrator's daily reconciliation
covers what was missed. Event ids are deterministic (same record, same
change → same id) so a repeated delivery is a duplicate, not a repeat.

Configuration (``Settings > Technical > System Parameters``, or ``just odoo-configure``):

* ``sc_agents.director_url``   e.g. ``http://director:8000`` inside compose
* ``sc_agents.events_secret``  the value of ``SC__EVENTS__SIGNING_SECRET``
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

PARAM_URL = "sc_agents.director_url"
PARAM_SECRET = "sc_agents.events_secret"
TIMEOUT_SECONDS = 5
SOURCE = "odoo"
SCHEMA_VERSION = 1


def canonical(parts):
    """Same canonical JSON as ``sc_core.shared.idempotency`` so ids match across systems."""
    return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def deterministic_id(prefix, *parts, length=16):
    digest = hashlib.sha256(canonical((prefix, *parts)).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def iso_utc(value):
    """Odoo datetimes are naive UTC; the contract wants an aware ISO string."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class ScEventEmitter(models.AbstractModel):
    _name = "sc.event.emitter"
    _description = "Signed event delivery to the orchestrator"

    @api.model
    def sc_emit(self, event_type, case_id, payload, *dedupe_parts):
        """Queue ``payload`` as an event of ``event_type`` for delivery after commit.

        ``dedupe_parts`` identify the change (record id, new state...); the
        event id is derived from them so retries never duplicate work.
        Returns the event id, or ``False`` when the director is not configured.
        """
        params = self.env["ir.config_parameter"].sudo()
        url = (params.get_param(PARAM_URL) or "").strip().rstrip("/")
        secret = (params.get_param(PARAM_SECRET) or "").strip()
        if not url or not secret:
            _logger.info(
                "sc_agents: director url or events secret not set; %s not sent", event_type
            )
            return False
        event_id = deterministic_id("evt", event_type, *dedupe_parts)
        envelope = {
            "type": event_type,
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": SOURCE,
            "case_id": case_id,
            "trace_id": None,
        }
        envelope.update(payload)
        body = json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-SC-Signature": sign(secret, body),
            "X-SC-Event-Type": event_type,
            "X-SC-Event-Id": event_id,
        }
        target = f"{url}/events"

        def deliver():
            try:
                response = requests.post(
                    target, data=body, headers=headers, timeout=TIMEOUT_SECONDS
                )
                response.raise_for_status()
                _logger.info("sc_agents: sent %s %s to %s", event_type, event_id, target)
            except requests.RequestException as exc:
                _logger.warning("sc_agents: could not send %s %s: %s", event_type, event_id, exc)

        self.env.cr.postcommit.add(deliver)
        return event_id
