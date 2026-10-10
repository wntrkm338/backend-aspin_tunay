"""Bounded server-side integrations. Provider credentials never leave Django."""
import re
import time
from decimal import Decimal

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import serializers
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle


class IntegrationError(APIException):
    status_code = 502
    default_detail = "The external service is unavailable. Please try again later."


class PublicIntegrationThrottle(AnonRateThrottle):
    scope = "public_integrations"
    rate = "30/min"


class CheckoutThrottle(AnonRateThrottle):
    scope = "checkout"
    rate = "5/min"


class PolygonThrottle(AnonRateThrottle):
    scope = "polygon_receipts"
    rate = "300/min"


def unavailable(message):
    error = IntegrationError(message)
    error.status_code = 503
    raise error


def provider_json(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=(5, 20), **kwargs)
        if response.status_code == 429:
            error = IntegrationError("The service is busy. Please try again shortly.")
            error.status_code = 429
            raise error
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected response")
        return data
    except (requests.RequestException, ValueError):
        # Never return provider bodies, URLs, or exception strings containing keys.
        raise IntegrationError() from None


def paymongo_key(mode):
    if mode not in ("test", "live"):
        raise serializers.ValidationError({"mode": "Choose test or live."})
    key = getattr(settings, f"PAYMONGO_{mode.upper()}_KEY", "").strip()
    legacy = settings.PAYMONGO_SECRET_KEY.strip()
    if not key and legacy.startswith(f"sk_{mode}_"):
        key = legacy
    if not key or not key.startswith(f"sk_{mode}_"):
        unavailable(f"PayMongo {mode} mode is not configured on the server.")
    return key


def payment_page(mode, limit=100, after=None):
    params = {"limit": limit}
    if after:
        params["after"] = after
    data = provider_json("GET", "https://api.paymongo.com/v1/payments",
                         auth=(paymongo_key(mode), ""), params=params)
    rows = data.get("data")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("attributes"), dict)
        or not isinstance(row.get("id"), str)
        for row in rows
    ):
        raise IntegrationError()
    if not isinstance(data.get("has_more", False), bool):
        raise IntegrationError()
    return {"data": rows, "has_more": data.get("has_more", False)}


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PublicIntegrationThrottle])
def paymongo_status(request):
    modes = {}
    for mode in ("test", "live"):
        try:
            paymongo_key(mode)
            modes[mode] = True
        except IntegrationError:
            modes[mode] = False
    return Response({"mode": settings.PAYMONGO_MODE, "available_modes": modes})


class PaymentQuery(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("test", "live"), required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=100)
    after = serializers.RegexField(r"^pay_[A-Za-z0-9]+$", max_length=100, required=False)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def paymongo_payments(request):
    query = PaymentQuery(data=request.query_params)
    query.is_valid(raise_exception=True)
    params = query.validated_data
    return Response(payment_page(params.get("mode", settings.PAYMONGO_MODE),
                                 params["limit"], params.get("after")))


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PublicIntegrationThrottle])
def paymongo_summary(request):
    mode = settings.PAYMONGO_MODE
    cache_key = f"paymongo_summary_v1_{mode}"
    result = cache.get(cache_key)
    if result is None:
        total, count, after = 0, 0, None
        seen = set()
        started = time.monotonic()
        for _ in range(50):
            page = payment_page(mode, after=after)
            for row in page["data"]:
                attributes = row["attributes"]
                if row.get("id") in seen:
                    continue
                seen.add(row.get("id"))
                if attributes.get("status") == "paid" and attributes.get("currency") == "PHP":
                    amount = attributes.get("amount")
                    if not isinstance(amount, int) or amount < 0:
                        raise IntegrationError()
                    total += amount
                    count += 1
            if not page["has_more"]:
                result = {"total_amount": total / 100, "total_amount_centavos": total,
                          "payment_count": count, "currency": "PHP", "mode": mode}
                cache.set(cache_key, result, 300)
                break
            next_cursor = page["data"][-1].get("id") if page["data"] else None
            if not next_cursor or next_cursor == after or time.monotonic() - started > 20:
                raise IntegrationError("Donation totals are temporarily unavailable.")
            after = next_cursor
        if result is None:
            raise IntegrationError("Donation totals are temporarily unavailable.")
    return Response(result)


