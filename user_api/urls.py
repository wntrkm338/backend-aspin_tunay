from django.urls import path
from .views import (
    MyTokenObtainPairView,
    UserProfileView, UserDashboardView, NotificationListView,
    UserAnimalListView, ApplyAdoptionView,
    PublicAnimalListView, PublicAnimalDetailView, PublicStoryListView, PublicImpactStatsView,
    PublicLedgerView,
    MyAdoptionApplicationListView,
    RequestRescheduleView,
    FurryFeedView, LikePostView, GetPostLikersView, CommentView, GetCommentsView,
    MatchingFormView, UserDonationView,
    PostDetailView, CommentDetailView,
    like_comment,
    RecordImageView, GetImageViewersView,
    LikeImageView, GetImageLikersView,
    CommentImageView, GetImageCommentsView, ImageCommentDetailView,
    UserAssessmentListView,
    AcceptAssessmentView,
    CancelAssessmentView,
    CancelRescheduleRequestView,
    AdminAdoptionApplicationDetailView,
)

urlpatterns = [
    # Auth
    path('login/', MyTokenObtainPairView.as_view(), name='login'),

    # Profile & Dashboard
    path('profile/', UserProfileView.as_view(), name='my-profile'),
    path('profile/<str:username>/', UserProfileView.as_view(), name='user-profile'),
    path('dashboard/', UserDashboardView.as_view(), name='dashboard'),
    path('notifications/', NotificationListView.as_view(), name='notifications'),

    # Public read-only site data
    path('public/animals/', PublicAnimalListView.as_view(), name='public-animals'),
    path('public/animals/<str:animal_id>/', PublicAnimalDetailView.as_view(), name='public-animal-detail'),
    path('public/stories/', PublicStoryListView.as_view(), name='public-stories'),
    path('public/impact-stats/', PublicImpactStatsView.as_view(), name='public-impact-stats'),
    path('public/ledger/', PublicLedgerView.as_view(), name='public-ledger'),

    # Feed
    path('feed/', FurryFeedView.as_view(), name='feed'),
    path('post/<str:post_id>/like/', LikePostView.as_view(), name='like-post'),
    path('post/<str:post_id>/likers/', GetPostLikersView.as_view(), name='get-post-likers'),
    path('post/<str:post_id>/comment/', CommentView.as_view(), name='comment-post'),
    path('post/<str:post_id>/comments/', GetCommentsView.as_view(), name='get-comments'),

    # Post Edit/Delete
    path('post/<int:pk>/', PostDetailView.as_view(), name='post-detail'),

    # Comment Actions
    path('comment/<int:comment_id>/like/', like_comment, name='like-comment'),
    path('comment/<int:pk>/', CommentDetailView.as_view(), name='comment-detail'),

    # Image Actions
    path('image/<int:image_id>/view/', RecordImageView.as_view(), name='record-image-view'),
    path('image/<int:image_id>/viewers/', GetImageViewersView.as_view(), name='get-image-viewers'),
    path('image/<int:image_id>/like/', LikeImageView.as_view(), name='like-image'),
    path('image/<int:image_id>/likers/', GetImageLikersView.as_view(), name='get-image-likers'),
    path('image/<int:image_id>/comment/', CommentImageView.as_view(), name='comment-image'),
    path('image/<int:image_id>/comments/', GetImageCommentsView.as_view(), name='get-image-comments'),
    path('image/comment/<int:pk>/', ImageCommentDetailView.as_view(), name='image-comment-detail'),

    # Animal & Adoption
    path('animals/', UserAnimalListView.as_view(), name='user-animals'),
    path('animals/apply/', ApplyAdoptionView.as_view(), name='apply-adoption'),
    path('my/applications/', MyAdoptionApplicationListView.as_view(), name='my-applications'),

    # Matching
    path('matching/', MatchingFormView.as_view(), name='matching'),

    # Assessments
    path('assessments/', UserAssessmentListView.as_view(), name='user-assessments'),
    path('assessments/<str:assessment_id>/accept/', AcceptAssessmentView.as_view(), name='accept-assessment'),
    path('assessments/<str:assessment_id>/reschedule/', RequestRescheduleView.as_view(), name='reschedule-assessment'),
    path('assessments/<str:assessment_id>/cancel/', CancelAssessmentView.as_view(), name='cancel-assessment'),
    path('assessments/<str:assessment_id>/cancel-reschedule/', CancelRescheduleRequestView.as_view(), name='cancel-reschedule'),

    # Donations
    path('donations/', UserDonationView.as_view(), name='user-donations'),

    # --- ADMIN ENDPOINTS ---
    path('admin/applications/<int:pk>/', AdminAdoptionApplicationDetailView.as_view(), name='admin-application-detail'),
]
