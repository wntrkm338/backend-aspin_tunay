from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework import status, generics
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.decorators import api_view, authentication_classes
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from datetime import timedelta, datetime
from django.contrib.auth.models import User
from django.db.models import Q
from django.db import transaction

from admin_api.models import (
    Animal,
    Story,
    IntakeRecord,
    RemoteAssessment,
    LedgerBlock,
    AdoptionApplication,
    Post as AdminPost,
    PostComment as AdminFeedPostComment,
    PostLike as AdminFeedPostLike,
    PostCommentLike as AdminFeedPostCommentLike,
)
from .models import (
    UserProfile, Post, PostImage, Like, Comment, Notification, UserMatchingAnswer,
    AdminPostComment, CommentLike, AdminPostCommentLike,
    ImageLike, ImageComment
)
from .serializers import (
    MyTokenObtainPairSerializer, RegisterSerializer,
    UserProfileSerializer, UserProfileUpdateSerializer, NotificationSerializer, PostSerializer, CommentSerializer,
    AnimalSerializer, RemoteAssessmentSerializer, LedgerSerializer,
    AdoptionApplicationCreateSerializer, AdoptionApplicationAdminSerializer,
    PublicAnimalSerializer, PublicStorySerializer,
    RELEASED_APPLICATION_STATUSES, get_blocking_adoption_application,
    animal_has_adopted_application, animal_is_available_for_adoption,
    animal_availability_label, ADOPTED_APPLICATION_STATUSES,
)
from .notifications import (
    notify_application_submitted,
    notify_admin_post_interaction,
    notify_assessment_accepted,
    notify_assessment_cancelled,
    notify_assessment_reschedule_cancelled,
    notify_assessment_reschedule_requested,
    notify_user_post_created,
    notify_user_post_deleted,
    notify_user_post_interaction,
)

# --- Auth Views ---
class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

def _get_user_avatar(user, request):
    profile = getattr(user, 'profile', None)
    if not profile:
        profile = getattr(user, 'userprofile', None)
    
    if profile and profile.avatar:
        url = profile.avatar.url
        if not url.startswith('http'):
            return request.build_absolute_uri(url)
        return url
    return None


def _get_profile_display_name(user, profile=None):
    if profile and profile.full_name:
        cleaned_name = profile.full_name.strip()
        if cleaned_name:
            return cleaned_name

    full_name = user.get_full_name().strip()
    return full_name or user.username

def _calculate_match_score(animal, prefs):
    if not prefs or not animal: return 0
    score = 0

    if prefs.preferred_type == 'Any' or (animal.species and animal.species.lower() == (prefs.preferred_type or '').lower()): score += 25
    if prefs.preferred_size == 'Any' or animal.size == prefs.preferred_size: score += 20
    if prefs.preferred_gender == 'Any' or animal.gender == prefs.preferred_gender: score += 10
    age = animal.age or 0
    if prefs.preferred_age_range == 'Any': score += 15
    elif prefs.preferred_age_range == 'young' and age <= 12: score += 15
    elif prefs.preferred_age_range == 'adult' and 12 <= age <= 84: score += 15
    elif prefs.preferred_age_range == 'senior' and age > 84: score += 15
    else: score += 5
    hours = prefs.hours_away_home or 8
    if hours <= 4: score += 15
    elif hours <= 8: score += 10
    else: score += 5
    if prefs.housing_type == 'House_Yard': score += 10
    elif prefs.housing_type == 'House_No_Yard': score += 7
    else: score += 4
    if prefs.experience_level == 'Expert': score += 5
    elif prefs.experience_level == 'Intermediate': score += 4
    else: score += 3

    return min(99, max(1, round(score)))


def _get_comment_type_hint(request):
    hint = request.query_params.get('comment_type')
    if not hint:
        try:
            hint = request.data.get('comment_type')
        except Exception:
            hint = None
    return (hint or '').strip().lower()


def _comment_models_for_hint(comment_type):
    model_map = {
        'social': Comment,
        'user': Comment,
        'post': Comment,
        'news': AdminFeedPostComment,
        'admin_post': AdminFeedPostComment,
        'admin-news': AdminFeedPostComment,
        'admin_feed': AdminFeedPostComment,
        'admin': AdminFeedPostComment,
        'feed': AdminFeedPostComment,
        'legacy_admin_post': AdminPostComment,
    }
    hinted_model = model_map.get(comment_type)
    if hinted_model:
        return [hinted_model]
    return [Comment, AdminPostComment, AdminFeedPostComment]


def _get_comment_candidates(comment_id, comment_type=''):
    models = _comment_models_for_hint(comment_type)
    candidates = [obj for model in models if (obj := model.objects.filter(pk=comment_id).first())]

    if not candidates and comment_type:
        fallback_models = [Comment, AdminPostComment, AdminFeedPostComment]
        candidates = [obj for model in fallback_models if (obj := model.objects.filter(pk=comment_id).first())]

    return candidates


def _comment_owner_matches(comment, user):
    if isinstance(comment, AdminFeedPostComment):
        return comment.user == user.username
    return comment.user == user


