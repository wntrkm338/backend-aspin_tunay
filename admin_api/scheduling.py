"""Transactional assessment scheduling and a durable, deduplicated email outbox."""
import calendar
import hashlib
import logging
import smtplib
import ssl
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from backend.email_config import email_configuration_errors
from backend.email_templates import render_email

from .models import AdoptionApplication, AssessmentEmail, RemoteAssessment

logger = logging.getLogger(__name__)
INACTIVE = {'Cancelled', 'Declined', 'Reschedule Declined', 'Assessment 1 Rejected',
            'Post-Adoption Rejected', 'Done', 'Assessment 1 Done', 'Assessment 1 Passed', 'Monitoring Passed'}
CANCELLED = {'Cancelled', 'Declined', 'Reschedule Declined', 'Assessment 1 Rejected', 'Post-Adoption Rejected'}
SCHEDULE_FIELDS = ('date', 'time', 'time_zone', 'link')


def email_delivery_configured():
    return not email_configuration_errors()


def calendar_month_later(value):
    year = value.year + (value.month == 12)
    month = value.month % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def scheduled_datetime(date, time, time_zone):
    try:
        zone = ZoneInfo(time_zone)
        wall_time = datetime.strptime(str(time), '%H:%M').time()
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError({'time': 'Use HH:MM and a valid IANA time zone, such as Asia/Manila.'})
    local = datetime.combine(date, wall_time).replace(tzinfo=zone)
    # zoneinfo permits nonexistent local times; explicitly reject DST gaps.
    if local.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValidationError({'time': 'This local time does not exist due to daylight saving. Choose another time.'})
    return local


def applicant_email(application):
    recipient = (application.email or '').strip()
    try:
        validate_email(recipient)
    except DjangoValidationError:
        raise ValidationError({'appId': 'The adoption application must have a valid applicant email address. Update the application email before scheduling.'})
    return recipient


def ensure_link(assessment):
    if not assessment.link:
        assessment.jitsiRoom = f'Aspin_{uuid.uuid4().hex}'
        assessment.link = f'https://meet.jit.si/{assessment.jitsiRoom}'
    if assessment.status == 'Pending Link':
        assessment.status = 'Scheduled'


def email_body(application, heading, assessments, reminder=None):
    lines = [f'Hello {application.fullName},', '', heading, '',
             f'Adoption application #{application.pk}']
    for item in assessments:
        label = 'First assessment' if item.phase == 'first' else 'Post-adoption assessment'
        scheduled = scheduled_datetime(item.date, item.time, item.time_zone)
        lines.extend(['', f'{label}: {scheduled:%A, %B %d, %Y at %I:%M %p} '
                      f'({item.time_zone}, UTC{scheduled:%z})', f'Status: {item.status}'])
        if item.status in CANCELLED:
            lines.append('This assessment is cancelled. Do not join this appointment.')
        else:
            lines.append(f'Access link: {item.link}')
    lines.extend(['', 'Instructions:',
                  '- Join 5 minutes early using the access link for that assessment.',
                  '- Use a stable internet connection and enable your camera and microphone.',
                  '- For the first assessment, have your identification ready and be prepared to show the pet living area.',
                  '- For the post-adoption assessment, have your adopted pet present and share updates about their care.',
                  '- If you cannot attend, request a reschedule in your account.', '',
                  f'View your assessments: {settings.FRONTEND_BASE_URL.rstrip("/")}/user/assessments', '',
                  'Aspin Tunay at Tapat'])
    return '\n'.join(lines)


