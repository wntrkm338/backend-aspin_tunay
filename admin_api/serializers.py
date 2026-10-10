import base64
import hashlib
import json
import datetime 
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import DisallowedHost
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import transaction
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

# NEW: Import Web3
from web3 import Web3
from web3.exceptions import Web3Exception

# === VERSION 7+ COMPATIBILITY FIX ===
try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware

from user_api.models import UserProfile
from .models import (
    Animal,
    Story,
    IntakeRecord,
    AdoptionApplication,
    LedgerBlock,
    RemoteAssessment,
    Post,
    PostImage,
    Receipt,
    PostComment,
    PostLike,
    PostCommentLike,
    PostImageLike,
)

def build_media_url(request, file_field):
    if not file_field:
        return None

    try:
        media_url = file_field.url
    except Exception:
        return None

    if request and not media_url.startswith('http'):
        try:
            return request.build_absolute_uri(media_url)
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


def compute_ledger_hash(index, timestamp, block_type, description, amount, category, recipient, previous_hash, nonce):
    payload = {
        "index": int(index),
        "timestamp": js_iso_format(timestamp),
        "data": {
            "type": block_type,
            "description": description,
            "amount": float(amount or 0),
            "category": category,
            "recipient": recipient,
        },
        "previousHash": previous_hash,
        "nonce": int(nonce),
    }
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode('utf-8')).hexdigest()


# ==================================
# POLYGON WEB3 INTEGRATION
# ==================================
def log_expense_to_polygon(expense_data):
    """
    Connects to Polygon Testnet and sends a modern EIP-1559 (Type 2) 0-value transaction
    with the expense data embedded in the transaction's input data field.
    """
    # Using the most reliable public RPC for Amoy
    rpc_url = getattr(settings, 'POLYGON_RPC_URL', 'https://rpc-amoy.polygon.technology')
    private_key = getattr(settings, 'POLYGON_PRIVATE_KEY', None)
    
    if not private_key:
        raise ValueError("POLYGON_PRIVATE_KEY is not configured in settings. Check your .env file.")

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    # === INJECT MIDDLEWARE WITH V7 COMPATIBILITY ===
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    if not w3.is_connected():
        raise Web3Exception("Failed to connect to the Polygon network RPC.")

    account = w3.eth.account.from_key(private_key)
    wallet_address = account.address

    # Serialize data to store on-chain
    data_string = json.dumps(expense_data, separators=(',', ':')).encode('utf-8')
    
    # --- EIP-1559 GAS CALCULATION ---
    latest_block = w3.eth.get_block('latest')
    base_fee = latest_block['baseFeePerGas']
    
    # Offer a generous tip to validators (in Wei) so they mine it instantly (30 Gwei)
    max_priority_fee = w3.to_wei(30, 'gwei') 
    
    # Max fee we are willing to pay (Base fee x2 for safety + our tip)
    max_fee = (base_fee * 2) + max_priority_fee
    
    # Build a Modern Type 2 Transaction
    tx = {
        'type': 2, 
        'to': wallet_address, 
        'value': 0,
        'gas': 2000000,
        'maxFeePerGas': max_fee,
        'maxPriorityFeePerGas': max_priority_fee,
        'nonce': w3.eth.get_transaction_count(wallet_address, 'pending'),
        'data': data_string,
        'chainId': 80002 # Chain ID for Polygon Amoy Testnet
    }

    # Sign & Send
    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    return w3.to_hex(tx_hash)


class Base64ImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if not data: 
            return None
            
        if isinstance(data, str):
            if data.startswith('data:image'):
                format, imgstr = data.split(';base64,') 
                ext = format.split('/')[-1] 
                data = ContentFile(base64.b64decode(imgstr), name='temp.' + ext)
            elif data.startswith('http') or data.startswith('/media/'):
                raise serializers.SkipField()
                
        return super(Base64ImageField, self).to_internal_value(data)

