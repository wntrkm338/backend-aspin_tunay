from datetime import date, timedelta
import smtplib
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AdoptionApplication, AssessmentEmail, RemoteAssessment
from .scheduling import calendar_month_later, process_due_emails, scheduled_datetime


class AssessmentSchedulingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='scheduler', is_staff=True)
        self.adopter = User.objects.create_user(username='adopter', email='registered@example.com')
        self.application = AdoptionApplication.objects.create(
            submitted_by=self.adopter, animalId='DOG1', fullName='Test Adopter', age=30,
            email='applicant@example.com', status='Pending',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def schedule(self, **changes):
        data = dict(appId=str(self.application.pk), adopterName='Test Adopter', animalName='Milo',
                    date='2030-01-31', time='09:30', time_zone='Asia/Manila', status='Scheduled')
        data.update(changes)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/admin_api/assessments/', data, format='json')

    def update(self, item, **changes):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(f'/admin_api/assessments/{item.pk}/', changes, format='json')

    def pair(self):
        return (RemoteAssessment.objects.get(application=self.application, phase='first'),
                RemoteAssessment.objects.get(application=self.application, phase='post_adoption'))

    def test_initial_schedule_creates_pair_and_one_email_to_application_address(self):
        response = self.schedule()
        self.assertEqual(response.status_code, 201, response.data)
        first, post = self.pair()
        self.assertEqual(post.date, date(2030, 2, 28))
        self.assertEqual((post.time, post.time_zone), (first.time, first.time_zone))
        self.assertTrue(post.auto_follow)
        self.assertNotEqual(first.link, post.link)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['applicant@example.com'])
        html, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        for value in ('First assessment', 'Post-adoption assessment', 'Asia/Manila', first.link, post.link, 'Instructions:'):
            self.assertIn(value, mail.outbox[0].body)
            self.assertIn(value, html)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'Assessment 1 Scheduled')
        self.assertIsNotNone(self.application.assessment_1_schedule)
        self.assertIsNotNone(self.application.assessment_2_schedule)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='pending').count(), 4)

    def test_calendar_month_handles_leap_year_and_year_rollover(self):
        self.assertEqual(calendar_month_later(date(2032, 1, 31)), date(2032, 2, 29))
        self.assertEqual(calendar_month_later(date(2030, 12, 31)), date(2031, 1, 31))
        self.assertEqual(calendar_month_later(date(2030, 3, 31)), date(2030, 4, 30))

    def test_dst_preserves_wall_clock_and_zone(self):
        response = self.schedule(date='2030-02-15', time_zone='America/New_York')
        self.assertEqual(response.status_code, 201, response.data)
        first, post = self.pair()
        a = scheduled_datetime(first.date, first.time, first.time_zone)
        b = scheduled_datetime(post.date, post.time, post.time_zone)
        self.assertEqual(a.hour, b.hour)
        self.assertNotEqual(a.utcoffset(), b.utcoffset())

    def test_invalid_zone_or_dst_gap_rolls_back_both_schedules(self):
        for changes in ({'time_zone': 'Invalid/Zone'}, {'date': '2027-02-14', 'time': '02:30', 'time_zone': 'America/New_York'}):
            response = self.schedule(**changes)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(RemoteAssessment.objects.count(), 0)
            self.assertEqual(AssessmentEmail.objects.count(), 0)

    def test_duplicate_create_and_noop_updates_do_not_duplicate_email_or_schedules(self):
        self.assertEqual(self.schedule().status_code, 201)
        self.assertEqual(self.schedule().status_code, 201)
        first, _ = self.pair()
        response = self.update(first, date='2030-01-31', time='09:30', status='Scheduled')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(RemoteAssessment.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AssessmentEmail.objects.count(), 5)
        response = self.schedule(date='2030-02-01')
        self.assertEqual(response.status_code, 400)

    def test_database_constraint_prevents_duplicate_phase(self):
        self.schedule()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RemoteAssessment.objects.create(id='duplicate', application=self.application, phase='first', date='2030-01-31', time='09:30')

    def test_missing_post_can_be_scheduled_after_completed_first_without_duplicates(self):
        self.application.status = 'Verified Adopted'
        self.application.save()
        first = RemoteAssessment.objects.create(
            id='old-first', application=self.application, appId=str(self.application.pk),
            phase='first', adopterName='Test Adopter', animalName='Milo',
            date=date(2030, 1, 31), time='09:30', time_zone='Asia/Manila', status='Done',
        )
        response = self.schedule(phase='post_adoption', date='2030-02-28')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.schedule(phase='post_adoption', date='2030-02-28').status_code, 201)
        first.refresh_from_db()
        self.assertEqual((first.status, first.date, first.revision), ('Done', date(2030, 1, 31), 1))
        _, post = self.pair()
        self.assertTrue(post.auto_follow)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'Post-Adoption Scheduled')
        self.assertEqual(self.application.assessment_2_schedule.date(), post.date)
        self.assertEqual(RemoteAssessment.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', assessment=post).count(), 2)

    def test_post_creation_requires_an_eligible_first_assessment(self):
        response = self.schedule(phase='post_adoption')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RemoteAssessment.objects.count(), 0)
        RemoteAssessment.objects.create(
            id='cancelled-first', application=self.application, appId=str(self.application.pk),
            phase='first', date=date(2030, 1, 31), time='09:30', status='Cancelled',
        )
        self.assertEqual(self.schedule(phase='post_adoption').status_code, 400)
        self.assertEqual(RemoteAssessment.objects.count(), 1)
        self.assertEqual(AssessmentEmail.objects.count(), 0)

    def test_first_reschedule_moves_automatic_followup_and_supersedes_reminders(self):
        self.schedule()
        first, post = self.pair()
        response = self.update(first, date='2030-03-31', time='11:00')
        self.assertEqual(response.status_code, 200, response.data)
        post.refresh_from_db()
        self.assertEqual((post.date, post.time), (date(2030, 4, 30), '11:00'))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='pending').count(), 4)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='cancelled').count(), 4)

    def test_manual_followup_reschedule_detaches_from_first(self):
        self.schedule()
        first, post = self.pair()
        response = self.update(post, date='2030-05-05', time='12:00')
        self.assertEqual(response.status_code, 200, response.data)
        self.update(first, date='2030-04-04')
        post.refresh_from_db()
        self.assertFalse(post.auto_follow)
        self.assertEqual(post.date, date(2030, 5, 5))
        self.assertEqual(len(mail.outbox), 3)

    def test_cancellation_sends_once_and_only_cancels_selected_appointment_reminders(self):
        self.schedule()
        first, post = self.pair()
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(f'/admin_api/assessments/{post.pk}/')
            self.assertEqual(response.status_code, 204)
        post.refresh_from_db()
        self.assertEqual(post.status, 'Cancelled')
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('This assessment is cancelled', mail.outbox[-1].body)
        self.assertEqual(AssessmentEmail.objects.filter(assessment=post, kind='reminder', status='pending').count(), 0)
        self.assertEqual(AssessmentEmail.objects.filter(assessment=first, kind='reminder', status='pending').count(), 2)

    def test_reminders_are_due_once_and_late_reminders_are_skipped(self):
        self.schedule()
        first, post = self.pair()
        at = scheduled_datetime(first.date, first.time, first.time_zone)
        self.assertEqual(process_due_emails(now=at - timedelta(hours=25)), 0)
        self.assertEqual(process_due_emails(now=at - timedelta(hours=24)), 1)
        self.assertEqual(process_due_emails(now=at - timedelta(hours=24)), 0)
        self.assertEqual(process_due_emails(now=at - timedelta(hours=1)), 1)
        post_at = scheduled_datetime(post.date, post.time, post.time_zone)
        self.assertEqual(process_due_emails(now=post_at + timedelta(minutes=1)), 0)

    def test_new_booking_after_admin_delete_reopens_cancelled_slot_once(self):
        self.schedule()
        first, post = self.pair()
        old_link = first.link
        first.reschedule_requested_date = date(2030, 2, 2)
        first.reschedule_reason = 'Old request'
        first.reschedule_decline_reason = 'Old decline'
        first.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'/admin_api/assessments/{first.pk}/').status_code, 204)
        first.refresh_from_db()
        cancelled_revision = first.revision
        self.assertEqual(first.status, 'Cancelled')
        response = self.schedule(date='2030-03-31', time='11:00')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['id'], first.pk)
        first.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual(first.status, 'Scheduled')
        self.assertEqual(first.revision, cancelled_revision + 1)
        self.assertNotEqual(first.link, old_link)
        self.assertIsNone(first.reschedule_requested_date)
        self.assertEqual(first.reschedule_reason, '')
        self.assertEqual(first.reschedule_decline_reason, '')
        self.assertEqual((post.date, post.time), (date(2030, 4, 30), '11:00'))
        self.assertEqual(RemoteAssessment.objects.count(), 2)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='pending').count(), 4)
        self.assertEqual(len(mail.outbox), 3)
        count = AssessmentEmail.objects.count()
        self.assertEqual(self.schedule(date='2030-03-31', time='11:00').status_code, 201)
        self.assertEqual(AssessmentEmail.objects.count(), count)
        self.assertEqual(len(mail.outbox), 3)

    def test_rebooking_same_cancelled_time_creates_new_reminders_and_keeps_history(self):
        self.schedule()
        first, _ = self.pair()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/admin_api/assessments/{first.pk}/')
        cancelled_jobs = list(AssessmentEmail.objects.filter(assessment=first, kind='reminder').values_list('pk', flat=True))
        self.assertEqual(self.schedule().status_code, 201)
        self.assertEqual(AssessmentEmail.objects.filter(pk__in=cancelled_jobs, status='cancelled').count(), 2)
        self.assertEqual(AssessmentEmail.objects.filter(assessment=first, kind='reminder', status='pending').count(), 2)
        self.application.refresh_from_db()
        self.assertEqual(self.application.assessment_1_status, 'scheduled')
        self.assertEqual(self.application.status, 'Assessment 1 Scheduled')

    def test_rebooking_cancelled_first_preserves_independently_changed_followup(self):
        self.schedule()
        first, post = self.pair()
        self.update(post, date='2030-05-05', time='12:00')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/admin_api/assessments/{first.pk}/')
        self.assertEqual(self.schedule(date='2030-03-01').status_code, 201)
        post.refresh_from_db()
        self.assertEqual((post.date, post.time), (date(2030, 5, 5), '12:00'))
        self.assertFalse(post.auto_follow)

    def test_rebooking_cancelled_post_does_not_change_first(self):
        self.schedule()
        first, post = self.pair()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/admin_api/assessments/{post.pk}/')
        response = self.schedule(phase='post_adoption', date='2030-05-05', time='12:00')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['id'], post.pk)
        post.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(post.status, 'Scheduled')
        self.assertFalse(post.auto_follow)
        self.assertEqual(first.date, date(2030, 1, 31))
        self.assertEqual(RemoteAssessment.objects.count(), 2)

    def test_rebooking_first_restores_cancelled_automatic_post_one_calendar_month_later(self):
        self.schedule()
        first, post = self.pair()
        for item in (first, post):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.delete(f'/admin_api/assessments/{item.pk}/')
        old_post_link = post.link
        response = self.schedule(date='2030-03-31', time='17:00', time_zone='Asia/Manila')
        self.assertEqual(response.status_code, 201, response.data)
        post.refresh_from_db()
        self.assertEqual(post.status, 'Scheduled')
        self.assertEqual((post.date, post.time, post.time_zone), (date(2030, 4, 30), '17:00', 'Asia/Manila'))
        self.assertTrue(post.auto_follow)
        self.assertNotEqual(post.link, old_post_link)
        self.assertEqual(RemoteAssessment.objects.count(), 2)
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='pending').count(), 4)
        self.assertIn('Post-adoption assessment', mail.outbox[-1].body)
        self.assertNotIn('This assessment is cancelled', mail.outbox[-1].body)
        messages = len(mail.outbox)
        self.assertEqual(self.schedule(date='2030-03-31', time='17:00').status_code, 201)
        self.assertEqual(len(mail.outbox), messages)

    def test_repeat_first_booking_repairs_cancelled_automatic_post_without_duplicate_notifications(self):
        self.schedule()
        first, post = self.pair()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/admin_api/assessments/{post.pk}/')
        self.assertEqual(self.schedule().status_code, 201)
        first.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual(first.revision, 2)
        self.assertEqual(post.status, 'Scheduled')
        self.assertEqual(post.date, date(2030, 2, 28))
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(self.schedule().status_code, 201)
        self.assertEqual(len(mail.outbox), 3)

    def test_rebooking_first_preserves_cancelled_independently_changed_post(self):
        self.schedule()
        first, post = self.pair()
        self.update(post, date='2030-06-01')
        for item in (first, post):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.delete(f'/admin_api/assessments/{item.pk}/')
        self.assertEqual(self.schedule(date='2030-03-31').status_code, 201)
        post.refresh_from_db()
        self.assertEqual(post.status, 'Cancelled')
        self.assertEqual(post.date, date(2030, 6, 1))
        self.assertFalse(post.auto_follow)

    def test_repeat_first_booking_recreates_missing_automatic_post(self):
        self.schedule()
        _, post = self.pair()
        post.delete()
        self.assertEqual(self.schedule().status_code, 201)
        _, post = self.pair()
        self.assertEqual(post.date, date(2030, 2, 28))
        self.assertEqual(post.status, 'Scheduled')
        self.assertEqual(len(mail.outbox), 2)

    def test_completed_or_rejected_assessments_still_block_new_bookings(self):
        self.schedule()
        first, _ = self.pair()
        for status in ['Assessment 1 Done', 'Assessment 1 Rejected', 'Declined']:
            self.update(first, status=status)
            self.assertEqual(self.schedule(date='2030-03-01').status_code, 400)
        self.assertEqual(RemoteAssessment.objects.count(), 2)

    def test_application_email_change_used_at_delivery(self):
        self.schedule()
        self.application.email = 'updated-applicant@example.com'
        self.application.save(update_fields=['email'])
        first, _ = self.pair()
        at = scheduled_datetime(first.date, first.time, first.time_zone)
        process_due_emails(now=at - timedelta(hours=24))
        self.assertEqual(mail.outbox[-1].to, ['updated-applicant@example.com'])

    def test_account_email_change_does_not_redirect_application_notifications(self):
        self.schedule()
        self.adopter.email = 'other-account@example.com'
        self.adopter.save(update_fields=['email'])
        first, post = self.pair()
        self.update(first, time='10:30')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f'/admin_api/assessments/{post.pk}/')
        first.refresh_from_db()
        at = scheduled_datetime(first.date, first.time, first.time_zone)
        process_due_emails(now=at - timedelta(hours=24))
        self.assertEqual(len(mail.outbox), 4)
        self.assertTrue(all(message.to == ['applicant@example.com'] for message in mail.outbox))
        detail = self.client.get(f'/admin_api/assessments/{first.pk}/').data
        self.assertEqual(detail['email_recipient'], 'applicant@example.com')

    def test_valid_application_email_does_not_require_matching_account_email(self):
        self.adopter.email = ''
        self.adopter.save(update_fields=['email'])
        self.assertEqual(self.schedule().status_code, 201)
        self.assertEqual(mail.outbox[0].to, ['applicant@example.com'])

    def test_invalid_application_email_is_rejected_despite_valid_account_email(self):
        self.application.email = 'invalid-address'
        self.application.save(update_fields=['email'])
        response = self.schedule()
        self.assertEqual(response.status_code, 400)
        self.assertIn('valid applicant email', str(response.data))
        self.assertFalse(RemoteAssessment.objects.exists())

    def test_failed_delivery_is_recorded_and_not_blindly_retried(self):
        with patch('admin_api.scheduling.EmailMultiAlternatives.send', side_effect=RuntimeError('SMTP ambiguous failure')):
            response = self.schedule()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(RemoteAssessment.objects.count(), 2)
        self.assertEqual(AssessmentEmail.objects.filter(status='failed').count(), 1)
        self.assertEqual(process_due_emails(), 0)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587,
                       EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
                       EMAIL_HOST_USER='shelter@gmail.com', EMAIL_HOST_PASSWORD='',
                       DEFAULT_FROM_EMAIL='shelter@gmail.com')
    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_gmail_missing_password_keeps_assessment_emails_queued(self, smtp):
        self.assertEqual(self.schedule().status_code, 201)
        self.assertEqual(AssessmentEmail.objects.filter(status='pending').count(), 5)
        self.assertEqual(process_due_emails(), 0)
        self.assertFalse(AssessmentEmail.objects.exclude(attempts=0).exists())
        smtp.assert_not_called()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587,
                       EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
                       EMAIL_HOST_USER='shelter@gmail.com', EMAIL_HOST_PASSWORD='test-app-password',
                       DEFAULT_FROM_EMAIL='shelter@gmail.com')
    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_assessment_uses_gmail_transport_and_application_recipient_once(self, smtp):
        smtp.return_value.sendmail.return_value = {}
        self.assertEqual(self.schedule().status_code, 201)
        smtp.return_value.starttls.assert_called_once()
        smtp.return_value.login.assert_called_once_with('shelter@gmail.com', 'test-app-password')
        sent = smtp.return_value.sendmail.call_args
        self.assertEqual(sent.args[0], 'shelter@gmail.com')
        self.assertEqual(sent.args[1], ['applicant@example.com'])
        self.assertIn(b'First assessment', sent.args[2])
        self.assertIn(b'Post-adoption assessment', sent.args[2])
        self.assertEqual(AssessmentEmail.objects.filter(status='sent').count(), 1)
        self.assertEqual(process_due_emails(), 0)
        smtp.return_value.sendmail.assert_called_once()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587,
                       EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
                       EMAIL_HOST_USER='shelter@gmail.com', EMAIL_HOST_PASSWORD='test-app-password',
                       DEFAULT_FROM_EMAIL='shelter@gmail.com')
    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_gmail_authentication_failure_is_visible_and_safe_to_retry(self, smtp):
        from django.core.management import call_command
        from io import StringIO
        smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'private-provider-detail')
        response = self.schedule()
        self.assertEqual(response.status_code, 201)
        first, _ = self.pair()
        job = AssessmentEmail.objects.get(kind='schedule')
        self.assertEqual(job.status, 'pending')
        self.assertIn('Gmail rejected SMTP authentication', job.last_error)
        self.assertNotIn('private-provider-detail', job.last_error)
        detail = self.client.get(f'/admin_api/assessments/{first.pk}/').data
        self.assertIn('Gmail rejected the sender login', detail['email_delivery_message'])
        smtp.return_value.sendmail.assert_not_called()
        smtp.return_value.quit.assert_called_once()
        smtp.return_value.login.side_effect = None
        smtp.return_value.sendmail.return_value = {}
        call_command('process_assessment_emails', retry_connections=True, stdout=StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, 'sent')
        self.assertEqual(smtp.return_value.sendmail.call_args.args[1], ['applicant@example.com'])
        call_command('process_assessment_emails', retry_connections=True, stdout=StringIO())
        smtp.return_value.sendmail.assert_called_once()

    def test_missing_application_address_and_non_admin_requests_are_rejected(self):
        self.application.email = ''
        self.application.save(update_fields=['email'])
        self.assertEqual(self.schedule().status_code, 400)
        self.assertEqual(RemoteAssessment.objects.count(), 0)
        self.client.force_authenticate(self.adopter)
        self.assertEqual(self.schedule().status_code, 403)

    def test_accepting_post_assessment_preserves_first_schedule_and_phase(self):
        self.schedule()
        first, post = self.pair()
        self.application.refresh_from_db()
        original = self.application.assessment_1_schedule
        self.client.force_authenticate(self.adopter)
        response = self.client.post(f'/api/assessments/{post.pk}/accept/')
        self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.assessment_1_schedule, original)
        self.assertEqual(self.application.status, 'Assessment 1 Scheduled')

    def test_worker_skips_completed_and_outdated_jobs(self):
        self.schedule()
        first, _ = self.pair()
        at = scheduled_datetime(first.date, first.time, first.time_zone)
        RemoteAssessment.objects.filter(pk=first.pk).update(status='Done')
        self.assertEqual(process_due_emails(now=at - timedelta(hours=1)), 0)
