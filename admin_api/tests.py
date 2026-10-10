from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from user_api.models import UserProfile
from .models import (
    AdoptionApplication,
    Animal,
    LedgerBlock,
    Post,
    PostComment,
    PostCommentLike,
    PostImage,
    PostImageLike,
    RemoteAssessment,
)
from .serializers import LEDGER_DIFFICULTY


class AdminApiPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin",
            password="SafePass123!",
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            username="regular",
            password="SafePass123!",
            is_staff=False,
        )

    def test_admin_api_root_requires_admin_user(self):
        anonymous_response = self.client.get("/admin_api/")
        self.assertIn(anonymous_response.status_code, [401, 403])

        self.client.force_authenticate(user=self.regular_user)
        regular_response = self.client.get("/admin_api/")
        self.assertEqual(regular_response.status_code, 403)

        self.client.force_authenticate(user=self.admin_user)
        admin_response = self.client.get("/admin_api/")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("animals", admin_response.data)

    def test_admin_api_viewsets_require_admin_user(self):
        Animal.objects.create(id="A001", name="Bantay")

        anonymous_response = self.client.get("/admin_api/animals/")
        self.assertIn(anonymous_response.status_code, [401, 403])

        self.client.force_authenticate(user=self.regular_user)
        regular_response = self.client.get("/admin_api/animals/")
        self.assertEqual(regular_response.status_code, 403)

        self.client.force_authenticate(user=self.admin_user)
        admin_response = self.client.get("/admin_api/animals/")
        self.assertEqual(admin_response.status_code, 200)

    def test_admin_api_custom_post_routes_require_admin_user(self):
        post = Post.objects.create(
            id="post-permission",
            title="Admin update",
            content="Feed content",
            category="NEWS",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )

        anonymous_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Should not pass"},
            format="json",
        )
        self.assertIn(anonymous_response.status_code, [401, 403])

        self.client.force_authenticate(user=self.regular_user)
        regular_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Should not pass"},
            format="json",
        )
        self.assertEqual(regular_response.status_code, 403)

        self.client.force_authenticate(user=self.admin_user)
        admin_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Allowed admin comment"},
            format="json",
        )
        self.assertEqual(admin_response.status_code, 200)


class AdminPostFeedInteractionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin",
            password="SafePass123!",
            first_name="Rhealyn",
            is_staff=True,
        )
        UserProfile.objects.create(user=self.admin_user, full_name="Rhealyn Admin")
        self.client.force_authenticate(user=self.admin_user)

    def test_admin_post_comments_support_replies_likes_and_author_profile(self):
        post = Post.objects.create(
            id="post-1",
            title="Admin update",
            content="Feed content",
            category="NEWS",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )

        first_comment_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Root comment"},
            format="json",
        )
        self.assertEqual(first_comment_response.status_code, 200)
        root_comment = PostComment.objects.get(id=first_comment_response.data["comment"]["id"])

        reply_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Reply comment", "parent_id": root_comment.id},
            format="json",
        )
        self.assertEqual(reply_response.status_code, 200)
        reply_comment = PostComment.objects.get(id=reply_response.data["comment"]["id"])
        self.assertEqual(reply_comment.parent_id, root_comment.id)

        like_response = self.client.post(f"/api/comment/{root_comment.id}/like/")
        self.assertEqual(like_response.status_code, 200)
        self.assertTrue(PostCommentLike.objects.filter(comment=root_comment, user="admin").exists())

        comments_response = self.client.get(f"/admin_api/posts/{post.id}/comments/")
        self.assertEqual(comments_response.status_code, 200)
        comments_by_id = {item["id"]: item for item in comments_response.data}

        self.assertEqual(comments_by_id[root_comment.id]["likes"], 1)
        self.assertTrue(comments_by_id[root_comment.id]["is_liked"])
        self.assertEqual(comments_by_id[root_comment.id]["username"], "admin")
        self.assertEqual(comments_by_id[root_comment.id]["name"], "Rhealyn Admin")
        self.assertEqual(comments_by_id[reply_comment.id]["parent_id"], root_comment.id)

        posts_response = self.client.get("/admin_api/posts/")
        self.assertEqual(posts_response.status_code, 200)
        feed_post = posts_response.data[0]

        self.assertEqual(feed_post["author_username"], "admin")
        self.assertEqual(feed_post["author_name"], "Rhealyn Admin")
        self.assertEqual(feed_post["metrics"]["comments"], 2)

    def test_admin_post_images_can_be_liked_individually(self):
        post = Post.objects.create(
            id="post-2",
            title="Photo update",
            content="Photo feed content",
            category="SOCIAL",
            status="Published",
            author="admin",
            date="2026-04-27",
        )
        image = PostImage.objects.create(
            post=post,
            image="feed_posts/admin-photo.gif",
        )

        like_response = self.client.post(f"/admin_api/post-images/{image.id}/like/")
        self.assertEqual(like_response.status_code, 200)
        self.assertTrue(like_response.data["is_liked"])
        self.assertEqual(like_response.data["likes"], 1)
        self.assertTrue(PostImageLike.objects.filter(image=image, user="admin").exists())

        posts_response = self.client.get("/admin_api/posts/")
        self.assertEqual(posts_response.status_code, 200)
        feed_image = posts_response.data[0]["images"][0]

        self.assertEqual(feed_image["id"], image.id)
        self.assertEqual(feed_image["likes"], 1)
        self.assertTrue(feed_image["is_liked"])
        self.assertEqual(feed_image["source"], "admin")
        self.assertFalse(feed_image["supports_comments"])

    def test_admin_me_returns_profile_payload(self):
        response = self.client.get("/admin_api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["username"], "admin")
        self.assertEqual(response.data["user"]["full_name"], "Rhealyn Admin")
        self.assertTrue(response.data["user"]["is_staff"])
        self.assertEqual(response.data["profile"]["username"], "admin")


class LedgerBlockTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin",
            password="SafePass123!",
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin_user)

    def test_created_ledger_block_is_mined_and_chained(self):
        response = self.client.post(
            "/admin_api/ledger-blocks/",
            {
                "timestamp": "2026-06-24T00:00:00.000Z",
                "data": {
                    "type": "EXPENSE",
                    "description": "Vet supplies",
                    "amount": 1500,
                    "category": "Medical",
                    "recipient": "Clinic",
                    "receipts": [],
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["hash"].startswith("0" * LEDGER_DIFFICULTY))
        self.assertGreaterEqual(response.data["nonce"], 0)
        self.assertEqual(response.data["previousHash"], "0" * 64)
        self.assertEqual(LedgerBlock.objects.count(), 1)


class RemoteAssessmentSchedulingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin",
            password="SafePass123!",
            is_staff=True,
        )
        self.adopter = User.objects.create_user(
            username="adopter",
            email="adopter@example.com",
            password="SafePass123!",
        )
        self.animal = Animal.objects.create(
            id="A100",
            name="Kopi",
            species="Dog",
            status="Ready for Adoption",
            availability="Available",
        )
        self.application = AdoptionApplication.objects.create(
            submitted_by=self.adopter,
            animalId=self.animal.id,
            status="Under Review",
            fullName="Applicant One",
            age=28,
            civilStatus="Single",
            address="Lucena City",
            contact="09171234567",
            email=self.adopter.email,
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
        )

    def test_admin_schedule_links_assessment_to_user_application(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            "/admin_api/assessments/",
            {
                "id": "RA-1001",
                "application_id": self.application.id,
                "adopterName": "Applicant One",
                "animalName": "Kopi",
                "date": "2026-06-20",
                "time": "09:00",
                "status": "Scheduled",
                "link": "https://meet.jit.si/ShelterCall_RA1001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["appId"]), str(self.application.id))
        self.assertEqual(str(response.data["application_id"]), str(self.application.id))

        assessment = RemoteAssessment.objects.get(id="RA-1001")
        self.assertEqual(assessment.appId, str(self.application.id))

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "Assessment 1 Scheduled")
        self.assertEqual(self.application.assessment_1_status, "scheduled")
        self.assertEqual(self.application.assessment_1_link, "https://meet.jit.si/ShelterCall_RA1001")
        self.assertIsNotNone(self.application.assessment_1_schedule)

        self.client.force_authenticate(user=self.adopter)
        user_response = self.client.get("/api/assessments/")

        self.assertEqual(user_response.status_code, 200)
        self.assertEqual(len(user_response.data), 1)
        self.assertEqual(user_response.data[0]["id"], "RA-1001")
        self.assertEqual(str(user_response.data[0]["appId"]), str(self.application.id))
        self.assertEqual(str(user_response.data[0]["application_id"]), str(self.application.id))