class StorySerializer(serializers.ModelSerializer):
    beforeImage = Base64ImageField(required=False, allow_null=True)
    afterImage = Base64ImageField(required=False, allow_null=True)
    id = serializers.IntegerField(required=False) 

    class Meta:
        model = Story
        fields = ['id', 'title', 'date', 'content', 'beforeImage', 'afterImage', 'beforeLabel', 'afterLabel']

class AnimalSerializer(serializers.ModelSerializer):
    image = Base64ImageField(required=False, allow_null=True)
    stories = StorySerializer(many=True, required=False)

    class Meta:
        model = Animal
        fields = '__all__'

    def create(self, validated_data):
        stories_data = validated_data.pop('stories', [])
        animal = Animal.objects.create(**validated_data)
        for story_data in stories_data:
            story_data.pop('id', None) 
            Story.objects.create(animal=animal, **story_data)
        return animal

    def update(self, instance, validated_data):
        stories_data = validated_data.pop('stories', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if stories_data is not None:
            instance.stories.all().delete()
            for story_data in stories_data:
                story_data.pop('id', None)
                Story.objects.create(animal=instance, **story_data)
                
        return instance
    
class IntakeRecordSerializer(serializers.ModelSerializer):
    photo = Base64ImageField(required=False, allow_null=True)

    class Meta:
        model = IntakeRecord
        fields = '__all__'

class AdoptionApplicationSerializer(serializers.ModelSerializer):
    photo_validId = Base64ImageField(required=False, allow_null=True)
    photo_house = Base64ImageField(required=False, allow_null=True)
    photo_petArea = Base64ImageField(required=False, allow_null=True)
    photo_currentPets = Base64ImageField(required=False, allow_null=True)
    applicant = serializers.SerializerMethodField()

    class Meta:
        model = AdoptionApplication
        fields = '__all__'

    def get_applicant(self, obj):
        request = self.context.get('request')

        photos = {
            "validIdPhoto": [url] if (url := build_media_url(request, obj.photo_validId)) else [],
            "house": [url] if (url := build_media_url(request, obj.photo_house)) else [],
            "petArea": [url] if (url := build_media_url(request, obj.photo_petArea)) else [],
            "pets": [url] if (url := build_media_url(request, obj.photo_currentPets)) else [],
        }

        return {
            "fullName": obj.fullName,
            "age": obj.age,
            "civilStatus": obj.civilStatus,
            "address": obj.address,
            "contact": obj.contact,
            "email": obj.email,
            "validId": obj.validIdType,
            "residenceType": obj.residenceType,
            "landlordPermission": obj.landlordPermission,
            "hasPets": obj.hasPets,
            "petDetails": obj.petDetails,
            "householdChildren": obj.householdChildren,
            "householdAgreement": obj.householdAgreement,
            "dogPlace": obj.dogPlace,
            "indoorAllowed": obj.indoorAllowed,
            "whyAdopt": obj.whyAdopt,
            "lifetimeCommitment": obj.lifetimeCommitment,
            "agreedToTerms": obj.agreedToTerms,
            "photos": photos
        }

class LedgerBlockSerializer(serializers.ModelSerializer):
    timestamp = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = LedgerBlock
        fields = '__all__'
        read_only_fields = ('index', 'previousHash', 'hash')

    def to_internal_value(self, data):
        internal_data = data.copy() if hasattr(data, 'copy') else dict(data)

        if not internal_data.get('timestamp'):
            internal_data['timestamp'] = js_iso_format(timezone.now())

        if 'data' in internal_data:
            nested_data = internal_data.pop('data')
            internal_data['block_type'] = nested_data.get('type', 'EXPENSE')
            internal_data['description'] = nested_data.get('description', '')
            internal_data['amount'] = nested_data.get('amount', 0)
            internal_data['category'] = nested_data.get('category', '')
            internal_data['recipient'] = nested_data.get('recipient', '')
            self.context['receipts_data'] = nested_data.get('receipts', [])

        return super().to_internal_value(internal_data)

    @transaction.atomic
    def create(self, validated_data):
        receipts_data = self.context.get('receipts_data', [])

        last_block = LedgerBlock.objects.select_for_update().order_by('-index').first()
        next_index = 0 if last_block is None else last_block.index + 1
        
        # Maps locally, but true tracking is done on-chain
        previous_hash = "ON_CHAIN" if last_block is None else last_block.hash

        raw_timestamp = validated_data.get('timestamp')
        if isinstance(raw_timestamp, str):
            parsed_timestamp = parse_datetime(raw_timestamp)
            validated_data['timestamp'] = parsed_timestamp or timezone.now()
        elif not raw_timestamp:
            validated_data['timestamp'] = timezone.now()

        validated_data['index'] = next_index
        validated_data['previousHash'] = previous_hash
        validated_data['nonce'] = 0 
        
        # Prepare the payload to push to Polygon
        on_chain_payload = {
            "index": next_index,
            "type": validated_data.get('block_type'),
            "desc": validated_data.get('description'),
            "amt": float(validated_data.get('amount', 0)),
            "cat": validated_data.get('category'),
            "recip": validated_data.get('recipient'),
        }

        # Send to Polygon and get the real transaction hash back
        try:
            polygon_tx_hash = log_expense_to_polygon(on_chain_payload)
            validated_data['hash'] = polygon_tx_hash
        except Exception as e:
            # THIS PRINTS THE ERROR TO YOUR TERMINAL SO WE CAN DEBUG
            print("\n" + "!"*60)
            print("🚨🚨🚨 WEB3 BLOCKCHAIN ERROR 🚨🚨🚨")
            print(str(e))
            print("!"*60 + "\n")
            raise serializers.ValidationError({"error": f"Blockchain network error: {str(e)}"})

        block = LedgerBlock.objects.create(**validated_data)

        for receipt_index, img_str in enumerate(receipts_data):
            if isinstance(img_str, str) and img_str.startswith('data:image'):
                format_part, img_base64 = img_str.split(';base64,')
                ext = format_part.split('/')[-1]
                file_data = ContentFile(
                    base64.b64decode(img_base64),
                    name=f'block_{block.index}_receipt_{receipt_index}.{ext}'
                )
                Receipt.objects.create(block=block, image=file_data)

        return block

    def to_representation(self, instance):
        request = self.context.get('request')

        receipt_urls = []
        for receipt in instance.receipts.all():
            receipt_url = build_media_url(request, receipt.image)
            if receipt_url:
                receipt_urls.append(receipt_url)

        try:
            ts = js_iso_format(instance.timestamp)
        except Exception:
            ts = js_iso_format(timezone.now())

        return {
            "index": instance.index,
            "timestamp": ts,
            "data": {
                "type": instance.block_type,
                "description": instance.description,
                "amount": float(instance.amount),
                "category": instance.category,
                "recipient": instance.recipient,
                "receipts": receipt_urls
            },
            "previousHash": instance.previousHash,
            "hash": instance.hash,  
            "nonce": instance.nonce,
            "polygon_explorer_url": f"https://amoy.polygonscan.com/tx/{instance.hash}" 
        }

class RemoteAssessmentSerializer(serializers.ModelSerializer):
    application_id = serializers.SerializerMethodField()
    email_status = serializers.SerializerMethodField()
    email_delivery_message = serializers.SerializerMethodField()
    email_recipient = serializers.SerializerMethodField()
    scheduled_at = serializers.SerializerMethodField()
    time_zone = serializers.CharField(required=False)

    class Meta:
        model = RemoteAssessment
        fields = '__all__'
        read_only_fields = ['application', 'revision', 'auto_follow']
        extra_kwargs = {'id': {'required': False}}
        validators = []  # The scheduling service checks uniqueness under an application lock.

    def get_email_status(self, obj):
        from .scheduling import email_delivery_configured
        if not email_delivery_configured():
            return 'not_configured'
        job = obj.application.assessment_emails.exclude(kind='reminder').order_by('-created_at').first() if obj.application_id else None
        return job.status if job else None

    def get_email_recipient(self, obj):
        return (obj.application.email or '').strip() if obj.application_id else ''

    def get_email_delivery_message(self, obj):
        from backend.email_config import email_configuration_errors
        errors = email_configuration_errors()
        if errors:
            return ' '.join(errors)
        job = obj.application.assessment_emails.exclude(kind='reminder').order_by('-created_at').first() if obj.application_id else None
        if not job:
            return ''
        if job.status == 'pending' and job.last_error:
            error = job.last_error.lower()
            if any(value in error for value in ('authentication', 'username and password', '535', 'credentials')):
                return 'Email not sent: Gmail rejected the sender login. Check the sending Gmail address and Google App Password.'
            return 'Email not sent: unable to connect to the mail server. The notification remains queued for retry.'
        if job.status == 'failed':
            return 'Email delivery failed. Review its delivery status before retrying to avoid duplicates.'
        if job.status == 'sent':
            return 'Accepted by the mail server. The recipient may need to check Spam.'
        return 'Waiting for the email worker.' if job.status == 'pending' else ''

    def get_scheduled_at(self, obj):
        from .services import assessment_schedule_datetime
        value = assessment_schedule_datetime(obj)
        return value.isoformat() if value else None

    def validate_link(self, value):
        if value:
            if not value.startswith(('https://', 'http://')):
                raise serializers.ValidationError('Use an HTTP or HTTPS meeting link.')
            serializers.URLField().run_validation(value)
        return value

    def validate_time(self, value):
        try:
            return datetime.datetime.strptime(value, '%H:%M').strftime('%H:%M')
        except ValueError:
            raise serializers.ValidationError('Use HH:MM for the scheduled time.')

    def create(self, validated_data):
        from .scheduling import save_assessment
        return save_assessment(validated_data)

    def update(self, instance, validated_data):
        from .scheduling import save_assessment
        return save_assessment(validated_data, instance)

    def get_application_id(self, obj):
        return obj.appId

    def to_internal_value(self, data):
        internal_data = data.copy() if hasattr(data, 'copy') else dict(data)

        if not internal_data.get('appId') and internal_data.get('application_id'):
            internal_data['appId'] = internal_data.get('application_id')

        internal_data.pop('application_id', None)
        internal_data.pop('animal_id', None)

        return super().to_internal_value(internal_data)

class PostImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    comments = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    supports_comments = serializers.SerializerMethodField()

    class Meta:
        model = PostImage
        fields = ['id', 'url', 'likes', 'comments', 'is_liked', 'source', 'supports_comments']

    def get_url(self, obj):
        request = self.context.get('request')
        return build_media_url(request, obj.image)

    def get_likes(self, obj):
        return obj.image_likes.count()

    def get_comments(self, obj):
        return 0

    def get_is_liked(self, obj):
        request = self.context.get('request')
        current_user = request.user.username if request and request.user.is_authenticated else "Anonymous Viewer"
        return obj.image_likes.filter(user=current_user).exists()

    def get_source(self, obj):
        return 'admin'

    def get_supports_comments(self, obj):
        return False

class PostCommentSerializer(serializers.ModelSerializer):
    parent_id = serializers.SerializerMethodField()
    likes = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    username = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = ['id', 'user', 'username', 'name', 'text', 'parent_id', 'likes', 'is_liked', 'created_at', 'avatar']

    def get_parent_id(self, obj):
        return obj.parent_id

    def get_likes(self, obj):
        return obj.likes_rel.count()

    def get_is_liked(self, obj):
        request = self.context.get('request')
        current_user = request.user.username if request and request.user.is_authenticated else "Anonymous Viewer"
        return obj.likes_rel.filter(user=current_user).exists()

    def get_avatar(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['avatar']

    def get_username(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['username']

    def get_name(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['name']

class PostLikeSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    name = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = PostLike
        fields = ['id', 'user', 'username', 'name', 'avatar', 'created_at']

    def get_username(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['username']

    def get_name(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['name']

    def get_avatar(self, obj):
        request = self.context.get('request')
        return _serialize_user_identity(obj.user, request)['avatar']

def _resolve_user(identifier):
    if not identifier:
        return None

    value = str(identifier).strip()
    if not value:
        return None

    user = User.objects.filter(username__iexact=value).first()
    if user:
        return user

    user = User.objects.filter(email__iexact=value).first()
    if user:
        return user

    profile = UserProfile.objects.select_related('user').filter(full_name__iexact=value).first()
    if profile:
        return profile.user

    return None

def _get_display_name(user):
    if not user:
        return None

    profile = getattr(user, 'userprofile', None)
    if profile and profile.full_name:
        return profile.full_name

    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username

def _get_avatar_url(user, request):
    if not user:
        return None

    profile = getattr(user, 'userprofile', None)
    if not profile or not profile.avatar:
        return None

    return build_media_url(request, profile.avatar)

def _serialize_user_identity(identifier, request):
    user = _resolve_user(identifier)
    username = user.username if user else (str(identifier).strip() if identifier else None)
    display_name = _get_display_name(user) or username

    return {
        "username": username,
        "name": display_name,
        "avatar": _get_avatar_url(user, request),
    }

class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = '__all__'

    def to_internal_value(self, data):
        internal_data = data.copy() if hasattr(data, 'copy') else dict(data)
        
        metrics = internal_data.pop('metrics', {})
        if isinstance(metrics, dict):
            internal_data['views'] = metrics.get('views', 0)

        self.context['images_data'] = internal_data.pop('images', [])
        internal_data.pop('activity', None)

        return super().to_internal_value(internal_data)

    def create(self, validated_data):
        images_data = self.context.get('images_data', [])
        request = self.context.get('request')
        if request and request.user.is_authenticated and not validated_data.get('author'):
            validated_data['author'] = request.user.username
        post = Post.objects.create(**validated_data)
        self._process_images(post, images_data)
        return post

    def update(self, instance, validated_data):
        images_data = self.context.get('images_data', None)
        request = self.context.get('request')

        if request and request.user.is_authenticated and not validated_data.get('author') and not instance.author:
            validated_data['author'] = request.user.username
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if images_data is not None:
            for img in instance.images_rel.all():
                keep = any(img.image.url in inc_img for inc_img in images_data if isinstance(inc_img, str))
                if not keep:
                    img.delete()
            self._process_images(instance, images_data)
            
        return instance

    def _process_images(self, post, images_data):
        for index, img_str in enumerate(images_data):
            if isinstance(img_str, str) and img_str.startswith('data:image'):
                format, imgstr = img_str.split(';base64,')
                ext = format.split('/')[-1]
                file_data = ContentFile(base64.b64decode(imgstr), name=f'post_{post.id}_{index}.{ext}')
                PostImage.objects.create(post=post, image=file_data)

    def to_representation(self, instance):
        request = self.context.get('request')
        current_user = request.user.username if request and request.user.is_authenticated else "Anonymous Viewer"
        author_data = _serialize_user_identity(instance.author, request)

        images_data = PostImageSerializer(
            instance.images_rel.all(),
            many=True,
            context={'request': request},
        ).data

        reactions_data = PostLikeSerializer(
            instance.post_likes.all().order_by('-created_at'),
            many=True,
            context={'request': request},
        ).data

        all_comments = PostCommentSerializer(
            instance.post_comments.all().order_by('created_at'),
            many=True,
            context={'request': request},
        ).data

        recent_comments = PostCommentSerializer(
            instance.post_comments.all().order_by('-created_at')[:3],
            many=True,
            context={'request': request},
        ).data

        return {
            "id": instance.id,
            "title": instance.title,
            "content": instance.content,
            "category": instance.category,
            "status": instance.status,
            "author": instance.author,
            "author_username": author_data['username'],
            "author_name": author_data['name'],
            "avatar": author_data['avatar'],
            "date": instance.date,
            "images": images_data,
            "metrics": {
                "views": instance.views,
                "reactions": instance.post_likes.count(),
                "comments": instance.post_comments.count(),
            },
            "is_liked": instance.post_likes.filter(user=current_user).exists(),
            "activity": {
                "views": [],
                "reactions": reactions_data,
                "comments": all_comments,
            },
            "recent_comments": recent_comments,
            "created_at": instance.created_at,
        }
