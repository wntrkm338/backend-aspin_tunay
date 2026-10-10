"""Django mail backend for the existing Gmail account over HTTPS, without SMTP."""
import base64
from email.policy import SMTP
from threading import RLock
import time

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from .email_config import email_configuration_errors

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'


class GmailOAuthError(RuntimeError):
    pass


class GmailDeliveryError(RuntimeError):
    pass


def _post_json(url, error_type, **kwargs):
    try:
        # Credentials must never follow a redirect to another server. No
        # automatic retry: a timeout after submission may still have sent mail.
        response = requests.post(url, timeout=(5, settings.EMAIL_TIMEOUT),
                                 allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise error_type(f'Gmail HTTPS request was rejected (HTTP {response.status_code}). Check the Gmail OAuth configuration and sending permission.')
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (requests.RequestException, ValueError):
        # Provider errors can contain credentials, recipient addresses, or
        # message bodies. Never propagate their text into logs or API errors.
        raise error_type('Gmail HTTPS request failed. Check network access and Gmail OAuth configuration.') from None


class EmailBackend(BaseEmailBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._access_token = None
        self._expires_at = 0
        self._lock = RLock()

    def open(self):
        with self._lock:
            if self._access_token and self._expires_at > time.monotonic() + 30:
                return False
            errors = email_configuration_errors()
            if errors:
                raise GmailOAuthError(' '.join(errors))
            data = _post_json(TOKEN_URL, GmailOAuthError, data={
                'client_id': settings.GMAIL_OAUTH_CLIENT_ID,
                'client_secret': settings.GMAIL_OAUTH_CLIENT_SECRET,
                'refresh_token': settings.GMAIL_OAUTH_REFRESH_TOKEN,
                'grant_type': 'refresh_token',
            })
            token = data.get('access_token')
            expires_in = data.get('expires_in', 3600)
            if not isinstance(token, str) or not token or not isinstance(expires_in, (int, float)) or expires_in <= 0:
                raise GmailOAuthError('Gmail returned an invalid OAuth token response.')
            self._access_token = token
            self._expires_at = time.monotonic() + expires_in
            return True

    def close(self):
        with self._lock:
            self._access_token = None
            self._expires_at = 0

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        with self._lock:
            try:
                self.open()
                for message in email_messages:
                    if not message.recipients():
                        continue
                    try:
                        self.open()
                        mime = message.message()
                        # SMTP uses envelope recipients; Gmail API reads them
                        # from MIME headers, including Bcc when supplied.
                        if message.bcc and 'Bcc' not in mime:
                            mime['Bcc'] = ', '.join(message.bcc)
                        raw = base64.urlsafe_b64encode(mime.as_bytes(policy=SMTP)).decode('ascii')
                        result = _post_json(SEND_URL, GmailDeliveryError,
                            headers={'Authorization': f'Bearer {self._access_token}'},
                            json={'raw': raw})
                        if not isinstance(result.get('id'), str) or not result['id']:
                            raise GmailDeliveryError('Gmail did not confirm acceptance of the message.')
                        sent += 1
                    except Exception:
                        if not self.fail_silently:
                            raise
            except Exception:
                if not self.fail_silently:
                    raise
            finally:
                self.close()
        return sent