class CheckoutInput(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("100"))
    donor_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    donor_email = serializers.EmailField(max_length=254)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([CheckoutThrottle])
def create_checkout_session(request):
    form = CheckoutInput(data=request.data)
    form.is_valid(raise_exception=True)
    values = form.validated_data
    mode = settings.PAYMONGO_MODE  # A visitor cannot switch billing to a different mode.
    name = values.get("donor_name") or "Anonymous Donor"
    data = provider_json("POST", "https://api.paymongo.com/v1/checkout_sessions",
        auth=(paymongo_key(mode), ""), json={"data": {"attributes": {
            "billing": {"name": name, "email": values["donor_email"]},
            "send_email_receipt": True, "show_line_items": True, "show_description": True,
            "description": "Donation to ASPIN TUNAY AT TAPAT",
            "line_items": [{"currency": "PHP", "amount": int(values["amount"] * 100),
                            "name": "Donation", "quantity": 1}],
            "payment_method_types": settings.PAYMONGO_PAYMENT_METHODS,
            "success_url": settings.FRONTEND_SUCCESS_URL,
            "cancel_url": settings.FRONTEND_CANCEL_URL,
            "metadata": {"source": "django-react", "donor_name": name,
                         "donor_email": values["donor_email"],
                         "donation_amount_php": str(values["amount"])},
        }}})
    checkout = data.get("data")
    if not isinstance(checkout, dict) or not isinstance(checkout.get("attributes"), dict):
        raise IntegrationError()
    attributes = checkout["attributes"]
    url = attributes.get("checkout_url")
    if not isinstance(url, str) or not url.startswith("https://checkout.paymongo.com/"):
        raise IntegrationError("PayMongo did not return a valid checkout link.")
    from user_api.notifications import notify_donation_checkout_created
    notify_donation_checkout_created(donor_name=name, donor_email=values["donor_email"],
                                     amount=values["amount"])
    return Response({"success": True, "checkout_session_id": checkout.get("id"),
                     "checkout_url": url, "reference_number": attributes.get("reference_number"),
                     "payment_method_types": attributes.get("payment_method_types", [])}, status=201)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PolygonThrottle])
def polygon_transaction(request, tx_hash):
    if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
        raise serializers.ValidationError({"hash": "A 32-byte transaction hash is required."})
    if not settings.POLYGONSCAN_API_KEY:
        unavailable("Polygon transaction lookup is not configured on the server.")
    chain_id = settings.POLYGONSCAN_CHAIN_ID
    if chain_id not in ("137", "80002"):
        unavailable("The server must select Polygon mainnet or Amoy.")
    cache_key = f"polygon_receipt_{chain_id}_{tx_hash.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Response(cached)
    data = provider_json("GET", "https://api.etherscan.io/v2/api", params={
        "chainid": chain_id, "module": "proxy", "action": "eth_getTransactionReceipt",
        "txhash": tx_hash, "apikey": settings.POLYGONSCAN_API_KEY,
    })
    receipt = data.get("result")
    if data.get("error") or "result" not in data or (receipt is not None and not isinstance(receipt, dict)):
        raise IntegrationError("Polygon transaction lookup is unavailable. Check the server's V2 API configuration.")
    confirmed = bool(receipt and receipt.get("blockNumber") and receipt.get("status") == "0x1")
    result = {"hash": tx_hash, "chain_id": chain_id, "confirmed": confirmed,
              "status": "pending" if receipt is None else ("confirmed" if confirmed else "failed")}
    cache.set(cache_key, result, 300 if confirmed else 15)
    return Response(result)
