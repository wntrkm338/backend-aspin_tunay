"""Check delivery configuration without connecting or exposing credentials."""
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def email_configuration_errors():
    if settings.EMAIL_BACKEND in {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
    }:
        return ["Select the SMTP email backend to deliver assessment emails."]
    if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        return []  # Allows the in-memory test backend and other delivery providers.
    errors = []
    host = (settings.EMAIL_HOST or "").strip().lower()
    if not host:
        errors.append("Set EMAIL_HOST in the backend environment.")
    if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
        errors.append("Enable either EMAIL_USE_TLS or EMAIL_USE_SSL, not both.")
    if host == "smtp.gmail.com":
        try:
            validate_email(settings.EMAIL_HOST_USER)
        except ValidationError:
            errors.append("Set EMAIL_HOST_USER to the sending Gmail or Google Workspace email address.")
        if not settings.EMAIL_HOST_PASSWORD.strip():
            errors.append("Set EMAIL_HOST_PASSWORD to a Google App Password in backend-main/.env.")
        if not ((settings.EMAIL_PORT == 587 and settings.EMAIL_USE_TLS and not settings.EMAIL_USE_SSL)
                or (settings.EMAIL_PORT == 465 and settings.EMAIL_USE_SSL and not settings.EMAIL_USE_TLS)):
            errors.append("Gmail requires port 587 with TLS, or port 465 with SSL.")
    sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    try:
        validate_email(sender)
    except ValidationError:
        errors.append("Set DEFAULT_FROM_EMAIL to a valid sender, or leave it blank to use EMAIL_HOST_USER.")
    if sender.endswith(".local"):
        errors.append("Replace the local placeholder sender, or leave DEFAULT_FROM_EMAIL blank to use EMAIL_HOST_USER.")
    return errors
