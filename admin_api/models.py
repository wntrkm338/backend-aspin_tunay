from django.db import models
from django.conf import settings

class Animal(models.Model):
    id = models.CharField(max_length=10, primary_key=True)
    name = models.CharField(max_length=100)
    species = models.CharField(max_length=50, blank=True, null=True)
    breed = models.CharField(max_length=100, blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    age = models.IntegerField(help_text="Age in months", blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    availability = models.CharField(max_length=50, blank=True, null=True)
    size = models.CharField(max_length=20, blank=True, null=True)
    urgency = models.CharField(max_length=20, blank=True, null=True)
    image = models.ImageField(upload_to='animals/', blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    medical = models.TextField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} - {self.name}"

class Story(models.Model):
    animal = models.ForeignKey(Animal, related_name='stories', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    date = models.DateField()
    content = models.TextField()
    beforeImage = models.ImageField(upload_to='stories/', blank=True, null=True)
    afterImage = models.ImageField(upload_to='stories/', blank=True, null=True)
    beforeLabel = models.CharField(max_length=100, blank=True, default="Before")
    afterLabel = models.CharField(max_length=100, blank=True, default="After")

    def __str__(self):
        return self.title

class IntakeRecord(models.Model):
    id = models.CharField(max_length=20, primary_key=True) 
    name = models.CharField(max_length=100)
    species = models.CharField(max_length=50, blank=True, null=True)
    breed = models.CharField(max_length=100, blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    age = models.IntegerField(blank=True, null=True)
    entryDate = models.DateField(blank=True, null=True)
    source = models.CharField(max_length=100, blank=True, null=True)
    condition = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    photo = models.ImageField(upload_to='intakes/', blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    progress = models.IntegerField(default=20)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} - {self.name}"

class AdoptionApplication(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Under Review', 'Under Review'),
        ('Approved', 'Approved'),
        ('Declined', 'Declined'),
        ('Assessment 1 Scheduled', 'Assessment 1 Scheduled'),
        ('Assessment 1 Rescheduled', 'Assessment 1 Rescheduled'),
        ('Assessment 1 Passed', 'Assessment 1 Passed'),
        ('Assessment 1 Rejected', 'Assessment 1 Rejected'),
        ('Verification Rejected', 'Verification Rejected'),
        ('Verified Adopted', 'Verified Adopted'),
        ('Post-Adoption Scheduled', 'Post-Adoption Scheduled'),
        ('Post-Adoption Done', 'Post-Adoption Done'),
        ('Post-Adoption Rejected', 'Post-Adoption Rejected'),
        ('Monitoring Passed', 'Monitoring Passed'),
        ('Awaiting Feed Post', 'Awaiting Feed Post'),
        ('Completed', 'Completed'),
    ]

    ASSESSMENT_STATUS_CHOICES = [
        ('waiting', 'Waiting'),
        ('scheduled', 'Scheduled'),
        ('done', 'Done'),
        ('passed', 'Passed'),
        ('rescheduled', 'Rescheduled'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    animalId = models.CharField(max_length=50) 
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='adoption_applications',
    )
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Pending')
    submittedAt = models.DateField(auto_now_add=True)
    matchScore = models.IntegerField(default=0)
    notes = models.TextField(blank=True, null=True)
    reviewedBy = models.CharField(max_length=100, blank=True, null=True)

    fullName = models.CharField(max_length=255)
    age = models.IntegerField()
    civilStatus = models.CharField(max_length=50)
    address = models.TextField()
    contact = models.CharField(max_length=20)
    email = models.EmailField()
    validIdType = models.CharField(max_length=100)
    
    residenceType = models.CharField(max_length=50)
    landlordPermission = models.CharField(max_length=50, blank=True, null=True)
    hasPets = models.BooleanField(default=False)
    petDetails = models.TextField(blank=True, null=True)
    householdChildren = models.TextField(blank=True, null=True)
    householdAgreement = models.CharField(max_length=50)
    dogPlace = models.TextField()
    indoorAllowed = models.CharField(max_length=10)
    
    whyAdopt = models.TextField()
    lifetimeCommitment = models.CharField(max_length=100)
    agreedToTerms = models.BooleanField(default=False)

    photo_validId = models.ImageField(upload_to='apps/ids/', blank=True, null=True)
    photo_house = models.ImageField(upload_to='apps/house/', blank=True, null=True)
    photo_petArea = models.ImageField(upload_to='apps/areas/', blank=True, null=True)
    photo_currentPets = models.ImageField(upload_to='apps/pets/', blank=True, null=True)

    assessment_1_schedule = models.DateTimeField(blank=True, null=True)
    assessment_1_link = models.CharField(max_length=500, blank=True, null=True)
    assessment_1_status = models.CharField(max_length=20, choices=ASSESSMENT_STATUS_CHOICES, default='waiting')
    
    assessment_2_schedule = models.DateTimeField(blank=True, null=True)
    assessment_2_link = models.CharField(max_length=500, blank=True, null=True)
    assessment_2_status = models.CharField(max_length=20, choices=ASSESSMENT_STATUS_CHOICES, default='waiting')

    def __str__(self):
        return f"App #{self.id} - {self.fullName}"

# ==================================
# BLOCKCHAIN LEDGER MODELS
# ==================================
class LedgerBlock(models.Model):
    index = models.IntegerField(unique=True)
    timestamp = models.DateTimeField()
    previousHash = models.CharField(max_length=100) # Increased length for Polygon TxHash
    hash = models.CharField(max_length=100)         # Stores Polygon TxHash
    nonce = models.IntegerField(default=0)
    
    block_type = models.CharField(max_length=50, default='EXPENSE')
    description = models.TextField(blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    category = models.CharField(max_length=100, blank=True, null=True)
    recipient = models.CharField(max_length=255, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Block #{self.index} - {self.hash[:8]}"

class Receipt(models.Model):
    block = models.ForeignKey(LedgerBlock, related_name='receipts', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='ledger_receipts/')

    def __str__(self):
        return f"Receipt for Block #{self.block.index}"
    
class RemoteAssessment(models.Model):
    id = models.CharField(max_length=20, primary_key=True) 
    appId = models.CharField(max_length=20, blank=True, null=True) 
    application = models.ForeignKey(AdoptionApplication, null=True, blank=True, on_delete=models.CASCADE, related_name='remote_assessments')
    phase = models.CharField(max_length=20, default='first', choices=[('first', 'First assessment'), ('post_adoption', 'Post-adoption assessment'), ('legacy', 'Historical assessment')])
    time_zone = models.CharField(max_length=64, default='Asia/Manila')
    auto_follow = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    adopterName = models.CharField(max_length=255)
    animalName = models.CharField(max_length=255)
    date = models.DateField()
    time = models.CharField(max_length=10) 
    status = models.CharField(max_length=50, default='Pending Link')
    link = models.CharField(max_length=500, blank=True, null=True) 
    
    jitsiRoom = models.CharField(max_length=255, blank=True, null=True) 

    reschedule_requested_date = models.DateField(null=True, blank=True)
    reschedule_requested_time = models.TimeField(null=True, blank=True)
    reschedule_reason = models.TextField(blank=True, default='')
    reschedule_decline_reason = models.TextField(blank=True, default='')
    
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['application', 'phase'],
            condition=models.Q(application__isnull=False, phase__in=['first', 'post_adoption']),
            name='unique_application_assessment_phase',
        )]

    def __str__(self):
        return f"{self.id} - {self.adopterName} ({self.date} {self.time})"


class AssessmentEmail(models.Model):
    application = models.ForeignKey(AdoptionApplication, on_delete=models.CASCADE, related_name='assessment_emails')
    assessment = models.ForeignKey(RemoteAssessment, on_delete=models.CASCADE, related_name='email_jobs')
    event_key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=20, choices=[('schedule', 'Schedule'), ('update', 'Update'), ('reminder', 'Reminder')])
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    schedule_versions = models.JSONField(default=dict)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, default='pending', choices=[('pending', 'Pending'), ('sending', 'Sending'), ('sent', 'Sent'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Post(models.Model):
    id = models.CharField(max_length=50, primary_key=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    content = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=50)
    status = models.CharField(max_length=50, default='Draft')
    author = models.CharField(max_length=100, default='Admin')
    date = models.CharField(max_length=50) 
    
    views = models.IntegerField(default=0)
    reactions = models.IntegerField(default=0)
    comments = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.status})"

class PostImage(models.Model):
    post = models.ForeignKey(Post, related_name='images_rel', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='feed_posts/')

    def __str__(self):
        return f"Image for {self.post.id}"

class PostComment(models.Model):
    post = models.ForeignKey(Post, related_name='post_comments', on_delete=models.CASCADE)
    user = models.CharField(max_length=100)
    text = models.TextField()
    parent = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Comment by {self.user} on {self.post.id}"

class PostLike(models.Model):
    post = models.ForeignKey(Post, related_name='post_likes', on_delete=models.CASCADE)
    user = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'user')

    def __str__(self):
        return f"Like by {self.user} on {self.post.id}"

class PostCommentLike(models.Model):
    comment = models.ForeignKey(PostComment, related_name='likes_rel', on_delete=models.CASCADE)
    user = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('comment', 'user')

    def __str__(self):
        return f"Like by {self.user} on comment {self.comment_id}"

class PostImageLike(models.Model):
    image = models.ForeignKey(PostImage, related_name='image_likes', on_delete=models.CASCADE)
    user = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('image', 'user')

    def __str__(self):
        return f"Like by {self.user} on image {self.image_id}"
