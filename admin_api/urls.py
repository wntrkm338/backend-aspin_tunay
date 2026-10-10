from django.urls import path, include
from rest_framework.permissions import IsAdminUser
from rest_framework.routers import DefaultRouter
from .backup_views import export_data, preview_import, confirm_import
from backend.integrations import create_checkout_session, paymongo_status

# Make sure you import the custom functions from views.py
from .views import (
    AnimalViewSet, 
    StoryViewSet, 
    IntakeRecordViewSet, 
    AdoptionApplicationViewSet, 
    LedgerBlockViewSet, 
    RemoteAssessmentViewSet, 
    PostViewSet,
    toggle_like_post,
    toggle_like_post_image,
    add_comment_post,
    get_post_likers,
    get_post_comments,
    admin_me,
    remote_assessment_call_ended,
)


class AdminOnlyRouter(DefaultRouter):
    class APIRootView(DefaultRouter.APIRootView):
        permission_classes = [IsAdminUser]


router = AdminOnlyRouter()
router.register(r'animals', AnimalViewSet)
router.register(r'stories', StoryViewSet)
router.register(r'intake-records', IntakeRecordViewSet)
router.register(r'adoption-applications', AdoptionApplicationViewSet)
router.register(r'ledger-blocks', LedgerBlockViewSet)
router.register(r'assessments', RemoteAssessmentViewSet, basename='assessment')
router.register(r'posts', PostViewSet, basename='post')

urlpatterns = [
    path('admin_api/settings/export/', export_data, name='admin-data-export'),
    path('admin_api/settings/import/preview/', preview_import, name='admin-data-preview'),
    path('admin_api/settings/import/confirm/', confirm_import, name='admin-data-import'),
    # Standard ViewSet Routes (CRUD)
    path('admin_api/', include(router.urls)),
    path('admin_api/me/', admin_me, name='admin-me'),
    

    # ---------------------------------------------------------
    # ADMIN POST ROUTES (This fixes your 404 error!)
    # ---------------------------------------------------------
    path('admin_api/posts/<str:pk>/like/', toggle_like_post, name='admin-post-like'),
    path('admin_api/post-images/<int:image_id>/like/', toggle_like_post_image, name='admin-post-image-like'),
    path('admin_api/posts/<str:pk>/comment/', add_comment_post, name='admin-post-comment'),
    path('admin_api/posts/<str:pk>/likers/', get_post_likers, name='admin-post-likers'),
    path('admin_api/posts/<str:pk>/comments/', get_post_comments, name='admin-post-comments'),


    # ---------------------------------------------------------
    path("api/assessments/call-ended/", remote_assessment_call_ended, name="assessment-call-ended"),
    path("api/paymongo/status/", paymongo_status, name="paymongo-status"),
    path("api/paymongo/checkout/", create_checkout_session, name="paymongo-checkout"),
]
