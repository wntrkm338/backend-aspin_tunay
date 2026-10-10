from pathlib import Path
import re

from django.conf import settings
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from backend.integrations import IntegrationError, provider_json, unavailable
from .models import KnowledgeItem


class ChatThrottle(AnonRateThrottle):
    scope = "shelter_chat"
    rate = "10/min"


class ChatMessage(serializers.Serializer):
    role = serializers.ChoiceField(choices=("user", "bot", "assistant", "model"))
    text = serializers.CharField(max_length=16000)


class ChatInput(serializers.Serializer):
    message = serializers.CharField(max_length=4000)
    history = ChatMessage(many=True, required=False, max_length=8)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ChatThrottle])
def chat_api(request):
    form = ChatInput(data=request.data)
    form.is_valid(raise_exception=True)
    if not settings.GEMINI_API_KEY:
        unavailable("The shelter assistant is not configured yet. Please try again later.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", settings.GEMINI_MODEL):
        unavailable("The shelter assistant model is not configured correctly.")

    knowledge = Path(__file__).with_name("knowledge_base.txt").read_text(encoding="utf-8")
    additions = "\n\n".join(f"{item.title}: {item.content}" for item in
                            KnowledgeItem.objects.filter(is_active=True)[:100])
    instruction = (
        "You are ASPIN TUNAY AT TAPAT, an animal welfare assistant. "
        "Be warm and clear. Reply in the user's language. Answer only from the shelter "
        "knowledge below. If the answer is absent, say you don't have that information. "
        "Keep simple answers concise and give complete details when requested.\n\n"
        "The Logistics and Location Rules below are mandatory adoption eligibility policy. "
        "Apply them even if older chat history or additional knowledge suggests otherwise. "
        "For a home outside Quezon Province, politely state that the application cannot "
        "proceed because we must personally conduct home delivery. Do not suggest "
        "pick-up, paid delivery, or screening as an exception.\n\n"
        + knowledge + "\n\n" + additions
    )[:60000]
    contents = [{"role": "user" if row["role"] == "user" else "model",
                 "parts": [{"text": row["text"]}]} for row in form.validated_data.get("history", [])]
    contents.append({"role": "user", "parts": [{"text": form.validated_data["message"]}]})
    data = provider_json("POST",
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        json={"systemInstruction": {"parts": [{"text": instruction}]}, "contents": contents,
              "generationConfig": {"temperature": 0.35, "maxOutputTokens": 8192}})
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
        raise IntegrationError()
    candidate = candidates[0] if candidates else {}
    content = candidate.get("content") or {}
    if not isinstance(content, dict):
        raise IntegrationError()
    parts = content.get("parts") or []
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise IntegrationError()
    text = "\n".join(part["text"] for part in parts
                     if isinstance(part.get("text"), str) and not part.get("thought")).strip()
    if not text:
        raise IntegrationError("The assistant could not generate a reply. Please try a different question.")
    return Response({"text": text, "truncated": candidate.get("finishReason") == "MAX_TOKENS"})
