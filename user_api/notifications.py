from django.contrib.auth import get_user_model

from admin_api.models import AdoptionApplication, Animal
from .models import Notification


USER_APPLICATION_URL = '/user/animals'
USER_ASSESSMENT_URL = '/user/assessments'
USER_FEED_URL = '/user/feed'
ADMIN_APPLICATION_URL = '/admin/adoption'
ADMIN_ASSESSMENT_URL = '/admin/assessment'
ADMIN_ANIMALS_URL = '/admin/animals'
ADMIN_INTAKE_URL = '/admin/intake'
ADMIN_DONATION_URL = '/admin/donation'
ADMIN_FEED_URL = '/admin/feed'


def _clean(value, fallback=''):
    text = '' if value is None else str(value).strip()
    return text or fallback


def _animal_name_from_id(animal_id, fallback='an animal'):
    if not animal_id:
        return fallback

    try:
        animal = Animal.objects.filter(id=str(animal_id)).only('name').first()
    except Exception:
        animal = None

    return _clean(getattr(animal, 'name', ''), fallback)


def _application_for_assessment(assessment):
    app_id = _clean(getattr(assessment, 'appId', ''))
    if not app_id:
        return None

    try:
        return AdoptionApplication.objects.filter(id=app_id).select_related('submitted_by').first()
    except (TypeError, ValueError):
        return None


def _application_owner(application):
    if not application:
        return None

    owner = getattr(application, 'submitted_by', None)
    if owner:
        return owner

    email = _clean(getattr(application, 'email', '')).lower()
    if email:
        return get_user_model().objects.filter(email__iexact=email).first()

    return None


def _actor_name(user):
    if not user:
        return 'Someone'

    full_name = ''
    try:
        full_name = user.get_full_name().strip()
    except Exception:
        full_name = ''

    return full_name or getattr(user, 'username', '') or 'Someone'


def _notification_defaults(title, message, event_type, target_url, metadata):
    return {
        'title': _clean(title),
        'message': _clean(message),
        'event_type': _clean(event_type, 'general'),
        'target_url': _clean(target_url),
        'metadata': metadata or {},
    }


def _create_notification(user, audience, title, message, event_type='general', target_url='', metadata=None, unique_key=None):
    if audience == Notification.AUDIENCE_USER and not user:
        return None

    defaults = _notification_defaults(title, message, event_type, target_url, metadata)
    defaults.update({
        'user': user,
        'audience': audience,
    })

    if unique_key:
        notification, _ = Notification.objects.update_or_create(
            unique_key=unique_key,
            defaults=defaults,
        )
        return notification

    return Notification.objects.create(**defaults)


def create_user_notification(user, title, message, event_type='general', target_url='', metadata=None, unique_key=None):
    return _create_notification(
        user=user,
        audience=Notification.AUDIENCE_USER,
        title=title,
        message=message,
        event_type=event_type,
        target_url=target_url,
        metadata=metadata,
        unique_key=unique_key,
    )


def create_admin_notification(title, message, event_type='general', target_url='', metadata=None, unique_key=None):
    User = get_user_model()
    admins = list(User.objects.filter(is_staff=True, is_active=True).only('id'))

    if not admins:
        return [
            _create_notification(
                user=None,
                audience=Notification.AUDIENCE_ADMIN,
                title=title,
                message=message,
                event_type=event_type,
                target_url=target_url,
                metadata=metadata,
                unique_key=f'{unique_key}:admin' if unique_key else None,
            )
        ]

    notifications = []
    for admin in admins:
        notifications.append(
            _create_notification(
                user=admin,
                audience=Notification.AUDIENCE_ADMIN,
                title=title,
                message=message,
                event_type=event_type,
                target_url=target_url,
                metadata=metadata,
                unique_key=f'{unique_key}:admin:{admin.id}' if unique_key else None,
            )
        )
    return notifications


def notify_application_submitted(application, animal=None):
    animal_name = _clean(getattr(animal, 'name', ''), _animal_name_from_id(application.animalId))
    metadata = {
        'application_id': application.id,
        'animal_id': application.animalId,
        'animal_name': animal_name,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Application submitted',
        message=f'Your adoption application for {animal_name} was received.',
        event_type='adoption_submitted',
        target_url=USER_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'application:{application.id}:submitted:user',
    )
    create_admin_notification(
        title='New adoption application',
        message=f'{application.fullName} applied to adopt {animal_name}.',
        event_type='adoption_submitted',
        target_url=ADMIN_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'application:{application.id}:submitted',
    )


