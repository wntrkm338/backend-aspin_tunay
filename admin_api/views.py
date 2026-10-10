from rest_framework import viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.shortcuts import get_object_or_404
import secrets
from user_api.models import UserProfile
from user_api.notifications import (
    notify_animal_changed,
    notify_admin_post_interaction,
    notify_animal_deleted,
    notify_application_deleted,
    notify_application_submitted,
    notify_application_status_changed,
    notify_assessment_changed,
    notify_assessment_deleted,
    notify_intake_deleted,
    notify_intake_changed,
    notify_ledger_created,
    notify_post_changed,
    notify_post_deleted,
)


from .models import (
    Animal,
    Story,
    IntakeRecord,
    AdoptionApplication,
    LedgerBlock,
    RemoteAssessment,
    Post,
    PostImage,
    PostLike,
    PostComment,
    PostCommentLike,
    PostImageLike,
)
from .serializers import (
    AnimalSerializer,
    IntakeRecordSerializer,
    StorySerializer,
    AdoptionApplicationSerializer,
    LedgerBlockSerializer,
    RemoteAssessmentSerializer,
    PostSerializer,
    PostCommentSerializer,
    PostLikeSerializer,
)
from .services import (
    assessment_schedule_datetime,
    is_call_ended_event,
    mark_assessment_done_from_call_event,
    resolve_assessment_from_call_event,
    sync_application_from_assessment,
)

_assessment_schedule_datetime = assessment_schedule_datetime
_sync_application_from_assessment = sync_application_from_assessment

class AnimalViewSet(viewsets.ModelViewSet):
    queryset = Animal.objects.all().order_by('-created_at')
    serializer_class = AnimalSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        animal = serializer.save()
        notify_animal_changed(animal, created=True)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        old_availability = serializer.instance.availability
        animal = serializer.save()
        notify_animal_changed(
            animal,
            created=False,
            old_status=old_status,
            old_availability=old_availability,
        )

    def perform_destroy(self, instance):
        notify_animal_deleted(instance)
        instance.delete()

class StoryViewSet(viewsets.ModelViewSet):
    queryset = Story.objects.all().order_by('-date')
    serializer_class = StorySerializer
    permission_classes = [IsAdminUser]

class IntakeRecordViewSet(viewsets.ModelViewSet):
    queryset = IntakeRecord.objects.all().order_by('-created_at')
    serializer_class = IntakeRecordSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        record = serializer.save()
        notify_intake_changed(record, created=True)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        record = serializer.save()
        notify_intake_changed(record, created=False, old_status=old_status)

    def perform_destroy(self, instance):
        notify_intake_deleted(instance)
        instance.delete()

class AdoptionApplicationViewSet(viewsets.ModelViewSet):
    queryset = AdoptionApplication.objects.all().order_by('-submittedAt')
    serializer_class = AdoptionApplicationSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        application = serializer.save()
        notify_application_submitted(application)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        application = serializer.save()
        if application.status == 'Verified Adopted':
            from .services import sync_application_from_assessment
            post = RemoteAssessment.objects.filter(application=application, phase='post_adoption').first()
            if post:
                sync_application_from_assessment(post)
                application.refresh_from_db()
        notify_application_status_changed(application, old_status=old_status, new_status=application.status)

    def perform_destroy(self, instance):
        notify_application_deleted(instance)
        instance.delete()

class LedgerBlockViewSet(viewsets.ModelViewSet):
    queryset = LedgerBlock.objects.all().order_by('index')
    serializer_class = LedgerBlockSerializer
    permission_classes = [IsAdminUser]
    
   
    http_method_names = ['get', 'post', 'head', 'options']

    def perform_create(self, serializer):
        block = serializer.save()
        notify_ledger_created(block)

class RemoteAssessmentViewSet(viewsets.ModelViewSet):
    queryset = RemoteAssessment.objects.all().order_by('date', 'time')
    serializer_class = RemoteAssessmentSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        # Retain cancellation/delivery history. A new booking can reopen this
        # cancelled slot; repeated cancellations do not resend notifications.
        from .scheduling import save_assessment
        save_assessment({'status': 'Cancelled'}, instance)


def _call_event_request_authorized(request):
    if request.user.is_authenticated and request.user.is_staff:
        return True

    webhook_secret = getattr(settings, 'CALL_ENDED_WEBHOOK_SECRET', '').strip()
    if not webhook_secret:
        return False

    provided_secret = (
        request.headers.get('X-Call-Webhook-Secret')
        or request.headers.get('X-Webhook-Secret')
        or request.query_params.get('secret')
        or ''
    ).strip()

    return bool(provided_secret) and secrets.compare_digest(provided_secret, webhook_secret)


