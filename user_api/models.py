from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    avatar = models.ImageField(upload_to='avatars/', default='default.jpg')
    cover_photo = models.ImageField(upload_to='covers/', default='default_cover.jpg')
    full_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.user.username


class UserMatchingAnswer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # --- Basic Preferences ---
    preferred_type = models.CharField(max_length=50, blank=True, null=True)
    preferred_size = models.CharField(max_length=50, blank=True, null=True)
    preferred_gender = models.CharField(max_length=50, blank=True, null=True)
    preferred_age_range = models.CharField(max_length=50, blank=True, null=True)

    # --- Lifestyle & Compatibility ---
    ACTIVITY_CHOICES = [
        ('Low', 'Low (Couch Potato)'),
        ('Medium', 'Medium (Daily Walks)'),
        ('High', 'High (Running Partner)'),
    ]
    activity_level = models.CharField(max_length=20, choices=ACTIVITY_CHOICES, blank=True, null=True)
    hours_away_home = models.IntegerField(default=0, help_text="Average hours pet will be alone per day")

    # --- Household Environment ---
    HOUSING_CHOICES = [
        ('Apartment', 'Apartment/Condo'),
        ('House_Yard', 'House with Yard'),
        ('House_No_Yard', 'House without Yard'),
    ]
    housing_type = models.CharField(max_length=20, choices=HOUSING_CHOICES, blank=True, null=True)
    good_with_kids = models.BooleanField(default=False, help_text="Must be good with children")
    good_with_other_pets = models.BooleanField(default=False, help_text="Must be good with other pets")

    # --- Experience ---
    EXPERIENCE_CHOICES = [
        ('Beginner', 'First time owner'),
        ('Intermediate', 'Had pets before'),
        ('Expert', 'Experienced/Trainer'),
    ]
    experience_level = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES, blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"


# --- POST MODELS ---

class Post(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    caption = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.caption


class PostImage(models.Model):
    """Stores multiple images for a Post."""
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='posts/')

    @property
    def like_count(self):
        return self.likes.count()

    @property
    def comment_count(self):
        return self.comments.count()

    @property
    def view_count(self):
        return self.views.count()

    class Meta:
        ordering = ['id']


# --- INTERACTION MODELS (POST) ---

class Like(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='likes')

    class Meta:
        unique_together = ('user', 'post')


class Comment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    text = models.TextField()
    parent = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class CommentLike(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='likes')

    class Meta:
        unique_together = ('user', 'comment')


class Notification(models.Model):
    AUDIENCE_USER = 'user'
    AUDIENCE_ADMIN = 'admin'
    AUDIENCE_CHOICES = [
        (AUDIENCE_USER, 'User'),
        (AUDIENCE_ADMIN, 'Admin'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications')
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=AUDIENCE_USER)
    title = models.CharField(max_length=150, blank=True, default='')
    message = models.CharField(max_length=500)
    event_type = models.CharField(max_length=80, blank=True, default='general')
    target_url = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    unique_key = models.CharField(max_length=160, blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'audience', 'is_read', '-created_at'], name='user_api_no_user_id_1cc82a_idx'),
            models.Index(fields=['audience', 'is_read', '-created_at'], name='user_api_no_audienc_59bd9b_idx'),
            models.Index(fields=['event_type'], name='user_api_no_event_t_af68c7_idx'),
        ]


# --- ADMIN POST INTERACTIONS ---

class AdminPostLike(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    post_id = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'post_id')


class AdminPostComment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    post_id = models.CharField(max_length=50)
    text = models.TextField()
    parent = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class AdminPostCommentLike(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    comment = models.ForeignKey(AdminPostComment, on_delete=models.CASCADE, related_name='likes')

    class Meta:
        unique_together = ('user', 'comment')


# --- IMAGE INTERACTIONS ---

class ImageLike(models.Model):
    """Allows users to like a specific photo inside a post."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    image = models.ForeignKey(PostImage, on_delete=models.CASCADE, related_name='likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'image')


class ImageComment(models.Model):
    """Allows users to comment on a specific photo inside a post."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    image = models.ForeignKey(PostImage, on_delete=models.CASCADE, related_name='comments')
    text = models.TextField()
    parent = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class ImageView(models.Model):
    """Tracks unique users who viewed a specific image."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    image = models.ForeignKey(PostImage, on_delete=models.CASCADE, related_name='views')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('user', 'image')
