import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from admin_api.models import AssessmentEmail
from admin_api.scheduling import email_delivery_configured, process_due_emails
from backend.email_config import email_configuration_errors


class Command(BaseCommand):
    help = 'Send due assessment emails. Run every minute, or use --loop under a process supervisor.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
        parser.add_argument('--retry-email', type=int, help='Retry a failed/stuck email ONLY after confirming with the mail provider that it was not delivered.')
        parser.add_argument('--retry-connections', action='store_true', help='Retry pending messages that failed before SMTP submission, for example after fixing credentials.')

    def handle(self, *args, **options):
        if not email_delivery_configured():
            raise CommandError(' '.join(email_configuration_errors()) + ' Emails remain queued.')
        if options['retry_connections']:
            AssessmentEmail.objects.filter(status='pending', last_error__startswith='Connection failed:').update(due_at=timezone.now())
        if options['retry_email']:
            if not AssessmentEmail.objects.filter(pk=options['retry_email'], status__in=['failed', 'sending']).update(status='pending', due_at=timezone.now()):
                raise CommandError('That email does not exist or is not failed/stuck. Sent emails cannot be retried.')
        while True:
            close_old_connections()
            try:
                count = process_due_emails()
            except Exception as exc:
                if not options['loop']:
                    raise
                self.stderr.write(f'Worker error: {exc}. Retrying on the next poll.')
                count = 0
            if count:
                self.stdout.write(f'Sent {count} assessment email(s).')
            if not options['loop']:
                break
            time.sleep(max(1, options['interval']))