def notify_application_status_changed(application, old_status=None, new_status=None):
    new_status = _clean(new_status, getattr(application, 'status', ''))
    old_status = _clean(old_status)
    if old_status == new_status:
        return

    animal_name = _animal_name_from_id(application.animalId)
    is_assessment_status = 'Assessment' in new_status or 'Post-Adoption' in new_status
    user_target = USER_ASSESSMENT_URL if is_assessment_status else USER_APPLICATION_URL
    metadata = {
        'application_id': application.id,
        'animal_id': application.animalId,
        'animal_name': animal_name,
        'old_status': old_status,
        'new_status': new_status,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Application status updated',
        message=f'Your application for {animal_name} is now {new_status}.',
        event_type='adoption_status_changed',
        target_url=user_target,
        metadata=metadata,
        unique_key=f'application:{application.id}:status:{new_status}:user',
    )
    create_admin_notification(
        title='Application status updated',
        message=f'Application #{application.id} for {animal_name} changed to {new_status}.',
        event_type='adoption_status_changed',
        target_url=ADMIN_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'application:{application.id}:status:{new_status}',
    )


def notify_application_deleted(application):
    animal_name = _animal_name_from_id(application.animalId)
    metadata = {
        'application_id': application.id,
        'animal_id': application.animalId,
        'animal_name': animal_name,
        'status': application.status,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Application removed',
        message=f'Your application for {animal_name} was removed by the admin team.',
        event_type='adoption_deleted',
        target_url=USER_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'application:{application.id}:deleted:user',
    )
    create_admin_notification(
        title='Application removed',
        message=f'Application #{application.id} for {animal_name} was removed.',
        event_type='adoption_deleted',
        target_url=ADMIN_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'application:{application.id}:deleted',
    )


def notify_assessment_changed(assessment, created=False, old_status=None):
    status = _clean(getattr(assessment, 'status', ''))
    old_status = _clean(old_status)
    if not created and old_status == status:
        return

    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    action = 'scheduled' if created else f'updated to {status}'
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
        'old_status': old_status,
        'new_status': status,
        'date': str(getattr(assessment, 'date', '') or ''),
        'time': str(getattr(assessment, 'time', '') or ''),
    }

    create_user_notification(
        user=_application_owner(application),
        title='Remote assessment updated',
        message=f'Your remote assessment for {animal_name} was {action}.',
        event_type='assessment_updated',
        target_url=USER_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:{"created" if created else "status"}:{status}:user',
    )
    create_admin_notification(
        title='Remote assessment updated',
        message=f'Assessment {assessment.id} for {assessment.adopterName} was {action}.',
        event_type='assessment_updated',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:{"created" if created else "status"}:{status}',
    )


def notify_assessment_accepted(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Assessment confirmed',
        message=f'Your remote assessment for {animal_name} is confirmed.',
        event_type='assessment_accepted',
        target_url=USER_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:accepted:user',
    )
    create_admin_notification(
        title='Assessment accepted',
        message=f'{assessment.adopterName} accepted assessment {assessment.id}.',
        event_type='assessment_accepted',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:accepted',
    )


def notify_assessment_reschedule_requested(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
        'requested_date': str(getattr(assessment, 'reschedule_requested_date', '') or ''),
        'requested_time': str(getattr(assessment, 'reschedule_requested_time', '') or ''),
    }

    create_user_notification(
        user=_application_owner(application),
        title='Reschedule request sent',
        message=f'Your reschedule request for {animal_name} was sent to the admin team.',
        event_type='assessment_reschedule_requested',
        target_url=USER_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:reschedule-requested:user',
    )
    create_admin_notification(
        title='Assessment reschedule requested',
        message=f'{assessment.adopterName} requested a new assessment time for {animal_name}.',
        event_type='assessment_reschedule_requested',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:reschedule-requested',
    )


def notify_assessment_reschedule_cancelled(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Reschedule request cancelled',
        message=f'Your reschedule request for {animal_name} was cancelled.',
        event_type='assessment_reschedule_cancelled',
        target_url=USER_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:reschedule-cancelled:user',
    )
    create_admin_notification(
        title='Reschedule request cancelled',
        message=f'{assessment.adopterName} cancelled the reschedule request for {animal_name}.',
        event_type='assessment_reschedule_cancelled',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:reschedule-cancelled',
    )


def notify_assessment_cancelled(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
    }

    create_user_notification(
        user=_application_owner(application),
        title='Assessment cancelled',
        message=f'Your remote assessment for {animal_name} was cancelled.',
        event_type='assessment_cancelled',
        target_url=USER_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:cancelled:user',
    )
    create_admin_notification(
        title='Assessment cancelled',
        message=f'{assessment.adopterName} cancelled assessment {assessment.id}.',
        event_type='assessment_cancelled',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:cancelled',
    )


