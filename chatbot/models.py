from django.db import models

class KnowledgeItem(models.Model):
    title = models.CharField(max_length=200, help_text="e.g., Shelter Hours, Adoption Rules")
    content = models.TextField(help_text="The actual information.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title