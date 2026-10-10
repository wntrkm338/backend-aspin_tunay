import base64
from email import message_from_bytes
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from backend.email_config import email_configuration_errors
from backend.gmail_api import EmailBackend, GmailDeliveryError, GmailOAuthError, SEND_URL, TOKEN_URL

GMAIL_SETTINGS = dict(
    EMAIL_BACKEND='backend.gmail_api.EmailBackend',
    GMAIL_OAUTH_CLIENT_ID='client-id', GMAIL_OAUTH_CLIENT_SECRET='private-client-secret',
    GMAIL_OAUTH_REFRESH_TOKEN='private-refresh-token', DEFAULT_FROM_EMAIL='shelter@gmail.com',
    EMAIL_TIMEOUT=15,
)


def response(data, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


def authorized_response():
    return response({'access_token': 'private-access-token', 'expires_in': 3600})


@override_settings(**GMAIL_SETTINGS)
class GmailApiBackendTests(SimpleTestCase):
    @patch('backend.gmail_api.requests.post')
    def test_sends_mime_with_both_alternatives_and_refreshes_once_per_batch(self, post):
        post.side_effect = [authorized_response(), response({'id': 'mail-1'}), response({'id': 'mail-2'})]
        message = mail.EmailMultiAlternatives('Assessment reminder', 'Plain-text instructions',
            'shelter@gmail.com', ['adopter@example.com'], cc=['staff@example.com'],
            bcc=['archive@example.com'], headers={'Message-ID': '<stable-assessment-id@example.com>'})
        message.attach_alternative('<p>HTML instructions</p>', 'text/html')
        self.assertEqual(EmailBackend().send_messages([message, message]), 2)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(post.call_args_list[0].args, (TOKEN_URL,))
        self.assertEqual(post.call_args_list[0].kwargs['data']['grant_type'], 'refresh_token')
        send = post.call_args_list[1]
        self.assertEqual(send.args, (SEND_URL,))
        self.assertEqual(send.kwargs['headers'], {'Authorization': 'Bearer private-access-token'})
        self.assertFalse(send.kwargs['allow_redirects'])
        self.assertEqual(send.kwargs['timeout'], (5, 15))
        mime = message_from_bytes(base64.urlsafe_b64decode(send.kwargs['json']['raw']))
        self.assertEqual(mime['Message-ID'], '<stable-assessment-id@example.com>')
        self.assertEqual(mime['Bcc'], 'archive@example.com')
        self.assertEqual([part.get_content_type() for part in mime.get_payload()], ['text/plain', 'text/html'])
        self.assertNotIn('private-refresh-token', mime.as_string())

    @patch('backend.gmail_api.requests.post')
    def test_missing_oauth_cannot_contact_google_or_send(self, post):
        with override_settings(GMAIL_OAUTH_REFRESH_TOKEN=''):
            self.assertIn('GMAIL_OAUTH_REFRESH_TOKEN', ' '.join(email_configuration_errors()))
            with self.assertRaises(GmailOAuthError):
                EmailBackend().open()
        post.assert_not_called()

    @patch('backend.gmail_api.requests.post')
    def test_failed_oauth_does_not_leak_google_response_or_attempt_send(self, post):
        post.return_value = response({'error': 'private-client-secret'}, 400)
        with self.assertRaises(GmailOAuthError) as raised:
            EmailBackend().open()
        self.assertNotIn('private-client-secret', str(raised.exception))
        self.assertEqual(post.call_count, 1)

    @patch('backend.gmail_api.requests.post')
    def test_uncertain_send_timeout_is_sanitized_and_not_retried(self, post):
        post.side_effect = [authorized_response(), requests.Timeout('private-access-token and OTP: 123456')]
        with self.assertRaises(GmailDeliveryError) as raised:
            mail.send_mail('Verify email', 'Code: 123456', None, ['adopter@example.com'])
        self.assertNotIn('private-access-token', str(raised.exception))
        self.assertNotIn('123456', str(raised.exception))
        self.assertEqual(post.call_count, 2)

    @patch('backend.gmail_api.requests.post')
    def test_fail_silently_does_not_report_a_failed_send_as_successful(self, post):
        post.side_effect = [authorized_response(), response({'error': 'private'}, 403)]
        self.assertEqual(mail.send_mail('Subject', 'Body', None, ['adopter@example.com'], fail_silently=True), 0)

    @patch('backend.gmail_api.requests.post')
    def test_connection_check_refreshes_oauth_without_sending_email(self, post):
        post.return_value = authorized_response()
        output = StringIO()
        call_command('check_assessment_email', connect=True, stdout=output)
        post.assert_called_once()
        self.assertEqual(post.call_args.args, (TOKEN_URL,))
        self.assertIn('No emails were sent', output.getvalue())
        self.assertNotIn('private-', output.getvalue())


@override_settings(**GMAIL_SETTINGS, FRONTEND_BASE_URL='https://aspin-tunay-at-tapat.netlify.app')
class GmailAccountDeliveryTests(TestCase):
    @patch('backend.gmail_api.requests.post')
    def test_assessment_confirmation_uses_existing_outbox_and_gmail_backend(self, post):
        from admin_api.models import AdoptionApplication, AssessmentEmail
        from admin_api.scheduling import process_due_emails
        administrator = User.objects.create_user(username='gmail-scheduler', is_staff=True)
        application = AdoptionApplication.objects.create(animalId='GMAIL-DOG', age=30,
            fullName='Adopter', email='applicant@example.com', status='Pending')
        client = APIClient()
        client.force_authenticate(administrator)
        post.side_effect = [authorized_response(), response({'id': 'assessment-mail'})]
        with self.captureOnCommitCallbacks(execute=True):
            result = client.post('/admin_api/assessments/', {
                'appId': str(application.pk), 'adopterName': 'Adopter', 'animalName': 'Milo',
                'date': '2030-01-31', 'time': '09:30', 'time_zone': 'Asia/Manila', 'status': 'Scheduled',
            }, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        job = AssessmentEmail.objects.get(kind='schedule')
        self.assertEqual(job.status, 'sent')
        self.assertEqual(job.recipient, 'applicant@example.com')
        self.assertEqual(AssessmentEmail.objects.filter(kind='reminder', status='pending').count(), 4)
        mime = message_from_bytes(base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw']))
        self.assertEqual(mime['To'], 'applicant@example.com')
        self.assertIn('assessment-', mime['Message-ID'])
        self.assertEqual(process_due_emails(), 0)
        self.assertEqual(post.call_count, 2)

    @patch('backend.gmail_api.requests.post')
    def test_password_reset_uses_gmail_https_and_the_deployed_frontend_link(self, post):
        User.objects.create_user(username='gmail-user', email='adopter@example.com', password='StrongPassword123!')
        post.side_effect = [authorized_response(), response({'id': 'reset-mail'})]
        result = APIClient().post('/api/password/forgot/', {'identifier': 'gmail-user'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.data), {'message'})
        mime = base64.urlsafe_b64decode(post.call_args.kwargs['json']['raw']).decode()
        self.assertIn('https://aspin-tunay-at-tapat.netlify.app/reset-password?', mime)
        self.assertNotIn('private-', str(result.data))
