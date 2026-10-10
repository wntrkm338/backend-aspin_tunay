from django.contrib import admin
from .models import *

admin.site.register(UserProfile)
admin.site.register(Notification)
admin.site.register(Post)
admin.site.register(Like)
admin.site.register(Comment)
admin.site.register(CommentLike)
admin.site.register(AdminPostComment)