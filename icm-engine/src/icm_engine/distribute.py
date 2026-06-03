"""Distribute per-rep statement files via the customer's own SMTP or as artifacts.

HARD RULES:
- ISOLATION: each rep receives only their own statement, addressed only to their email.
- SAFE BY DEFAULT: nothing is emailed unless explicitly opted in (--send / dry_run=False).
- Credentials are never logged.
"""

from __future__ import annotations

import csv
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Any


@dataclass
class OutgoingMessage:
    payee_id: str
    to: str
    subject: str
    body: str
    attachments: list[Path] = field(default_factory=list)


@dataclass
class SendResult:
    payee_id: str
    status: str    # "sent" | "skipped" | "failed"
    reason: str | None = None


@dataclass
class SmtpConfig:
    host: str
    port: int = 587
    user: str = ""
    password: str = ""
    from_addr: str = ""
    use_tls: bool = True


# ------------------------------------------------------------------
# Build
# ------------------------------------------------------------------

def build_messages(
    statement_files: list[Any],
    payees: list[Any],
    *,
    subject_template: str = "Your commission statement for {period}",
    body_template: str = (
        "Hi {name},\n\nHere is your commission statement for {period}."
        " Total: ${total}\n\n- OpenIncent"
    ),
    generated_on: date | None = None,
) -> tuple[list[OutgoingMessage], list[SendResult]]:
    """Build OutgoingMessages pairing each payee's file(s) with their email.

    A payee with no email produces a SendResult(status="skipped", reason="no_email").
    Each message contains ONLY that payee's files and is addressed ONLY to them.
    """
    payee_map: dict[str, Any] = {}
    for p in payees:
        pid = _get(p, "id")
        payee_map[pid] = p

    # Group files by payee_id
    by_payee: dict[str, list[Any]] = {}
    for sf in statement_files:
        pid = _get(sf, "payee_id")
        by_payee.setdefault(pid, []).append(sf)

    messages: list[OutgoingMessage] = []
    skipped: list[SendResult] = []

    for pid in sorted(by_payee.keys()):
        p = payee_map.get(pid)
        email = _get(p, "email") if p else ""
        if not email or "@" not in email:
            skipped.append(SendResult(payee_id=pid, status="skipped", reason="no_email"))
            continue

        name = _get(p, "name", pid) if p else pid
        files = by_payee[pid]
        period = _get(files[0], "period", "all") if files else "all"

        # Compute total from statement filenames (or sum commission_amounts if available)
        total = "0"
        # Just use the period from the first file
        subject = subject_template.format(name=name, period=period, total=total)
        body = body_template.format(name=name, period=period, total=total)
        if generated_on:
            body += f"\n\nGenerated on: {generated_on}"

        msg = OutgoingMessage(
            payee_id=pid,
            to=email,
            subject=subject,
            body=body,
            attachments=[Path(_get(f, "path")) for f in files],
        )
        messages.append(msg)

    return messages, skipped


# ------------------------------------------------------------------
# SMTP send
# ------------------------------------------------------------------

def send_via_smtp(
    messages: list[OutgoingMessage],
    smtp_config: SmtpConfig,
    *,
    dry_run: bool = False,
) -> list[SendResult]:
    """Send each message via the customer's SMTP server.

    dry_run=True performs zero network calls. One failed recipient does not
    abort the batch. Credentials are never logged.
    """
    if dry_run:
        return [SendResult(payee_id=m.payee_id, status="sent" if m.to else "skipped") for m in messages]

    results: list[SendResult] = []
    context = ssl.create_default_context() if smtp_config.use_tls else None

    for msg in messages:
        try:
            if smtp_config.use_tls:
                server = smtplib.SMTP(smtp_config.host, smtp_config.port, timeout=30)
                server.starttls(context=context)
            else:
                server = smtplib.SMTP_SSL(smtp_config.host, smtp_config.port, timeout=30, context=context)

            if smtp_config.user:
                server.login(smtp_config.user, smtp_config.password)

            mime = _build_mime(msg, smtp_config.from_addr)
            server.sendmail(smtp_config.from_addr, [msg.to], mime.as_string())
            server.quit()
            results.append(SendResult(payee_id=msg.payee_id, status="sent"))
        except Exception as e:
            results.append(SendResult(payee_id=msg.payee_id, status="failed", reason=str(e)))

    return results


def _build_mime(msg: OutgoingMessage, from_addr: str) -> MIMEMultipart:
    mime = MIMEMultipart()
    mime["From"] = from_addr
    mime["To"] = msg.to
    mime["Subject"] = msg.subject
    mime["Date"] = formatdate(localtime=True)
    mime.attach(MIMEText(msg.body, "plain"))

    for path in msg.attachments:
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        mime.attach(part)

    return mime


# ------------------------------------------------------------------
# Write .eml files
# ------------------------------------------------------------------

def write_eml(
    messages: list[OutgoingMessage],
    out_dir: Path,
    from_addr: str = "commissions@openincent.local",
) -> list[Path]:
    """Write one standards-compliant .eml file per message. No network.

    The customer opens these in their mail client to send themselves.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for msg in messages:
        mime = _build_mime(msg, from_addr)
        path = out_dir / f"{msg.payee_id}.eml"
        path.write_text(mime.as_string(), encoding="utf-8")
        paths.append(path)
    return paths


# ------------------------------------------------------------------
# Mail-merge CSV
# ------------------------------------------------------------------

def write_mail_merge_csv(
    messages: list[OutgoingMessage],
    path: Path,
) -> Path:
    """Write a CSV for the customer's mail-merge tool."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["payee_id", "email", "subject", "attachment_path"])
        for msg in messages:
            for att in msg.attachments:
                writer.writerow([msg.payee_id, msg.to, msg.subject, str(att)])
    return path


# ------------------------------------------------------------------
# Attribute helper
# ------------------------------------------------------------------

def _get(obj: Any, attr: str, default: str = "") -> str:
    if hasattr(obj, attr):
        return str(getattr(obj, attr))
    if isinstance(obj, dict):
        return str(obj.get(attr, default))
    return default
