from rest_framework import serializers
from .models import KnowledgeItem

class KnowledgeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeItem
        # '__all__' means it will serialize every field in the model
        fields = '__all__'