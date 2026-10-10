from django.db import models
from django.contrib.auth.models import User
import uuid


class RegistrationVerification(models.Model):
    email = models.EmailField(unique=True)
    challenge = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    username = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=128)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)


class AuthEmailLimit(models.Model):
    key = models.CharField(max_length=64, unique=True)
    last_sent_at = models.DateTimeField(null=True)
    window_start = models.DateTimeField(null=True)
    count = models.PositiveSmallIntegerField(default=0)

class UserProfile(models.Model):
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE,
        related_name='profile'  # FIX: specific related name to avoid clash
    )
    avatar = models.ImageField(upload_to='avatars/', default='default.jpg')
    cover_photo = models.ImageField(upload_to='covers/', default='default_cover.jpg')
    full_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"


class UserMatchingAnswer(models.Model):
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE,
        related_name='matching_answers' # FIX: specific related name
    )
    
    # Basic Preferences
    preferred_type = models.CharField(max_length=50, blank=True, null=True)
    preferred_size = models.CharField(max_length=50, blank=True, null=True)
    preferred_gender = models.CharField(max_length=50, blank=True, null=True)
    preferred_age_range = models.CharField(max_length=50, blank=True, null=True)

    # Lifestyle
    activity_level = models.CharField(max_length=20, blank=True, null=True)
    hours_away_home = models.IntegerField(default=0)

    # Environment
    housing_type = models.CharField(max_length=20, blank=True, null=True)
    good_with_kids = models.BooleanField(default=False)
    good_with_other_pets = models.BooleanField(default=False)

    # Experience
    experience_level = models.CharField(max_length=20, blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"
