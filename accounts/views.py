from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.db.models import Q
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .email_auth import (
    EMAIL_COOLDOWN, AuthEmailThrottle, OTPAttemptThrottle, deliver_email, reserve_email_send,
    start_registration, resend_registration, verify_registration,
)
from .serializers import CustomTokenSerializer, ForgotPasswordSerializer, RegisterSerializer, ResetPasswordSerializer


def _build_password_reset_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}/reset-password?uid={uid}&token={token}"


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([AuthEmailThrottle])
def register_user(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return Response(start_registration(serializer.validated_data), status=status.HTTP_202_ACCEPTED)


class RegistrationChallengeSerializer(serializers.Serializer):
    challenge = serializers.UUIDField()


class RegistrationCodeSerializer(RegistrationChallengeSerializer):
    code = serializers.RegexField(r'^[0-9]{6}$', max_length=6, min_length=6)


class VerifyRegistrationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [OTPAttemptThrottle]

    def post(self, request):
        serializer = RegistrationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verify_registration(**serializer.validated_data)
        return Response({'message': 'Email verified. Your account is ready; you can now log in.'}, status=201)


class ResendRegistrationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEmailThrottle]

    def post(self, request):
        serializer = RegistrationChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(resend_registration(**serializer.validated_data))


class CustomLoginView(TokenObtainPairView):
    serializer_class = CustomTokenSerializer


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEmailThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data['identifier']
        user = User.objects.filter(
            Q(email__iexact=identifier) | Q(username__iexact=identifier), is_active=True,
        ).first()
        reserve_email_send('password-reset', user.email if user and user.email else identifier)
        if user and user.email and user.has_usable_password():
            reset_link = _build_password_reset_link(user)
            deliver_email(
                'Reset your Transpawrrency password',
                'We received a request to reset your password.\n\n'
                f'Use this link to choose a new password:\n{reset_link}\n\n'
                f'This link expires in {settings.PASSWORD_RESET_TIMEOUT // 60} minutes and can only be used once.\n\n'
                'If you did not request this, ignore this email. Your password has not changed.',
                user.email,
                html_body='We received a request to reset your password. Use the button below to choose a new password.\n\n'
                          f'This link expires in {settings.PASSWORD_RESET_TIMEOUT // 60} minutes and can only be used once.',
                category='Account security', action_url=reset_link, action_label='Reset my password',
                note='If you did not request this, ignore this email. Your password has not changed.',
            )
        # Never disclose reset tokens through the API, even during development.
        return Response({'message': 'If an account with that email exists, reset instructions have been sent. Check your inbox and Spam folder.'},
                        headers={'Retry-After': str(EMAIL_COOLDOWN)})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [OTPAttemptThrottle]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Your password has been reset. You can now log in.'})