def notify_assessment_deleted(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
        'status': getattr(assessment, 'status', ''),
    }

    create_user_notification(
        user=_application_owner(application),
        title='Assessment removed',
        message=f'Your remote assessment for {animal_name} was removed by the admin team.',
        event_type='assessment_deleted',
        target_url=USER_APPLICATION_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:deleted:user',
    )
    create_admin_notification(
        title='Assessment removed',
        message=f'Assessment {assessment.id} for {assessment.adopterName} was removed.',
        event_type='assessment_deleted',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:deleted',
    )


def notify_assessment_call_done(assessment):
    application = _application_for_assessment(assessment)
    animal_name = _clean(getattr(assessment, 'animalName', ''), _animal_name_from_id(getattr(application, 'animalId', None)))
    metadata = {
        'assessment_id': assessment.id,
        'application_id': getattr(application, 'id', None) or getattr(assessment, 'appId', None),
        'animal_name': animal_name,
        'status': getattr(assessment, 'status', ''),
    }

    create_user_notification(
        user=_application_owner(application),
        title='Assessment completed',
        message=f'Your remote assessment for {animal_name} is marked Done.',
        event_type='assessment_completed',
        target_url=USER_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:call-ended:user',
    )
    create_admin_notification(
        title='Assessment completed',
        message=f'Assessment {assessment.id} ended and was marked Done automatically.',
        event_type='assessment_completed',
        target_url=ADMIN_ASSESSMENT_URL,
        metadata=metadata,
        unique_key=f'assessment:{assessment.id}:call-ended',
    )


def notify_animal_changed(animal, created=False, old_status=None, old_availability=None):
    status = _clean(getattr(animal, 'status', ''))
    availability = _clean(getattr(animal, 'availability', ''))
    old_status = _clean(old_status)
    old_availability = _clean(old_availability)

    if not created and old_status == status and old_availability == availability:
        return

    metadata = {
        'animal_id': animal.id,
        'animal_name': animal.name,
        'old_status': old_status,
        'new_status': status,
        'old_availability': old_availability,
        'new_availability': availability,
    }
    suffix = 'created' if created else f'{status}:{availability}'

    create_admin_notification(
        title='Animal profile updated',
        message=f'{animal.name} was {"added" if created else "updated"} in the animal registry.',
        event_type='animal_updated',
        target_url=ADMIN_ANIMALS_URL,
        metadata=metadata,
        unique_key=f'animal:{animal.id}:{suffix}',
    )


def notify_animal_deleted(animal):
    metadata = {
        'animal_id': animal.id,
        'animal_name': animal.name,
        'status': getattr(animal, 'status', ''),
        'availability': getattr(animal, 'availability', ''),
    }
    create_admin_notification(
        title='Animal profile removed',
        message=f'{animal.name} was removed from the animal registry.',
        event_type='animal_deleted',
        target_url=ADMIN_ANIMALS_URL,
        metadata=metadata,
        unique_key=f'animal:{animal.id}:deleted',
    )


def notify_intake_changed(record, created=False, old_status=None):
    status = _clean(getattr(record, 'status', ''))
    old_status = _clean(old_status)
    if not created and old_status == status:
        return

    metadata = {
        'intake_id': record.id,
        'name': record.name,
        'old_status': old_status,
        'new_status': status,
    }
    suffix = 'created' if created else f'status:{status}'

    create_admin_notification(
        title='Intake record updated',
        message=f'Intake record {record.id} for {record.name} was {"created" if created else f"updated to {status}"}.',
        event_type='intake_updated',
        target_url=ADMIN_INTAKE_URL,
        metadata=metadata,
        unique_key=f'intake:{record.id}:{suffix}',
    )


def notify_intake_deleted(record):
    metadata = {
        'intake_id': record.id,
        'name': record.name,
        'status': getattr(record, 'status', ''),
    }
    create_admin_notification(
        title='Intake record removed',
        message=f'Intake record {record.id} for {record.name} was removed.',
        event_type='intake_deleted',
        target_url=ADMIN_INTAKE_URL,
        metadata=metadata,
        unique_key=f'intake:{record.id}:deleted',
    )


def notify_ledger_created(block):
    metadata = {
        'block_id': block.id,
        'index': block.index,
        'block_type': block.block_type,
        'amount': str(block.amount),
        'category': block.category,
    }
    create_admin_notification(
        title='Donation ledger recorded',
        message=f'Ledger block #{block.index} was recorded for {block.description or block.block_type}.',
        event_type='ledger_created',
        target_url=ADMIN_DONATION_URL,
        metadata=metadata,
        unique_key=f'ledger:{block.id}:created',
    )


def notify_donation_checkout_created(donor_name='', donor_email='', amount=''):
    donor = _clean(donor_name, 'Anonymous Donor')
    metadata = {
        'donor_name': donor,
        'donor_email': _clean(donor_email),
        'amount': str(amount or ''),
    }

    create_admin_notification(
        title='Donation checkout started',
        message=f'{donor} started a donation checkout for PHP {amount}.',
        event_type='donation_checkout_created',
        target_url=ADMIN_DONATION_URL,
        metadata=metadata,
    )


