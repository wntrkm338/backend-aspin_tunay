import smtplib
import ssl

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError

from backend.email_config import email_configuration_errors


class Command(BaseCommand):
    help = 'Validate email settings; --connect verifies Gmail OAuth or SMTP login without sending email.'

    def add_arguments(self, parser):
        parser.add_argument('--connect', action='store_true')

    def handle(self, *args, **options):
        errors = email_configuration_errors()
        if settings.EMAIL_BACKEND == 'backend.gmail_api.EmailBackend':
            if errors:
                raise CommandError(' '.join(errors))
            self.stdout.write('Gmail HTTPS configuration is complete.')
            if options['connect']:
                from backend.gmail_api import GmailOAuthError
                try:
                    with get_connection(fail_silently=False):
                        pass  # Refreshes OAuth credentials; never sends mail.
                except GmailOAuthError as exc:
                    raise CommandError(str(exc)) from None
                self.stdout.write(self.style.SUCCESS('Gmail OAuth refresh succeeded. No emails were sent; sending permission and inbox delivery were not tested.'))
            else:
                self.stdout.write('Run with --connect to verify Gmail OAuth without sending email.')
            return
        if settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
            errors.append('This check requires the SMTP backend.')
        if errors:
            raise CommandError(' '.join(errors))
        self.stdout.write('SMTP configuration is complete.')
        if not options['connect']:
            self.stdout.write('Run with --connect to verify SMTP authentication without sending email.')
            return
        try:
            with get_connection(fail_silently=False):
                pass  # Opens, authenticates, and closes; never processes the outbox.
        except smtplib.SMTPAuthenticationError:
            raise CommandError('SMTP authentication failed. Check the Gmail address and Google App Password, with 2-Step Verification enabled.') from None
        except ssl.SSLCertVerificationError:
            raise CommandError('Gmail TLS certificate verification failed. Check the system clock and trusted root certificates; do not disable TLS verification.') from None
        except (smtplib.SMTPException, OSError) as exc:
            # Class and numeric OS code help diagnose networking without logging
            # provider responses, account addresses, or credentials.
            code = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
            diagnostic = type(exc).__name__ + (f' (OS code {code})' if code else '')
            raise CommandError(f'Could not establish the SMTP connection: {diagnostic}. Check the host, port, TLS settings, and outbound network access.') from None
        self.stdout.write(self.style.SUCCESS('SMTP login succeeded. No emails were sent.'))
