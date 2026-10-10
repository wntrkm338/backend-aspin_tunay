"""Email verification and delivery using the shared backend mail configuration."""
import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import APIException, Throttled, ValidationError
from rest_framework.throttling import AnonRateThrottle

from backend.email_config import email_configuration_errors
from backend.email_templates import render_email
from backend.gmail_api import GmailDeliveryError, GmailOAuthError
from .models import AuthEmailLimit, RegistrationVerification

logger = logging.getLogger(__name__)
OTP_LIFETIME = 600
OTP_ATTEMPTS = 5
EMAIL_COOLDOWN = 60


class AuthEmailThrottle(AnonRateThrottle):
    rate = '30/hour'
    scope = 'auth_email'


class OTPAttemptThrottle(AnonRateThrottle):
    rate = '60/hour'
    scope = 'registration_verify'


class EmailDeliveryError(APIException):
    status_code = 503
    wait = EMAIL_COOLDOWN
    default_detail = 'We could not send the email. Please try again later or contact the administrator.'


def deliver_email(subject, body, recipient, *, html_body=None, **design):
    configuration_errors = email_configuration_errors()
    if configuration_errors:
        # These diagnostics contain setting names and instructions, never values.
        logger.warning('Account email configuration is incomplete: %s', ' '.join(configuration_errors))
        raise EmailDeliveryError()
    try:
        delivered = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False,
                              html_message=render_email(subject, body if html_body is None else html_body, **design))
        if delivered != 1:
            raise EmailDeliveryError()
    except EmailDeliveryError:
        raise
    except Exception as exc:
        # Never log reset URLs, OTPs, passwords, or SMTP credentials.
        if type(exc) in (GmailOAuthError, GmailDeliveryError):
            # Our Gmail backend sanitizes provider errors before raising them.
            logger.warning('Account email delivery failed (%s): %s', type(exc).__name__, str(exc))
        else:
            logger.warning('Account email delivery failed (%s).', type(exc).__name__)
        raise EmailDeliveryError() from None


@transaction.atomic
def reserve_email_send(purpose, email):
    key = salted_hmac('account-email-limit', f'{purpose}:{email.strip().lower()}', algorithm='sha256').hexdigest()
    limit, _ = AuthEmailLimit.objects.select_for_update().get_or_create(key=key)
    now = timezone.now()
    if limit.last_sent_at and (now - limit.last_sent_at).total_seconds() < EMAIL_COOLDOWN:
        raise Throttled(wait=EMAIL_COOLDOWN - (now - limit.last_sent_at).total_seconds())
    if not limit.window_start or now - limit.window_start >= timedelta(hours=1):
        limit.window_start, limit.count = now, 0
    if limit.count >= 5:
        raise Throttled(wait=3600 - (now - limit.window_start).total_seconds())
    limit.count += 1
    limit.last_sent_at = now
    limit.save()


def send_registration_code(pending):
    code = f'{secrets.randbelow(1000000):06d}'
    pending.code_hash = make_password(code)
    pending.expires_at = timezone.now() + timedelta(seconds=OTP_LIFETIME)
    pending.attempts = 0
    pending.save()
    deliver_email('Verify your Transpawrrency email',
                  f'Your email verification code is: {code}\n\n'
                  'Enter this code on the registration page to create your account. '
                  'It expires in 10 minutes and can only be used once.\n\n'
                  'Do not share this code. If you did not request an account, ignore this email.', pending.email,
                  html_body='Welcome to Transpawrrency! Enter the six-digit code below on the registration page to verify your email and create your account.',
                  category='Welcome to the community', code=code,
                  note='Do not share this code. If you did not request an account, you can ignore this email.')
    return {
        'verification_required': True, 'challenge': str(pending.challenge),
        'email': pending.email, 'expires_in': OTP_LIFETIME, 'resend_after': EMAIL_COOLDOWN,
        'message': 'Check your email for the six-digit verification code.',
    }


def start_registration(data):
    reserve_email_send('register', data['email'])
    with transaction.atomic():
        pending, _ = RegistrationVerification.objects.select_for_update().get_or_create(
            email=data['email'], defaults={'expires_at': timezone.now()},
        )
        pending.challenge = uuid.uuid4()
        pending.username = data['username']
        pending.password_hash = make_password(data['password'])
        return send_registration_code(pending)


def resend_registration(challenge):
    pending = RegistrationVerification.objects.filter(challenge=challenge).first()
    if pending is None:
        raise ValidationError({'detail': 'This verification request is no longer valid. Please register again.'})
    reserve_email_send('register', pending.email)
    with transaction.atomic():
        pending = RegistrationVerification.objects.select_for_update().filter(challenge=challenge).first()
        if pending is None:
            raise ValidationError({'detail': 'This verification request is no longer valid. Please register again.'})
        return send_registration_code(pending)


def verify_registration(challenge, code):
    # Return errors outside the transaction so failed attempt counts are committed.
    error = None
    with transaction.atomic():
        pending = RegistrationVerification.objects.select_for_update().filter(challenge=challenge).first()
        if pending is None:
            error = 'This verification request is no longer valid. Please register again.'
        elif pending.expires_at <= timezone.now():
            error = 'This code has expired. Request a new code.'
        elif pending.attempts >= OTP_ATTEMPTS:
            error = 'Too many incorrect attempts. Request a new code.'
        elif not check_password(code, pending.code_hash):
            pending.attempts += 1
            pending.save(update_fields=['attempts'])
            error = 'Incorrect verification code. Please check your email.'
        elif User.objects.filter(email__iexact=pending.email).exists() or User.objects.filter(username__iexact=pending.username).exists():
            error = 'That username or email is already registered. Please log in or register with different details.'
        else:
            try:
                with transaction.atomic():
                    User.objects.create(username=pending.username, email=pending.email, password=pending.password_hash)
            except IntegrityError:
                error = 'That username is already registered. Please register with a different username.'
            else:
                pending.delete()
    if error:
        raise ValidationError({'code': error})