def queue_messages(application, changed, primary, kind):
    recipient = applicant_email(application)
    pair = list(RemoteAssessment.objects.filter(application=application, phase__in=['first', 'post_adoption']).order_by('phase'))
    versions = {a.pk: a.revision for a in pair}
    # A newer update supersedes unsent schedule emails, including their snapshots.
    AssessmentEmail.objects.filter(application=application, status='pending').exclude(kind='reminder').update(status='cancelled')
    heading = 'Your assessment schedules are confirmed.' if kind == 'schedule' else 'Your assessment schedules have been updated. Please review both appointments below.'
    AssessmentEmail.objects.get_or_create(
        event_key=f'{primary.pk}:{primary.revision}:{kind}',
        defaults=dict(application=application, assessment=primary, kind=kind, recipient=recipient,
                      subject=f'Aspin: assessment {"schedules" if kind == "schedule" else "schedule update"}',
                      body=email_body(application, heading, pair), schedule_versions=versions, due_at=timezone.now()),
    )
    # Rebuild pending reminder bodies for both appointments so they never quote
    # an obsolete counterpart schedule. Existing sent reminders remain sent.
    for item in pair:
        if item.status in INACTIVE:
            AssessmentEmail.objects.filter(assessment=item, kind='reminder', status='pending').update(status='cancelled')
            continue
        at = scheduled_datetime(item.date, item.time, item.time_zone)
        for hours in settings.ASSESSMENT_REMINDER_HOURS:
            due = at - timedelta(hours=hours)
            key = f'{item.pk}:{item.revision}:reminder:{hours}'
            existing = AssessmentEmail.objects.filter(event_key=key).first()
            if due <= timezone.now() and not existing:
                continue  # Do not send missed reminders for a newly booked imminent appointment.
            label = 'first' if item.phase == 'first' else 'post-adoption'
            defaults = dict(application=application, assessment=item, kind='reminder', recipient=recipient,
                            subject=f'Aspin: {label} assessment reminder ({hours} hour{"s" if hours != 1 else ""})',
                            body=email_body(application, f'Reminder: your {label} assessment is coming up.', pair),
                            schedule_versions=versions, due_at=due)
            if existing and existing.status == 'pending':
                AssessmentEmail.objects.filter(pk=existing.pk, status='pending').update(**defaults)
            elif not existing:
                AssessmentEmail.objects.create(event_key=key, **defaults)


