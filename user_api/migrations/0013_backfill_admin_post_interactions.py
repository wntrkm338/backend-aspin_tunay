from django.db import migrations


def backfill_admin_post_interactions(apps, schema_editor):
    AdminPost = apps.get_model('admin_api', 'Post')
    AdminPostLike = apps.get_model('admin_api', 'PostLike')
    AdminPostComment = apps.get_model('admin_api', 'PostComment')
    AdminPostCommentLike = apps.get_model('admin_api', 'PostCommentLike')

    LegacyAdminPostLike = apps.get_model('user_api', 'AdminPostLike')
    LegacyAdminPostComment = apps.get_model('user_api', 'AdminPostComment')
    LegacyAdminPostCommentLike = apps.get_model('user_api', 'AdminPostCommentLike')

    valid_post_ids = set(AdminPost.objects.values_list('id', flat=True))
    if not valid_post_ids:
        return

    for legacy_like in LegacyAdminPostLike.objects.select_related('user').iterator():
        if legacy_like.post_id not in valid_post_ids:
            continue

        username = getattr(legacy_like.user, 'username', '')
        if not username:
            continue

        like, _ = AdminPostLike.objects.get_or_create(
            post_id=legacy_like.post_id,
            user=username,
        )
        AdminPostLike.objects.filter(pk=like.pk).update(created_at=legacy_like.created_at)

    legacy_comments = (
        LegacyAdminPostComment.objects
        .select_related('user', 'parent')
        .filter(post_id__in=valid_post_ids)
        .order_by('created_at', 'id')
    )

    comment_id_map = {}
    for legacy_comment in legacy_comments:
        username = getattr(legacy_comment.user, 'username', '')
        if not username:
            continue

        parent_id = comment_id_map.get(legacy_comment.parent_id)
        parent = None
        if parent_id:
            parent = AdminPostComment.objects.filter(pk=parent_id).first()

        comment = AdminPostComment.objects.create(
            post_id=legacy_comment.post_id,
            user=username,
            text=legacy_comment.text,
            parent=parent,
        )
        AdminPostComment.objects.filter(pk=comment.pk).update(created_at=legacy_comment.created_at)
        comment_id_map[legacy_comment.pk] = comment.pk

    for legacy_comment_like in LegacyAdminPostCommentLike.objects.select_related('user', 'comment').iterator():
        comment_id = comment_id_map.get(legacy_comment_like.comment_id)
        username = getattr(legacy_comment_like.user, 'username', '')
        if not comment_id or not username:
            continue

        like, _ = AdminPostCommentLike.objects.get_or_create(
            comment_id=comment_id,
            user=username,
        )
        AdminPostCommentLike.objects.filter(pk=like.pk).update(
            created_at=legacy_comment_like.created_at
        )


class Migration(migrations.Migration):

    dependencies = [
        ('admin_api', '0021_backfill_adoption_application_submitted_by'),
        ('user_api', '0012_alter_adminpostcomment_id_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_admin_post_interactions, migrations.RunPython.noop),
    ]
