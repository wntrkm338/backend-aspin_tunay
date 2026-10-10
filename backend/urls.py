"""
URL configuration for backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from . import integrations
from chatbot.views import chat_api
from accounts.views import (
    register_user,
    CustomLoginView,
    ForgotPasswordView,
    ResetPasswordView,
    VerifyRegistrationView,
    ResendRegistrationView,
)

urlpatterns = [
    path('api/gemini/chat/', chat_api, name='gemini-chat'),
    path('api/paymongo/payments/', integrations.paymongo_payments, name='paymongo-payments'),
    path('api/paymongo/summary/', integrations.paymongo_summary, name='paymongo-summary'),
    path('api/polygonscan/transactions/<str:tx_hash>/', integrations.polygon_transaction, name='polygon-transaction'),
    path('admin/', admin.site.urls),
    path('api/register/', register_user),
    path('api/register/verify/', VerifyRegistrationView.as_view()),
    path('api/register/resend/', ResendRegistrationView.as_view()),
    path('api/login/', CustomLoginView.as_view()),
    path('api/password/forgot/', ForgotPasswordView.as_view()),
    path('api/password/reset/', ResetPasswordView.as_view()),
    path('', include('admin_api.urls')),
    path('api/', include('user_api.urls')),
    path('chat_api/', include('chatbot.urls')),
] 

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