@transaction.atomic
def save_assessment(data, instance=None, *, restore_followup=False):
    from .services import sync_application_from_assessment
    from user_api.notifications import notify_assessment_changed

    values = dict(data)
    app_id = values.get('appId') or (instance.appId if instance else None)
    try:
        application = AdoptionApplication.objects.select_for_update().get(pk=app_id)
    except (AdoptionApplication.DoesNotExist, ValueError, TypeError):
        raise ValidationError({'appId': 'Select a valid adoption application.'})
    applicant_email(application)
    created = instance is None
    if instance:
        item = RemoteAssessment.objects.select_for_update().get(pk=instance.pk)
        if str(application.pk) != item.appId or values.get('phase', item.phase) != item.phase:
            raise ValidationError({'appId': 'An assessment cannot be moved to another application or phase.'})
        before = {field: getattr(item, field) for field in SCHEDULE_FIELDS}
        previous_status = item.status
        actual_change = any(getattr(item, key) != value for key, value in values.items() if key != 'id')
        if not actual_change and not restore_followup:
            return item
        for key, value in values.items():
            if key != 'id':
                setattr(item, key, value)
    else:
        phase = values.get('phase', 'first')
        if phase not in {'first', 'post_adoption'}:
            raise ValidationError({'phase': 'Choose first or post_adoption.'})
        existing = RemoteAssessment.objects.filter(application=application, phase=phase).first()
        if existing:
            if existing.status == 'Cancelled' and values.get('status', 'Scheduled') not in INACTIVE:
                # DELETE keeps the canonical row for cancellation/email history.
                # A fresh booking reopens that slot through the normal update path,
                # which advances its revision and replaces stale reminders atomically.
                values.setdefault('status', 'Scheduled')
                values.setdefault('time_zone', existing.time_zone)
                values.setdefault('link', None)
                values.setdefault('jitsiRoom', None)
                values.update(reschedule_requested_date=None, reschedule_requested_time=None,
                              reschedule_reason='', reschedule_decline_reason='')
                return save_assessment(values, instance=existing, restore_followup=phase == 'first')
            matching = all(getattr(existing, key) == values.get(key, getattr(existing, key)) for key in ('date', 'time', 'time_zone'))
            matching = matching and (not values.get('link') or values['link'] == existing.link)
            if matching and existing.status not in INACTIVE:
                if phase == 'first':
                    post = RemoteAssessment.objects.filter(application=application, phase='post_adoption').first()
                    if post is None or (post.auto_follow and post.status == 'Cancelled'):
                        # "Schedule both" also repairs a missing/cancelled automatic
                        # follow-up when the first appointment already exists.
                        return save_assessment({}, instance=existing, restore_followup=True)
                return existing
            raise ValidationError({'phase': 'This application already has this assessment. Edit or reschedule the existing appointment.'})
        if phase == 'post_adoption':
            first = RemoteAssessment.objects.filter(application=application, phase='first').first()
            if first is None or first.status in CANCELLED or 'Rejected' in first.status:
                raise ValidationError({'phase': 'Schedule the first assessment before scheduling the post-adoption appointment.'})
            # Older applications may have a first appointment without its follow-up.
            # An explicit admin booking fills that slot without changing the first.
            values.setdefault('date', calendar_month_later(first.date))
            values.setdefault('time', first.time)
            values.setdefault('time_zone', first.time_zone)
            values['auto_follow'] = all(values[key] == expected for key, expected in (
                ('date', calendar_month_later(first.date)), ('time', first.time), ('time_zone', first.time_zone)
            ))
        values.setdefault('id', f'RA-{uuid.uuid4().hex[:16]}')
        values.setdefault('time_zone', settings.ASSESSMENT_TIME_ZONE)
        item = RemoteAssessment(**values)
        previous_status = None
        before = {}
    item.application = application
    item.appId = str(application.pk)
    ensure_link(item)
    scheduled_datetime(item.date, item.time, item.time_zone)
    time_changed = any(before.get(key) != getattr(item, key) for key in ('date', 'time', 'time_zone'))
    schedule_changed = created or restore_followup or any(before.get(key) != getattr(item, key) for key in SCHEDULE_FIELDS)
    cancellation_changed = (previous_status in CANCELLED) != (item.status in CANCELLED)
    if not created and (schedule_changed or cancellation_changed):
        item.revision += 1
    if not created and item.phase == 'post_adoption' and time_changed:
        item.auto_follow = False
    item.save()
    changed = [item]
    sync_application_from_assessment(item)
    if item.phase == 'first' and item.status not in INACTIVE:
        post = RemoteAssessment.objects.filter(application=application, phase='post_adoption').first()
        if post is None:
            post = RemoteAssessment(
                id=f'RA-{uuid.uuid4().hex[:16]}', application=application, appId=item.appId,
                phase='post_adoption', auto_follow=True, adopterName=item.adopterName,
                animalName=item.animalName, date=calendar_month_later(item.date), time=item.time,
                time_zone=item.time_zone, status='Scheduled',
            )
            ensure_link(post)
            scheduled_datetime(post.date, post.time, post.time_zone)
            post.save()
            changed.append(post)
            sync_application_from_assessment(post)
        elif post.auto_follow and (
            (time_changed and post.status not in INACTIVE)
            or (post.status == 'Cancelled' and (restore_followup or time_changed or previous_status == 'Cancelled'))
        ):
            reopening_post = post.status == 'Cancelled'
            post.date, post.time, post.time_zone = calendar_month_later(item.date), item.time, item.time_zone
            scheduled_datetime(post.date, post.time, post.time_zone)
            post.revision += 1
            post.status = 'Scheduled' if reopening_post else 'Reschedule Approved'
            if reopening_post:
                post.link = post.jitsiRoom = None
                post.reschedule_requested_date = post.reschedule_requested_time = None
                post.reschedule_reason = post.reschedule_decline_reason = ''
                ensure_link(post)
            post.save()
            changed.append(post)
            sync_application_from_assessment(post)
    for changed_item in changed:
        if changed_item.status in INACTIVE:
            AssessmentEmail.objects.filter(assessment=changed_item, kind='reminder', status='pending').update(status='cancelled')
        else:
            AssessmentEmail.objects.filter(assessment=changed_item, kind='reminder', status='pending').exclude(
                event_key__startswith=f'{changed_item.pk}:{changed_item.revision}:'
            ).update(status='cancelled')
    if len(changed) > 1 and not schedule_changed and not cancellation_changed:
        item.revision += 1
        item.save(update_fields=['revision'])
    if schedule_changed or cancellation_changed or len(changed) > 1:
        queue_messages(application, changed, item, 'schedule' if created else 'update')
        transaction.on_commit(lambda: process_due_emails(application_id=application.pk), robust=True)
    notify_assessment_changed(item, created=created, old_status=previous_status)
    return item


