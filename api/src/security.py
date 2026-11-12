import base64
import binascii
import json
import time
import uuid
from urllib.parse import urlparse

from js import AbortSignal, TextEncoder, Uint8Array, crypto
from pyodide.ffi import to_js
from workers import fetch

from common import ApiError, _js_options
from constants import MAX_BODY_BYTES, MAX_QUERY_LENGTH


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64url(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


async def _jwt_key(env):
    secret = getattr(env, "SESSION_JWT_SECRET", "")
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise ApiError("Session authentication is not configured", 503)
    return await crypto.subtle.importKey(
        "raw", TextEncoder.new().encode(secret),
        _js_options({"name": "HMAC", "hash": "SHA-256"}), False, to_js(["sign", "verify"]),
    )


async def _issue_session(env, origin):
    now = int(time.time())
    header = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64url(json.dumps({
        "anon_id": uuid.uuid4().hex, "origin": origin, "iat": now, "exp": now + 300,
    }, separators=(",", ":")).encode())
    message = f"{header}.{payload}"
    signature = await crypto.subtle.sign("HMAC", await _jwt_key(env), TextEncoder.new().encode(message))
    return f"{message}.{_b64url(Uint8Array.new(signature).to_bytes())}", now + 300


async def _verify_session(env, authorization, origin):
    if not authorization.startswith("Bearer ") or len(authorization) > 4096:
        raise ApiError("Session authentication is required", 401)
    try:
        header, payload, signature = authorization[7:].split(".")
        if json.loads(_unb64url(header)) != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("Invalid JWT header")
        claims = json.loads(_unb64url(payload))
        signature_bytes = _unb64url(signature)
        if not isinstance(claims, dict) or len(signature_bytes) != 32:
            raise ValueError("Invalid JWT")
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError):
        raise ApiError("Invalid session", 401)
    valid = await crypto.subtle.verify(
        "HMAC", await _jwt_key(env), to_js(signature_bytes),
        TextEncoder.new().encode(f"{header}.{payload}"),
    )
    now = int(time.time())
    if (not valid or claims.get("origin") != origin or
        not isinstance(claims.get("anon_id"), str) or len(claims["anon_id"]) != 32 or
        type(claims.get("iat")) is not int or type(claims.get("exp")) is not int or
        claims["iat"] > now or claims["exp"] <= now or claims["exp"] - claims["iat"] != 300):
        raise ApiError("Invalid or expired session", 401)


async def _verify_turnstile(env, token, origin, client_ip):
    secret = getattr(env, "TURNSTILE_SECRET_KEY", "")
    allow_test = getattr(env, "TURNSTILE_ALLOW_TEST_KEYS", "false").lower() == "true"
    is_test_key = isinstance(secret, str) and secret.startswith(("1x0000000000000000000000000000000", "2x0000000000000000000000000000000", "3x0000000000000000000000000000000"))
    if not secret or (is_test_key and not allow_test):
        raise ApiError("Verification is not configured", 503)
    if not isinstance(token, str) or not token or len(token) > 2048:
        raise ApiError("Verification is required", 403)
    try:
        response = await fetch(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            method="POST", headers={"Content-Type": "application/json"},
            body=json.dumps({"secret": secret, "response": token, **({"remoteip": client_ip} if client_ip != "unknown" else {})}),
            signal=AbortSignal.timeout(5000),
        )
        if not response.ok:
            raise ValueError("Siteverify failed")
        result = await response.json()
    except Exception as error:
        print(json.dumps({"event": "turnstile_unavailable", "error_type": type(error).__name__}))
        raise ApiError("Verification is unavailable", 503)
    test_response = allow_test and is_test_key and result.get("hostname") == "example.com"
    if not result.get("success") or (not test_response and (
        result.get("hostname") != urlparse(origin).hostname or result.get("action") != "aeroquery"
    )):
        raise ApiError("Verification failed", 403)


def _validate_query(query):
    if not isinstance(query, str):
        raise ApiError("query must be a string", 400)
    query = query.strip()
    if not query:
        raise ApiError("query is required", 400)
    if len(query) > MAX_QUERY_LENGTH:
        raise ApiError(f"query must be shorter than {MAX_QUERY_LENGTH} characters", 400)
    return query


async def _read_json_body(request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError("Content-Type must be application/json", 415)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise ApiError("Request body is too large", 413)
        except ValueError:
            raise ApiError("Invalid Content-Length", 400)
    reader = request.body.getReader()
    body_parts = []
    body_size = 0
    while True:
        item = await reader.read()
        if item.done:
            break
        part = item.value.to_bytes()
        body_size += len(part)
        if body_size > MAX_BODY_BYTES:
            await reader.cancel()
            raise ApiError("Request body is too large", 413)
        body_parts.append(part)
    try:
        body = json.loads(b"".join(body_parts).decode("utf-8"))
    except Exception:
        raise ApiError("Invalid JSON", 400)
    if not isinstance(body, dict):
        raise ApiError("Request body must be a JSON object", 400)
    return body
