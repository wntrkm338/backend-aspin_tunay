import hashlib
import json
from datetime import date, datetime, timezone as dt_timezone

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from admin_api.models import (
    AdoptionApplication,
    Animal,
    IntakeRecord,
    LedgerBlock,
    Post as AdminPost,
    PostComment as AdminFeedPostComment,
    PostCommentLike as AdminFeedPostCommentLike,
    PostLike as AdminFeedPostLike,
    Story,
)
from admin_api.serializers import compute_ledger_hash, js_iso_format
from user_api.models import AdminPostLike as LegacyAdminPostLike, Comment, CommentLike, Post, UserProfile


class PublicSiteDataTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_public_animals_and_stories_are_readable_without_admin_api(self):
        animal = Animal.objects.create(
            id="A001",
            name="Bantay",
            species="Dog",
            status="Ready for Adoption",
            availability="Available",
        )
        Story.objects.create(
            animal=animal,
            title="Bantay's Recovery",
            date=date(2026, 5, 8),
            content="Bantay is doing well.",
        )

        animals_response = self.client.get("/api/public/animals/")
        stories_response = self.client.get("/api/public/stories/")
        detail_response = self.client.get("/api/public/animals/A001/")

        self.assertEqual(animals_response.status_code, 200)
        self.assertEqual(stories_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(animals_response.data[0]["id"], "A001")
        self.assertEqual(animals_response.data[0]["stories"][0]["title"], "Bantay's Recovery")
        self.assertEqual(stories_response.data[0]["title"], "Bantay's Recovery")
        self.assertEqual(detail_response.data["stories"][0]["title"], "Bantay's Recovery")

    def test_public_impact_stats_are_sanitized(self):
        Animal.objects.create(id="A002", name="Miming")
        IntakeRecord.objects.create(id="INT-001", name="Miming")
        AdoptionApplication.objects.create(
            animalId="A002",
            status="Pending",
            fullName="Applicant One",
            age=25,
            civilStatus="Single",
            address="Lucena City",
            contact="09171234567",
            email="applicant@example.com",
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
        )

        response = self.client.get("/api/public/impact-stats/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rescued"], 1)
        self.assertEqual(response.data["intakes"], 1)
        self.assertEqual(response.data["pending_animal_ids"], ["A002"])
        self.assertNotIn("applications", response.data)

    def test_public_ledger_is_readable_without_login(self):
        timestamp = datetime(2026, 4, 4, 14, 46, 58, 879000, tzinfo=dt_timezone.utc)
        LedgerBlock.objects.create(
            index=0,
            timestamp=timestamp,
            previousHash="ON_CHAIN",
            hash="0xabc123",
            nonce=0,
            block_type="EXPENSE",
            description="Vet supplies",
            amount=1250.5,
            category="Medical",
            recipient="Clinic",
        )

        response = self.client.get("/api/public/ledger/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["index"], 0)
        self.assertEqual(response.data[0]["timestamp"], js_iso_format(timestamp))
        self.assertEqual(response.data[0]["data"]["type"], "EXPENSE")
        self.assertEqual(response.data[0]["data"]["description"], "Vet supplies")
        self.assertEqual(response.data[0]["data"]["amount"], 1250.5)
        self.assertEqual(response.data[0]["data"]["category"], "Medical")
        self.assertEqual(response.data[0]["data"]["recipient"], "Clinic")
        self.assertEqual(response.data[0]["previousHash"], "ON_CHAIN")
        self.assertEqual(response.data[0]["hash"], "0xabc123")


class UserDonationLedgerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="donor1", password="SafePass123!")
        self.client.force_authenticate(user=self.user)

    def test_user_ledger_uses_canonical_timestamp_for_hash_verification(self):
        timestamp = datetime(2026, 4, 4, 14, 46, 58, 879000, tzinfo=dt_timezone.utc)
        block_hash = compute_ledger_hash(
            index=0,
            timestamp=timestamp,
            block_type="EXPENSE",
            description="Vet supplies",
            amount=1250.5,
            category="Medical",
            recipient="Clinic",
            previous_hash="0" * 64,
            nonce=0,
        )
        LedgerBlock.objects.create(
            index=0,
            timestamp=timestamp,
            previousHash="0" * 64,
            hash=block_hash,
            nonce=0,
            block_type="EXPENSE",
            description="Vet supplies",
            amount=1250.5,
            category="Medical",
            recipient="Clinic",
        )

        response = self.client.get("/api/donations/")

        self.assertEqual(response.status_code, 200)
        block = response.data[0]
        self.assertEqual(block["timestamp"], js_iso_format(timestamp))

        frontend_payload = {
            "index": int(block["index"]),
            "timestamp": block["timestamp"],
            "data": {
                "type": block["data"]["type"],
                "description": block["data"]["description"],
                "amount": float(block["data"]["amount"]),
                "category": block["data"]["category"],
                "recipient": block["data"]["recipient"],
            },
            "previousHash": block["previousHash"],
            "nonce": int(block["nonce"]),
        }
        frontend_hash = hashlib.sha256(
            json.dumps(frontend_payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        self.assertEqual(frontend_hash, block["hash"])


class MyAdoptionApplicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="adopter1",
            email="account@example.com",
            password="SafePass123!",
        )
        self.other_user = User.objects.create_user(
            username="adopter2",
            email="other-account@example.com",
            password="SafePass123!",
        )
        self.animal = Animal.objects.create(
            id="A100",
            name="Kopi",
            species="Dog",
            status="Ready for Adoption",
            availability="Available",
        )
        self.client.force_authenticate(user=self.user)

    def application_payload(self, **overrides):
        payload = {
            "animal_id": self.animal.id,
            "fullName": "Applicant One",
            "age": 28,
            "civilStatus": "Single",
            "address": "Lucena City",
            "contact": "09171234567",
            "email": "form-email@example.com",
            "validIdType": "Passport",
            "residenceType": "Owned",
            "hasPets": False,
            "householdAgreement": "Yes",
            "dogPlace": "Inside",
            "indoorAllowed": "Yes",
            "whyAdopt": "Companionship",
            "lifetimeCommitment": "Yes",
            "agreedToTerms": True,
        }
        payload.update(overrides)
        return payload

    def test_submit_application_links_to_logged_in_user(self):
        response = self.client.post(
            "/api/animals/apply/",
            self.application_payload(email="different-form@example.com"),
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        application = AdoptionApplication.objects.get(id=response.data["app_id"])
        self.assertEqual(application.submitted_by, self.user)
        self.assertEqual(application.email, self.user.email)

        applications_response = self.client.get("/api/my/applications/")
        self.assertEqual(applications_response.status_code, 200)
        self.assertEqual(applications_response.data[0]["id"], application.id)
        self.assertEqual(applications_response.data[0]["applicant"]["username"], self.user.username)

    def test_my_applications_uses_submitted_by_and_excludes_other_users(self):
        my_application = AdoptionApplication.objects.create(
            submitted_by=self.user,
            animalId=self.animal.id,
            fullName="Applicant One",
            age=28,
            civilStatus="Single",
            address="Lucena City",
            contact="09171234567",
            email="old-form-email@example.com",
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
        )
        AdoptionApplication.objects.create(
            submitted_by=self.other_user,
            animalId=self.animal.id,
            fullName="Other Applicant",
            age=30,
            civilStatus="Single",
            address="Lucena City",
            contact="09170000000",
            email="someone-else@example.com",
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
        )

        response = self.client.get("/api/my/applications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [my_application.id])

    def test_legacy_application_matches_username_when_account_has_no_email(self):
        legacy_user = User.objects.create_user(username="Hershey", password="SafePass123!")
        legacy_application = AdoptionApplication.objects.create(
            animalId=self.animal.id,
            fullName="Hershey",
            age=21,
            civilStatus="Single",
            address="Lucena City",
            contact="09171234567",
            email="form-email@example.com",
            validIdType="National ID",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
        )

        self.client.force_authenticate(user=legacy_user)
        response = self.client.get("/api/my/applications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [legacy_application.id])

    def test_animal_with_active_application_is_unavailable_to_other_users(self):
        AdoptionApplication.objects.create(
            submitted_by=self.other_user,
            animalId=self.animal.id,
            fullName="Other Applicant",
            age=30,
            civilStatus="Single",
            address="Lucena City",
            contact="09170000000",
            email="someone-else@example.com",
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
            status="Pending",
        )

        animals_response = self.client.get("/api/animals/")
        self.assertEqual(animals_response.status_code, 200)
        animal_payload = next(item for item in animals_response.data if item["id"] == self.animal.id)
        self.assertTrue(animal_payload["has_application"])
        self.assertFalse(animal_payload["is_available_for_adoption"])
        self.assertEqual(animal_payload["application_status"], "Pending")
        self.assertIsNone(animal_payload["my_application"])

        apply_response = self.client.post(
            "/api/animals/apply/",
            self.application_payload(),
            format="json",
        )
        self.assertEqual(apply_response.status_code, 400)
        self.assertIn("in progress", apply_response.data["error"])

    def test_declined_application_does_not_block_availability(self):
        AdoptionApplication.objects.create(
            submitted_by=self.other_user,
            animalId=self.animal.id,
            fullName="Other Applicant",
            age=30,
            civilStatus="Single",
            address="Lucena City",
            contact="09170000000",
            email="someone-else@example.com",
            validIdType="Passport",
            residenceType="Owned",
            householdAgreement="Yes",
            dogPlace="Inside",
            indoorAllowed="Yes",
            whyAdopt="Companionship",
            lifetimeCommitment="Yes",
            agreedToTerms=True,
            status="Declined",
        )

        animals_response = self.client.get("/api/animals/")
        self.assertEqual(animals_response.status_code, 200)
        animal_payload = next(item for item in animals_response.data if item["id"] == self.animal.id)
        self.assertFalse(animal_payload["has_application"])
        self.assertTrue(animal_payload["is_available_for_adoption"])

        apply_response = self.client.post(
            "/api/animals/apply/",
            self.application_payload(),
            format="json",
        )
        self.assertEqual(apply_response.status_code, 201)


class FurryFeedViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.viewer = User.objects.create_user(username="viewer", password="SafePass123!")
        self.admin = User.objects.create_user(
            username="admin",
            password="SafePass123!",
            first_name="Rhealyn",
            last_name="Admin",
            is_staff=True,
        )
        UserProfile.objects.create(user=self.admin, full_name="Rhealyn Admin")
        self.client.force_authenticate(user=self.viewer)

    def test_news_feed_includes_resolved_admin_identity(self):
        AdminPost.objects.create(
            id="post-admin-name",
            title="Shelter update",
            content="Admin feed content",
            category="News",
            status="Published",
            author="admin",
            date="2026-05-08",
        )

        response = self.client.get("/api/feed/")

        self.assertEqual(response.status_code, 200)
        news_post = response.data[0]
        self.assertEqual(news_post["source"], "admin")
        self.assertEqual(news_post["author_username"], "admin")
        self.assertEqual(news_post["author_name"], "Rhealyn Admin")
        self.assertEqual(news_post["content"], "Admin feed content")
        self.assertIn("metrics", news_post)

    def test_user_admin_post_interactions_are_shared_with_admin_metrics(self):
        post = AdminPost.objects.create(
            id="post-shared-user-activity",
            title="Shelter update",
            content="Admin feed content",
            category="News",
            status="Published",
            author="admin",
            date="2026-05-08",
        )

        like_response = self.client.post(f"/api/post/{post.id}/like/")
        comment_response = self.client.post(
            f"/api/post/{post.id}/comment/",
            {"text": "User-side comment"},
            format="json",
        )

        self.assertEqual(like_response.status_code, 200)
        self.assertEqual(comment_response.status_code, 200)
        self.assertTrue(AdminFeedPostLike.objects.filter(post=post, user="viewer").exists())
        self.assertFalse(LegacyAdminPostLike.objects.filter(post_id=post.id).exists())

        self.client.force_authenticate(user=self.admin)
        admin_posts_response = self.client.get("/admin_api/posts/")

        self.assertEqual(admin_posts_response.status_code, 200)
        admin_post = admin_posts_response.data[0]
        self.assertEqual(admin_post["metrics"]["reactions"], 1)
        self.assertEqual(admin_post["metrics"]["comments"], 1)
        self.assertEqual(admin_post["activity"]["comments"][0]["username"], "viewer")

    def test_admin_post_interactions_are_visible_in_user_feed(self):
        post = AdminPost.objects.create(
            id="post-admin-activity",
            title="Shelter update",
            content="Admin feed content",
            category="News",
            status="Published",
            author="admin",
            date="2026-05-08",
        )

        self.client.force_authenticate(user=self.admin)
        like_response = self.client.post(f"/admin_api/posts/{post.id}/like/")
        comment_response = self.client.post(
            f"/admin_api/posts/{post.id}/comment/",
            {"text": "Admin-side comment"},
            format="json",
        )

        self.assertEqual(like_response.status_code, 200)
        self.assertEqual(comment_response.status_code, 200)

        self.client.force_authenticate(user=self.viewer)
        feed_response = self.client.get("/api/feed/")
        comments_response = self.client.get(f"/api/post/{post.id}/comments/")

        self.assertEqual(feed_response.status_code, 200)
        news_post = next(item for item in feed_response.data if item["id"] == post.id)
        self.assertEqual(news_post["metrics"]["reactions"], 1)
        self.assertEqual(news_post["metrics"]["comments"], 1)
        self.assertEqual(news_post["recent_comments"][0]["username"], "admin")

        self.assertEqual(comments_response.status_code, 200)
        self.assertEqual(comments_response.data[0]["text"], "Admin-side comment")
        self.assertEqual(comments_response.data[0]["username"], "admin")


class CommentDetailViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="commenter1", password="SafePass123!")
        self.other_user = User.objects.create_user(username="commenter2", password="SafePass123!")
        self.client.force_authenticate(user=self.user)

    def test_delete_admin_feed_comment_via_generic_comment_endpoint(self):
        post = AdminPost.objects.create(
            id="feed-post-1",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        comment = AdminFeedPostComment.objects.create(
            post=post,
            user=self.user.username,
            text="Delete me",
        )

        response = self.client.delete(f"/api/comment/{comment.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AdminFeedPostComment.objects.filter(id=comment.id).exists())

    def test_update_admin_feed_comment_via_generic_comment_endpoint(self):
        post = AdminPost.objects.create(
            id="feed-post-2",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        comment = AdminFeedPostComment.objects.create(
            post=post,
            user=self.user.username,
            text="Old text",
        )

        response = self.client.put(
            f"/api/comment/{comment.id}/",
            {"text": "New text"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertEqual(comment.text, "New text")

    def test_like_admin_feed_comment_via_generic_comment_endpoint(self):
        post = AdminPost.objects.create(
            id="feed-post-3",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        comment = AdminFeedPostComment.objects.create(
            post=post,
            user=self.user.username,
            text="Like me",
        )

        first_response = self.client.post(f"/api/comment/{comment.id}/like/")
        second_response = self.client.post(f"/api/comment/{comment.id}/like/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertFalse(
            AdminFeedPostCommentLike.objects.filter(comment=comment, user=self.user.username).exists()
        )

    def test_delete_uses_owned_comment_when_ids_collide_across_tables(self):
        social_post = Post.objects.create(user=self.other_user, caption="Social post")
        social_comment = Comment.objects.create(
            user=self.other_user,
            post=social_post,
            text="Someone else's social comment",
        )

        admin_post = AdminPost.objects.create(
            id="feed-post-4",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        admin_comment = AdminFeedPostComment.objects.create(
            post=admin_post,
            user=self.user.username,
            text="My admin feed comment",
        )

        self.assertEqual(social_comment.id, admin_comment.id)

        response = self.client.delete(f"/api/comment/{admin_comment.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Comment.objects.filter(id=social_comment.id).exists())
        self.assertFalse(AdminFeedPostComment.objects.filter(id=admin_comment.id).exists())

    def test_update_uses_owned_comment_when_ids_collide_across_tables(self):
        social_post = Post.objects.create(user=self.other_user, caption="Social post")
        social_comment = Comment.objects.create(
            user=self.other_user,
            post=social_post,
            text="Someone else's social comment",
        )

        admin_post = AdminPost.objects.create(
            id="feed-post-5",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        admin_comment = AdminFeedPostComment.objects.create(
            post=admin_post,
            user=self.user.username,
            text="Old admin text",
        )

        self.assertEqual(social_comment.id, admin_comment.id)

        response = self.client.put(
            f"/api/comment/{admin_comment.id}/",
            {"text": "Updated admin text"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        social_comment.refresh_from_db()
        admin_comment.refresh_from_db()
        self.assertEqual(social_comment.text, "Someone else's social comment")
        self.assertEqual(admin_comment.text, "Updated admin text")

    def test_like_uses_comment_type_hint_when_ids_collide_across_tables(self):
        social_post = Post.objects.create(user=self.other_user, caption="Social post")
        social_comment = Comment.objects.create(
            user=self.other_user,
            post=social_post,
            text="Someone else's social comment",
        )

        admin_post = AdminPost.objects.create(
            id="feed-post-6",
            title="Sample",
            content="Hello",
            category="News",
            status="Published",
            author="Admin",
            date="2026-04-27",
        )
        admin_comment = AdminFeedPostComment.objects.create(
            post=admin_post,
            user=self.other_user.username,
            text="Admin feed comment",
        )

        self.assertEqual(social_comment.id, admin_comment.id)

        response = self.client.post(f"/api/comment/{admin_comment.id}/like/?comment_type=admin_feed")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CommentLike.objects.filter(comment=social_comment, user=self.user).exists())
        self.assertTrue(
            AdminFeedPostCommentLike.objects.filter(comment=admin_comment, user=self.user.username).exists()
        )


class UserProfileViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="profileadmin",
            password="SafePass123!",
            first_name="Rhealyn Admin",
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_profile_get_uses_user_name_as_full_name_fallback(self):
        response = self.client.get("/api/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["profile"]["full_name"], "Rhealyn Admin")

    def test_profile_put_persists_full_name_on_profile(self):
        response = self.client.put(
            "/api/profile/",
            {"full_name": "Shelter Admin"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["full_name"], "Shelter Admin")

        profile = UserProfile.objects.get(user=self.user)
        self.user.refresh_from_db()

        self.assertEqual(profile.full_name, "Shelter Admin")
        self.assertEqual(self.user.first_name, "Shelter Admin")
        self.assertEqual(self.user.last_name, "")
