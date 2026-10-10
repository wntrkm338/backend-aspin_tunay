from io import StringIO
import smtplib
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings

from backend.email_config import email_configuration_errors
from backend.email_templates import render_email


class EmailPresentationTests(SimpleTestCase):
    def test_dynamic_content_is_escaped(self):
        html = render_email('<img src=x>', 'Hello <script>alert(1)</script>',
                            action_url='https://shelter.example/reset?uid=1&token=abc',
                            action_label='Reset password')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('href="https://shelter.example/reset?uid=1&amp;token=abc"', html)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
    EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587, EMAIL_USE_TLS=True, EMAIL_USE_SSL=False,
    EMAIL_HOST_USER='shelter@gmail.com', EMAIL_HOST_PASSWORD='test-app-password',
    DEFAULT_FROM_EMAIL='shelter@gmail.com',
)
class GmailConfigurationTests(SimpleTestCase):
    def test_gmail_settings_are_ready(self):
        self.assertEqual(email_configuration_errors(), [])

    def test_missing_credentials_and_local_sender_are_reported_without_values(self):
        with override_settings(EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='',
                               DEFAULT_FROM_EMAIL='no-reply@aspintunayattapat.local'):
            errors = ' '.join(email_configuration_errors())
            self.assertIn('EMAIL_HOST_USER', errors)
            self.assertIn('EMAIL_HOST_PASSWORD', errors)
            self.assertIn('DEFAULT_FROM_EMAIL', errors)
            self.assertNotIn('no-reply@', errors)

    def test_invalid_gmail_transport_is_rejected(self):
        with override_settings(EMAIL_USE_TLS=False):
            self.assertIn('Gmail requires', ' '.join(email_configuration_errors()))
        with override_settings(EMAIL_USE_SSL=True):
            self.assertIn('not both', ' '.join(email_configuration_errors()))

    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_connect_check_negotiates_tls_and_logs_in_without_sending(self, smtp):
        output = StringIO()
        call_command('check_assessment_email', connect=True, stdout=output)
        self.assertEqual(smtp.call_args.args, ('smtp.gmail.com', 587))
        smtp.return_value.starttls.assert_called_once()
        smtp.return_value.login.assert_called_once_with('shelter@gmail.com', 'test-app-password')
        smtp.return_value.sendmail.assert_not_called()
        smtp.return_value.quit.assert_called_once()
        self.assertIn('No emails were sent', output.getvalue())
        self.assertNotIn('test-app-password', output.getvalue())

    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_local_check_does_not_connect(self, smtp):
        call_command('check_assessment_email', stdout=StringIO())
        smtp.assert_not_called()

    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_authentication_error_is_actionable_and_sanitized(self, smtp):
        smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'provider-detail-secret')
        with self.assertRaises(CommandError) as raised:
            call_command('check_assessment_email', connect=True, stdout=StringIO())
        self.assertIn('Google App Password', str(raised.exception))
        self.assertNotIn('provider-detail-secret', str(raised.exception))
        smtp.return_value.sendmail.assert_not_called()

    @patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_missing_credentials_cannot_attempt_login(self, smtp):
        with override_settings(EMAIL_HOST_PASSWORD=''):
            with self.assertRaises(CommandError):
                call_command('check_assessment_email', connect=True, stdout=StringIO())
        smtp.assert_not_called()
