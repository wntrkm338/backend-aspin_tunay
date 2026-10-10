import logging

from django.core import signing
from django.db import DatabaseError
from django.http import FileResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .data_backup import (
    BackupError, apply_import, database_fingerprint, export_file,
    parse_upload, preview_summary, validate_records,
)

logger = logging.getLogger(__name__)
TOKEN_SALT = "admin-data-import-v1"


def failure(exc):
    if isinstance(exc, BackupError):
        return Response({"errors": exc.errors}, status=400)
    logger.exception("Data backup operation failed")
    if isinstance(exc, DatabaseError):
        return Response({"errors": [
            "Records conflict with a database constraint. Check unique values and related record IDs. "
            "No records were imported.",
        ]}, status=400)
    return Response({"errors": [
        "The operation could not finish. Check server storage and database availability, then try again."
    ]}, status=500)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def export_data(request):
    format_name = request.query_params.get("file_format", "json")
    try:
        stream, content_type = export_file(format_name)
        response = FileResponse(
            stream, as_attachment=True, content_type=content_type,
            filename=f"aspin-backup-{timezone.now():%Y%m%d-%H%M%S}.{format_name}",
        )
        response["Cache-Control"] = "no-store"
        return response
    except (BackupError, DatabaseError, OSError) as exc:
        return failure(exc)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def preview_import(request):
    try:
        records, files, digest = parse_upload(request.FILES.get("file"))
        objects = validate_records(records, request.user)
        state = database_fingerprint(files)
        summary = preview_summary(records, files)
        apply_import(objects, files, dry_run=True)
        summary["preview_token"] = signing.dumps(
            {"user": request.user.pk, "file": digest, "state": state}, salt=TOKEN_SALT,
        )
        summary["expires_in"] = 900
        response = Response(summary)
        response["Cache-Control"] = "no-store"
        return response
    except (BackupError, DatabaseError, OSError) as exc:
        return failure(exc)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def confirm_import(request):
    if request.data.get("confirm") != "true":
        return Response({"errors": ["Review the preview and explicitly confirm the import."]}, status=400)
    try:
        try:
            preview = signing.loads(request.data.get("preview_token", ""), salt=TOKEN_SALT, max_age=900)
        except signing.BadSignature:
            raise BackupError("The preview expired or is invalid. Preview the file again.")
        records, files, digest = parse_upload(request.FILES.get("file"))
        if preview.get("user") != request.user.pk or preview.get("file") != digest:
            raise BackupError("The file or administrator changed. Preview the file again.")
        if preview.get("state") != database_fingerprint(files):
            return Response({"errors": ["Application data changed after the preview. Preview again before importing."]}, status=409)
        objects = validate_records(records, request.user)
        summary = preview_summary(records, files)
        apply_import(objects, files)
        response = Response({"message": "Import completed successfully.", **summary})
        response["Cache-Control"] = "no-store"
        return response
    except (BackupError, DatabaseError, OSError) as exc:
        return failure(exc)
