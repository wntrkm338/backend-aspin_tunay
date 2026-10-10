from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import UserProfile, UserMatchingAnswer

# 1. Define Inlines to show Profile/Matching inside the User page
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'User Profile'
    fk_name = 'user'

class UserMatchingAnswerInline(admin.StackedInline):
    model = UserMatchingAnswer
    can_delete = False
    verbose_name_plural = 'Matching Preferences'
    fk_name = 'user'

# 2. Unregister the default User so we can re-register it with our Inlines
admin.site.unregister(User)

# 3. Register User with the new Inlines
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = [UserProfileInline, UserMatchingAnswerInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active')