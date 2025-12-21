import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from flask import Flask, jsonify, redirect, request, render_template

# ----------------------------
# Config
# ----------------------------
APP_VERSION = os.getenv("APP_VERSION", "phase1-dev")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
TABLE_NAME = os.getenv("DDB_TABLE", "shortstack-urls")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

CODE_LEN = int(os.getenv("CODE_LEN", "7"))  # 7 is a nice balance
MAX_URL_LEN = 2048

# If you want basic safety (recommended):
BLOCK_PRIVATE_HOSTS = os.getenv("BLOCK_PRIVATE_HOSTS", "true").lower() == "true"

CODE_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ----------------------------
# Logging (JSON lines)
# ----------------------------
logger = logging.getLogger("shortstack")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [handler]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_log(**fields):
    base = {"ts": now_iso(), "app": "shortstack", "version": APP_VERSION}
    base.update(fields)
    logger.info(json.dumps(base, separators=(",", ":")))


# ----------------------------
# Shortcode generator (base62 from UUID)
# ----------------------------
def base62(n: int) -> str:
    if n == 0:
        return CODE_ALPHABET[0]
    out = []
    base = len(CODE_ALPHABET)
    while n > 0:
        n, r = divmod(n, base)
        out.append(CODE_ALPHABET[r])
    return "".join(reversed(out))


def generate_code(length: int = CODE_LEN) -> str:
    raw = uuid.uuid4().int
    s = base62(raw)
    s = s[-length:] if len(s) >= length else s.rjust(length, CODE_ALPHABET[0])
    return s


# ----------------------------
# URL validation (real-ish)
# ----------------------------
_private_host_patterns = [
    r"^localhost$",
    r"^127\.",
    r"^10\.",
    r"^192\.168\.",
    r"^172\.(1[6-9]|2\d|3[0-1])\.",
    r"\.local$",
]


def is_private_host(host: str) -> bool:
    host = (host or "").lower()
    for p in _private_host_patterns:
        if re.search(p, host):
            return True
    return False


def is_valid_url(u: str) -> (bool, str):
    if not u or len(u) > MAX_URL_LEN:
        return False, "URL is empty or too long"
    if re.search(r"\s", u):
        return False, "URL contains whitespace"

    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return False, "Only http/https URLs are allowed"
    if not parsed.netloc:
        return False, "URL must include a hostname"

    host = parsed.hostname or ""
    if BLOCK_PRIVATE_HOSTS and is_private_host(host):
        return False, "Private/local URLs are not allowed"

    return True, ""


def clean_alias(alias: str) -> str:
    alias = (alias or "").strip()
    if not alias:
        return ""
    # keep it URL-safe and short
    if not re.fullmatch(r"[0-9A-Za-z_-]{4,32}", alias):
        return ""
    return alias


# ----------------------------
# DynamoDB client
# ----------------------------
def ddb():
    # Supports DynamoDB Local via DDB_ENDPOINT_URL
    endpoint_url = os.getenv("DDB_ENDPOINT_URL")  # e.g. http://localhost:8000
    session = boto3.session.Session()
    return session.resource(
        "dynamodb",
        region_name=AWS_REGION,
        endpoint_url=endpoint_url,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def table():
    return ddb().Table(TABLE_NAME)


def put_link(code: str, long_url: str, expires_at: int | None):
    item = {
        "code": code,
        "long_url": long_url,
        "created_at": int(time.time()),
        "hits": 0,
    }
    if expires_at:
        item["expires_at"] = expires_at  # DynamoDB TTL attribute

    # ConditionExpression prevents overwriting an existing code
    table().put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(code)",
    )


def get_link(code: str):
    resp = table().get_item(Key={"code": code})
    return resp.get("Item")


def bump_hit(code: str):
    table().update_item(
        Key={"code": code},
        UpdateExpression="SET hits = if_not_exists(hits, :z) + :o",
        ExpressionAttributeValues={":z": 0, ":o": 1},
    )


# ----------------------------
# Flask app
# ----------------------------
def create_app():
    app = Flask(__name__)

    @app.before_request
    def _before():
        request._start = time.time()
        request._rid = request.headers.get("X-Request-Id") or request.headers.get("X-Amzn-Trace-Id") or str(uuid.uuid4())

    @app.after_request
    def _after(resp):
        latency_ms = int((time.time() - getattr(request, "_start", time.time())) * 1000)
        json_log(
            request_id=getattr(request, "_rid", None),
            method=request.method,
            path=request.path,
            status=resp.status_code,
            latency_ms=latency_ms,
            remote_addr=request.headers.get("X-Forwarded-For", request.remote_addr),
            ua=request.headers.get("User-Agent"),
        )
        return resp

    # UI
    @app.get("/")
    def home():
        return render_template("index.html", public_base_url=PUBLIC_BASE_URL, version=APP_VERSION)

    # Health/version
    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "time": now_iso()})

    @app.get("/version")
    def version():
        return jsonify({"version": APP_VERSION})

    # API: shorten
    @app.post("/api/shorten")
    def shorten():
        payload = request.get_json(silent=True) or {}
        long_url = (payload.get("url") or "").strip()
        alias = clean_alias(payload.get("alias"))
        ttl_hours = payload.get("ttlHours")

        ok, reason = is_valid_url(long_url)
        if not ok:
            return jsonify({"error": reason}), 400

        expires_at = None
        if ttl_hours is not None:
            try:
                ttl_hours = int(ttl_hours)
                if ttl_hours < 1 or ttl_hours > 24 * 365:
                    return jsonify({"error": "ttlHours must be between 1 and 8760"}), 400
                expires_at = int((datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).timestamp())
            except ValueError:
                return jsonify({"error": "ttlHours must be an integer"}), 400

        # Use alias if provided, else generate
        if alias:
            code = alias
            try:
                put_link(code, long_url, expires_at)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                    return jsonify({"error": "Alias already taken"}), 409
                raise
        else:
            # retry on collisions 
            last_err = None
            for _ in range(10):
                code = generate_code()
                try:
                    put_link(code, long_url, expires_at)
                    last_err = None
                    break
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                        last_err = e
                        continue
                    raise
            if last_err:
                return jsonify({"error": "Could not allocate a unique code"}), 500

        short_url = f"{PUBLIC_BASE_URL}/{code}" if PUBLIC_BASE_URL else code
        return jsonify({"code": code, "shortUrl": short_url}), 201

    # Redirect: /<code>
    @app.get("/<code>")
    def go(code: str):
        if not re.fullmatch(r"[0-9A-Za-z_-]{4,64}", code or ""):
            return jsonify({"error": "Not found"}), 404

        item = get_link(code)
        if not item:
            return jsonify({"error": "Not found"}), 404

        # TTL check (DynamoDB TTL is eventual, so we enforce it too)
        exp = item.get("expires_at")
        if exp and int(time.time()) >= int(exp):
            return jsonify({"error": "Link expired"}), 410

        bump_hit(code)
        return redirect(item["long_url"], code=302)

    return app


application = create_app()

if __name__ == "__main__":
    application.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
