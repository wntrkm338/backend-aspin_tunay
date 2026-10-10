from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
import datetime
from django.contrib.auth.models import User
from django.core.exceptions import DisallowedHost
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import UserProfile, Post, PostImage, Like, Comment, Notification
from admin_api.models import Animal, RemoteAssessment, LedgerBlock, AdoptionApplication, Story

# --- Auth Serializers ---

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['email'] = user.email
        token['is_staff'] = user.is_staff
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = {
            'id': self.user.id,
            'username': self.user.username,
            'email': self.user.email,
            'is_staff': self.user.is_staff
        }
        return data

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user

# --- User Serializers ---

class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.ReadOnlyField(source='user.username')
    email = serializers.ReadOnlyField(source='user.email')
    
    class Meta:
        model = UserProfile
        fields = ['username', 'full_name', 'email', 'avatar', 'cover_photo']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if not request or request.user.pk != instance.user_id:
            data.pop('email', None)
        return data


class UserProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=100, required=False, allow_blank=False)
    email = serializers.EmailField(max_length=254, required=False, allow_blank=False)
    avatar = serializers.ImageField(required=False)
    cover_photo = serializers.ImageField(required=False)

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.context['user'].pk).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return email

class NotificationSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source='event_type', read_only=True)
    link = serializers.CharField(source='target_url', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'user',
            'audience',
            'title',
            'message',
            'event_type',
            'type',
            'target_url',
            'link',
            'is_read',
            'metadata',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

class CommentSerializer(serializers.ModelSerializer):
    user_username = serializers.ReadOnlyField(source='user.username')
    
    class Meta:
        model = Comment
        fields = ['id', 'user_username', 'text', 'created_at']

class PostImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostImage
        fields = ['id', 'image']

class PostSerializer(serializers.ModelSerializer):
    user_username = serializers.ReadOnlyField(source='user.username')
    likes_count = serializers.SerializerMethodField()
    comments_count = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()

    images = PostImageSerializer(many=True, read_only=True)
    
    uploaded_images = serializers.ListField(
        child=serializers.ImageField(max_length=1000000, allow_empty_file=False, use_url=True),
        write_only=True,
        required=False
    )

    class Meta:
        model = Post
        fields = [
            'id', 'user_username', 'caption', 'created_at', 
            'likes_count', 'comments_count', 'is_liked', 
            'images', 'uploaded_images'
        ]

    def get_likes_count(self, obj):
        return obj.likes.count()

    def get_comments_count(self, obj):
        return obj.comments.count()
    
    def get_is_liked(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return Like.objects.filter(user=user, post=obj).exists()
        return False

    def validate_uploaded_images(self, value):
        if len(value) > 10:
            raise serializers.ValidationError("Maximum 10 images allowed.")
        return value

    def create(self, validated_data):
        images_data = validated_data.pop('uploaded_images', [])
        post = Post.objects.create(**validated_data)
        for image_data in images_data:
            PostImage.objects.create(post=post, image=image_data)
        return post

# --- Admin Data Serializers (Read-Only for User) ---

RELEASED_APPLICATION_STATUSES = {
    'Declined',
    'Assessment 1 Rejected',
    'Post-Adoption Rejected',
    'Returned',
}

ADOPTED_APPLICATION_STATUSES = {
    'Completed',
    'Verified Adopted',
    'Post-Adoption Scheduled',
    'Post-Adoption Done', 'Monitoring Passed',
    'Awaiting Feed Post',
}


def get_blocking_adoption_application(animal_id):
    return (
        AdoptionApplication.objects
        .filter(animalId=str(animal_id))
        .exclude(status__in=RELEASED_APPLICATION_STATUSES)
        .order_by('-submittedAt', '-id')
        .first()
    )


def animal_has_adopted_application(animal, application=None):
    status = (getattr(animal, 'status', '') or '').lower()
    if 'adopted' in status:
        return True

    if application and application.status in ADOPTED_APPLICATION_STATUSES:
        return True

    return False


def animal_is_available_for_adoption(animal, application=None):
    status = (getattr(animal, 'status', '') or '').lower()
    availability = (getattr(animal, 'availability', '') or '').lower()

    return (
        availability == 'available'
        and status == 'ready for adoption'
        and application is None
        and not animal_has_adopted_application(animal, application)
    )


def animal_availability_label(animal, application=None):
    if animal_has_adopted_application(animal, application):
        return 'Adopted'

    if application:
        return 'Application Pending'

    if animal_is_available_for_adoption(animal, application):
        return 'Available'

    return getattr(animal, 'status', None) or getattr(animal, 'availability', None) or 'Unavailable'


def build_public_media_url(request, file_field):
    if not file_field:
        return None

    try:
        media_url = file_field.url
    except Exception:
        return None

    # Sanitize whitespace/newlines from URL
    media_url = media_url.strip().replace('\n', '').replace('\r', '')

    if request and not media_url.startswith('http'):
        try:
            return request.build_absolute_uri(media_url).strip().replace('\n', '').replace('\r', '')
        except DisallowedHost:
            return media_url

    return media_url


def js_iso_format(dt):
    if isinstance(dt, str):
        parsed = parse_datetime(dt)
        if not parsed:
            return dt
        dt = parsed

    if timezone.is_aware(dt):
        dt = dt.astimezone(datetime.timezone.utc)

    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


class PublicStorySerializer(serializers.ModelSerializer):
    beforeImage = serializers.SerializerMethodField()
    afterImage = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            'id', 'title', 'date', 'content',
            'beforeImage', 'afterImage', 'beforeLabel', 'afterLabel',
        ]

    def get_beforeImage(self, obj):
        return build_public_media_url(self.context.get('request'), obj.beforeImage)

    def get_afterImage(self, obj):
        return build_public_media_url(self.context.get('request'), obj.afterImage)


class PublicAnimalSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    stories = PublicStorySerializer(many=True, read_only=True)
    has_application = serializers.SerializerMethodField()
    application_status = serializers.SerializerMethodField()
    is_adopted_by_application = serializers.SerializerMethodField()
    is_available_for_adoption = serializers.SerializerMethodField()
    availability_label = serializers.SerializerMethodField()

    class Meta:
        model = Animal
        fields = [
            'id', 'name', 'species', 'breed', 'gender', 'age',
            'status', 'availability', 'size', 'urgency', 'image',
            'bio', 'medical', 'description', 'created_at', 'stories',
            'has_application', 'application_status',
            'is_adopted_by_application', 'is_available_for_adoption',
            'availability_label',
        ]

    def get_image(self, obj):
        url = build_public_media_url(self.context.get('request'), obj.image)
        return url.strip() if url else None

    def get_blocking_application(self, obj):
        if hasattr(obj, '_blocking_application_loaded'):
            return getattr(obj, '_blocking_application', None)

        blocking_application = get_blocking_adoption_application(obj.id)
        obj._blocking_application = blocking_application
        obj._blocking_application_loaded = True
        return blocking_application

    def get_has_application(self, obj):
        return self.get_blocking_application(obj) is not None

    def get_application_status(self, obj):
        application = self.get_blocking_application(obj)
        return application.status if application else None

    def get_is_adopted_by_application(self, obj):
        return animal_has_adopted_application(obj, self.get_blocking_application(obj))

    def get_is_available_for_adoption(self, obj):
        return animal_is_available_for_adoption(obj, self.get_blocking_application(obj))

    def get_availability_label(self, obj):
        return animal_availability_label(obj, self.get_blocking_application(obj))


class AnimalSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    has_application = serializers.SerializerMethodField()
    application_status = serializers.SerializerMethodField()
    is_adopted_by_application = serializers.SerializerMethodField()
    is_available_for_adoption = serializers.SerializerMethodField()
    availability_label = serializers.SerializerMethodField()

    class Meta:
        model = Animal
        fields = [
            'id', 'name', 'breed', 'age', 'gender', 'status',
            'image', 'species', 'size', 'availability',
            'has_application', 'application_status',
            'is_adopted_by_application', 'is_available_for_adoption',
            'availability_label',
        ]

    def get_image(self, obj):
        if not obj.image:
            return None
        try:
            image_str = str(obj.image).strip().replace('\n', '').replace('\r', '')
            if image_str.startswith('http://') or image_str.startswith('https://'):
                return image_str
            request = self.context.get('request')
            if request:
                url = request.build_absolute_uri(obj.image.url)
                return url.strip().replace('\n', '').replace('\r', '')
            return obj.image.url.strip().replace('\n', '').replace('\r', '')
        except Exception:
            return None

    def get_blocking_application(self, obj):
        if hasattr(obj, '_blocking_application_loaded'):
            return getattr(obj, '_blocking_application', None)

        blocking_application = get_blocking_adoption_application(obj.id)
        obj._blocking_application = blocking_application
        obj._blocking_application_loaded = True
        return blocking_application

    def get_has_application(self, obj):
        return self.get_blocking_application(obj) is not None

    def get_application_status(self, obj):
        application = self.get_blocking_application(obj)
        return application.status if application else None

    def get_is_adopted_by_application(self, obj):
        return animal_has_adopted_application(obj, self.get_blocking_application(obj))

    def get_is_available_for_adoption(self, obj):
        return animal_is_available_for_adoption(obj, self.get_blocking_application(obj))

    def get_availability_label(self, obj):
        return animal_availability_label(obj, self.get_blocking_application(obj))


class RemoteAssessmentSerializer(serializers.ModelSerializer):
    application_id = serializers.SerializerMethodField()
    scheduled_at = serializers.SerializerMethodField()

    class Meta:
        model = RemoteAssessment
        fields = [
            'id', 'appId', 'application_id', 'adopterName', 'animalName',
            'phase', 'time_zone', 'auto_follow', 'scheduled_at',
            'date', 'time', 'status', 'link',
            'jitsiRoom', 'notes', 'reschedule_requested_date',
            'reschedule_requested_time', 'reschedule_reason',
            'reschedule_decline_reason', 'created_at'
        ]

    def get_application_id(self, obj):
        return obj.appId

    def get_scheduled_at(self, obj):
        from admin_api.services import assessment_schedule_datetime
        value = assessment_schedule_datetime(obj)
        return value.isoformat() if value else None


class LedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerBlock
        fields = [
            'index', 'timestamp', 'description', 'amount', 
            'category', 'recipient', 'hash'
        ]

    def to_representation(self, instance):
        receipt_urls = []
        request = self.context.get('request')
        for receipt in instance.receipts.all():
            receipt_url = build_public_media_url(request, receipt.image)
            if receipt_url:
                receipt_urls.append(receipt_url)

        return {
            'index': instance.index,
            'timestamp': js_iso_format(instance.timestamp),
            'data': {
                'type': instance.block_type,
                'description': instance.description,
                'amount': float(instance.amount),
                'category': instance.category,
                'recipient': instance.recipient,
                'receipts': receipt_urls,
            },
            'previousHash': instance.previousHash,
            'hash': instance.hash,
            'nonce': instance.nonce,
        }


# --- Adoption Application Serializer (Handles Photos for Users) ---

class AdoptionApplicationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdoptionApplication
        fields = [
            'animalId', 'fullName', 'age', 'civilStatus', 'address', 
            'contact', 'email', 'validIdType',
            'residenceType', 'landlordPermission', 'hasPets', 'petDetails',
            'householdChildren', 'householdAgreement', 'dogPlace', 'indoorAllowed',
            'whyAdopt', 'lifetimeCommitment', 'agreedToTerms',
            'photo_validId', 'photo_house', 'photo_petArea', 'photo_currentPets'
        ]
        
        extra_kwargs = {
            'animalId': {'required': False}, 
            'landlordPermission': {'required': False, 'allow_blank': True},
            'petDetails': {'required': False, 'allow_blank': True},
            'householdChildren': {'required': False, 'allow_blank': True},
            'householdAgreement': {'required': False, 'allow_blank': True},
            'photo_validId': {'required': False, 'allow_null': True},
            'photo_house': {'required': False, 'allow_null': True},
            'photo_petArea': {'required': False, 'allow_null': True},
            'photo_currentPets': {'required': False, 'allow_null': True},
        }


# --- Admin Adoption Application Serializer ---

class AdoptionApplicationAdminSerializer(serializers.ModelSerializer):
    applicant = serializers.SerializerMethodField()

    class Meta:
        model = AdoptionApplication
        fields = '__all__'
        read_only_fields = [
            'submitted_by',
            'animalId', 'fullName', 'age', 'civilStatus', 'address', 
            'contact', 'email', 'validIdType', 'residenceType', 
            'landlordPermission', 'hasPets', 'petDetails',
            'householdChildren', 'householdAgreement', 'dogPlace', 'indoorAllowed',
            'whyAdopt', 'lifetimeCommitment', 'agreedToTerms',
            'photo_validId', 'photo_house', 'photo_petArea', 'photo_currentPets',
            'created_at' 
        ]

    def get_applicant(self, obj):
        request = self.context.get('request')
        user = getattr(obj, 'submitted_by', None)
        profile = getattr(user, 'userprofile', None) if user else None
        full_name = obj.fullName

        if user:
            profile_name = (profile.full_name or '').strip() if profile else ''
            user_full_name = user.get_full_name().strip()
            full_name = profile_name or user_full_name or obj.fullName or user.username

        return {
            'id': user.id if user else None,
            'username': user.username if user else None,
            'fullName': full_name,
            'full_name': full_name,
            'email': obj.email or (user.email if user else ''),
            'photos': {
                'validIdPhoto': [url] if (url := build_public_media_url(request, obj.photo_validId)) else [],
                'house': [url] if (url := build_public_media_url(request, obj.photo_house)) else [],
                'petArea': [url] if (url := build_public_media_url(request, obj.photo_petArea)) else [],
                'pets': [url] if (url := build_public_media_url(request, obj.photo_currentPets)) else [],
            },
        }