def _resolve_owned_comment(request, comment_id):
    candidates = _get_comment_candidates(comment_id, _get_comment_type_hint(request))
    if not candidates:
        return None, Response({'error': 'Not found'}, status=404)

    if request.user.is_staff:
        return candidates[0], None

    for comment in candidates:
        if _comment_owner_matches(comment, request.user):
            return comment, None

    return None, Response({'error': 'Denied'}, status=403)


def _resolve_likeable_comment(request, comment_id):
    candidates = _get_comment_candidates(comment_id, _get_comment_type_hint(request))
    if not candidates:
        return None
    return candidates[0]


def _post_lookup_candidates(post_id):
    raw_id = str(post_id)
    candidates = [raw_id]
    if raw_id.startswith('post-'):
        candidates.append(raw_id.replace('post-', '', 1))
    return candidates


def _clean_lookup_value(value):
    if value is None:
        return ''
    return str(value).strip()


def _get_profile_for_user(user):
    return getattr(user, 'userprofile', None) or getattr(user, 'profile', None)


def _resolve_user_from_identifier(identifier):
    if isinstance(identifier, User):
        return identifier

    value = _clean_lookup_value(identifier)
    if not value:
        return None

    user = User.objects.filter(username__iexact=value).first()
    if user:
        return user

    user = User.objects.filter(email__iexact=value).first()
    if user:
        return user

    profile = UserProfile.objects.select_related('user').filter(full_name__iexact=value).first()
    return profile.user if profile else None


def _serialize_user_identity(identifier, request):
    user = _resolve_user_from_identifier(identifier)
    raw_identifier = _clean_lookup_value(identifier)

    if user:
        profile = _get_profile_for_user(user)
        return {
            'username': user.username,
            'name': _get_profile_display_name(user, profile),
            'avatar': _get_user_avatar(user, request),
        }

    fallback = raw_identifier or 'Admin'
    return {
        'username': fallback,
        'name': fallback,
        'avatar': None,
    }


def _adoption_application_owner_query(user):
    owner_query = Q(submitted_by=user)

    email = _clean_lookup_value(getattr(user, 'email', ''))
    legacy_query = Q()

    if email:
        legacy_query |= Q(email__iexact=email)

    profile = _get_profile_for_user(user)
    possible_names = {
        _clean_lookup_value(user.username),
        _clean_lookup_value(getattr(profile, 'full_name', '')),
        _clean_lookup_value(user.get_full_name()),
    }
    for name in possible_names:
        if name:
            legacy_query |= Q(fullName__iexact=name)

    return owner_query | Q(submitted_by__isnull=True) & legacy_query


def _adoption_applications_for_user(user):
    return AdoptionApplication.objects.filter(_adoption_application_owner_query(user)).distinct()


# --- User Logic Views ---
class UserProfileView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, username=None):
        user = get_object_or_404(User, username=username) if username else request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        
        profile_data = UserProfileSerializer(profile, context={'request': request}).data
        profile_data['full_name'] = _get_profile_display_name(user, profile)
        if profile_data.get('avatar') and not profile_data['avatar'].startswith('http'):
            profile_data['avatar'] = request.build_absolute_uri(profile_data['avatar'])

        posts = Post.objects.filter(user=user)
        return Response({
            'profile': profile_data,
            'posts': PostSerializer(posts, many=True, context={'request': request}).data
        })

    @transaction.atomic
    def put(self, request, username=None):
        if username and username != request.user.username:
            return Response({'error': 'Permission denied'}, status=403)
        
        user = User.objects.select_for_update().get(pk=request.user.pk)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        # Keep explicitly empty multipart fields so they are validated rather
        # than treated as omitted optional fields by DRF's HTML form handling.
        data = request.data.dict() if hasattr(request.data, 'dict') else request.data
        form = UserProfileUpdateSerializer(data=data, context={'user': user})
        form.is_valid(raise_exception=True)
        values = form.validated_data
        user_fields = []
        application_fields = {}
        for field in ('avatar', 'cover_photo'):
            if field in values:
                setattr(profile, field, values[field])
        if 'full_name' in values:
            profile.full_name = values['full_name']
            user.first_name = profile.full_name
            user.last_name = ''
            user_fields.extend(['first_name', 'last_name'])
            application_fields['fullName'] = profile.full_name
            # Both profile models exist in this project; keep their names aligned.
            from accounts.models import UserProfile as AccountProfile
            AccountProfile.objects.filter(user=user).update(full_name=profile.full_name)
            RemoteAssessment.objects.filter(application__submitted_by=user).update(adopterName=profile.full_name)
        if 'email' in values:
            user.email = values['email']
            user_fields.append('email')
            application_fields['email'] = user.email
        if user_fields:
            user.save(update_fields=user_fields)
        if application_fields:
            # Only explicitly owned applications are editable; never match by name/email.
            AdoptionApplication.objects.filter(submitted_by=user).update(**application_fields)
            if 'email' in values:
                from admin_api.models import AssessmentEmail
                AssessmentEmail.objects.filter(application__submitted_by=user, status='pending').update(recipient=user.email)
        profile.save()
        profile_data = UserProfileSerializer(profile, context={'request': request}).data
        profile_data['full_name'] = _get_profile_display_name(user, profile)
        if profile_data.get('avatar') and not profile_data['avatar'].startswith('http'):
            profile_data['avatar'] = request.build_absolute_uri(profile_data['avatar'])
        return Response(profile_data)

    def patch(self, request, username=None):
        return self.put(request, username=username)

