"""Failure notifications: generic webhook (Mattermost / Slack / Rocket.Chat compatible) and SMTP e-mail."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import requests

from datahub_pipelines.settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailureNotice:
    job_name: str
    run_id: str
    error: str
    env: str
    public_url: str

    @property
    def run_url(self) -> str:
        return f"{self.public_url.rstrip('/')}/dagster/runs/{self.run_id}"

    @property
    def title(self) -> str:
        return f"[Data Hub {self.env.upper()}] Échec du pipeline {self.job_name}"

    def as_text(self) -> str:
        error = (self.error or "Erreur inconnue").strip()
        if len(error) > 1500:
            error = error[:1500] + "…"
        return (
            f"**{self.title}**\n"
            f"Run : `{self.run_id}`\n"
            f"Erreur : {error}\n"
            f"Détails : {self.run_url}\n"
            f"Data Hub : {self.public_url.rstrip('/')}/"
        )


def send_webhook(url: str, notice: FailureNotice, timeout: float = 10.0) -> bool:
    try:
        response = requests.post(url, json={"text": notice.as_text()}, timeout=timeout)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:  # a failing notification must never break the sensor
        log.warning("Webhook notification failed: %s", exc)
        return False


def send_email(settings: Settings, notice: FailureNotice) -> bool:
    msg = EmailMessage()
    msg["Subject"] = notice.title
    msg["From"] = settings.smtp_from
    msg["To"] = settings.smtp_to
    msg.set_content(notice.as_text())
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("E-mail notification failed: %s", exc)
        return False


def notify(settings: Settings, notice: FailureNotice) -> dict[str, bool]:
    """Send the notice on every configured channel. Returns {channel: delivered}."""
    results: dict[str, bool] = {}
    if settings.notify_webhook_url:
        results["webhook"] = send_webhook(settings.notify_webhook_url, notice)
    if settings.smtp_host and settings.smtp_to:
        results["email"] = send_email(settings, notice)
    return results
