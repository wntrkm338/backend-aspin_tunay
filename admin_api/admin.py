from django.contrib import admin
from .models import Animal, Story, IntakeRecord, AdoptionApplication, LedgerBlock, Receipt, RemoteAssessment, Post, PostLike, PostComment
from .models import AssessmentEmail

admin.site.register(Animal)
admin.site.register(Story)
admin.site.register(IntakeRecord)
admin.site.register(AdoptionApplication)
admin.site.register(RemoteAssessment)
admin.site.register(Post)
admin.site.register(PostLike)
admin.site.register(PostComment)


class ReadOnlyLedgerAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


@admin.register(AssessmentEmail)
class AssessmentEmailAdmin(ReadOnlyLedgerAdmin):
    list_display = ('id', 'application', 'kind', 'recipient', 'status', 'due_at', 'sent_at', 'attempts')
    list_filter = ('status', 'kind')
    search_fields = ('recipient', 'event_key')
    readonly_fields = tuple(field.name for field in AssessmentEmail._meta.fields)


@admin.register(LedgerBlock)
class LedgerBlockAdmin(ReadOnlyLedgerAdmin):
    list_display = ('index', 'block_type', 'amount', 'category', 'recipient', 'created_at')
    readonly_fields = (
        'index',
        'timestamp',
        'previousHash',
        'hash',
        'nonce',
        'block_type',
        'description',
        'amount',
        'category',
        'recipient',
        'created_at',
    )


@admin.register(Receipt)
class ReceiptAdmin(ReadOnlyLedgerAdmin):
    list_display = ('id', 'block', 'image')
    readonly_fields = ('block', 'image')