class MatchingFormView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            data = {
                'preferred_type': request.data.get('type'),
                'preferred_size': request.data.get('size'),
                'preferred_gender': request.data.get('gender'),
                'preferred_age_range': request.data.get('age_range'),
                'activity_level': request.data.get('activity'),
                'hours_away_home': int(request.data.get('hours') or 0),
                'housing_type': request.data.get('housing'),
                'good_with_kids': str(request.data.get('kids', False)).lower() in ['true', '1', 'yes'],
                'good_with_other_pets': str(request.data.get('pets', False)).lower() in ['true', '1', 'yes'],
                'experience_level': request.data.get('experience'),
            }
            obj, created = UserMatchingAnswer.objects.update_or_create(user=request.user, defaults=data)
            return Response({'status': 'Preferences saved', 'id': obj.id})
        except Exception as e:
            return Response({'status': 'saved_locally', 'error': str(e)})

class UserDashboardView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        adoptions = _adoption_applications_for_user(user)
        analytics = {
            'adoption_stats': {
                'pending': adoptions.filter(status='Pending').count(),
                'approved': adoptions.filter(status='Approved').count(),
                'rejected': adoptions.filter(status__in=['Declined', 'Assessment 1 Rejected', 'Post-Adoption Rejected']).count(),
            },
            'profile_metrics': {
                'posts': Post.objects.filter(user=user).count(),
                'likes_received': Like.objects.filter(post__user=user).count(),
            }
        }
        user_app_ids = adoptions.values_list('id', flat=True)
        assessments = RemoteAssessment.objects.filter(appId__in=[str(uid) for uid in user_app_ids])
        latest_report = LedgerBlock.objects.order_by('-index').first()
        suggestions = []
        try:
            prefs = UserMatchingAnswer.objects.get(user=request.user)
            query = Q(status='Ready for Adoption')
            if prefs.preferred_type: query &= Q(species__icontains=prefs.preferred_type)
            if prefs.preferred_size: query &= Q(size__icontains=prefs.preferred_size)
            suggestions = list(Animal.objects.filter(query)[:3].values('id', 'name', 'breed', 'species', 'size', 'gender', 'age'))
        except UserMatchingAnswer.DoesNotExist:
            suggestions = list(Animal.objects.filter(status='Ready for Adoption').order_by('?')[:3].values('id', 'name', 'breed', 'species', 'size', 'gender', 'age'))

        return Response({
            'analytics': analytics,
            'assessments': RemoteAssessmentSerializer(assessments, many=True).data,
            'transparency_report': {
                'index': latest_report.index if latest_report else 0,
                'description': latest_report.description if latest_report else "No records yet",
                'amount': latest_report.amount if latest_report else 0,
                'timestamp': latest_report.timestamp if latest_report else None
            },
            'animal_suggestions': suggestions
        })

class NotificationListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def _visible_queryset(self, request):
        audience = request.query_params.get('audience')
        if request.user.is_staff and audience == Notification.AUDIENCE_ADMIN:
            return Notification.objects.filter(
                Q(audience=Notification.AUDIENCE_ADMIN),
                Q(user=request.user) | Q(user__isnull=True),
            )

        return Notification.objects.filter(
            user=request.user,
            audience=Notification.AUDIENCE_USER,
        )

    def get(self, request):
        one_month_ago = timezone.now() - timedelta(days=30)
        self._visible_queryset(request).filter(created_at__lt=one_month_ago).delete()
        notifications = self._visible_queryset(request).order_by('-created_at')
        return Response(NotificationSerializer(notifications, many=True).data)

    def patch(self, request):
        ids = request.data.get('ids')
        mark_all = bool(request.data.get('all'))
        is_read = request.data.get('is_read', True)
        if isinstance(is_read, str):
            is_read = is_read.strip().lower() not in {'false', '0', 'no'}

        notifications = self._visible_queryset(request)
        if not mark_all:
            if not isinstance(ids, list) or not ids:
                return Response({'error': 'Provide ids or set all=true.'}, status=status.HTTP_400_BAD_REQUEST)
            notifications = notifications.filter(id__in=ids)

        updated = notifications.update(is_read=bool(is_read))
        return Response({'updated': updated})


class PublicAnimalListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        animals = list(Animal.objects.prefetch_related('stories').all().order_by('-created_at'))
        # Load application status once for the whole list rather than querying
        # separately for each animal during serialization.
        applications = (
            AdoptionApplication.objects.filter(animalId__in=[str(animal.id) for animal in animals])
            .exclude(status__in=RELEASED_APPLICATION_STATUSES)
            .only('animalId', 'status', 'submittedAt')
            .order_by('-submittedAt', '-id')
        )
        latest_by_animal = {}
        for application in applications:
            latest_by_animal.setdefault(str(application.animalId), application)
        for animal in animals:
            animal._blocking_application = latest_by_animal.get(str(animal.id))
            animal._blocking_application_loaded = True
        return Response(PublicAnimalSerializer(animals, many=True, context={'request': request}).data)


class PublicAnimalDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, animal_id):
        animal = get_object_or_404(Animal.objects.prefetch_related('stories'), id=animal_id)
        return Response(PublicAnimalSerializer(animal, context={'request': request}).data)


class PublicStoryListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        stories = Story.objects.select_related('animal').all().order_by('-date')
        return Response(PublicStorySerializer(stories, many=True, context={'request': request}).data)


class PublicImpactStatsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        # These public statistics need only animal IDs and statuses, never the
        # full application rows containing applicant details and uploads.
        applications = list(AdoptionApplication.objects.values_list('animalId', 'status'))
        adopted_statuses = {
            'Completed',
            'Verified Adopted',
            'Post-Adoption Done', 'Monitoring Passed',
            'Awaiting Feed Post',
            'Approved',
        }

        pending_animal_ids = [
            str(animal_id) for animal_id, application_status in applications
            if application_status in {'Pending', 'Under Review'}
        ]
        adopted_animal_ids = [
            str(animal_id) for animal_id, application_status in applications
            if application_status in adopted_statuses
        ]

        return Response({
            'rescued': Animal.objects.count(),
            'adoptions': sum(application_status in adopted_statuses for _, application_status in applications),
            'intakes': IntakeRecord.objects.count(),
            'pending_animal_ids': pending_animal_ids,
            'adopted_animal_ids': adopted_animal_ids,
        })


class PublicLedgerView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        ledger_blocks = LedgerBlock.objects.prefetch_related('receipts').all().order_by('index')
        return Response(LedgerSerializer(ledger_blocks, many=True, context={'request': request}).data)


class UserAnimalListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        animals = Animal.objects.all().order_by('-created_at')
        user_applications = _adoption_applications_for_user(request.user)
        app_map = {str(app.animalId): app for app in user_applications}
        
        # Find adopted applications so adopted animals are correctly labeled
        adopted_app_map = {}
        for app in AdoptionApplication.objects.filter(status__in=ADOPTED_APPLICATION_STATUSES):
            adopted_app_map.setdefault(str(app.animalId), app)

        try: 
            prefs = UserMatchingAnswer.objects.get(user=request.user)
        except UserMatchingAnswer.DoesNotExist: 
            prefs = None

        data = []
        for animal in animals:
            animal_id = str(animal.id)
            
            # Use the current user's application first, then adopted app
            # This ensures each user sees their OWN application status
            if animal_id in app_map:
                animal._blocking_application = app_map[animal_id]
            elif animal_id in adopted_app_map:
                animal._blocking_application = adopted_app_map[animal_id]
            else:
                animal._blocking_application = None
            animal._blocking_application_loaded = True
            
            animal_data = AnimalSerializer(animal).data
            
            if animal_id in app_map:
                application = app_map[animal_id]
                animal_data['my_application'] = {
                    'id': application.id, 'status': application.status, 'date': application.submittedAt
                }
            else: 
                animal_data['my_application'] = None
            
            animal_data['matchPercentage'] = _calculate_match_score(animal, prefs) if prefs else None
            data.append(animal_data)
        
        if prefs: 
            data.sort(key=lambda x: (x['matchPercentage'] is None, x['matchPercentage'] or 0), reverse=True)
        return Response(data)


class MyAdoptionApplicationListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        applications = _adoption_applications_for_user(request.user).order_by('-submittedAt')
        return Response(AdoptionApplicationAdminSerializer(applications, many=True, context={'request': request}).data)

