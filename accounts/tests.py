import re
import smtplib
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AuthEmailLimit, RegistrationVerification


@override_settings(DEBUG=True, FRONTEND_BASE_URL='https://shelter.example', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.credentials = {'username': 'rescuer1', 'email': 'rescuer1@example.com', 'password': 'SafePass123!'}

    def start(self, **changes):
        return self.client.post('/api/register/', {**self.credentials, **changes}, format='json')

    def code(self):
        return re.search(r'code is: ([0-9]{6})', mail.outbox[-1].body).group(1)

    def verify(self, challenge, code):
        return self.client.post('/api/register/verify/', {'challenge': challenge, 'code': code}, format='json')

    def cooldown(self):
        AuthEmailLimit.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))

    def user(self, **changes):
        return User.objects.create_user(**{**self.credentials, **changes})

    def request_reset(self, identifier):
        return self.client.post('/api/password/forgot/', {'identifier': identifier}, format='json')

    def reset_payload(self):
        link = re.search(r'https://shelter\.example/reset-password\?\S+', mail.outbox[-1].body).group(0)
        query = parse_qs(urlparse(link).query)
        return {'uid': query['uid'][0], 'token': query['token'][0], 'password': 'Different123!Password', 'confirm_password': 'Different123!Password'}

    def test_registration_requires_email_verification_and_stores_only_hashes(self):
        response = self.start(email=' Rescuer1@EXAMPLE.com ')
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(User.objects.filter(username='rescuer1').exists())
        self.assertEqual(mail.outbox[0].to, ['rescuer1@example.com'])
        html, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn(self.code(), html)
        self.assertIn('Do not share this code', html)
        pending = RegistrationVerification.objects.get()
        self.assertNotEqual(pending.password_hash, self.credentials['password'])
        self.assertNotEqual(pending.code_hash, self.code())
        self.assertNotIn(self.code(), str(response.data))
        login = self.client.post('/api/login/', self.credentials, format='json')
        self.assertEqual(login.status_code, 401)
        verified = self.verify(response.data['challenge'], self.code())
        self.assertEqual(verified.status_code, 201, verified.data)
        self.assertTrue(User.objects.get(username='rescuer1').check_password(self.credentials['password']))
        self.assertEqual(RegistrationVerification.objects.count(), 0)
        self.assertEqual(self.client.post('/api/login/', self.credentials, format='json').status_code, 200)

    def test_otp_is_single_use(self):
        response = self.start()
        code = self.code()
        self.assertEqual(self.verify(response.data['challenge'], code).status_code, 201)
        self.assertEqual(self.verify(response.data['challenge'], code).status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_wrong_codes_are_persistently_limited(self):
        response = self.start()
        wrong = '000000' if self.code() != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.verify(response.data['challenge'], wrong).status_code, 400)
        self.assertEqual(RegistrationVerification.objects.get().attempts, 5)
        self.assertEqual(self.verify(response.data['challenge'], self.code()).status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_expired_otp_is_rejected(self):
        response = self.start()
        RegistrationVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        result = self.verify(response.data['challenge'], self.code())
        self.assertEqual(result.status_code, 400)
        self.assertIn('expired', str(result.data))

    @patch('accounts.email_auth.secrets.randbelow', side_effect=[123456, 654321])
    def test_resend_invalidates_previous_code_and_has_cooldown(self, _):
        response = self.start()
        old_code = self.code()
        data = {'challenge': response.data['challenge']}
        self.assertEqual(self.client.post('/api/register/resend/', data).status_code, 429)
        self.assertEqual(len(mail.outbox), 1)
        self.cooldown()
        result = self.client.post('/api/register/resend/', data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.verify(data['challenge'], old_code).status_code, 400)
        self.assertEqual(self.verify(data['challenge'], self.code()).status_code, 201)

    def test_restarting_registration_rotates_challenge_and_keeps_resend_limit(self):
        first = self.start()
        self.assertEqual(self.start().status_code, 429)
        self.cooldown()
        second = self.start(password='Replacement123!Password')
        self.assertNotEqual(first.data['challenge'], second.data['challenge'])
        self.assertEqual(self.verify(first.data['challenge'], self.code()).status_code, 400)
        self.assertEqual(self.verify(second.data['challenge'], self.code()).status_code, 201)
        self.assertTrue(User.objects.get().check_password('Replacement123!Password'))

    def test_existing_account_cannot_be_overwritten_by_pending_verification(self):
        response = self.start()
        user = self.user(password='Original123!Password')
        self.assertEqual(self.verify(response.data['challenge'], self.code()).status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Original123!Password'))

    def test_registration_rejects_missing_email_weak_password_and_existing_email(self):
        for changes in [{'email': ''}, {'email': 'invalid'}, {'password': '123'}]:
            self.assertEqual(self.start(**changes).status_code, 400)
        self.user()
        self.assertEqual(self.start(username='different', email='RESCUER1@example.com').status_code, 400)
        self.assertFalse(RegistrationVerification.objects.exists())

    @patch('accounts.email_auth.send_mail', side_effect=smtplib.SMTPAuthenticationError(535, b'sensitive provider detail'))
    def test_smtp_failure_does_not_create_account_or_claim_code_was_sent(self, _):
        response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('sensitive', str(response.data))
        self.assertFalse(User.objects.exists())
        self.assertFalse(RegistrationVerification.objects.exists())

    def test_reset_link_is_sent_to_registered_email_and_not_exposed_even_in_debug(self):
        user = self.user()
        response = self.request_reset(user.username)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'message'})
        self.assertEqual(mail.outbox[0].to, [user.email])
        html, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        from html import unescape
        link = re.search(r'https://shelter\.example/reset-password\?\S+', mail.outbox[0].body).group(0)
        self.assertIn(f'href="{link}"', unescape(html))
        self.assertIn('Reset my password', html)
        payload = self.reset_payload()
        result = self.client.post('/api/password/reset/', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        user.refresh_from_db()
        self.assertTrue(user.check_password(payload['password']))
        self.assertEqual(self.client.post('/api/password/reset/', payload, format='json').status_code, 400)

    def test_unknown_email_missing_email_and_inactive_users_get_no_token_or_email(self):
        missing = self.user(email='')
        inactive = self.user(username='inactive', email='inactive@example.com', is_active=False)
        results = [self.request_reset(value) for value in ['unknown@example.com', missing.username, inactive.email]]
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertTrue(all(result.data == results[0].data for result in results))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_reset_token_is_rejected(self):
        user = self.user()
        self.request_reset(user.email)
        result = self.client.post('/api/password/reset/', self.reset_payload(), format='json')
        self.assertEqual(result.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.credentials['password']))

    def test_reset_rejects_mismatched_and_weak_passwords(self):
        self.request_reset(self.user().email)
        payload = self.reset_payload()
        for changes in [{'confirm_password': 'Wrong!'}, {'password': '123', 'confirm_password': '123'}]:
            self.assertEqual(self.client.post('/api/password/reset/', {**payload, **changes}, format='json').status_code, 400)

    @patch('accounts.email_auth.send_mail', side_effect=smtplib.SMTPException('provider failure'))
    def test_reset_reports_smtp_failure_without_leaking_reset_link(self, _):
        response = self.request_reset(self.user().email)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('token', str(response.data))

    def test_reset_email_and_username_share_cooldown(self):
        user = self.user()
        self.assertEqual(self.request_reset(user.username).status_code, 200)
        self.assertEqual(self.request_reset(user.email).status_code, 429)
        self.assertEqual(len(mail.outbox), 1)

    def test_auth_challenges_are_excluded_from_application_backups(self):
        from admin_api.data_backup import backup_models
        self.assertNotIn('accounts.registrationverification', backup_models())
        self.assertNotIn('accounts.authemaillimit', backup_models())
