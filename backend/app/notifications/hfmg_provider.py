"""Email provider backed by the HFMG internal mail API (see app/services/hfmg_mail_service.py).

Selected with EMAIL_PROVIDER=hfmg_internal. The rest of the app hands providers
plain-text bodies; the internal API takes HTML, so the text is escaped and
line breaks preserved here. That escaping is what stops ticket text (caller
input) from being interpreted as markup in the recipient's mail client.
"""

import html

from app.core.config import get_settings
from app.services import hfmg_mail_service


def _plain_to_html(text: str) -> str:
    escaped = html.escape(text.replace("\r\n", "\n"))
    body = escaped.replace("\n", "<br>\n")
    return f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">{body}</div>'


def _single_line(text: str) -> str:
    return " ".join(text.split())


class HfmgInternalMailProvider:
    name = "hfmg_internal"

    @property
    def is_configured(self) -> bool:
        return hfmg_mail_service.is_configured()

    async def send(
        self,
        *,
        to: str,
        from_email: str,
        subject: str,
        body: str,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> bool:
        # DEFAULT_FROM_EMAIL is the mailbox the internal API is authorized to
        # send as; EMAIL_FROM is a SendGrid-era default, so it loses to it.
        sender = get_settings().default_from_email or from_email
        return await hfmg_mail_service.send_email(
            to=[to],
            subject=_single_line(subject),
            body_html=_plain_to_html(body),
            from_email=sender,
            timeout=timeout,
            max_retries=max_retries,
        )