class ApplyAdoptionView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        animal_id = request.data.get('animal_id') or request.POST.get('animal_id')
        if not animal_id: 
            return Response({'error': 'Animal ID is missing'}, status=status.HTTP_400_BAD_REQUEST)
        
        animal = get_object_or_404(Animal, id=animal_id)

        # ── CHECK 1: Is the animal already adopted by any application? ──
        if AdoptionApplication.objects.filter(
            animalId=str(animal.id),
            status__in=ADOPTED_APPLICATION_STATUSES
        ).exists():
            return Response({'error': 'This animal has already been adopted.'}, status=status.HTTP_400_BAD_REQUEST)

        if animal_has_adopted_application(animal):
            return Response({'error': 'This animal has already been adopted.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── CHECK 2: Has the CURRENT user already applied for this animal? ──
        if (
            _adoption_applications_for_user(request.user)
            .exclude(status__in=RELEASED_APPLICATION_STATUSES)
            .filter(animalId=animal.id)
            .exists()
        ):
            return Response({'error': 'You have already applied for this animal.'}, status=status.HTTP_400_BAD_REQUEST)

        # ── CHECK 3: Is the animal available for adoption? ──
        animal_status = (getattr(animal, 'status', '') or '').lower()
        animal_availability = (getattr(animal, 'availability', '') or '').lower()
        if animal_availability != 'available' or animal_status != 'ready for adoption':
            return Response(
                {'error': f'{animal.name} is currently not available for adoption.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── FIX: Build mutable dict WITHOUT deep copying file objects ──
        # request.data.copy() / deepcopy fails on BufferedRandom (uploaded files)
        mutable_data = {}
        for key in request.data:
            values = request.data.getlist(key)
            if len(values) == 1:
                mutable_data[key] = values[0]
            else:
                mutable_data[key] = values

        # Add files from request.FILES (ensures they're in our plain dict)
        for key in request.FILES:
            if key not in mutable_data:
                mutable_data[key] = request.FILES[key]

        # Remove fields the serializer doesn't expect
        for key in ['animal_id', 'captcha_token', 'id_ocr_verified', 'id_ocr_confidence', 'numberOfElderly']:
            mutable_data.pop(key, None)

        # Map frontend field names → backend field names
        if 'numberOfChildren' in mutable_data:
            mutable_data['householdChildren'] = mutable_data.pop('numberOfChildren')
        if 'householdAgreement' not in mutable_data and 'agreedToTerms' in mutable_data:
            mutable_data['householdAgreement'] = mutable_data['agreedToTerms']

        serializer = AdoptionApplicationCreateSerializer(data=mutable_data)
        if serializer.is_valid():
            form_email = mutable_data.get('email')
            form_name = mutable_data.get('fullName')
            
            account_email = _clean_lookup_value(request.user.email)
            final_email = account_email or form_email
            final_name = form_name if form_name else f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
            
            app = serializer.save(
                animalId=animal.id,
                submitted_by=request.user,
                fullName=final_name,
                email=final_email,
            )
            notify_application_submitted(app, animal)
            response_data = AdoptionApplicationAdminSerializer(app, context={'request': request}).data
            response_data['status_message'] = 'Application Submitted'
            response_data['app_id'] = app.id
            return Response(response_data, status=status.HTTP_201_CREATED)
        
        # Return detailed validation errors
        return Response({
            'error': 'Validation failed',
            'details': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)

# --- ASSESSMENT VIEWS ---
class UserAssessmentListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request):
        app_ids = _adoption_applications_for_user(request.user).values_list('id', flat=True)
        assessments = RemoteAssessment.objects.filter(appId__in=[str(uid) for uid in app_ids]).order_by('date', 'time')
        return Response(RemoteAssessmentSerializer(assessments, many=True).data)

class AcceptAssessmentView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request, assessment_id):
        try:
            app_ids = _adoption_applications_for_user(request.user).values_list('id', flat=True)
            assessment = RemoteAssessment.objects.get(id=assessment_id, appId__in=[str(a) for a in app_ids])
        except RemoteAssessment.DoesNotExist:
            return Response({'error': 'Assessment not found'}, status=404)

        if assessment.status not in ('Pending Link', 'Scheduled', 'Pending'):
            return Response({'error': f'Cannot accept assessment with status: {assessment.status}'}, status=400)

        assessment.status = 'Accepted'
        assessment.save(update_fields=['status'])

        from admin_api.services import sync_application_from_assessment
        sync_application_from_assessment(assessment)

        notify_assessment_accepted(assessment)
        return Response(RemoteAssessmentSerializer(assessment).data)

class RequestRescheduleView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request, assessment_id):
        try:
            app_ids = _adoption_applications_for_user(request.user).values_list('id', flat=True)
            assessment = RemoteAssessment.objects.get(id=assessment_id, appId__in=[str(a) for a in app_ids])
            assessment.status = 'Reschedule Requested'
            requested_date = request.data.get('reschedule_requested_date') or request.data.get('date')
            requested_time = request.data.get('reschedule_requested_time') or request.data.get('time')
            reason = request.data.get('reschedule_reason') or request.data.get('reason') or request.data.get('note')

            if requested_date:
                assessment.reschedule_requested_date = parse_date(str(requested_date))
            if requested_time:
                assessment.reschedule_requested_time = parse_time(str(requested_time))
            assessment.reschedule_reason = reason or 'User requested reschedule.'
            assessment.notes = assessment.reschedule_reason
            assessment.save()
            notify_assessment_reschedule_requested(assessment)
            return Response({'status': 'Request sent to admin'})
        except RemoteAssessment.DoesNotExist: return Response({'error': 'Not found'}, status=404)


class CancelAssessmentView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, assessment_id):
        user_applications = _adoption_applications_for_user(request.user)
        app_ids = user_applications.values_list('id', flat=True)
        assessment = RemoteAssessment.objects.filter(id=assessment_id, appId__in=[str(a) for a in app_ids]).first()
        if not assessment:
            return Response({'error': 'Not found'}, status=404)

        from admin_api.scheduling import save_assessment
        if assessment.status != 'Cancelled':
            save_assessment({'status': 'Cancelled'}, assessment)
            notify_assessment_cancelled(assessment)

        return Response({'status': 'cancelled'})


class CancelRescheduleRequestView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, assessment_id):
        app_ids = _adoption_applications_for_user(request.user).values_list('id', flat=True)
        assessment = RemoteAssessment.objects.filter(id=assessment_id, appId__in=[str(a) for a in app_ids]).first()
        if not assessment:
            return Response({'error': 'Not found'}, status=404)

        assessment.status = 'Accepted'
        assessment.reschedule_requested_date = None
        assessment.reschedule_requested_time = None
        assessment.reschedule_reason = ''
        assessment.reschedule_decline_reason = ''
        assessment.save()

        notify_assessment_reschedule_cancelled(assessment)
        return Response(RemoteAssessmentSerializer(assessment).data)

