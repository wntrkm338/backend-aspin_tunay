from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user_api', '0013_backfill_admin_post_interactions'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='notification',
            options={'ordering': ['-created_at']},
        ),
        migrations.AlterField(
            model_name='notification',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='notifications',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='message',
            field=models.CharField(max_length=500),
        ),
        migrations.AddField(
            model_name='notification',
            name='audience',
            field=models.CharField(
                choices=[('user', 'User'), ('admin', 'Admin')],
                default='user',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='title',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='notification',
            name='event_type',
            field=models.CharField(blank=True, default='general', max_length=80),
        ),
        migrations.AddField(
            model_name='notification',
            name='target_url',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='notification',
            name='is_read',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='notification',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='notification',
            name='unique_key',
            field=models.CharField(blank=True, max_length=160, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='notification',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['user', 'audience', 'is_read', '-created_at'], name='user_api_no_user_id_1cc82a_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['audience', 'is_read', '-created_at'], name='user_api_no_audienc_59bd9b_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['event_type'], name='user_api_no_event_t_af68c7_idx'),
        ),
    ]
