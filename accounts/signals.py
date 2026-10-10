from django.db import connections
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .security import secure_public_schema


_processed_aliases = set()


@receiver(post_migrate)
def harden_supabase_public_schema(sender, using, **kwargs):
    if using in _processed_aliases:
        return

    connection = connections[using]
    if secure_public_schema(connection):
        _processed_aliases.add(using)