class UserDonationView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request):
        donations = LedgerBlock.objects.all().order_by('-index')
        return Response(LedgerSerializer(donations, many=True, context={'request': request}).data)

# --- FEED & SOCIAL ---
class FurryFeedView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_posts = Post.objects.all()
        admin_posts = AdminPost.objects.filter(status='Published')
        data = []
        
        for p in user_posts:
            images_data = []
            for img in p.images.all():
                images_data.append({
                    'id': img.id, 'url': request.build_absolute_uri(img.image.url),
                    'source': 'viewer', 'supports_comments': True,
                    'likes': img.likes.count(), 'comments': img.comments.count(),
                    'is_liked': ImageLike.objects.filter(user=request.user, image=img).exists()
                })
            last_comments = p.comments.all().order_by('-created_at')[:2]
            author_name = _get_profile_display_name(p.user, _get_profile_for_user(p.user))
            data.append({
                'id': p.id, 'type': 'social', 'source': 'viewer',
                'user': p.user.username, 'user_username': p.user.username,
                'author_name': author_name, 'avatar': _get_user_avatar(p.user, request),
                'caption': p.caption, 'images': images_data, 'created_at': p.created_at, 
                'likes': p.likes.count(), 'comments': p.comments.count(),
                'is_liked': Like.objects.filter(user=request.user, post=p).exists(),
                'recent_comments': self._format_comments(last_comments, request)
            })
        
        for p in admin_posts:
            admin_images = []
            for img in p.images_rel.all():
                try:
                    image_url = request.build_absolute_uri(img.image.url)
                except Exception:
                    image_url = None

                if image_url:
                    admin_images.append({
                        'id': img.id,
                        'url': image_url,
                        'source': 'admin',
                        'supports_comments': False,
                        'likes': img.image_likes.count(),
                        'comments': 0,
                        'is_liked': img.image_likes.filter(user=request.user.username).exists(),
                    })

            admin_comments_qs = p.post_comments.all().order_by('-created_at')
            last_admin_comments = admin_comments_qs[:2]
            author_identity = _serialize_user_identity(p.author, request)
            reactions_count = p.post_likes.count()
            comments_count = admin_comments_qs.count()
            data.append({
                'id': p.id,
                'type': 'news',
                'source': 'admin',
                'user': author_identity['username'],
                'author': p.author,
                'author_username': author_identity['username'],
                'author_name': author_identity['name'],
                'avatar': author_identity['avatar'],
                'title': p.title,
                'content': p.content,
                'caption': p.content,
                'category': p.category,
                'images': admin_images,
                'created_at': p.created_at,
                'likes': reactions_count,
                'comments': comments_count,
                'metrics': {
                    'views': p.views,
                    'reactions': reactions_count,
                    'comments': comments_count,
                },
                'is_liked': p.post_likes.filter(user=request.user.username).exists(),
                'recent_comments': self._format_comments_admin(last_admin_comments, request)
            })
        data.sort(key=lambda x: x['created_at'], reverse=True)
        return Response(data)

    def _format_comments(self, comments, request):
        data = []
        for c in comments:
            data.append({
                'id': c.id,
                'user': c.user.username,
                'username': c.user.username,
                'name': _get_profile_display_name(c.user, _get_profile_for_user(c.user)),
                'text': c.text,
                'likes': c.likes.count(),
                'is_liked': CommentLike.objects.filter(user=request.user, comment=c).exists(),
                'parent_id': c.parent.id if c.parent else None,
                'created_at': c.created_at,
                'avatar': _get_user_avatar(c.user, request),
            })
        return data

    def _format_comments_admin(self, comments, request):
        data = []
        for c in comments:
            author_identity = _serialize_user_identity(c.user, request)
            data.append({
                'id': c.id,
                'user': author_identity['username'],
                'username': author_identity['username'],
                'name': author_identity['name'],
                'text': c.text,
                'likes': c.likes_rel.count(),
                'is_liked': c.likes_rel.filter(user=request.user.username).exists(),
                'parent_id': c.parent.id if c.parent else None,
                'created_at': c.created_at,
                'avatar': author_identity['avatar'],
            })
        return data

    def post(self, request):
        serializer = PostSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            post = serializer.save(user=request.user)
            notify_user_post_created(post)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class LikePostView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, post_id):
        candidates = _post_lookup_candidates(post_id)
        numeric_id = next((int(candidate) for candidate in candidates if candidate.isdigit()), None)

        if numeric_id is not None:
            post = Post.objects.filter(id=numeric_id).first()
            if post:
                like, created = Like.objects.get_or_create(user=request.user, post=post)
                if not created:
                    like.delete()
                else:
                    notify_user_post_interaction(post, request.user, 'like')
                return Response({'status': 'toggled', 'type': 'social'})

        admin_post = AdminPost.objects.filter(id__in=candidates).first()
        if admin_post:
            like, created = AdminFeedPostLike.objects.get_or_create(
                post=admin_post,
                user=request.user.username,
            )
            if not created:
                like.delete()
            else:
                notify_admin_post_interaction(admin_post, request.user, 'like')
            return Response({
                'status': 'toggled',
                'type': 'news',
                'is_liked': created,
                'likes': admin_post.post_likes.count(),
            })

        return Response({'error': 'Post not found'}, status=404)