def process_due_emails(*, now=None, application_id=None, limit=100):
    """Claim each email once. Ambiguous/failed deliveries require manual review."""
    now = now or timezone.now()
    if not email_delivery_configured():
        return 0  # Keep real notifications queued until a delivery backend is configured.
    query = AssessmentEmail.objects.filter(status='pending', due_at__lte=now).order_by('due_at', 'pk')
    if application_id:
        query = query.filter(application_id=application_id)
    sent = 0
    for job_id in list(query.values_list('pk', flat=True)[:limit]):
        # Commit the claim before SMTP. A crash leaves 'sending', never an
        # automatically retried email that might already have been delivered.
        claimed = AssessmentEmail.objects.filter(pk=job_id, status='pending').update(status='sending', attempts=F('attempts') + 1)
        if not claimed:
            continue
        try:
            with transaction.atomic():
                job = AssessmentEmail.objects.get(pk=job_id)
                application = AdoptionApplication.objects.select_for_update().get(pk=job.application_id)
                current = {a.pk: a for a in RemoteAssessment.objects.filter(application=application)}
                item = current.get(job.assessment_id)
                stale = any(pk not in current or current[pk].revision != version for pk, version in job.schedule_versions.items())
                if stale or not item or (job.kind == 'reminder' and (
                        item.status in INACTIVE or scheduled_datetime(item.date, item.time, item.time_zone) <= now)):
                    job.status = 'cancelled'
                    job.save(update_fields=['status'])
                    continue
                # Read the application's current contact email at delivery time,
                # including messages queued before the applicant corrected it.
                recipient = applicant_email(application)
                if job.recipient != recipient:
                    job.recipient = recipient
                    job.save(update_fields=['recipient'])
                message_id = hashlib.sha256(job.event_key.encode()).hexdigest()
                backend = get_connection(fail_silently=False)
                try:
                    backend.open()
                except Exception as exc:
                    # No message was handed to the server, so connection failures
                    # can be retried safely with backoff by the periodic worker.
                    job.status = 'pending'
                    if isinstance(exc, smtplib.SMTPAuthenticationError):
                        job.last_error = 'Connection failed: Gmail rejected SMTP authentication. Check EMAIL_HOST_USER and the Google App Password in EMAIL_HOST_PASSWORD.'
                    elif isinstance(exc, ssl.SSLCertVerificationError):
                        job.last_error = 'Connection failed: SMTP TLS certificate verification failed. Check trusted certificates and the system clock.'
                    else:
                        job.last_error = f'Connection failed: {type(exc).__name__}. Check SMTP host, port, and network access.'
                    with suppress(Exception):
                        backend.close()
                    job.due_at = now + timedelta(minutes=min(60, 2 ** min(job.attempts, 6)))
                    job.save(update_fields=['status', 'last_error', 'due_at'])
                    continue
                try:
                    message = EmailMultiAlternatives(job.subject, job.body, settings.DEFAULT_FROM_EMAIL, [recipient], connection=backend,
                                           headers={'Message-ID': f'<assessment-{message_id}@aspintunay.local>'})
                    message.attach_alternative(render_email(
                        job.subject, job.body, category='Your adoption journey',
                        action_url=f'{settings.FRONTEND_BASE_URL.rstrip("/")}/user/assessments',
                        action_label='View my assessments',
                    ), 'text/html')
                    if message.send(fail_silently=False) != 1:
                        raise RuntimeError('Email backend did not accept the message.')
                finally:
                    backend.close()
                job.status, job.sent_at, job.recipient = 'sent', timezone.now(), recipient
                job.last_error = ''
                job.save(update_fields=['status', 'sent_at', 'recipient', 'last_error'])
                sent += 1
        except Exception as exc:
            logger.exception('Assessment email %s failed; review before retrying.', job_id)
            AssessmentEmail.objects.filter(pk=job_id, status='sending').update(status='failed', last_error=str(exc)[:2000])
    return sent
