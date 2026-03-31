"""Webhook and Slack alerting — zero external dependencies (uses urllib)."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from orchestrator.monitoring.config import WebhookConfig

if TYPE_CHECKING:
    from orchestrator.db.repositories.alerts import AlertRepository

logger = logging.getLogger(__name__)


class AlertManager:
    """Dispatches alerts to configured webhook endpoints."""

    def __init__(
        self,
        webhooks: list[WebhookConfig],
        workspace: Path | None = None,
        alert_repo: "AlertRepository | None" = None,
    ) -> None:
        self._webhooks = webhooks
        self._alert_log: Path | None = None
        self._alert_repo = alert_repo
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="alert")
        if workspace:
            self._alert_log = workspace / "alerts.jsonl"

    def shutdown(self) -> None:
        """Shut down the thread pool, waiting for pending webhook deliveries."""
        self._executor.shutdown(wait=True)

    def send_alert(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }

        # Persist to DB (best-effort)
        if self._alert_repo is not None:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                coro = self._alert_repo.append(
                    alert_type=event_type,
                    message=payload.get("message", event_type),
                    severity=payload.get("severity", "info"),
                    run_id=payload.get("run_id"),
                    data=record,
                )
                if loop.is_running():
                    asyncio.ensure_future(coro)
                else:
                    loop.run_until_complete(coro)
            except Exception as exc:
                logger.debug("DB alert append failed: %s", exc)

        # Persist to alerts.jsonl sidecar for dashboard
        if self._alert_log:
            try:
                with open(self._alert_log, "a") as f:
                    f.write(json.dumps(record, default=str) + "\n")
            except OSError:
                pass

        # Dispatch to webhooks via bounded thread pool
        for webhook in self._webhooks:
            if event_type not in webhook.events:
                continue
            self._executor.submit(self._post_webhook, webhook.url, event_type, record)

    def _post_webhook(self, url: str, event_type: str, payload: dict[str, Any]) -> None:
        try:
            body = self._format_payload(url, event_type, payload)
            req = Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    redacted = url[:20] + "..." if len(url) > 20 else url
                    logger.warning(f"Webhook {redacted} returned {resp.status}")
        except (URLError, OSError) as e:
            redacted = url[:20] + "..." if len(url) > 20 else url
            logger.warning(f"Webhook delivery failed for {redacted}: {e}")

    def _format_payload(
        self, url: str, event_type: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        if "hooks.slack.com" in url:
            return self._format_slack(event_type, payload)
        return payload

    def _format_slack(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        emoji = {
            "run_complete": ":white_check_mark:",
            "phase_failed": ":x:",
            "budget_warning": ":warning:",
            "budget_exceeded": ":rotating_light:",
            "crash_restart": ":recycle:",
        }.get(event_type, ":bell:")

        title = f"{emoji} Orchestrator: {event_type.replace('_', ' ').title()}"

        fields = []
        for key in ("run_id", "workflow_type", "total_cost_usd", "error", "step"):
            if key in payload:
                fields.append(f"*{key}:* {payload[key]}")

        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(fields) if fields else "No details.",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_Timestamp: {payload.get('ts', 'N/A')}_",
                        }
                    ],
                },
            ]
        }