class GetPostLikersView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        candidates = _post_lookup_candidates(post_id)
        numeric_id = next((int(candidate) for candidate in candidates if candidate.isdigit()), None)

        data = []
        if numeric_id is not None and Post.objects.filter(id=numeric_id).exists():
            likes = Like.objects.filter(post_id=numeric_id)
            for like in likes:
                data.append({'id': like.user.id, 'user_username': like.user.username, 'name': like.user.username})
        else:
            admin_post = AdminPost.objects.filter(id__in=candidates).first()
            if admin_post:
                likes = admin_post.post_likes.all().order_by('-created_at')
                for like in likes:
                    user = _resolve_user_from_identifier(like.user)
                    identity = _serialize_user_identity(like.user, request)
                    data.append({
                        'id': user.id if user else None,
                        'user_username': identity['username'],
                        'username': identity['username'],
                        'name': identity['name'],
                        'avatar': identity['avatar'],
                    })
        return Response(data)

class CommentView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, post_id):
        text = request.data.get('text')
        parent_id = request.data.get('parent_id')
        candidates = _post_lookup_candidates(post_id)
        numeric_id = next((int(candidate) for candidate in candidates if candidate.isdigit()), None)

        if numeric_id is not None:
            post = Post.objects.filter(id=numeric_id).first()
            if post:
                parent = Comment.objects.filter(id=parent_id).first() if parent_id else None
                comment = Comment.objects.create(user=request.user, post=post, text=text, parent=parent)
                notify_user_post_interaction(post, request.user, 'comment', comment_id=comment.id)
                return Response({
                    'status': 'created',
                    'id': comment.id,
                    'user': request.user.username,
                    'username': request.user.username,
                    'name': _get_profile_display_name(request.user, _get_profile_for_user(request.user)),
                    'text': text,
                    'parent_id': parent_id,
                    'created_at': comment.created_at,
                    'avatar': _get_user_avatar(request.user, request),
                })

        admin_post = AdminPost.objects.filter(id__in=candidates).first()
        if admin_post:
            parent = AdminFeedPostComment.objects.filter(id=parent_id, post=admin_post).first() if parent_id else None
            comment = AdminFeedPostComment.objects.create(
                user=request.user.username,
                post=admin_post,
                text=text,
                parent=parent,
            )
            notify_admin_post_interaction(admin_post, request.user, 'comment', comment_id=comment.id)
            return Response({
                'status': 'created',
                'id': comment.id,
                'user': request.user.username,
                'username': request.user.username,
                'name': _get_profile_display_name(request.user, _get_profile_for_user(request.user)),
                'text': text,
                'parent_id': parent_id,
                'created_at': comment.created_at,
                'avatar': _get_user_avatar(request.user, request),
            })

        return Response({'error': 'Post not found'}, status=404)

class GetCommentsView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        candidates = _post_lookup_candidates(post_id)
        numeric_id = next((int(candidate) for candidate in candidates if candidate.isdigit()), None)

        data = []
        if numeric_id is not None and Post.objects.filter(id=numeric_id).exists():
            comments = Comment.objects.filter(post_id=numeric_id).order_by('created_at')
            for c in comments:
                data.append({
                    'id': c.id,
                    'user': c.user.username,
                    'username': c.user.username,
                    'name': _get_profile_display_name(c.user, _get_profile_for_user(c.user)),
                    'text': c.text,
                    'parent_id': c.parent.id if c.parent else None,
                    'likes': c.likes.count(),
                    'is_liked': CommentLike.objects.filter(user=request.user, comment=c).exists(),
                    'created_at': c.created_at,
                    'avatar': _get_user_avatar(c.user, request),
                })
        else:
            admin_post = AdminPost.objects.filter(id__in=candidates).first()
            if admin_post:
                comments = admin_post.post_comments.all().order_by('created_at')
                for c in comments:
                    author_identity = _serialize_user_identity(c.user, request)
                    data.append({
                        'id': c.id,
                        'user': author_identity['username'],
                        'username': author_identity['username'],
                        'name': author_identity['name'],
                        'text': c.text,
                        'parent_id': c.parent.id if c.parent else None,
                        'likes': c.likes_rel.count(),
                        'is_liked': c.likes_rel.filter(user=request.user.username).exists(),
                        'created_at': c.created_at,
                        'avatar': author_identity['avatar'],
                    })
        return Response(data)

