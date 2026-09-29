"""Wires core.reflection's pure cycle to email notification — the
infrastructure side of the nightly Sleep Cycle. Email specifics
(Gmail SMTP) live in adapters/notifications/email.py; this module's only
job is deciding what the notification says and when to send it. The HTTP
trigger itself is a couple of lines in interfaces/api/main.py.
"""

import html
import logging
from datetime import datetime
from urllib.parse import quote

from adapters.notifications.email import EmailConfig, is_configured, send_email
from core.reflection.base import DEFAULT_PROVIDER_ORDER, run_reflection_cycle
from interfaces.api.runtime import ApiRuntime

logger = logging.getLogger(__name__)


def _build_notification_html(narrative: str, action_items: list[dict], dashboard_url: str) -> str:
    today_str = datetime.now().strftime("%B %d, %Y")
    parts = [
        f"<h2>iV Sleep Cycle — {today_str}</h2>",
        f"<p>{html.escape(narrative).replace(chr(10), '<br>')}</p>",
    ]
    if action_items:
        parts.append(
            f'<p><a href="{dashboard_url}">Review {len(action_items)} '
            f"proposed action item(s) →</a></p><hr>"
        )
        for item in action_items:
            parts.append(f"<p><b>{html.escape(item['title'])}</b><br>{html.escape(item['description'])}</p>")
    else:
        parts.append("<p>No action items proposed today.</p>")
    return "".join(parts)


def run_sleep_cycle(
    runtime: ApiRuntime,
    *,
    provider_order: list[str] | None = None,
    email_config: EmailConfig,
    dashboard_base_url: str,
    api_access_secret: str | None,
) -> dict:
    """Best-effort failure notice: if the reflection cycle itself fails
    (e.g. every configured model provider is unavailable), send a plain
    failure email instead of letting the nightly job fail silently —
    same behavior as backend/app/sleep_cycle.py, just provider-agnostic
    now instead of hardcoded to a Gemini-then-Mistral-then-Groq order.
    """
    try:
        result = run_reflection_cycle(
            conversations=runtime.conversations,
            audit=runtime.audit,
            memory=runtime.memory,
            approvals=runtime.approvals,
            models=runtime.models,
            provider_order=provider_order or DEFAULT_PROVIDER_ORDER,
        )
    except Exception as exc:  # noqa: BLE001 - this is the last line of defense for a nightly job
        logger.exception("Sleep Cycle failed")
        if is_configured(email_config):
            send_email(
                config=email_config,
                subject="iV Sleep Cycle — FAILED",
                html_body=f"<p>iV Sleep Cycle failed tonight: {html.escape(str(exc))}</p>",
            )
        return {"status": "error", "error": str(exc)}

    dashboard_url = f"{dashboard_base_url}/approvals/pending?secret={quote(api_access_secret or '')}"
    if "localhost" in dashboard_base_url or "127.0.0.1" in dashboard_base_url:
        # AGENT_IV_BASE_URL defaults to localhost (core/configuration/
        # ports.py). A link built from that default is only reachable
        # from the machine running the API -- for the documented "check
        # from your phone over Tailscale" workflow, that link is dead on
        # arrival for every device except the server itself. This can't
        # be fixed here (the real address is per-install), but it should
        # not fail silently: it's what turns "the approval email arrived"
        # into "the approval email is useless" for a mobile-first setup.
        logger.warning(
            "Sleep Cycle dashboard link uses %s, which is only reachable from the "
            "machine running iV. Set AGENT_IV_BASE_URL in backend/.env to your "
            "Tailscale hostname or IP (e.g. http://your-machine.tailnet-name.ts.net:8024) "
            "so the emailed approval link works from your phone.",
            dashboard_base_url,
        )
    if is_configured(email_config):
        send_email(
            config=email_config,
            subject=f"iV Sleep Cycle — {datetime.now().strftime('%B %d, %Y')}",
            html_body=_build_notification_html(result.narrative, result.action_items, dashboard_url),
        )
    else:
        logger.info("Sleep Cycle: email not configured, skipping notification send.")

    return {"status": "ok", "action_items": len(result.action_items)}