@api_view(['POST'])
@authentication_classes([JWTAuthentication])
@permission_classes([AllowAny])
def remote_assessment_call_ended(request):
    if not _call_event_request_authorized(request):
        return Response({'error': 'Call-ended event is not authorized.'}, status=403)

    payload = request.data.copy() if hasattr(request.data, 'copy') else {}
    for key, value in request.query_params.items():
        payload.setdefault(key, value)

    if not is_call_ended_event(payload):
        return Response({
            'status': 'ignored',
            'reason': 'Event payload is not a call-ended signal.',
        }, status=202)

    assessment = resolve_assessment_from_call_event(payload)
    if not assessment:
        return Response({'error': 'Assessment not found for this call-ended event.'}, status=404)

    result = mark_assessment_done_from_call_event(assessment)
    return Response({
        'status': 'done',
        'updated': result.updated,
        'previous_status': result.previous_status,
        'assessment': RemoteAssessmentSerializer(result.assessment, context={'request': request}).data,
    })

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all().order_by('-created_at')
    serializer_class = PostSerializer
    permission_classes = [IsAdminUser]

    def perform_create(self, serializer):
        post = serializer.save()
        notify_post_changed(post, created=True)

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        post = serializer.save()
        notify_post_changed(post, created=False, old_status=old_status)

    def perform_destroy(self, instance):
        notify_post_deleted(instance)
        instance.delete()

# ==========================================
# NEW ENDPOINTS FOR LIKES AND COMMENTS
# ==========================================
def _build_profile_media_url(request, file_field):
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


def _serialize_authenticated_admin(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    full_name = (profile.full_name or request.user.get_full_name().strip() or request.user.username).strip()

    return {
        "id": request.user.id,
        "username": request.user.username,
        "email": request.user.email,
        "is_staff": request.user.is_staff,
        "full_name": full_name,
        "avatar": _build_profile_media_url(request, profile.avatar),
        "cover_photo": _build_profile_media_url(request, profile.cover_photo),
    }


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_me(request):
    user_data = _serialize_authenticated_admin(request)
    return Response({
        "user": user_data,
        "profile": {
            "username": user_data["username"],
            "full_name": user_data["full_name"],
            "avatar": user_data["avatar"],
            "cover_photo": user_data["cover_photo"],
        },
    })


def _get_actor_name(request):
    return request.user.username if request.user.is_authenticated else "Anonymous Viewer"


@api_view(['POST'])
@permission_classes([IsAdminUser])
def toggle_like_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    user_name = _get_actor_name(request)
    like, created = PostLike.objects.get_or_create(post=post, user=user_name)
    
    if not created:
        like.delete()
        liked = False
    else:
        liked = True
        notify_admin_post_interaction(post, request.user, 'like')
        
    return Response({
        'status': 'success', 
        'is_liked': liked, 
        'reactions': post.post_likes.count()
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def toggle_like_post_image(request, image_id):
    image = get_object_or_404(PostImage, pk=image_id)
    user_name = _get_actor_name(request)

    like, created = PostImageLike.objects.get_or_create(image=image, user=user_name)

    if not created:
        like.delete()
        liked = False
    else:
        liked = True
        notify_admin_post_interaction(image.post, request.user, 'like', image_id=image.id)

    return Response({
        'status': 'success',
        'is_liked': liked,
        'likes': image.image_likes.count(),
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def add_comment_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    user_name = _get_actor_name(request)
    
    text = request.data.get('text', '')
    if not text.strip():
        return Response({'error': 'Comment text cannot be empty'}, status=400)

    parent_id = request.data.get('parent_id')
    parent = None
    if parent_id:
        parent = get_object_or_404(PostComment, id=parent_id, post=post)

    comment = PostComment.objects.create(post=post, user=user_name, text=text, parent=parent)
    notify_admin_post_interaction(post, request.user, 'comment', comment_id=comment.id)
    
    return Response({
        'status': 'success',
        'comment': PostCommentSerializer(comment, context={'request': request}).data,
        'comments_count': post.post_comments.count()
    })

@api_view(['GET'])
@permission_classes([IsAdminUser])
def get_post_likers(request, pk):
    post = get_object_or_404(Post, pk=pk)
    likers = post.post_likes.all().order_by('-created_at')
    serializer = PostLikeSerializer(likers, many=True, context={'request': request})
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAdminUser])
def get_post_comments(request, pk):
    post = get_object_or_404(Post, pk=pk)
    comments = post.post_comments.all().order_by('created_at')
    serializer = PostCommentSerializer(comments, many=True, context={'request': request})
    return Response(serializer.data)