# --- EDIT/DELETE VIEWS (WITH ADMIN OVERRIDE) ---

class PostDetailView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        
        if post.user != request.user and not request.user.is_staff: 
            return Response({'error': 'Denied. You do not own this post.'}, status=403)
            
        if 'caption' in request.data: post.caption = request.data.get('caption')
        if hasattr(post, 'status') and 'status' in request.data: post.status = request.data.get('status')
            
        post.save()
        return Response({'status': 'updated'})

    def delete(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        
        if post.user != request.user and not request.user.is_staff: 
            return Response({'error': 'Denied. You do not have permission.'}, status=403)

        notify_user_post_deleted(post)
        post.delete()
        return Response({'status': 'deleted'})

class CommentDetailView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        comment, error_response = _resolve_owned_comment(request, pk)
        if error_response:
            return error_response

        comment.text = request.data.get('text', comment.text)
        comment.save()
        return Response({'status': 'updated'})

    def delete(self, request, pk):
        comment, error_response = _resolve_owned_comment(request, pk)
        if error_response:
            return error_response

        comment.delete()
        return Response({'status': 'deleted'})

# --- IMAGE INTERACTION VIEWS ---
class RecordImageView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request, image_id):
        return Response({'status': 'viewed'})

class GetImageViewersView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request, image_id):
        return Response([])

class LikeImageView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request, image_id):
        try:
            image = PostImage.objects.get(id=image_id)
            like, created = ImageLike.objects.get_or_create(user=request.user, image=image)
            if not created:
                like.delete()
            else:
                notify_user_post_interaction(image.post, request.user, 'like', image_id=image.id)
            return Response({'status': 'toggled'})
        except PostImage.DoesNotExist: return Response({'error': 'Image not found'}, status=404)

class GetImageLikersView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request, image_id):
        try:
            likes = ImageLike.objects.filter(image_id=image_id)
            data = [{'id': like.user.id, 'user_username': like.user.username, 'name': like.user.username} for like in likes]
            return Response(data)
        except Exception: return Response([])

class CommentImageView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request, image_id):
        text = request.data.get('text')
        try:
            image = PostImage.objects.get(id=image_id)
            comment = ImageComment.objects.create(user=request.user, image=image, text=text)
            notify_user_post_interaction(image.post, request.user, 'comment', comment_id=comment.id, image_id=image.id)
            return Response({'status': 'created'})
        except PostImage.DoesNotExist: return Response({'error': 'Image not found'}, status=404)

class GetImageCommentsView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def get(self, request, image_id):
        comments = ImageComment.objects.filter(image_id=image_id).order_by('created_at')
        data = [{'id': c.id, 'user_username': c.user.username, 'text': c.text, 'created_at': c.created_at, 'avatar': _get_user_avatar(c.user, request)} for c in comments]
        return Response(data)

class ImageCommentDetailView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    def put(self, request, pk):
        try:
            comment = ImageComment.objects.get(pk=pk)
            if comment.user != request.user and not request.user.is_staff: return Response({'error': 'Denied'}, status=403)
            comment.text = request.data.get('text', comment.text)
            comment.save()
            return Response({'status': 'updated'})
        except ImageComment.DoesNotExist: return Response({'error': 'Not found'}, status=404)
    def delete(self, request, pk):
        try:
            comment = ImageComment.objects.get(pk=pk)
            if comment.user != request.user and not request.user.is_staff: return Response({'error': 'Denied'}, status=403)
            comment.delete()
            return Response({'status': 'deleted'})
        except ImageComment.DoesNotExist: return Response({'error': 'Not found'}, status=404)

@api_view(['POST'])
@authentication_classes([JWTAuthentication])
def like_comment(request, comment_id):
    if not request.user.is_authenticated: return Response({'error': 'Auth required'}, status=401)
    comment = _resolve_likeable_comment(request, comment_id)
    if not comment:
        return Response({'error': 'Comment not found'}, status=404)

    if isinstance(comment, Comment):
        like, created = CommentLike.objects.get_or_create(user=request.user, comment=comment)
        if not created: like.delete()
        return Response({'status': 'toggled'})
    if isinstance(comment, AdminPostComment):
        like, created = AdminPostCommentLike.objects.get_or_create(user=request.user, comment=comment)
        if not created: like.delete()
        return Response({'status': 'toggled'})

    like, created = AdminFeedPostCommentLike.objects.get_or_create(user=request.user.username, comment=comment)
    if not created: like.delete()
    return Response({'status': 'toggled'})

# --- ADMIN ADOPTION APPLICATION VIEWS ---
class AdminAdoptionApplicationDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = AdoptionApplication.objects.all()
    serializer_class = AdoptionApplicationAdminSerializer
    authentication_classes = [JWTAuthentication] 
    permission_classes = [IsAdminUser]