def notify_post_changed(post, created=False, old_status=None):
    status = _clean(getattr(post, 'status', ''))
    old_status = _clean(old_status)
    became_published = status == 'Published' and (created or old_status != 'Published')

    metadata = {
        'post_id': post.id,
        'title': post.title,
        'category': post.category,
        'old_status': old_status,
        'new_status': status,
    }

    if became_published:
        User = get_user_model()
        for user in User.objects.filter(is_active=True, is_staff=False).only('id'):
            create_user_notification(
                user=user,
                title='New shelter update',
                message=f'{post.title or "A new shelter update"} is now available in the feed.',
                event_type='feed_post_published',
                target_url=USER_FEED_URL,
                metadata=metadata,
                unique_key=f'post:{post.id}:published:user:{user.id}',
            )

        create_admin_notification(
            title='Post published',
            message=f'{post.title or post.id} was published to the feed.',
            event_type='feed_post_published',
            target_url=ADMIN_FEED_URL,
            metadata=metadata,
            unique_key=f'post:{post.id}:published',
        )
        return

    if created:
        create_admin_notification(
            title='Post created',
            message=f'{post.title or post.id} was saved as {status or "Draft"}.',
            event_type='feed_post_created',
            target_url=ADMIN_FEED_URL,
            metadata=metadata,
            unique_key=f'post:{post.id}:created',
        )


def notify_post_deleted(post):
    metadata = {
        'post_id': post.id,
        'title': post.title,
        'category': post.category,
        'status': post.status,
    }
    create_admin_notification(
        title='Post removed',
        message=f'{post.title or post.id} was removed from feed management.',
        event_type='feed_post_deleted',
        target_url=ADMIN_FEED_URL,
        metadata=metadata,
        unique_key=f'post:{post.id}:deleted',
    )


def notify_user_post_created(post):
    metadata = {
        'post_id': post.id,
        'author_id': post.user_id,
        'author_username': getattr(post.user, 'username', ''),
    }
    create_admin_notification(
        title='New community post',
        message=f'{_actor_name(post.user)} shared a post in the feed.',
        event_type='community_post_created',
        target_url=ADMIN_FEED_URL,
        metadata=metadata,
        unique_key=f'community-post:{post.id}:created',
    )


def notify_user_post_deleted(post):
    metadata = {
        'post_id': post.id,
        'author_id': post.user_id,
        'author_username': getattr(post.user, 'username', ''),
    }
    create_admin_notification(
        title='Community post removed',
        message=f'{_actor_name(post.user)} removed a feed post.',
        event_type='community_post_deleted',
        target_url=ADMIN_FEED_URL,
        metadata=metadata,
        unique_key=f'community-post:{post.id}:deleted',
    )


def notify_user_post_interaction(post, actor, interaction, comment_id=None, image_id=None):
    if not post or not getattr(post, 'user_id', None):
        return
    if actor and post.user_id == actor.id:
        return

    interaction_text = 'liked' if interaction == 'like' else 'commented on'
    metadata = {
        'post_id': post.id,
        'actor_id': getattr(actor, 'id', None),
        'actor_username': getattr(actor, 'username', ''),
        'comment_id': comment_id,
        'image_id': image_id,
    }
    unique_bits = [f'community-post:{post.id}', interaction]
    if image_id:
        unique_bits.append(f'image:{image_id}')
    unique_bits.append(str(comment_id or getattr(actor, 'id', 'unknown')))

    create_user_notification(
        user=post.user,
        title='Feed activity',
        message=f'{_actor_name(actor)} {interaction_text} your feed post.',
        event_type=f'feed_{interaction}',
        target_url=USER_FEED_URL,
        metadata=metadata,
        unique_key=':'.join(unique_bits),
    )


def notify_admin_post_interaction(post, actor, interaction, comment_id=None, image_id=None):
    interaction_text = 'liked' if interaction == 'like' else 'commented on'
    metadata = {
        'post_id': post.id,
        'post_title': post.title,
        'actor_id': getattr(actor, 'id', None),
        'actor_username': getattr(actor, 'username', ''),
        'comment_id': comment_id,
        'image_id': image_id,
    }
    unique_bits = [f'admin-post:{post.id}', interaction]
    if image_id:
        unique_bits.append(f'image:{image_id}')
    unique_bits.append(str(comment_id or getattr(actor, 'id', 'unknown')))

    create_admin_notification(
        title='Feed activity',
        message=f'{_actor_name(actor)} {interaction_text} {post.title or "an admin feed post"}.',
        event_type=f'feed_{interaction}',
        target_url=ADMIN_FEED_URL,
        metadata=metadata,
        unique_key=':'.join(unique_bits),
    )
