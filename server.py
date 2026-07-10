from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlencode, urlparse
import json
import html
import hashlib
import hmac
import os
import random
import queue
import re
import secrets
import ssl
import time
import threading
import unicodedata
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
DATA = Path(os.getenv("COMPETIDOR_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
DATA.mkdir(parents=True, exist_ok=True)
CERTS = DATA / "certs"
CERTS.mkdir(exist_ok=True)
APP_DATA_FILE = "app.json"
CATALOG_DATA_FILE = "catalog.json"
SYNC_LOCK = threading.Lock()
DATA_LOCK = threading.RLock()
ACTIVE_SYNC_ACCOUNTS = set()
MELI_NOTIFICATION_QUEUE = queue.Queue(maxsize=5000)
CATEGORY_ATTRIBUTES_LOCK = threading.RLock()
CATEGORY_ATTRIBUTES_CACHE = {}

MELI_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
MELI_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
MELI_API_URL = "https://api.mercadolibre.com"
TELEGRAM_API_URL = "https://api.telegram.org"
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
SESSION_SECONDS = 60 * 60 * 12
SENSITIVE_MELI_PATH_TERMS = (
    "mercadopago",
    "mercado_pago",
    "/payments",
    "/payment",
    "/billing",
    "/bank",
    "/cards",
    "/wallet",
    "/mp/",
    "/money",
)
ALLOWED_MELI_PATHS = (
    re.compile(r"^/users/me$"),
    re.compile(r"^/users/[^/]+$"),
    re.compile(r"^/users/[^/]+/items/search(\?|$)"),
    re.compile(r"^/items[^/]*(\?|$)"),
    re.compile(r"^/items/[^/]+(\?|$)"),
    re.compile(r"^/items/[^/]+/description(\?|$)"),
    re.compile(r"^/items/[^/]+/price_to_win(\?|$)"),
    re.compile(r"^/categories/[^/]+/attributes(\?|$)"),
    re.compile(r"^/products/[^/]+(\?|$)"),
    re.compile(r"^/products/[^/]+/items(\?|$)"),
    re.compile(r"^/orders/search(\?|$)"),
    re.compile(r"^/claims/search(\?|$)"),
    re.compile(r"^/post-purchase/v1/claims/search(\?|$)"),
)


def read_json(name, fallback):
    with DATA_LOCK:
        path = DATA / name
        if not path.exists():
            write_json(name, fallback)
        return json.loads(path.read_text(encoding="utf-8"))


def write_json(name, payload):
    with DATA_LOCK:
        path = DATA / name
        temporary = DATA / f".{name}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)


def read_payload(include_catalog=True):
    with DATA_LOCK:
        payload = read_json(APP_DATA_FILE, empty_payload())
        embedded_catalog = payload.pop("catalog", None)
        catalog_path = DATA / CATALOG_DATA_FILE
        if embedded_catalog is not None:
            if embedded_catalog or not catalog_path.exists():
                write_json(CATALOG_DATA_FILE, embedded_catalog)
            migration_payload = {**payload, "catalog": embedded_catalog}
            payload["catalog_counts_snapshot"] = catalog_counts(embedded_catalog)
            payload["operations_snapshot"] = build_operations(migration_payload)
            write_json(APP_DATA_FILE, payload)
        payload["catalog"] = read_json(CATALOG_DATA_FILE, []) if include_catalog else []
        if include_catalog:
            for item in payload["catalog"]:
                if item.get("winner_source") in {
                    "catalog_lowest_active_offer",
                    "catalog_reference",
                    "products_items_winner_marker",
                    "public_purchase_options",
                }:
                    item.update(competition_snapshot(item))
        payload["_catalog_loaded"] = bool(include_catalog)
        payload.setdefault("_revision", 0)
        return payload


def record_key(collection, record):
    if collection == "users":
        return str(record.get("id") or (record.get("email") or "").lower())
    if collection == "accounts":
        return str(record.get("seller_id") or record.get("id") or record.get("nickname") or "")
    return str(record.get("id") or "")


def merge_critical_records(collection, incoming, latest):
    merged = []
    incoming_by_key = {record_key(collection, row): row for row in incoming or [] if record_key(collection, row)}
    latest_by_key = {record_key(collection, row): row for row in latest or [] if record_key(collection, row)}
    for key in [*latest_by_key.keys(), *[key for key in incoming_by_key if key not in latest_by_key]]:
        current = incoming_by_key.get(key)
        saved = latest_by_key.get(key)
        if current is None:
            merged.append(saved)
            continue
        if saved is None:
            merged.append(current)
            continue
        if collection == "accounts" and int(current.get("token_created_at") or 0) > int(saved.get("token_created_at") or 0):
            merged.append({**saved, **current})
        else:
            merged.append({**current, **saved})
    return merged


def write_payload(payload, replace_collections=None):
    replace_collections = set(replace_collections or [])
    with DATA_LOCK:
        path = DATA / APP_DATA_FILE
        latest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        latest.pop("catalog", None)
        incoming_revision = int(payload.get("_revision") or 0)
        latest_revision = int(latest.get("_revision") or 0)
        if incoming_revision < latest_revision:
            for collection in ("users", "accounts"):
                if collection not in replace_collections:
                    payload[collection] = merge_critical_records(
                        collection,
                        payload.get(collection, []),
                        latest.get(collection, []),
                    )
            latest_notifications = latest.get("user_notifications") or {}
            payload["user_notifications"] = {**(payload.get("user_notifications") or {}), **latest_notifications}
        payload["_revision"] = latest_revision + 1
        catalog_loaded = bool(payload.get("_catalog_loaded", True))
        if catalog_loaded:
            write_json(CATALOG_DATA_FILE, payload.get("catalog", []))
            payload["catalog_counts_snapshot"] = catalog_counts(payload.get("catalog", []))
            payload["operations_snapshot"] = build_operations(payload)
        metadata = dict(payload)
        metadata.pop("catalog", None)
        metadata.pop("_catalog_loaded", None)
        write_json(APP_DATA_FILE, metadata)


def catalog_counts(catalog):
    return {
        "total": len(catalog or []),
        "winning": len([item for item in catalog or [] if item.get("status") == "winning"]),
        "losing": len([item for item in catalog or [] if item.get("status") == "losing"]),
        "sharing": len([item for item in catalog or [] if item.get("status") == "sharing"]),
        "paused": len(
            [
                item
                for item in catalog or []
                if item.get("status") == "paused" or item.get("meli_status") == "paused"
            ]
        ),
    }


def now_label():
    return datetime.now(APP_TZ).strftime("%Y-%m-%d %H:%M")


def https_port():
    return int(os.getenv("HTTPS_PORT", str(int(os.getenv("PORT", "8765")) + 1)))


def ensure_dev_certificate():
    cert_path = CERTS / "localhost.crt"
    key_path = CERTS / "localhost.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CompeTIDOR local"),
            x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def app_settings():
    settings = read_json("settings.json", {"meli": {}})
    return settings


def meli_config():
    settings = app_settings().get("meli", {})
    return {
        "client_id": os.getenv("MELI_CLIENT_ID") or settings.get("client_id", ""),
        "client_secret": os.getenv("MELI_CLIENT_SECRET") or settings.get("client_secret", ""),
        "redirect_uri": os.getenv("MELI_REDIRECT_URI") or settings.get("redirect_uri", ""),
    }


def app_configured():
    config = meli_config()
    return all([config["client_id"], config["client_secret"], config["redirect_uri"]])


def oauth_issues():
    issues = []
    config = meli_config()
    if not config["client_id"]:
        issues.append("MELI_CLIENT_ID ausente")
    if not config["client_secret"]:
        issues.append("MELI_CLIENT_SECRET ausente")
    if not config["redirect_uri"]:
        issues.append("MELI_REDIRECT_URI ausente")
    redirect_uri = config["redirect_uri"]
    if redirect_uri and not redirect_uri.startswith(("http://", "https://")):
        issues.append("MELI_REDIRECT_URI precisa comecar com http:// ou https://")
    return issues


def public_account(account):
    clean = dict(account)
    clean.pop("access_token", None)
    clean.pop("refresh_token", None)
    return clean


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return salt, digest.hex()


def verify_password(password, user):
    salt = user.get("password_salt", "")
    expected = user.get("password_hash", "")
    if not salt or not expected:
      return False
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected)


def public_user(user):
    clean = dict(user)
    clean.pop("password_hash", None)
    clean.pop("password_salt", None)
    return clean


def is_master(user):
    return (user or {}).get("role") == "master"


def can_manage_users(user):
    return (user or {}).get("role") in {"master", "admin"}


def visible_users_for(actor, users):
    visible = users if is_master(actor) else [user for user in users if user.get("role") != "master"]
    return [public_user(user) for user in visible]


def public_notifications(notifications):
    clean = json.loads(json.dumps(notifications or blank_notifications()))
    if clean.get("telegram", {}).get("bot_token"):
        clean["telegram"]["bot_token"] = "********"
    return clean


def blank_notifications():
    return {
        "telegram": {
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
            "alert_types": ["stock", "catalog", "shipping", "scan"],
            "status": "Aguardando token criado no BotFather",
        }
    }


def user_notifications(payload, user, create=False):
    user_id = (user or {}).get("id")
    if not user_id:
        return blank_notifications()
    store = payload.setdefault("user_notifications", {}) if create else payload.get("user_notifications", {})
    if user_id in store:
        return store[user_id]
    if create:
        if is_master(user) and payload.get("notifications"):
            store[user_id] = json.loads(json.dumps(payload.get("notifications")))
        else:
            store[user_id] = blank_notifications()
        return store[user_id]
    if is_master(user) and payload.get("notifications"):
        return payload.get("notifications")
    return blank_notifications()


def notification_targets(payload):
    migrate_legacy_notifications(payload)
    targets = []
    store = payload.get("user_notifications") or {}
    for user_id, config in store.items():
        targets.append((user_id, config))
    if not targets and payload.get("notifications"):
        targets.append(("legacy", payload.get("notifications")))
    return targets


def migrate_legacy_notifications(payload):
    legacy = payload.get("notifications")
    if not legacy or payload.get("notifications_migrated_to_users"):
        return False
    users = ensure_users(payload)
    master = next((user for user in users if user.get("role") == "master"), None)
    if not master:
        return False
    payload.setdefault("user_notifications", {}).setdefault(master["id"], json.loads(json.dumps(legacy)))
    payload["notifications_migrated_to_users"] = True
    return True


def telegram_type_enabled(config, alert_type):
    telegram = (config or {}).get("telegram") or {}
    types = telegram.get("alert_types") or []
    return not types or alert_type in types


def public_payload(payload, actor=None, include_catalog=True):
    if payload.get("catalog"):
        enrich_recent_sale_thumbnails(payload)
    if include_catalog:
        clean = json.loads(json.dumps(payload))
    else:
        clean = json.loads(json.dumps({key: value for key, value in payload.items() if key not in {"catalog", "item_logs"}}))
        clean["catalog"] = []
        clean["item_logs"] = []
    clean.setdefault("scan_items", [])
    clean.setdefault("recent_sales", [])
    clean["clone_jobs"] = clean.get("clone_jobs", [])[:50]
    for job in clean["clone_jobs"]:
        if job.get("errors"):
            job["errors"] = job["errors"][:10]
    clean["item_logs"] = clean.get("item_logs", [])[:500]
    catalog = clean.get("catalog", [])
    clean["catalog_counts"] = catalog_counts(catalog) if include_catalog else clean.get("catalog_counts_snapshot") or catalog_counts([])
    clean["accounts"] = [public_account(account) for account in clean.get("accounts", [])]
    clean["notifications"] = public_notifications(user_notifications(payload, actor, create=False))
    clean["operations"] = (
        build_operations(clean)
        if include_catalog
        else clean.get("operations_snapshot") or build_operations(clean)
    )
    clean["users"] = visible_users_for(actor, ensure_users(clean))
    clean.pop("_catalog_loaded", None)
    clean.pop("_revision", None)
    clean.pop("operations_snapshot", None)
    clean.pop("catalog_counts_snapshot", None)
    return clean


def enrich_recent_sale_thumbnails(payload):
    catalog_by_id = {
        item.get("id"): item
        for item in payload.get("catalog", [])
        if item.get("id") and item.get("thumbnail")
    }
    changed = False
    for sale in payload.get("recent_sales", []):
        if sale.get("thumbnail"):
            continue
        catalog_item = catalog_by_id.get(sale.get("item_id")) or {}
        thumbnail = catalog_item.get("thumbnail") or ""
        if thumbnail:
            sale["thumbnail"] = thumbnail
            changed = True
    return changed


def percent_rate(value):
    try:
        return round(float(value or 0) * 100, 2)
    except (TypeError, ValueError):
        return 0


def brl(value):
    try:
        return f"R$ {float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "R$ 0,00"


def default_tenant():
    return {
        "id": "tenant-production",
        "name": "Workspace CompeTIDOR",
        "plan": "Pro",
        "billing_status": "active",
        "account_limit": 10,
        "user_limit": 5,
    }


def default_users(tenant):
    return []


def ensure_users(payload):
    tenant = payload.get("tenant") or default_tenant()
    users = payload.get("users")
    if users is None:
        users = default_users(tenant)
    changed = False
    emails = set()
    for user in users:
        email = (user.get("email") or "").lower()
        if email in emails:
            user["email"] = f"{user.get('role') or 'usuario'}-{user.get('id', uuid.uuid4().hex[:6])}@competidor.umsoftware.com.br"
            changed = True
        emails.add((user.get("email") or "").lower())
    if users and not any(user.get("role") == "master" for user in users):
        first_admin = next((user for user in users if user.get("role") == "admin"), users[0] if users else None)
        if first_admin:
            first_admin["role"] = "master"
            first_admin["name"] = first_admin.get("name") or "Usuário Master"
            changed = True
    for user in users:
        if not user.get("password_hash"):
            password = secrets.token_urlsafe(18)
            salt, password_hash = hash_password(password)
            user["password_salt"] = salt
            user["password_hash"] = password_hash
            changed = True
    payload["users"] = users
    return users


def fallback_time(index):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - (index * 2700)))


def current_month_period():
    now = datetime.now(APP_TZ)
    return f"{now.year:04d}-{now.month:02d}"


def current_month_window():
    now = datetime.now(APP_TZ)
    start = datetime(now.year, now.month, 1, tzinfo=APP_TZ)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=APP_TZ)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=APP_TZ)
    return (
        current_month_period(),
        start.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        end.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
    )


def build_operations(payload):
    accounts = payload.get("accounts", [])
    catalog = payload.get("catalog", [])
    monthly = payload.get("monthly_revenue") or {}
    revenue_accounts = monthly.get("accounts") or {}
    period = monthly.get("period") or current_month_period()
    revenue = []
    revenue_source_accounts = [account for account in accounts if account.get("official")]
    for account in revenue_source_accounts:
        key = account.get("id") or account.get("nickname")
        record = revenue_accounts.get(key) or revenue_accounts.get(account.get("nickname")) or {}
        revenue.append(
            {
                "account": account.get("nickname"),
                "monthly_revenue": round(float(record.get("amount") or 0), 2),
                "currency": "BRL",
                "source": record.get("source") or "Pedidos oficiais Mercado Livre",
                "orders_count": int(record.get("orders_count") or 0),
                "period": period,
                "updated_at": record.get("updated_at") or "",
                "sync_status": record.get("sync_status") or account.get("sales_sync_status") or "Aguardando sincronização real",
            }
        )
    total_revenue = round(sum(item["monthly_revenue"] for item in revenue), 2)

    stock = []
    catalog_attention = []
    catalog_by_id = {item.get("id"): item for item in catalog if item.get("id")}
    seen_stock_items = set()
    for alert in payload.get("alerts", []):
        if alert.get("type") != "stock" or not alert.get("item_id"):
            continue
        item = catalog_by_id.get(alert.get("item_id")) or {}
        stock.append(
            {
                "id": alert.get("item_id"),
                "title": alert.get("product") or item.get("title") or alert.get("item_id"),
                "account": alert.get("account") or item.get("account"),
                "sku": alert.get("sku") or item.get("sku") or "-",
                "stock": 0,
                "price": item.get("price"),
                "thumbnail": item.get("thumbnail"),
                "occurred_at": alert.get("created_at") or "",
            }
        )
        seen_stock_items.add(alert.get("item_id"))
    for index, item in enumerate(catalog):
        row = {
            "id": item.get("id"),
            "title": item.get("title"),
            "account": item.get("account"),
            "sku": item.get("sku"),
            "stock": item.get("stock"),
            "price": item.get("price"),
            "thumbnail": item.get("thumbnail"),
            "occurred_at": item.get("updated_at") or fallback_time(index + 1),
        }
        if item.get("status") == "losing" or item.get("competition_status") in {"competing", "sharing"} and item.get("winner_name") not in ("", None, item.get("account")):
            catalog_attention.append({**row, "winner_name": item.get("winner_name"), "winner_price": item.get("winner_price")})

    stock.sort(key=lambda item: item["occurred_at"], reverse=True)
    catalog_attention.sort(key=lambda item: item["occurred_at"], reverse=True)

    details_by_account = {}
    for detail in payload.get("claim_details", []):
        details_by_account.setdefault(detail.get("account"), []).append(detail)
    claims = payload.get("claims") or [
        {
            "account": account.get("nickname"),
            "open": 0,
            "mediations": 0,
            "updated_at": now_label(),
            "details": details_by_account.get(account.get("nickname"), []),
            "sync_status": account.get("claims_sync_status") or "Aguardando sincronização de reclamações",
        }
        for account in accounts
        if account.get("official")
    ]
    for claim in claims:
        claim["details"] = claim.get("details") or details_by_account.get(claim.get("account"), [])
        account = next((account for account in accounts if account.get("nickname") == claim.get("account")), {})
        claim["sync_status"] = claim.get("sync_status") or account.get("claims_sync_status") or ""
    shipments = payload.get("pending_shipments") or [
        {
            "account": account.get("nickname"),
            "order_id": "-",
            "buyer": "A sincronizar",
            "deadline": "Aguardando pedidos oficiais",
            "time_left": "-",
            "sync_status": account.get("sales_sync_status") or "Aguardando sincronização de pedidos",
        }
        for account in accounts
        if account.get("official")
    ]

    return {
        "revenue": revenue,
        "total_monthly_revenue": total_revenue,
        "attention_stock": stock[:30],
        "attention_catalog": catalog_attention[:30],
        "claims": claims,
        "pending_shipments": shipments,
    }


def request_json(url, method="GET", payload=None, headers=None, retries=None):
    body = None
    request_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    method = method.upper()
    attempts = int(retries if retries is not None else (4 if method in {"GET", "PUT"} else 1))
    transient_statuses = {429, 500, 502, 503, 504}
    last_error = None
    for attempt in range(max(1, attempts)):
        req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                text = response.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code not in transient_statuses or attempt + 1 >= attempts:
                raise last_error from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.6 * (2**attempt)) + random.uniform(0.1, 0.5)
            except (TypeError, ValueError):
                delay = min(8.0, 0.6 * (2**attempt)) + random.uniform(0.1, 0.5)
            time.sleep(delay)
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Falha temporária de conexão com a API: {exc.reason}")
            if attempt + 1 >= attempts:
                raise last_error from exc
            time.sleep(min(8.0, 0.6 * (2**attempt)) + random.uniform(0.1, 0.5))
    raise last_error or RuntimeError("Não foi possível concluir a chamada à API.")


def validate_meli_path(path):
    lowered = (path or "").lower()
    if any(term in lowered for term in SENSITIVE_MELI_PATH_TERMS):
        raise RuntimeError("Endpoint bloqueado por segurança: Mercado Pago, pagamentos e dados financeiros sensíveis não são permitidos.")
    if not any(pattern.match(path or "") for pattern in ALLOWED_MELI_PATHS):
        raise RuntimeError("Endpoint Mercado Livre não autorizado pela política de segurança do CompeTIDOR.")


def request_form(url, payload, headers=None):
    request_headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=urlencode(payload).encode("utf-8"), headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def empty_payload():
    return {
        "tenant": default_tenant(),
        "users": [],
        "accounts": [],
        "catalog": [],
        "alerts": [],
        "metrics": [],
        "competitors": [],
        "clone_jobs": [],
        "scan_items": [],
        "recent_sales": [],
        "claim_details": [],
        "pending_shipments": [],
        "monthly_revenue": {"period": current_month_period(), "accounts": {}},
        "user_notifications": {},
        "notifications_migrated_to_users": True,
    }

class MercadoLivreClient:
    def __init__(self, access_token=None):
        self.access_token = access_token

    def auth_url(self, state, redirect_uri=None, switch_account=False):
        config = meli_config()
        client_id = config["client_id"]
        redirect_uri = redirect_uri or config["redirect_uri"]
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if switch_account:
            params["prompt"] = "login"
            params["switch_account"] = "true"
        return f"{MELI_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code, redirect_uri=None):
        config = meli_config()
        payload = {
            "grant_type": "authorization_code",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri or config["redirect_uri"],
        }
        return request_form(MELI_TOKEN_URL, payload)

    def refresh(self, refresh_token):
        payload = {
            "grant_type": "refresh_token",
            "client_id": meli_config()["client_id"],
            "client_secret": meli_config()["client_secret"],
            "refresh_token": refresh_token,
        }
        return request_form(MELI_TOKEN_URL, payload)

    def get(self, path):
        validate_meli_path(path)
        return request_json(
            f"{MELI_API_URL}{path}",
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
        )

    def post(self, path, payload):
        validate_meli_path(path)
        return request_json(
            f"{MELI_API_URL}{path}",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
        )

    def put(self, path, payload):
        validate_meli_path(path)
        return request_json(
            f"{MELI_API_URL}{path}",
            method="PUT",
            payload=payload,
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
        )

    def me(self):
        return self.get("/users/me")

    def user(self, seller_id):
        return self.get(f"/users/{seller_id}")

    def seller_items(self, seller_id, limit=50, offset=0):
        return self.get(f"/users/{seller_id}/items/search?limit={limit}&offset={offset}")

    def seller_items_scan(self, seller_id, limit=100, scroll_id="", status=""):
        params = {"search_type": "scan", "limit": min(int(limit or 100), 100)}
        if scroll_id:
            params["scroll_id"] = scroll_id
        if status:
            params["status"] = status
        return self.get(f"/users/{seller_id}/items/search?{urlencode(params)}")

    def seller_all_items(self, seller_id, max_items=None):
        max_items = None if max_items in (None, "all", 0, "0") else int(max_items)
        statuses = [
            status.strip()
            for status in os.getenv("MELI_SYNC_STATUSES", "active,paused,under_review").split(",")
            if status.strip()
        ]
        results = []
        seen = set()
        for status in statuses:
            scroll_id = ""
            empty_pages = 0
            max_pages = max(1, int(os.getenv("MELI_SCAN_MAX_PAGES", "1000")))
            for _ in range(max_pages):
                page = self.seller_items_scan(seller_id, limit=100, scroll_id=scroll_id, status=status)
                batch = page.get("results", []) or []
                added = 0
                for item_id in batch:
                    if item_id and item_id not in seen:
                        seen.add(item_id)
                        results.append(item_id)
                        added += 1
                        if max_items and len(results) >= max_items:
                            return results[:max_items]
                scroll_id = page.get("scroll_id") or scroll_id
                if not batch or added == 0:
                    empty_pages += 1
                if not scroll_id or empty_pages >= 2:
                    break
            if max_items and len(results) >= max_items:
                break
        if results:
            return results[:max_items] if max_items else results

        # Fallback para contas/permissões em que search_type=scan não esteja disponível.
        fallback_limit = max_items or int(os.getenv("MELI_OFFSET_FALLBACK_LIMIT", "1000"))
        results = []
        offset = 0
        page_size = 50
        while len(results) < fallback_limit:
            page = self.seller_items(seller_id, page_size, offset)
            batch = page.get("results", [])
            results.extend(batch)
            total = (page.get("paging") or {}).get("total") or len(results)
            if not batch or len(results) >= total:
                break
            offset += page_size
        return results[:fallback_limit]

    def item(self, item_id):
        return self.get(f"/items/{item_id}")

    def item_for_clone(self, item_id):
        return self.get(f"/items/{item_id}?include_attributes=all")

    def items_bulk(self, item_ids):
        ids = ",".join(item_ids)
        rows = self.get(f"/items?ids={ids}")
        items = []
        for row in rows if isinstance(rows, list) else []:
            body = row.get("body") if isinstance(row, dict) else None
            if body:
                items.append(body)
        return items

    def item_description(self, item_id):
        return self.get(f"/items/{item_id}/description")

    def product(self, catalog_product_id):
        return self.get(f"/products/{catalog_product_id}")

    def price_to_win(self, item_id):
        return self.get(f"/items/{item_id}/price_to_win?version=v2")

    def product_winners(self, catalog_product_id):
        return self.get(f"/products/{catalog_product_id}/items?site_id=MLB")

    def category_attributes(self, category_id):
        return self.get(f"/categories/{category_id}/attributes")

    def create_item(self, payload):
        return self.post("/items", payload)

    def create_item_description(self, item_id, plain_text):
        return self.post(f"/items/{item_id}/description", {"plain_text": plain_text})

    def update_item(self, item_id, payload):
        return self.put(f"/items/{item_id}", payload)

    def seller_orders(self, seller_id, limit=50, offset=0, date_from=None, date_to=None):
        params = {
            "seller": seller_id,
            "sort": "date_desc",
            "limit": min(int(limit or 50), 50),
            "offset": max(int(offset or 0), 0),
        }
        if date_from:
            params["order.date_created.from"] = date_from
        if date_to:
            params["order.date_created.to"] = date_to
        return self.get(f"/orders/search?{urlencode(params)}")

    def seller_claims(self, seller_id, limit=50, offset=0):
        params = {"seller_id": seller_id, "limit": min(int(limit or 50), 50), "offset": max(int(offset or 0), 0)}
        try:
            return self.get(f"/post-purchase/v1/claims/search?{urlencode(params)}")
        except Exception:
            return self.get(f"/claims/search?{urlencode(params)}")


class Notifier:
    def __init__(self, config):
        self.config = config

    def telegram_get_me(self):
        cfg = self.config.get("telegram", {})
        if not cfg.get("bot_token"):
            return {"ok": False, "status": "Token do Telegram não configurado"}
        return request_json(f"{TELEGRAM_API_URL}/bot{cfg['bot_token']}/getMe")

    def telegram_updates(self):
        cfg = self.config.get("telegram", {})
        if not cfg.get("bot_token"):
            return {"ok": False, "status": "Token do Telegram não configurado"}
        return request_json(f"{TELEGRAM_API_URL}/bot{cfg['bot_token']}/getUpdates")

    def send_telegram(self, text):
        cfg = self.config.get("telegram", {})
        if not (cfg.get("enabled") and cfg.get("bot_token") and cfg.get("chat_id")):
            return {"ok": False, "status": "Telegram não configurado"}
        try:
            me = self.telegram_get_me()
            bot_id = str((me.get("result") or {}).get("id") or "")
            if bot_id and str(cfg.get("chat_id")) == bot_id:
                return {
                    "ok": False,
                    "error": "O Chat ID informado é o ID do próprio bot. Envie uma mensagem para o bot no Telegram e use o chat.id retornado em getUpdates.",
                }
        except Exception:
            pass
        url = f"{TELEGRAM_API_URL}/bot{cfg['bot_token']}/sendMessage"
        return request_json(url, method="POST", payload={"chat_id": cfg["chat_id"], "text": text})


def add_or_update_account(payload, account):
    accounts = payload["accounts"]
    for index, current in enumerate(accounts):
        if str(current.get("seller_id")) == str(account.get("seller_id")):
            accounts[index] = {**current, **account}
            return accounts[index]
    accounts.append(account)
    return account


def account_client(account):
    if not account.get("access_token"):
        raise RuntimeError("Conta oficial sem access token salvo. Refaça o login OAuth.")
    expires_at = int(account.get("token_created_at") or 0) + int(account.get("expires_in") or 0)
    if account.get("refresh_token") and expires_at and expires_at - 300 <= int(time.time()):
        token = MercadoLivreClient().refresh(account["refresh_token"])
        account["access_token"] = token.get("access_token", account.get("access_token"))
        account["refresh_token"] = token.get("refresh_token", account.get("refresh_token"))
        account["expires_in"] = token.get("expires_in", account.get("expires_in"))
        account["token_created_at"] = int(time.time())
    return MercadoLivreClient(account["access_token"])


def item_sku(item):
    if item.get("seller_custom_field"):
        return item["seller_custom_field"]
    for key in ("seller_sku", "seller_custom_field", "sku"):
        if item.get(key):
            return item[key]
    for attribute in item.get("attributes", []) or []:
        if attribute.get("id") in {"SELLER_SKU", "SKU"} and attribute.get("value_name"):
            return attribute["value_name"]
    for variation in item.get("variations", []) or []:
        if variation.get("seller_custom_field"):
            return variation["seller_custom_field"]
        for attribute in variation.get("attributes", []) or []:
            if attribute.get("id") in {"SELLER_SKU", "SKU"} and attribute.get("value_name"):
                return attribute["value_name"]
    return "-"


def item_available_quantity(item):
    quantity = item.get("available_quantity")
    if quantity not in (None, ""):
        try:
            return int(float(quantity or 0))
        except (TypeError, ValueError):
            pass
    total = 0
    for variation in item.get("variations", []) or []:
        try:
            total += int(float(variation.get("available_quantity") or 0))
        except (TypeError, ValueError):
            pass
    if total:
        return total
    try:
        return int(float(item.get("initial_quantity") or item.get("stock") or 0))
    except (TypeError, ValueError):
        return 0


def item_flex_logistic_type(item):
    shipping = item.get("shipping") or {}
    logistic_type = shipping.get("logistic_type") or ""
    mode = shipping.get("mode") or ""
    tags = shipping.get("tags") or item.get("shipping_tags") or item.get("tags") or item.get("sub_status") or []
    if isinstance(tags, str):
        tags = [tags]
    tag_text = " ".join(str(tag).lower() for tag in tags)
    if logistic_type == "self_service" or mode == "self_service" or "self_service" in tag_text or "mercado_envios_flex" in tag_text or "flex" in tag_text:
        return "self_service"
    return logistic_type or mode


def item_attribute_value(item, attr_ids):
    wanted = {str(attr_id).upper() for attr_id in attr_ids}
    for attribute in item.get("attributes", []) or []:
        if str(attribute.get("id") or "").upper() not in wanted:
            continue
        value = clean_attribute_value(attribute.get("value_name"))
        if value:
            return normalize_package_value(value, attr_ids)
        struct = attribute.get("value_struct") or {}
        if struct.get("number") and struct.get("unit"):
            return normalize_package_value(f"{struct.get('number')} {struct.get('unit')}", attr_ids)
    return ""


def package_dimensions_from_shipping(item):
    dimensions = first_present(item, ["shipping.dimensions"], "")
    if not dimensions:
        return {}
    match = re.match(r"\s*([\d.,]+)x([\d.,]+)x([\d.,]+)\s*,\s*([\d.,]+)", str(dimensions), flags=re.I)
    if not match:
        return {}
    width, height, length, weight = [parse_decimal_number(part) for part in match.groups()]
    return {
        "package_width": f"{width:g} cm",
        "package_height": f"{height:g} cm",
        "package_length": f"{length:g} cm",
        "package_weight": f"{weight / 1000:g} kg",
    }


def normalize_package_value(value, attr_ids):
    text = clean_attribute_value(value)
    match = re.search(r"([\d.,]+)\s*([a-zA-Z]+)", text)
    if not match:
        return text
    number = parse_decimal_number(match.group(1))
    unit = match.group(2).lower()
    attr_text = " ".join(str(attr).upper() for attr in attr_ids)
    if "WEIGHT" in attr_text:
        if unit in {"g", "gr", "gramas"}:
            return f"{number / 1000:g} kg"
        if unit in {"mg"}:
            return f"{number / 1000000:g} kg"
        if unit in {"kg", "kgs", "quilo", "quilos"}:
            return f"{number:g} kg"
        return f"{number:g} {unit}"
    if unit == "mm":
        return f"{number / 10:g} cm"
    if unit in {"m", "metro", "metros"}:
        return f"{number * 100:g} cm"
    return f"{number:g} {unit}"


def parse_decimal_number(value):
    text = str(value or "").strip()
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def seller_package_api_value(field, value):
    text = clean_attribute_value(value)
    match = re.search(r"([\d.,]+)\s*([a-zA-Z]*)", text)
    if not match:
        raise RuntimeError(f"Valor inválido para {field}.")
    number = parse_decimal_number(match.group(1))
    unit = (match.group(2) or "").lower()
    if field == "package_weight":
        if unit in {"kg", "kgs", "quilo", "quilos"} or not unit:
            grams = round(number * 1000)
        elif unit in {"g", "gr", "grama", "gramas"}:
            grams = round(number)
        else:
            raise RuntimeError("Informe o peso em kg ou g. Exemplo: 1,16 kg.")
        return f"{max(1, grams)} g"
    if unit == "mm":
        number /= 10
    elif unit in {"m", "metro", "metros"}:
        number *= 100
    elif unit not in {"", "cm"}:
        raise RuntimeError("Informe as dimensões em cm. Exemplo: 31 cm.")
    return f"{number:g} cm"


def package_values_from_item(item):
    shipping_dimensions = package_dimensions_from_shipping(item)
    return {
        "package_weight": item_attribute_value(item, ["SELLER_PACKAGE_WEIGHT"]) or shipping_dimensions.get("package_weight") or "",
        "package_height": item_attribute_value(item, ["SELLER_PACKAGE_HEIGHT"]) or shipping_dimensions.get("package_height") or "",
        "package_width": item_attribute_value(item, ["SELLER_PACKAGE_WIDTH"]) or shipping_dimensions.get("package_width") or "",
        "package_length": item_attribute_value(item, ["SELLER_PACKAGE_LENGTH"]) or shipping_dimensions.get("package_length") or "",
    }


def package_value_number(field, value):
    normalized = normalize_package_value(value, ["SELLER_PACKAGE_WEIGHT" if field == "package_weight" else "SELLER_PACKAGE_LENGTH"])
    match = re.search(r"[\d.,]+", normalized or "")
    return parse_decimal_number(match.group(0)) if match else None


def verify_package_update(client, item_id, expected):
    expected_numbers = {
        field: package_value_number(field, value)
        for field, value in expected.items()
        if clean_attribute_value(value)
    }
    latest = {}
    mismatches = []
    for attempt in range(4):
        latest = client.item(item_id)
        actual = package_values_from_item(latest)
        mismatches = []
        for field, expected_number in expected_numbers.items():
            actual_number = package_value_number(field, actual.get(field))
            tolerance = 0.005 if field == "package_weight" else 0.05
            if actual_number is None or expected_number is None or abs(actual_number - expected_number) > tolerance:
                mismatches.append(field)
        if not mismatches:
            return latest
        if attempt < 3:
            time.sleep(0.5 * (attempt + 1))
    labels = {
        "package_weight": "peso",
        "package_height": "altura",
        "package_width": "largura",
        "package_length": "comprimento",
    }
    names = ", ".join(labels.get(field, field) for field in mismatches)
    raise RuntimeError(
        f"O Mercado Livre recebeu a solicitação, mas não confirmou a alteração de {names}. "
        "Em anúncios Full ou com dimensões gerenciadas pela logística, esses campos podem ser somente leitura."
    )


def item_thumbnail(item):
    thumbnail = item.get("secure_thumbnail") or item.get("thumbnail") or ""
    pictures = item.get("pictures") or []
    if pictures:
        thumbnail = pictures[0].get("secure_url") or pictures[0].get("url") or thumbnail
    return thumbnail.replace("http://", "https://") if thumbnail else ""


def product_thumbnail(product):
    pictures = product.get("pictures") or []
    if pictures:
        first = pictures[0]
        if isinstance(first, dict):
            thumbnail = first.get("secure_url") or first.get("url") or first.get("id") or ""
        else:
            thumbnail = str(first)
        return thumbnail.replace("http://", "https://") if thumbnail else ""
    thumbnail = product.get("secure_thumbnail") or product.get("thumbnail") or product.get("picture") or ""
    return thumbnail.replace("http://", "https://") if thumbnail else ""


def first_present(payload, keys, default=None):
    for key in keys:
        current = payload
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current not in (None, ""):
            return current
    return default


def extract_meli_item_id(value):
    text = (value or "").strip().upper()
    match = re.search(r"\b(ML[A-Z]\d{5,})\b", text)
    if match:
        return match.group(1)
    compact = re.sub(r"[^A-Z0-9]", "", text)
    match = re.search(r"(ML[A-Z]\d{5,})", compact)
    if match:
        return match.group(1)
    return text if re.fullmatch(r"ML[A-Z]\d{5,}", text) else ""


def policy_error_message(exc, action):
    text = str(exc)
    if "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in text or "PolicyAgent" in text or "HTTP 403" in text:
        return (
            f"O Mercado Livre bloqueou {action} por política/permissão da aplicação. "
            "Confirme no painel de desenvolvedores se a aplicação tem permissões de leitura de anúncios/vendas "
            "e refaça o OAuth da conta conectada."
        )
    return text


def official_reader_client(payload):
    account = next((item for item in payload.get("accounts", []) if item.get("official") and item.get("access_token")), None)
    if not account:
        return None
    return account_client(account)


def meli_read(payload, path):
    validate_meli_path(path)
    client = official_reader_client(payload)
    if client:
        return client.get(path)
    return request_json(f"{MELI_API_URL}{path}")


def meli_public_read(path):
    validate_meli_path(path)
    return request_json(f"{MELI_API_URL}{path}")


def try_meli_sources(payload, paths):
    last_error = None
    for path in paths:
        for reader in (lambda p: meli_read(payload, p), meli_public_read):
            try:
                return reader(path)
            except Exception as exc:
                last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Nenhuma rota Mercado Livre informada.")


def seller_name_for(payload, seller_id):
    if not seller_id:
        return "Vendedor não informado"
    try:
        seller = meli_read(payload, f"/users/{seller_id}")
        return seller.get("nickname") or f"Seller {seller_id}"
    except Exception:
        return f"Seller {seller_id}"


def resolve_scan_target(payload, target_id):
    try:
        item = meli_read(payload, f"/items/{target_id}")
        item["_scan_target_type"] = "item"
        return item
    except Exception as exc:
        text = str(exc)
        if "not_found" not in text and "HTTP 404" not in text:
            raise RuntimeError(policy_error_message(exc, "a leitura deste anúncio no Scan")) from exc

    try:
        product = meli_read(payload, f"/products/{target_id}")
        offers = meli_read(payload, f"/products/{target_id}/items?site_id=MLB")
    except Exception as exc:
        raise RuntimeError(
            "Não encontrei esse código como anúncio nem como produto de catálogo. "
            "Cole o link completo do anúncio/produto ou confira se o MLB está correto."
        ) from exc

    rows = offers.get("results") if isinstance(offers, dict) else offers
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("items") or []
    rows = rows or []
    candidates = catalog_offer_candidates(rows)
    if not candidates:
        raise RuntimeError("Produto de catálogo encontrado, mas sem ofertas disponíveis para acompanhar.")
    live_candidates = live_catalog_offers(payload, candidates)
    validation_source = "live_item"
    if not live_candidates:
        live_candidates = candidates
        validation_source = "catalog_endpoint_unverified"
    best = live_candidates[0]
    item = {
        "id": best["item_id"] or target_id,
        "catalog_product_id": target_id,
        "title": product.get("name") or product.get("title") or best.get("title") or target_id,
        "seller_id": best["seller_id"],
        "price": best["price"],
        "permalink": best["permalink"] or product.get("permalink") or "",
        "thumbnail": product_thumbnail(product) or best.get("thumbnail") or "",
        "_scan_target_type": "catalog_product",
        "_scan_offer_count": len(live_candidates),
        "_scan_total_offer_count": len(candidates),
        "_scan_offers": live_candidates,
        "_scan_validation_source": validation_source,
    }
    return item


def offer_value(row, keys, default=""):
    return first_present(row, keys, default)


def catalog_offer_candidates(rows, sort_by_price=True):
    candidates = []
    for position, row in enumerate(rows or []):
        price = offer_value(row, ["price", "sale_price.amount", "current_price", "amount"], 0) or 0
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 0
        item_id = offer_value(row, ["item_id", "id", "item.id"], "")
        seller_id = offer_value(row, ["seller_id", "seller.id", "seller.id_seller"], "")
        permalink = offer_value(row, ["permalink", "item.permalink"], "")
        tags = row.get("tags") if isinstance(row, dict) else []
        tags = tags if isinstance(tags, list) else []
        winner_markers = [
            row.get("winner") if isinstance(row, dict) else None,
            row.get("winning") if isinstance(row, dict) else None,
            row.get("is_winner") if isinstance(row, dict) else None,
            first_present(row, ["metadata.winner", "catalog_position.winner"], None) if isinstance(row, dict) else None,
        ]
        is_winner = any(str(marker).lower() == "true" for marker in winner_markers) or any(
            str(tag).lower() in {"winner", "catalog_winner", "best_seller"} for tag in tags
        )
        if price:
            candidates.append(
                {
                    "price": price,
                    "item_id": item_id,
                    "seller_id": str(seller_id) if seller_id else "",
                    "permalink": permalink,
                    "position": position,
                    "is_winner": is_winner,
                    "raw": row,
                }
            )
    if sort_by_price:
        return sorted(candidates, key=lambda item: item["price"])
    return sorted(candidates, key=lambda item: (0 if item.get("is_winner") else 1, item.get("position", 999999)))


def live_catalog_offers(payload, candidates, sort_by_price=True):
    live = []
    max_offers = max(1, int(os.getenv("SCAN_VALIDATE_OFFERS_LIMIT", "150")))
    for candidate in candidates[:max_offers]:
        item_id = candidate.get("item_id")
        if not item_id:
            continue
        try:
            item = try_meli_sources(payload, [f"/items/{item_id}"])
        except Exception:
            continue
        status = item.get("status") or ""
        if status and status not in {"active", "under_review"}:
            continue
        try:
            price = float(item.get("price") or 0)
        except (TypeError, ValueError):
            price = 0
        if not price:
            continue
        live.append(
            {
                **candidate,
                "price": price,
                "seller_id": str(item.get("seller_id") or candidate.get("seller_id") or ""),
                "permalink": item.get("permalink") or candidate.get("permalink") or "",
                "thumbnail": item_thumbnail(item),
                "status": status or "active",
                "title": item.get("title") or "",
            }
        )
    if sort_by_price:
        return sorted(live, key=lambda item: item["price"])
    return sorted(live, key=lambda item: (0 if item.get("is_winner") else 1, item.get("position", 999999)))


def seller_name(payload, seller_id, client=None):
    seller_id = str(seller_id or "")
    if not seller_id:
        return ""
    for account in payload.get("accounts", []):
        if str(account.get("seller_id")) == seller_id:
            return account.get("nickname") or f"Seller {seller_id}"
    if client:
        try:
            seller = client.user(seller_id)
            return seller.get("nickname") or f"Seller {seller_id}"
        except Exception:
            pass
    return f"Seller {seller_id}"


def scan_meli_item(payload, scan_id):
    scans = payload.setdefault("scan_items", [])
    scan = next((item for item in scans if item.get("id") == scan_id), None)
    if not scan:
        raise RuntimeError("Produto de scan não encontrado.")
    item_id = scan.get("item_id") or extract_meli_item_id(scan.get("url"))
    if not item_id:
        raise RuntimeError("Não foi possível identificar o código MLB do anúncio.")
    item_data = resolve_scan_target(payload, item_id)
    seller_id = item_data.get("seller_id")
    seller_name = seller_name_for(payload, seller_id)
    price = float(item_data.get("price") or 0)
    minimum = float(scan.get("minimum_price") or 0)
    below_minimum_offers = []
    for offer in item_data.get("_scan_offers", []) or []:
        offer_price = float(offer.get("price") or 0)
        if minimum and offer_price <= minimum:
            offer_seller_id = offer.get("seller_id") or ""
            below_minimum_offers.append(
                {
                    "item_id": offer.get("item_id") or "",
                    "seller_id": offer_seller_id,
                    "seller_name": seller_name_for(payload, offer_seller_id),
                    "price": offer_price,
                    "permalink": offer.get("permalink") or "",
                }
            )
    below_minimum_offers = sorted(below_minimum_offers, key=lambda item: item["price"])
    history = scan.setdefault("history", [])
    last_price = history[0].get("price") if history else None
    changed = last_price != price
    entry = {
        "id": f"scan-log-{uuid.uuid4().hex[:10]}",
        "item_id": item_id,
        "title": item_data.get("title") or scan.get("name") or item_id,
        "seller_id": seller_id or "",
        "seller_name": seller_name,
        "price": price,
        "permalink": item_data.get("permalink") or scan.get("url") or "",
        "thumbnail": item_thumbnail(item_data),
        "target_type": item_data.get("_scan_target_type", "item"),
        "offer_count": item_data.get("_scan_offer_count", 1),
        "validation_source": item_data.get("_scan_validation_source", "live_item"),
        "created_at": now_label(),
        "changed": changed,
    }
    if changed or not history:
        history.insert(0, entry)
    scan["item_id"] = item_id
    scan["last_title"] = entry["title"]
    scan["last_seller_name"] = seller_name
    scan["last_price"] = price
    scan["last_thumbnail"] = entry["thumbnail"]
    scan["last_scan_at"] = entry["created_at"]
    scan["last_permalink"] = entry["permalink"]
    scan["target_type"] = entry["target_type"]
    scan["offer_count"] = entry["offer_count"]
    scan["validation_source"] = entry["validation_source"]
    scan["below_minimum_offers"] = below_minimum_offers
    scan["catalog_reference_offers"] = []
    alert_signature = "|".join(f"{offer['item_id']}:{offer['price']}" for offer in below_minimum_offers) or f"{item_id}:{price}"
    if minimum and (below_minimum_offers or price <= minimum) and scan.get("last_alert_signature") != alert_signature:
        if below_minimum_offers:
            seller_lines = "\n".join(
                f"- {offer['seller_name']} | {offer['item_id']} | {brl(offer['price'])}"
                for offer in below_minimum_offers[:6]
            )
            message = "\n".join(
                [
                    "CompeTIDOR | Alerta Scan",
                    "------------------------",
                    f"Produto: {scan.get('name') or entry['title']}",
                    f"Mínimo configurado: {brl(minimum)}",
                    f"Ofertas abaixo: {len(below_minimum_offers)}",
                    "",
                    seller_lines,
                ]
            )
        else:
            message = "\n".join(
                [
                    "CompeTIDOR | Alerta Scan",
                    "------------------------",
                    f"Produto: {scan.get('name') or entry['title']}",
                    f"Preço atual: {brl(price)}",
                    f"Mínimo configurado: {brl(minimum)}",
                    f"Vendedor: {seller_name}",
                    f"Anúncio: {item_id}",
                ]
            )
        try:
            send_telegram_message_to_users(payload, "scan", message)
            scan["last_alert_price"] = price
            scan["last_alert_signature"] = alert_signature
            scan["last_alert_at"] = now_label()
        except Exception as exc:
            scan["last_alert_error"] = str(exc)
        payload.setdefault("alerts", []).insert(
            0,
            {
                "id": f"alert-scan-{uuid.uuid4().hex[:8]}",
                "type": "scan",
                "severity": "critical",
                "title": f"Preço abaixo do mínimo no Scan: {scan.get('name') or item_id}",
                "message": message,
                "account": "Scan",
                "channel": ["dashboard", "telegram"],
                "created_at": now_label(),
                "read": False,
            },
        )
    scan["history"] = history[:120]
    return scan, entry


def scan_competitor_profile(payload, seller_id, limit=50):
    seller_id = str(seller_id or "").strip()
    if not seller_id:
        raise RuntimeError("Informe o ID do vendedor concorrente.")
    requested_limit = max(1, min(int(limit or 100), 500))
    errors = []
    try:
        profile = try_meli_sources(payload, [f"/users/{seller_id}"])
    except Exception as exc:
        errors.append(str(exc))
        profile = {"nickname": f"Seller {seller_id}", "permalink": ""}
    ids = []
    total = 0
    source = "users_items_search"
    routes = [
        ("users_items_search", lambda size, offset: f"/users/{seller_id}/items/search?limit={size}&offset={offset}"),
        ("sites_search_public", lambda size, offset: f"/sites/MLB/search?seller_id={seller_id}&limit={size}&offset={offset}"),
    ]
    for route_name, route_builder in routes:
        ids = []
        total = 0
        source = route_name
        offset = 0
        try:
            while len(ids) < requested_limit:
                page_size = min(50, requested_limit - len(ids))
                page = try_meli_sources(payload, [route_builder(page_size, offset)])
                batch = page.get("results", []) or []
                for row in batch:
                    ids.append(row if isinstance(row, str) else row.get("id"))
                ids = [item_id for item_id in ids if item_id]
                total = (page.get("paging") or {}).get("total") or len(ids)
                if not batch or len(ids) >= total:
                    break
                offset += page_size
            if ids:
                break
        except Exception as exc:
            errors.append(str(exc))
            ids = []
            continue
    items = []
    for item_id in ids[:requested_limit]:
        try:
            item = try_meli_sources(payload, [f"/items/{item_id}"])
            price = float(item.get("price") or 0)
            sold = int(item.get("sold_quantity") or 0)
            items.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title") or item.get("id"),
                    "price": price,
                    "sold_quantity": sold,
                    "available_quantity": item_available_quantity(item),
                    "status": item.get("status") or "-",
                    "listing_type_id": item.get("listing_type_id") or "",
                    "thumbnail": item_thumbnail(item),
                    "permalink": item.get("permalink") or "",
                    "estimated_revenue": round(price * sold, 2),
                }
            )
        except Exception:
            continue
    prices = [item["price"] for item in items if item.get("price")]
    competitor = {
        "id": f"competitor-{seller_id}",
        "seller_id": seller_id,
        "name": profile.get("nickname") or f"Seller {seller_id}",
        "permalink": profile.get("permalink") or "",
        "reputation": first_present(profile, ["seller_reputation.level_id"], "Não informado"),
        "transactions_total": first_present(profile, ["seller_reputation.transactions.total"], None),
        "transactions_completed": first_present(profile, ["seller_reputation.transactions.completed"], None),
        "items_total": total or len(ids),
        "items_loaded": len(items),
        "analysis_limit": requested_limit,
        "source": source,
        "sync_status": "ok" if items else "blocked",
        "sync_error": policy_error_message(Exception(errors[-1]), "a análise deste concorrente") if errors and not items else "",
        "price_min": min(prices) if prices else 0,
        "price_max": max(prices) if prices else 0,
        "price_avg": round(sum(prices) / len(prices), 2) if prices else 0,
        "estimated_revenue": round(sum(item.get("estimated_revenue") or 0 for item in items), 2),
        "items": items,
        "updated_at": now_label(),
        "note": "Dados públicos oficiais. Faturamento é estimado por preço atual x sold_quantity público, quando disponível."
        if items
        else "O Mercado Livre bloqueou a listagem pública de anúncios deste vendedor por política da API. Tente acompanhar concorrência por produto/catálogo via Scan.",
    }
    competitors = payload.setdefault("competitors", [])
    existing = next((index for index, item in enumerate(competitors) if str(item.get("seller_id")) == seller_id), None)
    if existing is None:
        competitors.insert(0, competitor)
    else:
        competitors[existing] = {**competitors[existing], **competitor}
    return competitor


def process_meli_notification(event):
    topic = str(event.get("topic") or "")
    if topic not in {"items", "orders_v2"}:
        return
    seller_id = str(event.get("user_id") or "")
    payload = read_payload()
    account = next(
        (
            row
            for row in payload.get("accounts", [])
            if str(row.get("seller_id") or "") == seller_id and row.get("official")
        ),
        None,
    )
    if not account:
        return
    client = account_client(account)
    if topic == "orders_v2":
        sync_recent_sales(payload, account, client)
        account["last_webhook_at"] = now_label()
        write_payload(payload)
        return

    resource = str(event.get("resource") or "")
    match = re.fullmatch(r"/items/(MLB\d+)", resource, flags=re.I)
    if not match:
        return
    item_id = match.group(1).upper()
    official = client.item(item_id)
    existing = next(
        (
            row
            for row in payload.get("catalog", [])
            if row.get("id") == item_id and row.get("account_id") == account.get("id")
        ),
        None,
    )
    before = dict(existing) if existing else None
    is_catalog = bool(official.get("catalog_listing") or official.get("catalog_product_id"))
    competition = normalize_competition(client, account, official) if is_catalog else {}
    updated = synced_catalog_item(account, official, competition)
    updated["first_seen_at"] = (before or {}).get("first_seen_at") or now_label()
    updated["updated_at"] = now_label()
    updated["last_webhook_at"] = now_label()
    if existing is None:
        payload.setdefault("catalog", []).append(updated)
    else:
        existing.update(updated)

    if before and int(before.get("stock") or 0) > 0 and int(updated.get("stock") or 0) == 0:
        alert = stock_alert(f"stock-{account.get('id')}-{item_id}-{uuid.uuid4().hex[:8]}", account, updated)
        payload.setdefault("alerts", []).insert(0, alert)
        notify_alert(payload, alert)
    if before and before.get("status") != "losing" and updated.get("status") == "losing":
        alert = {
            "id": f"catalog-{account.get('id')}-{item_id}-{uuid.uuid4().hex[:8]}",
            "type": "catalog",
            "severity": "danger",
            "title": "Produto começou a perder catálogo",
            "message": (
                f"{updated.get('title')} deixou de vencer o catálogo. "
                f"Preço para ganhar: {updated.get('price_to_win') or 'não informado pela API'}."
            ),
            "account": updated.get("account"),
            "item_id": item_id,
            "sku": updated.get("sku"),
            "product": updated.get("title"),
            "created_at": now_label(),
            "read": False,
        }
        payload.setdefault("alerts", []).insert(0, alert)
        notify_alert(payload, alert)
    account["last_webhook_at"] = now_label()
    account["webhook_status"] = f"Última notificação processada: {topic} {item_id}"
    write_payload(payload)


def meli_notification_loop():
    while True:
        event = MELI_NOTIFICATION_QUEUE.get()
        try:
            process_meli_notification(event)
        except Exception:
            pass
        finally:
            MELI_NOTIFICATION_QUEUE.task_done()


def auto_scan_loop():
    interval = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
    time.sleep(8)
    while True:
        try:
            payload = read_payload()
            scans = [item for item in payload.get("scan_items", []) if item.get("status", "active") == "active"]
            changed = False
            for scan in scans:
                try:
                    scan_meli_item(payload, scan.get("id"))
                    scan["auto_scan_status"] = "Scan automático atualizado"
                    scan["auto_scan_error"] = ""
                    scan["last_auto_scan_at"] = now_label()
                    scan["auto_scan_interval_seconds"] = interval
                    changed = True
                except Exception as exc:
                    scan["auto_scan_status"] = "Scan automático com erro"
                    scan["auto_scan_error"] = str(exc)
                    scan["last_auto_scan_at"] = now_label()
                    scan["auto_scan_interval_seconds"] = interval
                    changed = True
            if changed:
                payload["scan_items"] = payload.get("scan_items", [])
                write_payload(payload)
        except Exception:
            pass
        time.sleep(interval)


def auto_official_sync_loop():
    interval = max(120, int(os.getenv("AUTO_SYNC_INTERVAL_SECONDS", "300")))
    startup_delay = max(20, int(os.getenv("AUTO_SYNC_STARTUP_DELAY_SECONDS", "45")))
    full_every = max(1, int(os.getenv("AUTO_FULL_SYNC_EVERY_N_RUNS", "72")))
    run_count = 0
    time.sleep(startup_delay)
    while True:
        try:
            payload = read_payload()
            accounts = [
                account
                for account in payload.get("accounts", [])
                if account.get("official") and account.get("access_token") and account.get("status") == "connected"
            ]
            changed = False
            run_count += 1
            for account in accounts:
                try:
                    if run_count % full_every == 0:
                        result = sync_official_account(payload, account.get("id"), "all")
                        account["auto_sync_status"] = f"Sincronização completa automática OK: {result.get('items', 0)} anúncios"
                    else:
                        result = refresh_official_account_items(payload, account)
                        account["auto_sync_status"] = account.get("auto_refresh_status") or f"Atualização automática OK: {result.get('items', 0)} anúncios"
                    account["auto_sync_error"] = ""
                    account["last_auto_sync_at"] = now_label()
                    changed = True
                except Exception as exc:
                    account["auto_sync_status"] = "Sincronização automática com erro"
                    account["auto_sync_error"] = str(exc)
                    account["last_auto_sync_at"] = now_label()
                    changed = True
            if changed:
                write_payload(payload)
        except Exception:
            pass
        time.sleep(interval)


def parse_brl_price(text):
    match = re.search(r"R\$\s*([\d\.]+)(?:,(\d{2}))?", text or "")
    if not match:
        return None
    whole = match.group(1).replace(".", "")
    cents = match.group(2) or "00"
    try:
        return float(f"{whole}.{cents}")
    except ValueError:
        return None


def visible_page_lines(html_text):
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", "\n", html_text or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|section|article|li|h[1-6]|span|a)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if re.sub(r"\s+", " ", line).strip()]


def canonical_product_url(catalog_product_id, url=""):
    if catalog_product_id:
        return f"https://www.mercadolivre.com.br/p/{catalog_product_id}"
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else url.split("?", 1)[0].split("#", 1)[0]


def product_public_urls(client, catalog_product_id, item=None):
    urls = [canonical_product_url(catalog_product_id)]
    try:
        product = client.product(catalog_product_id)
        permalink = product.get("permalink") or product.get("url")
        if permalink:
            urls.append(canonical_product_url(catalog_product_id, permalink))
        product_title = product.get("name") or product.get("title") or ""
    except Exception:
        product_title = ""
    item_permalink = (item or {}).get("permalink") or ""
    if item_permalink:
        urls.append(canonical_product_url(catalog_product_id, item_permalink))
    deduped = []
    for url in urls:
        if url and url not in deduped:
            deduped.append(url)
    return deduped, product_title


def fetch_public_buybox(client, catalog_product_id, item=None):
    urls, product_title = product_public_urls(client, catalog_product_id, item)
    last_error = None
    for url in urls:
        try:
            return fetch_public_buybox_url(url, product_title)
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError("Não foi possível consultar a página pública do Mercado Livre.")


def fetch_public_buybox_url(url, product_title=""):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=float(os.getenv("MELI_PUBLIC_PAGE_TIMEOUT_SECONDS", "4"))) as response:
        html_text = response.read().decode("utf-8", errors="replace")
        final_url = response.geturl() or url
    lines = visible_page_lines(html_text)
    seller_name = ""
    price = None
    price_patterns = [
        r'itemprop="price"\s+content="(\d+(?:\.\d+)?)"',
        r'content="(\d+(?:\.\d+)?)"\s+itemprop="price"',
        r'ui-pdp-price__second-line[\s\S]{0,1200}?andes-money-amount__fraction[^>]*>([\d\.]+)<',
    ]
    for pattern_index, pattern in enumerate(price_patterns):
        match = re.search(pattern, html_text, flags=re.I)
        if match:
            try:
                parsed_price = float(match.group(1)) if pattern_index < 2 else parse_brl_price(f"R$ {match.group(1)}")
                if parsed_price:
                    price = parsed_price
                    break
            except (TypeError, ValueError):
                pass
    anchors = []
    if product_title:
        anchors.append(product_title)
    anchors.extend(["Novo |", "Adicionar aos favoritos", "Compartilhar"])
    search_text = "\n".join(lines)
    start = 0
    for anchor in anchors:
        pos = search_text.lower().find(anchor.lower())
        if pos >= 0:
            start = pos
            break
    end = len(search_text)
    for anchor in ["Opções de compra", "Ir para a compra", "O que você precisa saber", "Características"]:
        pos = search_text.find(anchor, start)
        if pos > start:
            end = min(end, pos)
    main_area = search_text[start:end]
    sold_match = re.search(r"Vendido por\s+([^\n]+)", main_area, flags=re.I)
    if sold_match:
        seller_name = re.split(r"\s{2,}|\+\d+\s+vendas|NF-e|verificada", sold_match.group(1).strip())[0].strip()
    if not price:
        price_candidates = []
        for line in main_area.splitlines():
            if re.search(r"\b\d+x\b|sem juros|meios de pagamento", line, flags=re.I):
                continue
            for match in re.finditer(r"R\$\s*[\d\.]+(?:,\d{2})?", line):
                parsed = parse_brl_price(match.group(0))
                if parsed:
                    price_candidates.append(parsed)
        if price_candidates:
            price = min(price_candidates)
    if not seller_name or not price:
        raise RuntimeError("A página pública não confirmou simultaneamente vendedor e preço da buybox.")
    return {
        "seller_name": seller_name,
        "price": price,
        "url": final_url,
        "source": "public_product_page",
    }


def public_purchase_options_winner(lines):
    best_index = next((i for i, line in enumerate(lines) if "Melhor preço" in line), -1)
    if best_index < 0:
        options_index = next((i for i, line in enumerate(lines) if "Outras opções de compra" in line or "Opções de compra" in line), -1)
        if options_index >= 0:
            best_index = options_index + 1
    if best_index < 0:
        return {}

    window_start = max(0, best_index - 8)
    window_end = min(len(lines), best_index + 18)
    window = lines[window_start:window_end]
    price = None
    seller_name = ""
    price_candidates = []
    for line in window:
        if re.search(r"R\$\s*[\d\.]+(?:,\d{2})?", line):
            parsed = parse_brl_price(line)
            if parsed:
                price_candidates.append(parsed)
    if price_candidates:
        price = min(price_candidates)
    for index, line in enumerate(window):
        match = re.search(r"Loja oficial\s+(.+)", line, flags=re.I)
        if match:
            seller_name = re.split(r"\s{2,}|\s\+\d|NF-e|verificada", match.group(1).strip())[0].strip()
            break
        if "Vendido por" in line:
            seller_name = line.split("Vendido por", 1)[1].strip(" :·-")
            break
        if re.search(r"Loja\s+oficial", line, flags=re.I) and index + 1 < len(window):
            seller_name = window[index + 1].strip()
            break
    return {
        "seller_name": seller_name or "",
        "price": price,
        "source": "public_purchase_options",
    }


def normalize_competition(client, account, item):
    item_id = item.get("id")
    catalog_product_id = item.get("catalog_product_id")
    data = {
        "competition_status": "not_checked",
        "winner_seller_id": "",
        "winner_name": "Indisponível",
        "winner_price": None,
        "winner_confirmed": False,
        "winner_source": "",
        "catalog_reference_seller_id": "",
        "catalog_reference_name": "",
        "catalog_reference_price": None,
        "price_to_win": None,
        "current_price": None,
        "visit_share": None,
        "competition_reason": "",
        "competitors_sharing_first_place": None,
    }

    try:
        price = client.price_to_win(item_id)
        winner = price.get("winner") or {}
        data.update(
            {
                "competition_status": price.get("status") or "unknown",
                "price_to_win": price.get("price_to_win"),
                "current_price": price.get("current_price"),
                "visit_share": price.get("visit_share"),
                "competitors_sharing_first_place": price.get("competitors_sharing_first_place"),
                "competition_reason": ", ".join(price.get("reason") or []),
            }
        )
        if isinstance(winner, dict) and winner.get("item_id"):
            winner_item_id = winner.get("item_id")
            winner_seller_id = ""
            winner_name = ""
            try:
                winner_item = client.item(winner_item_id)
                winner_seller_id = str(winner_item.get("seller_id") or "")
                if winner_seller_id:
                    winner_profile = client.user(winner_seller_id)
                    winner_name = winner_profile.get("nickname") or f"Seller {winner_seller_id}"
            except Exception:
                pass
            if str(winner_item_id) == str(item_id):
                winner_seller_id = str(account.get("seller_id") or winner_seller_id)
                winner_name = account.get("nickname") or winner_name or "Sua conta"
            data["winner_seller_id"] = winner_seller_id
            data["winner_name"] = winner_name or f"Anúncio {winner_item_id}"
            data["winner_price"] = winner.get("price") or price.get("price_to_win") or price.get("current_price")
            data["winner_confirmed"] = True
            data["winner_source"] = "price_to_win_winner"
        elif price.get("status") in {"winning", "winner"}:
            data["winner_seller_id"] = account.get("seller_id", "")
            data["winner_name"] = account.get("nickname", "Sua conta")
            data["winner_price"] = price.get("current_price")
            data["winner_confirmed"] = True
            data["winner_source"] = "price_to_win"
    except Exception as exc:
        data["competition_reason"] = str(exc)

    if catalog_product_id and not data.get("winner_confirmed"):
        try:
            public_buybox = fetch_public_buybox(client, catalog_product_id, item)
            data["winner_name"] = public_buybox.get("seller_name") or "Vendedor da buybox"
            data["winner_price"] = public_buybox.get("price")
            data["winner_confirmed"] = True
            data["winner_source"] = public_buybox.get("source") or "public_product_page"
            data["competition_status"] = data["competition_status"] if data["competition_status"] != "not_checked" else "public_buybox"
            data["competition_reason"] = "Buybox verificada na página pública do Mercado Livre."
        except Exception as exc:
            data["public_buybox_error"] = str(exc)

    if catalog_product_id:
        try:
            winners = client.product_winners(catalog_product_id)
            rows = winners.get("results") if isinstance(winners, dict) else winners
            if isinstance(rows, dict):
                rows = rows.get("results") or rows.get("items") or []
            if rows:
                candidates = catalog_offer_candidates(rows, sort_by_price=True)
                explicit_winner = next((candidate for candidate in candidates if candidate.get("is_winner")), None)
                lowest_active = candidates[0] if candidates else None
                reference = explicit_winner or lowest_active or {"raw": rows[0], "price": None, "item_id": "", "seller_id": ""}
                raw_reference = reference.get("raw") or rows[0]
                reference_seller_id = str(reference.get("seller_id") or first_present(raw_reference, ["seller_id", "seller.id", "seller.id_seller"], ""))
                reference_item_id = reference.get("item_id") or first_present(raw_reference, ["item_id", "id", "item.id"], "")
                reference_price = reference.get("price") or first_present(raw_reference, ["price", "sale_price.amount", "current_price"], None)
                if reference_item_id:
                    try:
                        reference_item = client.item(reference_item_id)
                        if reference_item.get("status") in {"active", "under_review", None, ""}:
                            reference_seller_id = reference_seller_id or str(reference_item.get("seller_id") or "")
                            reference_price = reference_item.get("price") or reference_price
                    except Exception:
                        pass
                data["competition_status"] = data["competition_status"] if data["competition_status"] != "not_checked" else "listed"
                data["catalog_reference_seller_id"] = reference_seller_id
                data["catalog_reference_price"] = reference_price
                if reference_seller_id:
                    try:
                        seller = client.user(reference_seller_id)
                        data["catalog_reference_name"] = seller.get("nickname") or f"Seller {reference_seller_id}"
                    except Exception:
                        data["catalog_reference_name"] = f"Seller {reference_seller_id}"
                if not data.get("winner_confirmed") and lowest_active:
                    data["competition_reason"] = (
                        "A API oficial confirmou a disputa e o preço para ganhar, mas não expõe o vendedor da buybox. "
                        "A menor oferta ativa não será tratada como vencedora."
                    )
        except Exception as exc:
            if not data["competition_reason"]:
                data["competition_reason"] = str(exc)

    if not data.get("winner_confirmed"):
        data["winner_seller_id"] = ""
        data["winner_name"] = "Não exposto pela API oficial"
        data["winner_price"] = None
    if data["competition_status"] == "not_listed" and not data.get("winner_seller_id"):
        data["winner_name"] = "Sem vencedor disponível"
    return data


def classified_catalog_status(account, item, stock, is_catalog, competition):
    if item.get("status") == "paused":
        return "paused", "Anúncio pausado no Mercado Livre. Ative o anúncio e revise estoque, preço e políticas antes de disputar catálogo."
    if stock == 0:
        return "paused", "Anúncio importado sem estoque disponível. Pode estar pausado ou em risco de pausa por estoque zerado."
    if not is_catalog:
        return "winning", "Anúncio importado da API oficial. Sem vínculo de catálogo identificado neste retorno."

    own_seller_id = str(account.get("seller_id") or "")
    winner_seller_id = str(competition.get("winner_seller_id") or "")
    winner_name = str(competition.get("winner_name") or "")
    own_name = str(account.get("nickname") or "")
    status_text = str(competition.get("competition_status") or "").lower()
    try:
        own_price = float(item.get("price") or competition.get("current_price") or 0)
    except (TypeError, ValueError):
        own_price = 0
    try:
        winner_price = float(competition.get("winner_price") or 0)
    except (TypeError, ValueError):
        winner_price = 0
    try:
        price_to_win = float(competition.get("price_to_win") or 0)
    except (TypeError, ValueError):
        price_to_win = 0

    if price_to_win and own_price and own_price > price_to_win:
        return "losing", "Sua conta não está vencendo este catálogo. Revise preço, reputação e condições comerciais."
    if winner_seller_id and own_seller_id and winner_seller_id == own_seller_id:
        return "winning", "Sua conta está vencendo a buybox deste catálogo."
    if winner_name and own_name and winner_name.strip().lower() == own_name.strip().lower():
        return "winning", "Sua conta está vencendo a buybox deste catálogo."
    if "sharing" in status_text or "shared" in status_text or competition.get("competitors_sharing_first_place"):
        return "sharing", "Sua conta está compartilhando a primeira posição do catálogo."
    if competition.get("price_to_win") or status_text in {"competing", "losing", "not_winning"}:
        return "losing", "Sua conta não está vencendo este catálogo. Revise preço, reputação e condições comerciais."
    if competition.get("winner_confirmed") and winner_price and own_price:
        if own_price > winner_price:
            return "losing", "Sua conta está acima do preço vencedor informado para a buybox."
        if abs(own_price - winner_price) < 0.01:
            return "sharing", "Sua conta está no mesmo preço da oferta vencedora; pode estar compartilhando a disputa."
        return "winning", "Sua conta está com preço abaixo da oferta vencedora informada; confirme reputação e frete."
    return "sharing", "Anúncio de catálogo importado da API oficial. Aguardando confirmação completa da buybox."


def synced_catalog_item(account, item, competition=None):
    stock = item_available_quantity(item)
    catalog_product_id = item.get("catalog_product_id") or "-"
    listing_type_id = item.get("listing_type_id") or first_present(item, ["listing_type.id", "listing_type"], "")
    shipping_logistic_type = item_flex_logistic_type(item)
    shipping_mode = first_present(item, ["shipping.mode"], "")
    is_catalog = bool(item.get("catalog_listing") or item.get("catalog_product_id"))
    competition = competition or {}
    status, action = classified_catalog_status(account, item, stock, is_catalog, competition)
    package_values = package_values_from_item(item)
    return {
        "id": item.get("id"),
        "title": item.get("title") or item.get("id"),
        "account": account.get("nickname"),
        "account_id": account.get("id"),
        "official_source": True,
        "thumbnail": item_thumbnail(item),
        "sku": item_sku(item),
        "catalog_product_id": catalog_product_id,
        "catalog_listing": bool(item.get("catalog_listing")),
        "listing_type_id": listing_type_id,
        "shipping_logistic_type": shipping_logistic_type,
        "shipping_mode": shipping_mode,
        **package_values,
        "status": status,
        "share": 0 if is_catalog else 100,
        "price": item.get("price") or 0,
        "stock": stock,
        "competitor": "A sincronizar",
        "action": action,
        "meli_status": item.get("status", "-"),
        "permalink": item.get("permalink", ""),
        **competition,
    }


def append_item_log(payload, item, user, action, changes=None, sale_id=None):
    logs = payload.setdefault("item_logs", [])
    logs.insert(
        0,
        {
            "id": f"log-{uuid.uuid4().hex[:10]}",
            "item_id": item.get("id"),
            "account": item.get("account"),
            "title": item.get("title"),
            "user": (user or {}).get("name") or (user or {}).get("email") or "Sistema",
            "action": action,
            "changes": changes or {},
            "sale_id": sale_id or "",
            "created_at": now_label(),
        },
    )
    payload["item_logs"] = logs[:1000]


def upsert_metric(payload, account, user_profile):
    reputation = user_profile.get("seller_reputation", {}) or {}
    metrics = reputation.get("metrics", {}) or {}
    claims = percent_rate((metrics.get("claims") or {}).get("rate"))
    cancellations = percent_rate((metrics.get("cancellations") or {}).get("rate"))
    late = percent_rate((metrics.get("delayed_handling_time") or {}).get("rate"))
    metric = {
        "account": account.get("nickname"),
        "claims": claims,
        "mediations": percent_rate((metrics.get("sales") or {}).get("completed")),
        "cancellations": cancellations,
        "late_shipments": late,
        "agency_score": max(0, round(100 - late, 2)),
        "flex_score": max(0, round(100 - late, 2)),
        "period": "Período informado pela API oficial",
    }
    existing = payload.get("metrics", [])
    for index, current in enumerate(existing):
        if current.get("account") == account.get("nickname"):
            existing[index] = metric
            break
    else:
        existing.append(metric)
    payload["metrics"] = existing


def stock_alert(alert_id, account, item):
    account_name = account.get("nickname") or item.get("account")
    sku = item.get("sku") or "-"
    item_id = item.get("id") or "-"
    return {
        "id": alert_id,
        "type": "stock",
        "severity": "critical",
        "title": "Produto sem estoque",
        "message": f"{item.get('title')} está com estoque zerado na conta {account_name}. SKU: {sku}. MLB: {item_id}.",
        "account": account_name,
        "item_id": item_id,
        "sku": sku,
        "product": item.get("title") or item_id,
        "channel": ["dashboard", "telegram"],
        "created_at": now_label(),
        "read": False,
    }


def product_context_for_alert(payload, alert):
    if alert.get("type") == "scan":
        return {}
    item_id = alert.get("item_id") or ""
    if not item_id and str(alert.get("id", "")).startswith("stock-"):
        item_id = str(alert.get("id", "")).split("-")[-1]
    match = next(
        (
            item
            for item in payload.get("catalog", [])
            if item.get("id") == item_id
            or (
                alert.get("account")
                and item.get("account") == alert.get("account")
                and item.get("title")
                and item.get("title") in (alert.get("message") or "")
            )
        ),
        None,
    )
    if not match:
        return {
            "account": alert.get("account") or "",
            "item_id": alert.get("item_id") or "",
            "sku": alert.get("sku") or "",
            "product": alert.get("product") or "",
        }
    alert.setdefault("item_id", match.get("id") or "")
    alert.setdefault("sku", match.get("sku") or "")
    alert.setdefault("product", match.get("title") or "")
    return {
        "account": match.get("account") or alert.get("account") or "",
        "item_id": match.get("id") or alert.get("item_id") or "",
        "sku": match.get("sku") or alert.get("sku") or "",
        "product": match.get("title") or alert.get("product") or "",
    }


def telegram_alert_text(payload, alert):
    header = f"CompeTIDOR | {alert.get('title') or 'Alerta'}"
    lines = [
        header,
        "-" * min(42, max(18, len(header))),
    ]
    context = product_context_for_alert(payload, alert)
    if alert.get("type") != "scan" and any(context.values()):
        if context.get("product"):
            lines.append(f"Produto: {context['product']}")
        if context.get("account"):
            lines.append(f"Loja: {context['account']}")
        if context.get("item_id"):
            lines.append(f"MLB: {context['item_id']}")
        if context.get("sku"):
            lines.append(f"SKU: {context['sku']}")
        lines.append("")
    elif alert.get("account"):
        lines.append(f"Conta: {alert.get('account')}")
        lines.append("")
    lines.extend(
        [
            "Detalhe:",
            str(alert.get("message") or "-"),
            "",
            f"Horário: {alert.get('created_at') or now_label()}",
        ]
    )
    return "\n".join(lines)


def notify_alert(payload, alert):
    results = {}
    for user_id, config in notification_targets(payload):
        telegram = config.get("telegram") or {}
        if not telegram.get("enabled") or not telegram.get("bot_token") or not telegram.get("chat_id"):
            continue
        if not telegram_type_enabled(config, alert.get("type")):
            continue
        try:
            results[user_id] = Notifier(config).send_telegram(telegram_alert_text(payload, alert))
        except Exception as exc:
            results[user_id] = {"ok": False, "error": str(exc)}
    if results:
        alert["telegram_results"] = results
    return results or None


def send_telegram_message_to_users(payload, alert_type, text):
    results = {}
    for user_id, config in notification_targets(payload):
        telegram = config.get("telegram") or {}
        if not telegram.get("enabled") or not telegram.get("bot_token") or not telegram.get("chat_id"):
            continue
        if not telegram_type_enabled(config, alert_type):
            continue
        try:
            results[user_id] = Notifier(config).send_telegram(text)
        except Exception as exc:
            results[user_id] = {"ok": False, "error": str(exc)}
    return results


def add_stock_alerts(payload, account, items):
    created = 0
    existing_ids = {alert.get("id") for alert in payload.get("alerts", [])}
    for item in items:
        if item.get("stock") != 0:
            continue
        alert_id = f"stock-{account.get('id')}-{item.get('id')}"
        if alert_id in existing_ids:
            continue
        alert = stock_alert(alert_id, account, item)
        payload.setdefault("alerts", []).insert(0, alert)
        notify_alert(payload, alert)
        existing_ids.add(alert_id)
        created += 1
    return created


def ensure_stock_alerts(payload, notify=False):
    created = 0
    existing_ids = {alert.get("id") for alert in payload.get("alerts", [])}
    accounts = payload.get("accounts", [])
    for item in payload.get("catalog", []):
        if item.get("stock") != 0:
            continue
        account = next(
            (
                account
                for account in accounts
                if account.get("id") == item.get("account_id") or account.get("nickname") == item.get("account")
            ),
            {"id": item.get("account_id") or item.get("account"), "nickname": item.get("account")},
        )
        alert_id = f"stock-{account.get('id')}-{item.get('id')}"
        if alert_id in existing_ids:
            continue
        alert = stock_alert(alert_id, account, item)
        payload.setdefault("alerts", []).insert(0, alert)
        existing_ids.add(alert_id)
        created += 1
        if notify:
            notify_alert(payload, alert)
    return created


def enrich_product_alerts(payload):
    changed = False
    for alert in payload.get("alerts", []):
        if alert.get("type") == "scan":
            continue
        before = (alert.get("item_id"), alert.get("sku"), alert.get("product"))
        context = product_context_for_alert(payload, alert)
        if context.get("item_id"):
            alert["item_id"] = context["item_id"]
        if context.get("sku"):
            alert["sku"] = context["sku"]
        if context.get("product"):
            alert["product"] = context["product"]
        changed = changed or before != (alert.get("item_id"), alert.get("sku"), alert.get("product"))
    return changed


def sync_recent_sales(payload, account, client):
    period, date_from, date_to = current_month_window()
    try:
        orders = []
        offset = 0
        page_size = 50
        max_orders = int(os.getenv("MELI_MONTHLY_ORDERS_LIMIT", "500"))
        while len(orders) < max_orders:
            data = client.seller_orders(account.get("seller_id"), limit=page_size, offset=offset, date_from=date_from, date_to=date_to)
            batch = data.get("results", []) or []
            orders.extend(batch)
            total = (data.get("paging") or {}).get("total") or len(orders)
            if not batch or len(orders) >= total:
                break
            offset += page_size
    except Exception as exc:
        account["sales_sync_status"] = policy_error_message(exc, "a leitura das vendas reais do mês")
        mark_monthly_revenue_error(payload, account, period, account["sales_sync_status"])
        return []
    rows = []
    revenue_total = 0.0
    revenue_orders = 0
    ignored_statuses = {"cancelled", "canceled", "invalid"}
    catalog_by_id = {item.get("id"): item for item in payload.get("catalog", []) if item.get("id")}
    for order in orders:
        status = str(order.get("status") or "").lower()
        order_total = float(order.get("total_amount") or order.get("paid_amount") or 0)
        if order_total > 0 and status not in ignored_statuses:
            revenue_total += order_total
            revenue_orders += 1
        order_items = order.get("order_items") or []
        for order_item in order_items or [{}]:
            item = order_item.get("item") or {}
            quantity = order_item.get("quantity") or 1
            unit_price = order_item.get("unit_price") or order_item.get("full_unit_price") or 0
            line_total = round(float(unit_price or 0) * int(quantity or 1), 2)
            sale = {
                "id": f"{order.get('id')}-{item.get('id') or len(rows)}",
                "order_id": order.get("id"),
                "account": account.get("nickname"),
                "product": item.get("title") or "Produto sem título",
                "item_id": item.get("id") or "",
                "thumbnail": item.get("thumbnail") or item.get("secure_thumbnail") or (catalog_by_id.get(item.get("id")) or {}).get("thumbnail") or "",
                "sku": item.get("seller_sku") or item.get("seller_custom_field") or "-",
                "quantity": quantity,
                "unit_price": unit_price,
                "total": line_total,
                "order_total": order_total,
                "channel": order.get("context", {}).get("channel") or order.get("tags", ["Mercado Livre"])[0],
                "status": order.get("status") or "-",
                "date": order.get("date_created") or order.get("last_updated") or now_label(),
            }
            rows.append(sale)
    existing = [
        item
        for item in payload.get("recent_sales", [])
        if item.get("account") != account.get("nickname")
    ]
    payload["recent_sales"] = sorted([*rows, *existing], key=lambda item: item.get("date") or "", reverse=True)[:80]
    account["sales_sync_status"] = f"{revenue_orders} pedidos reais sincronizados no mês"
    upsert_monthly_revenue(payload, account, revenue_total, revenue_orders, period, account["sales_sync_status"])
    sync_pending_shipments_from_orders(payload, account, orders)
    return rows


def sync_claims(payload, account, client):
    try:
        data = client.seller_claims(account.get("seller_id"), 50, 0)
        rows = data.get("results") or data.get("claims") or []
    except Exception as exc:
        account["claims_sync_status"] = policy_error_message(exc, "a leitura das reclamações")
        if "404" in str(exc) or "resource not found" in str(exc).lower():
            account["claims_sync_status"] = (
                "Reclamações não retornadas pela API oficial nesta credencial. "
                "Verifique no painel do Mercado Livre se a aplicação tem permissão de pós-venda/reclamações."
            )
        return []
    details = []
    open_count = 0
    mediation_count = 0
    for claim in rows:
        status = str(claim.get("status") or claim.get("stage") or "").lower()
        if "medi" in status:
            mediation_count += 1
        elif status not in {"closed", "resolved", "cancelled", "canceled"}:
            open_count += 1
        details.append(
            {
                "id": claim.get("id") or claim.get("claim_id") or "-",
                "account": account.get("nickname"),
                "status": claim.get("status") or claim.get("stage") or "-",
                "subject": claim.get("type") or claim.get("reason") or "Reclamação Mercado Livre",
                "description": claim.get("description") or claim.get("detail") or claim.get("reason") or "",
                "created_at": claim.get("date_created") or claim.get("created_at") or now_label(),
            }
        )
    payload["claim_details"] = [item for item in payload.get("claim_details", []) if item.get("account") != account.get("nickname")]
    payload["claim_details"].extend(details[:50])
    claims = [item for item in payload.get("claims", []) if item.get("account") != account.get("nickname")]
    claims.append({"account": account.get("nickname"), "open": open_count, "mediations": mediation_count, "updated_at": now_label(), "details": details[:20]})
    payload["claims"] = claims
    account["claims_sync_status"] = f"{len(details)} reclamações sincronizadas"
    return details


def sync_pending_shipments_from_orders(payload, account, orders):
    pending = []
    pending_statuses = {"paid", "confirmed", "payment_required", "partially_paid"}
    final_statuses = {"cancelled", "canceled", "invalid"}
    final_shipping_statuses = {"delivered", "cancelled", "canceled", "not_delivered"}
    for order in orders:
        status = str(order.get("status") or "").lower()
        tags = [str(tag).lower() for tag in order.get("tags", []) or []]
        shipping = order.get("shipping") or {}
        shipping_status = str(shipping.get("status") or first_present(shipping, ["status_history.status"], "") or "").lower()
        fulfilled = any(tag in {"delivered", "cancelled", "canceled"} for tag in tags) or shipping_status in final_shipping_statuses
        if status in final_statuses or status not in pending_statuses or fulfilled:
            continue
        deadline = (
            first_present(shipping, ["estimated_handling_limit.date", "estimated_handling_limit", "shipping_option.estimated_handling_limit"])
            or first_present(shipping, ["estimated_delivery_time.date", "shipping_option.estimated_delivery_time.date"])
            or shipping.get("date_first_printed")
            or order.get("expiration_date")
            or order.get("date_closed")
            or "Aguardando prazo oficial"
        )
        pending.append(
            {
                "account": account.get("nickname"),
                "order_id": order.get("id") or "-",
                "buyer": first_present(order, ["buyer.nickname", "buyer.first_name"], "Comprador Mercado Livre"),
                "deadline": deadline,
                "time_left": "-",
            }
        )
    payload["pending_shipments"] = [item for item in payload.get("pending_shipments", []) if item.get("account") != account.get("nickname")]
    payload["pending_shipments"].extend(pending[:30])
    return pending


COMPETITION_FIELDS = (
    "competition_status",
    "winner_seller_id",
    "winner_name",
    "winner_price",
    "winner_confirmed",
    "winner_source",
    "catalog_reference_seller_id",
    "catalog_reference_name",
    "catalog_reference_price",
    "price_to_win",
    "current_price",
    "visit_share",
    "competition_reason",
    "competitors_sharing_first_place",
    "public_buybox_error",
)


def competition_snapshot(item):
    snapshot = {field: item.get(field) for field in COMPETITION_FIELDS if field in item}
    if snapshot.get("winner_source") in {
        "catalog_lowest_active_offer",
        "catalog_reference",
        "products_items_winner_marker",
        "public_purchase_options",
    }:
        snapshot.update(
            {
                "winner_seller_id": "",
                "winner_name": "Não exposto pela API oficial",
                "winner_price": None,
                "winner_confirmed": False,
                "winner_source": "",
                "competition_reason": (
                    "A API oficial informa o status da disputa e o preço para ganhar, "
                    "mas este registro antigo não possuía vencedor confirmado."
                ),
            }
        )
    return snapshot


def upsert_monthly_revenue(payload, account, amount, orders_count, period, status):
    monthly = payload.setdefault("monthly_revenue", {"period": period, "accounts": {}})
    if monthly.get("period") != period:
        monthly["period"] = period
        monthly["accounts"] = {}
    monthly.setdefault("accounts", {})[account.get("id") or account.get("nickname")] = {
        "account": account.get("nickname"),
        "amount": round(float(amount or 0), 2),
        "orders_count": int(orders_count or 0),
        "source": "Pedidos oficiais Mercado Livre",
        "sync_status": status,
        "updated_at": now_label(),
    }


def mark_monthly_revenue_error(payload, account, period, status):
    monthly = payload.setdefault("monthly_revenue", {"period": period, "accounts": {}})
    if monthly.get("period") != period:
        monthly["period"] = period
        monthly["accounts"] = {}
    accounts = monthly.setdefault("accounts", {})
    record = accounts.setdefault(
        account.get("id") or account.get("nickname"),
        {
            "account": account.get("nickname"),
            "amount": 0,
            "orders_count": 0,
            "source": "Pedidos oficiais Mercado Livre",
        },
    )
    record["sync_status"] = status
    record["updated_at"] = now_label()


def sync_official_account(payload, account_id, limit=None):
    account = next(
        (
            item
            for item in payload.get("accounts", [])
            if item.get("id") == account_id or str(item.get("seller_id")) == str(account_id) or item.get("nickname") == account_id
        ),
        None,
    )
    if not account:
        raise RuntimeError("Conta não encontrada.")
    if not account.get("official"):
        raise RuntimeError("Apenas contas com OAuth oficial podem ser sincronizadas.")

    client = account_client(account)
    user_profile = client.user(account["seller_id"])
    sync_recent_sales(payload, account, client)
    sync_claims(payload, account, client)
    item_ids = client.seller_all_items(account["seller_id"], max_items=None if limit in (None, "all") else int(limit))
    imported = []
    existing_by_id = {
        item.get("id"): item
        for item in payload.get("catalog", [])
        if item.get("official_source") and item.get("account_id") == account.get("id") and item.get("id")
    }
    competition_inline_limit = max(0, int(os.getenv("MELI_SYNC_COMPETITION_INLINE_LIMIT", "0")))
    batch_size = max(1, min(20, int(os.getenv("MELI_ITEM_BULK_SIZE", "20"))))
    for batch_start in range(0, len(item_ids), batch_size):
        batch_ids = item_ids[batch_start : batch_start + batch_size]
        try:
            batch_items = client.items_bulk(batch_ids)
        except Exception:
            batch_items = []
            for item_id in batch_ids:
                try:
                    batch_items.append(client.item(item_id))
                except Exception:
                    pass
        for batch_offset, item in enumerate(batch_items):
            item_id = item.get("id")
            index = batch_start + batch_offset
            if not item_id:
                continue
            is_catalog = bool(item.get("catalog_listing") or item.get("catalog_product_id"))
            if is_catalog and index < competition_inline_limit:
                competition = normalize_competition(client, account, item)
            else:
                competition = competition_snapshot(existing_by_id.get(item_id, {}))
            row = synced_catalog_item(account, item, competition)
            previous = existing_by_id.get(item_id, {})
            row["first_seen_at"] = previous.get("first_seen_at") or now_label()
            imported.append(row)

    payload["catalog"] = [
        item
        for item in payload.get("catalog", [])
        if not (item.get("official_source") and item.get("account_id") == account.get("id"))
    ]
    payload["catalog"].extend(imported)
    account["last_sync"] = now_label()
    account["sync_status"] = f"{len(imported)} anúncios importados da API oficial"
    account["sync_total_item_ids"] = len(item_ids)
    upsert_metric(payload, account, user_profile)
    # Estoque zerado antigo não gera alerta na sincronização completa; alertas vêm de transição no refresh automático.
    return {"account": public_account(account), "items": len(imported), "catalog": imported}


def refresh_official_account_items(payload, account, batch_size=None):
    batch_size = max(1, int(batch_size or os.getenv("AUTO_REFRESH_BATCH_SIZE", "400")))
    rows = [
        item
        for item in payload.get("catalog", [])
        if item.get("official_source") and item.get("account_id") == account.get("id") and item.get("id")
    ]
    if not rows:
        return {"items": 0, "alerts": 0}
    cursor_key = f"auto_refresh_cursor_{account.get('id')}"
    cursor = int(account.get(cursor_key) or 0)
    selected = rows[cursor : cursor + batch_size]
    if len(selected) < batch_size:
        selected.extend(rows[: max(0, batch_size - len(selected))])
    selected = selected[:batch_size]
    account[cursor_key] = 0 if cursor + batch_size >= len(rows) else cursor + batch_size

    client = account_client(account)
    refreshed = 0
    prior_by_id = {item.get("id"): dict(item) for item in selected}
    official_by_id = {}
    bulk_size = max(1, min(20, int(os.getenv("MELI_ITEM_BULK_SIZE", "20"))))
    selected_ids = [item.get("id") for item in selected if item.get("id")]
    for start in range(0, len(selected_ids), bulk_size):
        batch_ids = selected_ids[start : start + bulk_size]
        try:
            for official in client.items_bulk(batch_ids):
                if official.get("id"):
                    official_by_id[official["id"]] = official
        except Exception:
            for item_id in batch_ids:
                try:
                    official_by_id[item_id] = client.item(item_id)
                except Exception:
                    pass
    competition_limit = max(0, int(os.getenv("AUTO_COMPETITION_BATCH_SIZE", "12")))
    competition_checked = 0
    for current in selected:
        try:
            official = official_by_id.get(current.get("id"))
            if not official:
                raise RuntimeError("Anúncio não retornado pelo lote oficial nesta rodada.")
            is_catalog = bool(official.get("catalog_listing") or official.get("catalog_product_id"))
            if is_catalog and competition_checked < competition_limit:
                competition = normalize_competition(client, account, official)
                competition_checked += 1
            else:
                competition = competition_snapshot(current)
            updated = synced_catalog_item(account, official, competition)
            current.update(updated)
            current["updated_at"] = now_label()
            refreshed += 1
        except Exception as exc:
            current["auto_refresh_error"] = str(exc)
            current["auto_refresh_at"] = now_label()

    alerts_created = 0
    existing_alert_ids = {alert.get("id") for alert in payload.get("alerts", [])}
    for item in selected:
        before = prior_by_id.get(item.get("id")) or {}
        if int(before.get("stock") or 0) > 0 and int(item.get("stock") or 0) == 0:
            alert_id = f"stock-{account.get('id')}-{item.get('id')}-{now_label()[:10]}"
            if alert_id not in existing_alert_ids:
                alert = stock_alert(alert_id, account, item)
                payload.setdefault("alerts", []).insert(0, alert)
                notify_alert(payload, alert)
                alerts_created += 1
                existing_alert_ids.add(alert_id)
    for item in selected:
        before = prior_by_id.get(item.get("id")) or {}
        if before and before.get("status") != item.get("status") and item.get("status") == "losing":
            alert_id = f"catalog-{account.get('id')}-{item.get('id')}-{now_label()[:10]}"
            existing_ids = {alert.get("id") for alert in payload.get("alerts", [])}
            if alert_id not in existing_ids:
                alert = {
                    "id": alert_id,
                    "type": "catalog",
                    "severity": "danger",
                    "title": "Produto começou a perder catálogo",
                    "message": f"{item.get('title')} perdeu a buybox. Vencedor: {item.get('winner_name') or '-'} por {item.get('winner_price') or '-'}.",
                    "account": item.get("account"),
                    "item_id": item.get("id"),
                    "sku": item.get("sku"),
                    "product": item.get("title"),
                    "created_at": now_label(),
                }
                payload.setdefault("alerts", []).insert(0, alert)
                notify_alert(payload, alert)
                alerts_created += 1

    account["auto_refresh_status"] = (
        f"Atualização automática OK: {refreshed}/{len(rows)} anúncios revisados; "
        f"{competition_checked} disputas de catálogo recalculadas"
    )
    account["last_auto_refresh_at"] = now_label()
    return {"items": refreshed, "total": len(rows), "alerts": alerts_created}


def enqueue_official_sync(account_id, limit=None, reason="manual"):
    account_id = str(account_id or "")
    with SYNC_LOCK:
        if account_id in ACTIVE_SYNC_ACCOUNTS:
            return {"queued": False, "status": "running", "message": "Sincronização desta conta já está em andamento."}
        ACTIVE_SYNC_ACCOUNTS.add(account_id)
    payload = read_payload()
    account = next(
        (
            item
            for item in payload.get("accounts", [])
            if item.get("id") == account_id or str(item.get("seller_id")) == account_id or item.get("nickname") == account_id
        ),
        None,
    )
    if not account:
        with SYNC_LOCK:
            ACTIVE_SYNC_ACCOUNTS.discard(account_id)
        raise RuntimeError("Conta não encontrada.")
    account["sync_status"] = "Sincronização em andamento no servidor"
    account["sync_requested_at"] = now_label()
    account["sync_reason"] = reason
    write_payload(payload)

    def worker():
        try:
            payload_inner = read_payload()
            result = sync_official_account(payload_inner, account_id, limit)
            refreshed = next(
                (
                    item
                    for item in payload_inner.get("accounts", [])
                    if item.get("id") == account_id or str(item.get("seller_id")) == account_id
                ),
                None,
            )
            if refreshed:
                refreshed["sync_status"] = f"Sincronização concluída: {result.get('items', 0)} anúncios importados"
                refreshed["sync_finished_at"] = now_label()
                refreshed["sync_error"] = ""
            write_payload(payload_inner)
        except Exception as exc:
            payload_inner = read_payload()
            failed = next(
                (
                    item
                    for item in payload_inner.get("accounts", [])
                    if item.get("id") == account_id or str(item.get("seller_id")) == account_id
                ),
                None,
            )
            if failed:
                failed["sync_status"] = "Sincronização com erro"
                failed["sync_error"] = str(exc)
                failed["sync_finished_at"] = now_label()
            write_payload(payload_inner)
        finally:
            with SYNC_LOCK:
                ACTIVE_SYNC_ACCOUNTS.discard(account_id)

    threading.Thread(target=worker, daemon=True).start()
    return {"queued": True, "status": "queued", "message": "Sincronização iniciada em segundo plano."}


def unlink_official_account(payload, account_id):
    account = next(
        (
            item
            for item in payload.get("accounts", [])
            if item.get("id") == account_id or str(item.get("seller_id")) == str(account_id)
        ),
        None,
    )
    if not account:
        raise RuntimeError("Conta não encontrada.")
    if not account.get("official"):
        raise RuntimeError("Apenas contas oficiais podem ser desvinculadas.")

    payload["accounts"] = [item for item in payload.get("accounts", []) if item.get("id") != account.get("id")]
    payload["catalog"] = [
        item
        for item in payload.get("catalog", [])
        if not (item.get("account_id") == account.get("id") or item.get("account") == account.get("nickname") and item.get("official_source"))
    ]
    payload["alerts"] = [
        item
        for item in payload.get("alerts", [])
        if not (item.get("account") == account.get("nickname") and str(item.get("id", "")).startswith(("stock-", "catalog-")))
    ]
    payload["metrics"] = [item for item in payload.get("metrics", []) if item.get("account") != account.get("nickname")]
    return public_account(account)


def official_account_by_name(payload, name):
    return next(
        (
            account
            for account in payload.get("accounts", [])
            if account.get("nickname") == name or account.get("id") == name or str(account.get("seller_id")) == str(name)
        ),
        None,
    )


def clone_picture_payload(item):
    pictures = item.get("pictures") or []
    rows = []
    for picture in pictures:
        url = picture.get("secure_url") or picture.get("url")
        if url:
            rows.append({"source": url})
    return rows


PRODUCT_IDENTIFIER_ATTRS = {
    "GTIN",
    "EAN",
    "UPC",
    "ISBN",
    "JAN",
    "MPN",
    "PART_NUMBER",
    "UNIVERSAL_PRODUCT_CODE",
}


def clean_attribute_value(text):
    if text in (None, "", [], {}):
        return ""
    value = str(text).strip()
    return "" if value.lower() in {"null", "none", "nan", "undefined"} else value


def clone_attributes_payload(item, source_sku):
    attributes = []
    for attribute in item.get("attributes") or []:
        attr_id = attribute.get("id")
        if not attr_id:
            continue
        attr_id_text = str(attr_id).upper()
        if attr_id_text in PRODUCT_IDENTIFIER_ATTRS:
            continue
        row = {"id": attr_id}
        if attribute.get("value_id"):
            row["value_id"] = attribute.get("value_id")
        elif clean_attribute_value(attribute.get("value_name")):
            row["value_name"] = clean_attribute_value(attribute.get("value_name"))
        elif attribute.get("values"):
            values = []
            for value in attribute.get("values") or []:
                if value.get("id"):
                    values.append({"id": value.get("id")})
                elif clean_attribute_value(value.get("name")):
                    values.append({"name": clean_attribute_value(value.get("name"))})
            if values:
                row["values"] = values
        if len(row) > 1:
            attributes.append(row)
    if source_sku:
        existing = next((attr for attr in attributes if attr.get("id") in {"SELLER_SKU", "SKU"}), None)
        if existing:
            existing.pop("value_id", None)
            existing["value_name"] = source_sku
        else:
            attributes.append({"id": "SELLER_SKU", "value_name": source_sku})
    return attributes


def clone_attribute_row(attribute):
    attr_id = attribute.get("id")
    if not attr_id:
        return {}
    row = {"id": attr_id}
    if attribute.get("value_id") not in (None, ""):
        row["value_id"] = attribute.get("value_id")
    elif clean_attribute_value(attribute.get("value_name")):
        row["value_name"] = clean_attribute_value(attribute.get("value_name"))
    elif isinstance(attribute.get("value_struct"), dict) and attribute.get("value_struct"):
        row["value_struct"] = attribute.get("value_struct")
    elif attribute.get("values"):
        values = []
        for value in attribute.get("values") or []:
            clean = {}
            if value.get("id") not in (None, ""):
                clean["id"] = value.get("id")
            if clean_attribute_value(value.get("name")):
                clean["name"] = clean_attribute_value(value.get("name"))
            if isinstance(value.get("struct"), dict) and value.get("struct"):
                clean["struct"] = value.get("struct")
            if clean:
                values.append(clean)
        if values:
            row["values"] = values
    return row if len(row) > 1 else {}


def clone_source_item(client, item_id):
    method = getattr(client, "item_for_clone", None)
    if callable(method):
        try:
            return method(item_id)
        except Exception:
            pass
    return client.item(item_id)


def clone_source_catalog_product(client, source_item):
    product_id = source_item.get("catalog_product_id")
    if not product_id:
        return {}
    try:
        product = client.product(product_id)
        return product if isinstance(product, dict) else {}
    except Exception:
        return {}


def source_clone_attribute(source_item, catalog_product, attr_id):
    wanted = str(attr_id or "").upper()
    wanted_ids = {wanted}
    if wanted == "GTIN":
        wanted_ids.update({"EAN", "UPC", "JAN", "ISBN", "UNIVERSAL_PRODUCT_CODE"})
    for container in (source_item, catalog_product or {}):
        for attribute in container.get("attributes") or []:
            if str(attribute.get("id") or "").upper() in wanted_ids:
                row = clone_attribute_row(attribute)
                if row:
                    row["id"] = wanted
                    return row

    variations = source_item.get("variations") or []
    variation_rows = []
    for variation in variations:
        row = {}
        for section in (variation.get("attributes") or [], variation.get("attribute_combinations") or []):
            attribute = next((item for item in section if str(item.get("id") or "").upper() in wanted_ids), None)
            if attribute:
                row = clone_attribute_row(attribute)
                if row:
                    row["id"] = wanted
                break
        if not row:
            return {}
        variation_rows.append(row)
    if variation_rows:
        signatures = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in variation_rows}
        if len(signatures) == 1:
            return variation_rows[0]
    return {}


def clone_required_attribute_satisfied(create_payload, attr_id):
    attr_id = str(attr_id or "").upper()
    if clone_payload_has_attribute(create_payload, attr_id):
        return True
    if attr_id == "GTIN" and clone_payload_has_attribute(create_payload, "EMPTY_GTIN_REASON"):
        return True
    return False


def hydrate_required_clone_attributes(create_payload, source_item, category_attributes, catalog_product=None):
    copied = []
    for definition in category_attributes or []:
        attr_id = definition.get("id")
        if not attr_id or not clone_attribute_is_required(definition) or clone_required_attribute_satisfied(create_payload, attr_id):
            continue
        row = source_clone_attribute(source_item, catalog_product or {}, attr_id)
        if not row:
            continue
        create_payload.setdefault("attributes", []).append(row)
        copied.append(str(attr_id).upper())
    return copied


def clone_sale_terms_payload(item):
    terms = []
    for term in item.get("sale_terms") or []:
        term_id = term.get("id")
        if not term_id:
            continue
        row = {"id": term_id}
        if term.get("value_id"):
            row["value_id"] = term.get("value_id")
        elif term.get("value_name"):
            row["value_name"] = term.get("value_name")
        if len(row) > 1:
            terms.append(row)
    return terms


def clone_shipping_payload(item):
    shipping = item.get("shipping") or {}
    allowed_keys = ("local_pick_up", "free_shipping", "store_pick_up")
    payload = {key: shipping.get(key) for key in allowed_keys if key in shipping and shipping.get(key) is not None}
    return payload


def clone_extra_required_fields(item):
    fields = {}
    body_fields = (
        "family_name",
        "catalog_product_id",
        "domain_id",
    )
    for field in body_fields:
        if item.get(field) not in (None, "", [], {}):
            fields[field] = item.get(field)
    attribute_to_body = {
        "FAMILY_NAME": "family_name",
        "MODEL": "family_name",
    }
    for attribute in item.get("attributes") or []:
        attr_id = str(attribute.get("id") or "").upper()
        field = attribute_to_body.get(attr_id)
        if field and not fields.get(field):
            value = attribute.get("value_name")
            if not value and attribute.get("values"):
                value = (attribute.get("values") or [{}])[0].get("name")
            if value:
                fields[field] = value
    if fields.get("family_name"):
        fields["family_name"] = normalize_family_name(fields["family_name"])
    return fields


def build_clone_item_payload(source_item, edits):
    source_sku = source_item.get("seller_custom_field") or item_sku(source_item) or source_item.get("id") or "SEM-SKU"
    new_sku = clean_attribute_value(edits.get("sku") or edits.get("sku_suffix")) or source_sku
    requested_listing_type = edits.get("listing_type_id") or ""
    listing_type_id = requested_listing_type if requested_listing_type in {"gold_special", "gold_pro"} else source_item.get("listing_type_id") or "gold_special"
    source_quantity = item_available_quantity(source_item) or 1
    payload = {
        "title": (edits.get("title") or source_item.get("title") or "").strip()[:60],
        "category_id": source_item.get("category_id"),
        "price": float(edits.get("price") or source_item.get("price") or 0),
        "currency_id": source_item.get("currency_id") or "BRL",
        "available_quantity": max(1, int(float(edits.get("stock") or source_quantity or 1))),
        "buying_mode": source_item.get("buying_mode") or "buy_it_now",
        "listing_type_id": listing_type_id,
        "condition": source_item.get("condition") or "new",
        "pictures": clone_picture_payload(source_item),
        "attributes": clone_attributes_payload(source_item, new_sku),
        "sale_terms": clone_sale_terms_payload(source_item),
    }
    payload.update(clone_extra_required_fields(source_item))
    shipping = clone_shipping_payload(source_item)
    if shipping:
        payload["shipping"] = shipping
    if source_item.get("seller_custom_field"):
        payload["seller_custom_field"] = new_sku
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def apply_target_account_clone_rules(create_payload, source_item, source_account, target_account):
    source_seller_id = str(source_account.get("seller_id") or "")
    target_seller_id = str(target_account.get("seller_id") or "")
    same_seller = bool(source_seller_id and target_seller_id and source_seller_id == target_seller_id)
    if same_seller and source_item.get("official_store_id") not in (None, "", [], {}):
        create_payload["official_store_id"] = source_item.get("official_store_id")
    else:
        create_payload.pop("official_store_id", None)
    return create_payload


def required_fields_from_error(exc):
    text = str(exc)
    match = re.search(r"properties \[([^\]]+)\]", text)
    if not match:
        return []
    return [field.strip().strip("'\"") for field in match.group(1).split(",") if field.strip()]


def meli_error_detail(exc):
    text = str(exc)
    raw_json = ""
    if ": " in text:
        raw_json = text.split(": ", 1)[1]
    else:
        raw_json = text
    try:
        return json.loads(raw_json)
    except Exception:
        return {}


def meli_error_causes(exc):
    detail = meli_error_detail(exc)
    causes = detail.get("cause") if isinstance(detail, dict) else []
    return causes if isinstance(causes, list) else []


def meli_error_text(exc):
    detail = meli_error_detail(exc)
    parts = [str(exc)]
    if isinstance(detail, dict):
        parts.extend(str(detail.get(key) or "") for key in ("message", "error"))
        for cause in detail.get("cause") or []:
            parts.append(str(cause.get("message") or ""))
            parts.append(str(cause.get("code") or ""))
    return " ".join(part for part in parts if part)


def meli_error_status(exc):
    detail = meli_error_detail(exc)
    if isinstance(detail, dict):
        try:
            return int(detail.get("status"))
        except (TypeError, ValueError):
            pass
    match = re.search(r"HTTP\s+(\d{3})", str(exc), flags=re.I)
    return int(match.group(1)) if match else 0


def meli_rate_limited_error(exc):
    text = meli_error_text(exc).lower()
    return meli_error_status(exc) == 429 or "local_rate_limited" in text or "rate limit" in text


def clone_rate_limit_delay(attempt):
    base = max(0.1, float(os.getenv("MELI_CLONE_RATE_LIMIT_BASE_SECONDS", "1.5")))
    maximum = max(base, float(os.getenv("MELI_CLONE_RATE_LIMIT_MAX_SECONDS", "8")))
    return min(maximum, base * (2**attempt)) + random.uniform(0.05, 0.25)


def invalid_fields_from_error(exc):
    text = str(exc)
    fields = []
    match = re.search(r"fields \[([^\]]+)\] are invalid", text, flags=re.IGNORECASE)
    if match:
        fields.extend(field.strip().strip("'\"") for field in match.group(1).split(",") if field.strip())
    detail = meli_error_detail(exc)
    if isinstance(detail, dict):
        for cause in detail.get("cause") or []:
            code = str(cause.get("code") or cause.get("cause_id") or "")
            if "invalid_fields" not in code:
                continue
            for reference in cause.get("references") or []:
                field = str(reference or "").replace("body.", "").strip()
                if field and field != "body":
                    fields.append(field)
        error_text = " ".join(str(detail.get(key) or "") for key in ("message", "error"))
        match = re.search(r"fields \[([^\]]+)\] are invalid", error_text, flags=re.IGNORECASE)
        if match:
            fields.extend(field.strip().strip("'\"") for field in match.group(1).split(",") if field.strip())
    return list(dict.fromkeys(fields))


def attribute_ids_from_error_text(text):
    ids = []
    for pattern in (
        r"Attribute \[([A-Z0-9_]+)\]",
        r"attribute \[([A-Z0-9_]+)\]",
        r"attribute ([A-Z0-9_]+)",
        r"atributo \[([A-Z0-9_]+)\]",
    ):
        ids.extend(match.group(1).upper() for match in re.finditer(pattern, text or "", flags=re.I))
    return list(dict.fromkeys(ids))


def human_attribute_names_from_error_text(text):
    names = []
    for pattern in (
        r'em "([^"]+)"',
        r"em '([^']+)'",
        r'Attribute ([A-Z0-9_]+) with value null was omitted',
    ):
        for match in re.finditer(pattern, text or "", flags=re.I):
            value = match.group(1).strip()
            if value:
                names.append(value)
    return list(dict.fromkeys(names))


ATTRIBUTE_NAME_TO_ID = {
    "quantidade de pares": "UNITS_PER_PACKAGE",
    "comprimento do cabo": "CABLE_LENGTH",
}


def attribute_id_from_human_name(name):
    normalized = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower().strip()
    return ATTRIBUTE_NAME_TO_ID.get(normalized) or re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")


def remove_clone_attributes(create_payload, attr_ids):
    wanted = {str(attr_id).upper() for attr_id in attr_ids if attr_id}
    if not wanted:
        return []
    removed = []
    kept = []
    for attribute in create_payload.get("attributes") or []:
        attr_id = str(attribute.get("id") or "").upper()
        if attr_id in wanted:
            removed.append(attr_id)
        else:
            kept.append(attribute)
    create_payload["attributes"] = kept
    return list(dict.fromkeys(removed))


def add_or_update_clone_attribute(create_payload, attr_id, value):
    value = clean_attribute_value(value)
    if not attr_id or not value:
        return False
    attributes = create_payload.setdefault("attributes", [])
    existing = next((attr for attr in attributes if str(attr.get("id") or "").upper() == str(attr_id).upper()), None)
    if existing:
        existing.pop("value_id", None)
        existing.pop("values", None)
        existing.pop("value_struct", None)
        existing["value_name"] = value
    else:
        attributes.append({"id": attr_id, "value_name": value})
    return True


ATTRIBUTE_LABELS_PT = {
    "ALPHANUMERIC_MODEL": "Modelo alfanumérico",
    "DETAILED_MODEL": "Modelo detalhado",
    "MODEL": "Modelo",
    "LINE": "Linha",
    "BRAND": "Marca",
    "FAMILY_NAME": "Nome da família do produto",
    "UNITS_PER_PACKAGE": "Quantidade de unidades por embalagem",
    "CABLE_LENGTH": "Comprimento do cabo",
    "SELLER_PACKAGE_WEIGHT": "Peso da embalagem",
    "SELLER_PACKAGE_HEIGHT": "Altura da embalagem",
    "SELLER_PACKAGE_WIDTH": "Largura da embalagem",
    "SELLER_PACKAGE_LENGTH": "Comprimento da embalagem",
    "OUTPUT_CONNECTORS": "Conectores de saída",
    "GTIN": "Código universal do produto (GTIN/EAN)",
    "EAN": "Código EAN",
    "UPC": "Código UPC",
    "EMPTY_GTIN_REASON": "Motivo para não informar o código universal",
}


def clone_attribute_label(field_id, fallback=""):
    normalized = str(field_id or "").replace("attribute:", "").upper()
    return ATTRIBUTE_LABELS_PT.get(normalized) or fallback or normalized.replace("_", " ").title()


def clone_pending_field(field_id, label, kind="text", message="", item_id="", options=None, **metadata):
    field = {
        "id": field_id,
        "label": clone_attribute_label(field_id, label),
        "kind": kind,
        "message": message,
        "item_id": item_id,
        "options": options or [],
    }
    field.update({key: value for key, value in metadata.items() if value not in (None, "", [], {})})
    return field


def dedupe_pending_fields(fields):
    deduped = {}
    for field in fields:
        field_id = field.get("id")
        if not field_id:
            continue
        if field_id not in deduped:
            deduped[field_id] = field
    return list(deduped.values())


def normalize_family_name(value):
    text = clean_attribute_value(value)
    return text[:60] if len(text) > 60 else text


def source_attribute_value(source_item, ids):
    wanted = {item.upper() for item in ids}
    for attribute in source_item.get("attributes") or []:
        if str(attribute.get("id") or "").upper() not in wanted:
            continue
        value = attribute.get("value_name")
        if not value and attribute.get("values"):
            value = (attribute.get("values") or [{}])[0].get("name")
        if value:
            return value
    return ""


def normalized_attribute_label(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower(),
    ).strip()


def cached_category_attributes(client, category_id):
    category_id = str(category_id or "").strip()
    if not category_id:
        return []
    ttl = max(300, int(os.getenv("CATEGORY_ATTRIBUTES_CACHE_SECONDS", "3600")))
    now = time.monotonic()
    with CATEGORY_ATTRIBUTES_LOCK:
        cached = CATEGORY_ATTRIBUTES_CACHE.get(category_id)
        if cached and now - cached["time"] < ttl:
            return cached["attributes"]
    attributes = client.category_attributes(category_id)
    attributes = attributes if isinstance(attributes, list) else []
    with CATEGORY_ATTRIBUTES_LOCK:
        CATEGORY_ATTRIBUTES_CACHE[category_id] = {"time": now, "attributes": attributes}
    return attributes


def clone_attribute_is_required(attribute):
    tags = attribute.get("tags") or {}
    if not isinstance(tags, dict):
        return False
    for key, value in tags.items():
        normalized = str(key or "").lower()
        if "required" not in normalized or normalized.startswith(("not_", "non_", "optional_")):
            continue
        if value not in (False, None, "", 0, "0", "false", "False"):
            return True
    return False


def clone_attribute_units(attribute):
    units = []
    default_unit = attribute.get("default_unit")
    if isinstance(default_unit, dict):
        default_unit = default_unit.get("name") or default_unit.get("id")
    if clean_attribute_value(default_unit):
        units.append(clean_attribute_value(default_unit))
    for unit in attribute.get("allowed_units") or []:
        if isinstance(unit, dict):
            unit = unit.get("name") or unit.get("id")
        unit = clean_attribute_value(unit)
        if unit and unit not in units:
            units.append(unit)
    return units


def category_attribute_definition(category_attributes, attr_id="", label=""):
    wanted_id = str(attr_id or "").replace("attribute:", "").upper()
    wanted_label = normalized_attribute_label(label)
    for attribute in category_attributes or []:
        current_id = str(attribute.get("id") or "").upper()
        current_label = normalized_attribute_label(attribute.get("name") or "")
        if (wanted_id and current_id == wanted_id) or (wanted_label and current_label == wanted_label):
            return attribute
    return {}


def category_attribute_ids(category_attributes):
    return {
        str(attribute.get("id") or "").upper()
        for attribute in category_attributes or []
        if attribute.get("id")
    }


def sanitize_clone_answers(answers, category_attributes):
    if not category_attributes:
        return dict(answers or {})
    allowed = category_attribute_ids(category_attributes)
    clean = {}
    for key, value in (answers or {}).items():
        if not key.startswith("attribute:"):
            clean[key] = value
            continue
        attr_id = key.split(":", 1)[1].upper()
        if attr_id in allowed or attr_id in {"SELLER_SKU", "SKU"}:
            clean[key] = value
    return clean


def sanitize_clone_payload_attributes(create_payload, category_attributes):
    if not category_attributes:
        return []
    allowed = category_attribute_ids(category_attributes)
    removed = []
    kept = []
    for attribute in create_payload.get("attributes") or []:
        attr_id = str(attribute.get("id") or "").upper()
        if attr_id in allowed or attr_id in {"SELLER_SKU", "SKU"}:
            kept.append(attribute)
        elif attr_id:
            removed.append(attr_id)
    create_payload["attributes"] = kept
    return list(dict.fromkeys(removed))


def source_attribute_id_from_label(source_item, label):
    wanted = normalized_attribute_label(label)
    for attribute in source_item.get("attributes") or []:
        if normalized_attribute_label(attribute.get("name") or "") == wanted:
            return str(attribute.get("id") or "").upper()
    return ""


def pending_clone_attribute(attr_id, source_item, category_attributes, item_id="", fallback_label=""):
    definition = category_attribute_definition(category_attributes, attr_id, fallback_label)
    resolved_id = str(definition.get("id") or attr_id or "").replace("attribute:", "").upper()
    label = clone_attribute_label(resolved_id, definition.get("name") or fallback_label)
    options = []
    for value in definition.get("values") or []:
        name = clean_attribute_value(value.get("name"))
        if name and name not in options:
            options.append(name)
    value_type = str(definition.get("value_type") or "string").lower()
    units = clone_attribute_units(definition)
    kind = "select" if options else "number" if value_type in {"number", "number_unit"} else "text"
    max_length = definition.get("value_max_length")
    message = "Selecione ou informe o valor obrigatório exigido pelo Mercado Livre."
    if units:
        message = f"Informe o valor e use uma das unidades aceitas: {', '.join(units)}."
    elif max_length:
        message = f"Informe o valor obrigatório com no máximo {max_length} caracteres."
    return clone_pending_field(
        f"attribute:{resolved_id}",
        label,
        kind,
        message,
        item_id,
        options,
        units=units,
        max_length=max_length,
        value_type=value_type,
    )


def required_clone_attributes_from_error(exc, source_item, category_attributes):
    text = meli_error_text(exc)
    found = []

    for match in re.finditer(
        r"(?:attributes?|atributos?)\s*\[([^\]]+)\]\s*(?:are|is|são|sao)\s+required",
        text,
        flags=re.I,
    ):
        for raw_id in match.group(1).split(","):
            attr_id = raw_id.strip().strip("'\"").upper()
            if re.fullmatch(r"[A-Z0-9_]+", attr_id):
                found.append((attr_id, ""))

    human_patterns = (
        r'(?:campo|atributo)\s+["\']([^"\']+)["\']\s+(?:é|e)\s+obrigat[oó]rio',
        r'(?:field|attribute)\s+["\']([^"\']+)["\']\s+is\s+required',
    )
    for pattern in human_patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            label = match.group(1).strip()
            definition = category_attribute_definition(category_attributes, label=label)
            attr_id = str(definition.get("id") or "").upper() or source_attribute_id_from_label(source_item, label)
            if not attr_id:
                attr_id = attribute_id_from_human_name(label)
            found.append((attr_id, label))

    for match in re.finditer(
        r"(?:attribute|atributo)\s+([A-Z][A-Z0-9_]{2,})\s+(?:is\s+required|(?:é|e)\s+obrigat[oó]rio)",
        text,
        flags=re.I,
    ):
        found.append((match.group(1).upper(), ""))

    for cause in meli_error_causes(exc):
        code_message = f"{cause.get('code') or ''} {cause.get('message') or ''}".lower()
        if "required" not in code_message and "obrig" not in normalized_attribute_label(code_message):
            continue
        for reference in cause.get("references") or []:
            tokens = re.findall(r"[A-Z][A-Z0-9_]{2,}", str(reference or ""))
            for attr_id in tokens:
                if attr_id not in {"BODY", "ITEM", "ATTRIBUTES"}:
                    found.append((attr_id, ""))

    deduped = []
    seen = set()
    for attr_id, label in found:
        attr_id = str(attr_id or "").upper()
        if not attr_id or attr_id in seen:
            continue
        seen.add(attr_id)
        deduped.append(pending_clone_attribute(attr_id, source_item, category_attributes, fallback_label=label))
    return deduped


def dropped_clone_attributes_from_error(exc):
    text = meli_error_text(exc)
    attr_ids = []
    patterns = (
        r"Attribute:\s*([A-Z0-9_]+)\s+was dropped",
        r"Attribute\s+\[?([A-Z0-9_]+)\]?\s+(?:was dropped|does not exist|does not exists)",
        r"Atributo\s+\[?([A-Z0-9_]+)\]?\s+(?:não existe|nao existe|foi removido)",
    )
    for pattern in patterns:
        attr_ids.extend(match.group(1).upper() for match in re.finditer(pattern, text, flags=re.I))
    return list(dict.fromkeys(attr_ids))


def fill_missing_clone_fields(create_payload, source_item, fields):
    for field in fields:
        if create_payload.get(field):
            continue
        if field == "family_name":
            create_payload[field] = normalize_family_name(
                source_item.get("family_name")
                or source_attribute_value(source_item, ["FAMILY_NAME", "MODEL", "LINE", "BRAND"])
                or (source_item.get("title") or "")[:60]
            )
        elif field in source_item and source_item.get(field) not in (None, "", [], {}):
            create_payload[field] = source_item.get(field)
    return create_payload


def clone_payload_from_answers(create_payload, answers):
    answers = answers or {}
    for key, value in answers.items():
        if not clean_attribute_value(value):
            continue
        if key.startswith("attribute:"):
            add_or_update_clone_attribute(create_payload, key.split(":", 1)[1], value)
        elif key == "family_name":
            create_payload["family_name"] = normalize_family_name(value)
        elif key in {"title", "price", "available_quantity", "listing_type_id", "condition"}:
            create_payload[key] = value
    return create_payload


def clone_retry_adjustments_from_error(exc, create_payload, source_item, pending_fields, item_id="", category_attributes=None):
    changed = False
    adjustments = []
    error_text = meli_error_text(exc)
    lowered_error = error_text.lower()
    if "official_store_id" in lowered_error and (
        "not allowed" in lowered_error or "invalid_official_store_id" in lowered_error or "invalid official" in lowered_error
    ):
        if create_payload.pop("official_store_id", None) is not None:
            changed = True
            adjustments.append({"tipo": "loja_oficial_incompativel_removida", "campos": ["official_store_id"]})
    dropped_attrs = dropped_clone_attributes_from_error(exc)
    removed_dropped_attrs = remove_clone_attributes(create_payload, dropped_attrs)
    if removed_dropped_attrs:
        changed = True
        adjustments.append({"tipo": "atributos_inexistentes_removidos", "campos": removed_dropped_attrs})
    for field in required_clone_attributes_from_error(exc, source_item, category_attributes or []):
        field["item_id"] = item_id
        pending_fields.append(field)
    normalized_error = normalized_attribute_label(error_text)
    if "required" in normalized_error or "obrigatorio" in normalized_error:
        for attribute in category_attributes or []:
            attr_id = attribute.get("id")
            if not attr_id or not clone_attribute_is_required(attribute) or clone_required_attribute_satisfied(create_payload, attr_id):
                continue
            pending_fields.append(
                pending_clone_attribute(
                    attr_id,
                    source_item,
                    category_attributes or [],
                    item_id,
                    attribute.get("name") or "",
                )
            )
    missing_fields = required_fields_from_error(exc)
    if missing_fields:
        before = json.dumps(create_payload, sort_keys=True, ensure_ascii=False)
        create_payload = fill_missing_clone_fields(create_payload, source_item, missing_fields)
        after = json.dumps(create_payload, sort_keys=True, ensure_ascii=False)
        if before != after:
            changed = True
            adjustments.append({"tipo": "campos_obrigatorios_preenchidos", "campos": missing_fields})
        for field in missing_fields:
            if not create_payload.get(field):
                pending_fields.append(clone_pending_field(field, field.replace("_", " ").title(), "text", "Campo obrigatório exigido pelo Mercado Livre.", item_id))

    invalid_fields = invalid_fields_from_error(exc)
    removed_fields = []
    for field in invalid_fields:
        if field in create_payload:
            create_payload.pop(field, None)
            removed_fields.append(field)
            changed = True
    if removed_fields:
        adjustments.append({"tipo": "campos_invalidos_removidos", "campos": removed_fields})

    causes = meli_error_causes(exc)
    attrs_to_remove = []
    for cause in causes:
        code = str(cause.get("code") or "")
        message = str(cause.get("message") or "")
        cause_type = str(cause.get("type") or "")
        if code in {"item.attributes.ignored", "invalid.item.attribute.values", "item.attribute.invalid_product_identifier"}:
            attrs_to_remove.extend(attribute_ids_from_error_text(message))
        if code == "item.attribute.invalid_product_identifier":
            attrs_to_remove.extend(PRODUCT_IDENTIFIER_ATTRS)
        if code == "item.attribute.invalid":
            attr_ids = attribute_ids_from_error_text(message)
            attr_ids.extend(attribute_id_from_human_name(name) for name in human_attribute_names_from_error_text(message))
            attrs_to_remove.extend(attr_ids)
            for attr_id in attr_ids:
                pending_fields.append(
                    clone_pending_field(
                        f"attribute:{attr_id}",
                        attr_id.replace("_", " ").title(),
                        "text",
                        "O Mercado Livre exigiu um valor válido para este atributo.",
                        item_id,
                    )
                )
        if "está incorreto" in message or "esta incorreto" in message or "value null was omitted" in message or "Struct or text must be present" in message:
            human_names = human_attribute_names_from_error_text(message)
            attr_ids = [attribute_id_from_human_name(name) for name in human_names]
            attr_ids.extend(attribute_ids_from_error_text(message))
            removed_attrs = remove_clone_attributes(create_payload, attr_ids)
            if removed_attrs:
                changed = True
                adjustments.append({"tipo": "atributos_com_valor_invalido_removidos", "campos": removed_attrs})
            for name in human_names:
                attr_id = attribute_id_from_human_name(name)
                pending_fields.append(
                    clone_pending_field(
                        f"attribute:{attr_id}",
                        name,
                        "text",
                        "Informe exatamente no formato aceito pelo Mercado Livre. Ex: 1 ou 1.7 m.",
                        item_id,
                    )
                )
        if code == "item.family_name.length_invalid" or "Family Name length is over" in message:
            if create_payload.get("family_name"):
                create_payload["family_name"] = normalize_family_name(create_payload.get("family_name"))
                changed = True
                adjustments.append({"tipo": "family_name_ajustado", "campos": ["family_name"]})
        if "shipping" in code or "mode me1" in message.lower():
            if create_payload.pop("shipping", None) is not None:
                changed = True
                adjustments.append({"tipo": "frete_removido_para_padrao_da_conta", "campos": ["shipping"]})
        if cause_type == "warning" and code == "item.attributes.ignored":
            attrs_to_remove.extend(attribute_ids_from_error_text(message))

    if "Struct or text must be present" in error_text or "value null was omitted" in error_text or "está incorreto" in error_text or "esta incorreto" in error_text:
        human_names = human_attribute_names_from_error_text(error_text)
        attr_ids = [attribute_id_from_human_name(name) for name in human_names]
        attr_ids.extend(attribute_ids_from_error_text(error_text))
        removed_attrs = remove_clone_attributes(create_payload, attr_ids)
        if removed_attrs:
            changed = True
            adjustments.append({"tipo": "atributos_com_valor_invalido_removidos", "campos": removed_attrs})
        for name in human_names:
            attr_id = attribute_id_from_human_name(name)
            pending_fields.append(
                clone_pending_field(
                    f"attribute:{attr_id}",
                    name,
                    "text",
                    "Informe exatamente no formato aceito pelo Mercado Livre. Ex: 1 ou 1.7 m.",
                    item_id,
                )
            )

    if "item.attribute.invalid" in error_text or "Value name of attribute" in error_text:
        for attr_id in attribute_ids_from_error_text(error_text):
            pending_fields.append(
                clone_pending_field(
                    f"attribute:{attr_id}",
                    clone_attribute_label(attr_id),
                    "text",
                    "Informe um valor válido para este campo obrigatório.",
                    item_id,
                )
            )

    removed_attrs = remove_clone_attributes(create_payload, attrs_to_remove)
    if removed_attrs:
        changed = True
        adjustments.append({"tipo": "atributos_removidos", "campos": removed_attrs})

    if create_payload.get("family_name") and len(str(create_payload["family_name"])) > 60:
        create_payload["family_name"] = normalize_family_name(create_payload["family_name"])
        changed = True
        adjustments.append({"tipo": "family_name_ajustado", "campos": ["family_name"]})

    return create_payload, changed, adjustments


def friendly_clone_error(exc):
    if meli_rate_limited_error(exc):
        return "O Mercado Livre limitou temporariamente as criações. A aplicação tentou novamente com espera progressiva; aguarde alguns instantes e repita se necessário."
    error_text = meli_error_text(exc).lower()
    if "official_store_id" in error_text:
        return "O vínculo de loja oficial pertence à conta de origem e não pode ser usado pela conta destino. A aplicação removerá esse vínculo automaticamente na próxima tentativa."
    causes = meli_error_causes(exc)
    if not causes:
        return str(exc)
    messages = []
    for cause in causes:
        code = str(cause.get("code") or "")
        message = str(cause.get("message") or "")
        if code == "item.family_name.length_invalid":
            messages.append("Nome de família do catálogo passou de 60 caracteres; ajuste o campo solicitado e tente novamente.")
        elif code == "item.attribute.invalid_product_identifier":
            messages.append("Código universal do produto não pode ser reutilizado nessa categoria. O clone remove esse código; se a categoria exigir, informe um novo.")
        elif code == "invalid.item.attribute.values":
            attrs = ", ".join(attribute_ids_from_error_text(message)) or "atributo"
            messages.append(f"Atributo com valor inválido: {attrs}. Informe um valor válido ou deixe o clone remover quando não for obrigatório.")
        elif code == "item.attribute.invalid":
            attrs = ", ".join(attribute_ids_from_error_text(message)) or "atributo"
            messages.append(f"Atributo obrigatório sem valor válido: {attrs}.")
        elif code == "item.attributes.ignored":
            continue
        elif code:
            messages.append(message or code)
    return " ".join(dict.fromkeys(messages)) or str(exc)


def create_item_with_clone_retries(target_client, create_payload, source_item, answers=None, item_id="", category_attributes=None):
    payload = json.loads(json.dumps(create_payload, ensure_ascii=False))
    sanitized_attributes = sanitize_clone_payload_attributes(payload, category_attributes or [])
    payload = clone_payload_from_answers(payload, sanitize_clone_answers(answers or {}, category_attributes or []))
    adjustments = []
    if sanitized_attributes:
        adjustments.append({"tipo": "atributos_fora_da_categoria_removidos", "campos": sanitized_attributes})
    last_error = None
    pending_fields = []
    validation_attempts = 0
    rate_limit_attempts = 0
    max_rate_limit_attempts = max(0, int(os.getenv("MELI_CLONE_RATE_LIMIT_RETRIES", "3")))
    while validation_attempts < 5:
        try:
            created = target_client.create_item(payload)
            if adjustments and isinstance(created, dict):
                created["_clone_adjustments"] = adjustments
            return created
        except Exception as exc:
            last_error = exc
            if meli_rate_limited_error(exc) and rate_limit_attempts < max_rate_limit_attempts:
                delay = clone_rate_limit_delay(rate_limit_attempts)
                adjustments.append({"tipo": "limite_temporario_aguardado", "tentativa": rate_limit_attempts + 1, "espera_segundos": round(delay, 2)})
                rate_limit_attempts += 1
                time.sleep(delay)
                continue
            validation_attempts += 1
            payload, changed, new_adjustments = clone_retry_adjustments_from_error(
                exc,
                payload,
                source_item,
                pending_fields,
                item_id,
                category_attributes or [],
            )
            adjustments.extend(new_adjustments)
            if pending_fields:
                break
            if not changed:
                break
    if pending_fields:
        error = RuntimeError("Revise campos obrigatórios antes de copiar este anúncio.")
        error.pending_fields = dedupe_pending_fields(pending_fields)
        error.original_error = str(last_error)
        raise error
    raise last_error


def create_official_clone(payload, job, source_item_id, edits):
    source_account = official_account_by_name(payload, job.get("source_account_id") or job.get("source"))
    target_account = official_account_by_name(payload, job.get("target_account_id") or job.get("target"))
    if not source_account or not source_account.get("official"):
        raise RuntimeError("Conta origem oficial não encontrada para clonar via Mercado Livre.")
    if not target_account or not target_account.get("official"):
        raise RuntimeError("Conta destino oficial não encontrada para clonar via Mercado Livre.")
    source_client = account_client(source_account)
    target_client = account_client(target_account)
    source_item = clone_source_item(source_client, source_item_id)
    create_payload = build_clone_item_payload(source_item, edits)
    try:
        category_attributes = cached_category_attributes(source_client, source_item.get("category_id"))
    except Exception:
        category_attributes = []
    catalog_product = clone_source_catalog_product(source_client, source_item)
    hydrate_required_clone_attributes(create_payload, source_item, category_attributes, catalog_product)
    create_payload = apply_target_account_clone_rules(create_payload, source_item, source_account, target_account)
    answers = (job.get("field_answers") or {}).get(source_item_id) or {}
    created = create_item_with_clone_retries(
        target_client,
        create_payload,
        source_item,
        answers,
        source_item_id,
        category_attributes,
    )
    verified_item = {}
    if created.get("id"):
        try:
            verified_item = target_client.item(created["id"])
        except Exception as exc:
            created["_verification_warning"] = f"Anúncio criado, mas ainda não apareceu na leitura imediata da API: {exc}"
    description_text = (edits.get("description") or "").strip()
    if not description_text:
        try:
            description = source_client.item_description(source_item_id)
            description_text = description.get("plain_text") or description.get("text") or ""
        except Exception:
            description_text = ""
    if description_text and created.get("id"):
        try:
            target_client.create_item_description(created["id"], description_text)
        except Exception:
            pass
    return created, source_item, verified_item


def clone_payload_has_attribute(create_payload, attr_id):
    attr_id = str(attr_id or "").upper()
    for attribute in create_payload.get("attributes") or []:
        if str(attribute.get("id") or "").upper() != attr_id:
            continue
        if clean_attribute_value(attribute.get("value_name")) or attribute.get("value_id") or attribute.get("values"):
            return True
    return False


def clone_preflight_pending_fields(payload, source_name, item_ids, edits):
    source_account = official_account_by_name(payload, source_name)
    if not source_account or not source_account.get("official"):
        return []
    client = account_client(source_account)
    pending = []
    category_cache = {}
    for item_id in item_ids:
        try:
            source_item = clone_source_item(client, item_id)
            create_payload = build_clone_item_payload(source_item, edits)
            category_id = create_payload.get("category_id")
            if not category_id:
                continue
            if category_id not in category_cache:
                category_cache[category_id] = cached_category_attributes(client, category_id)
            catalog_product = clone_source_catalog_product(client, source_item)
            hydrate_required_clone_attributes(
                create_payload,
                source_item,
                category_cache.get(category_id) or [],
                catalog_product,
            )
            item_fields = []
            for attribute in category_cache.get(category_id) or []:
                attr_id = attribute.get("id")
                if not clone_attribute_is_required(attribute) or not attr_id:
                    continue
                if clone_required_attribute_satisfied(create_payload, attr_id):
                    continue
                item_fields.append(
                    pending_clone_attribute(
                        attr_id,
                        source_item,
                        category_cache.get(category_id) or [],
                        item_id,
                        attribute.get("name") or "",
                    )
                )
            if item_fields:
                pending.append(
                    {
                        "item_id": item_id,
                        "error": "O Mercado Livre exige informações antes de criar este anúncio.",
                        "pending_fields": dedupe_pending_fields(item_fields),
                    }
                )
        except Exception:
            continue
    return pending


class App(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_security_headers()
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https: http:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )

    def validate_same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        origin_host = urlparse(origin).netloc
        request_host = self.headers.get("Host", "")
        if origin_host == request_host:
            return True
        self.send_json({"error": "Requisição bloqueada por origem inválida."}, status=403)
        return False

    def cookie_value(self, name):
        cookies = self.headers.get("Cookie", "")
        for chunk in cookies.split(";"):
            if "=" not in chunk:
                continue
            key, value = chunk.strip().split("=", 1)
            if key == name:
                return value
        return ""

    def current_user(self, payload=None):
        token = self.cookie_value("competidor_session")
        if not token:
            return None
        sessions = read_json("sessions.json", [])
        now = int(time.time())
        session = next((item for item in sessions if item.get("token") == token and item.get("expires_at", 0) > now), None)
        if not session:
            return None
        payload = payload or read_payload()
        users = ensure_users(payload)
        return next((public_user(user) for user in users if user.get("id") == session.get("user_id")), None)

    def create_session(self, user):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with DATA_LOCK:
            sessions = [item for item in read_json("sessions.json", []) if item.get("expires_at", 0) > now]
            sessions.append({"token": token, "user_id": user["id"], "created_at": now, "expires_at": now + SESSION_SECONDS})
            write_json("sessions.json", sessions[-50:])
        return token

    def clear_session(self):
        token = self.cookie_value("competidor_session")
        if token:
            with DATA_LOCK:
                sessions = [item for item in read_json("sessions.json", []) if item.get("token") != token]
                write_json("sessions.json", sessions)

    def require_auth(self, payload=None):
        user = self.current_user(payload)
        if user:
            return user
        self.send_json({"error": "Login necessário"}, status=401)
        return None

    def redirect_uri(self):
        return meli_config()["redirect_uri"]

    def suggested_redirect_uri(self):
        host = self.headers.get("Host", "localhost:8765")
        hostname = host.split(":")[0]
        return f"https://{hostname}:{https_port()}/api/oauth/callback"

    def serve_file(self, path):
        if path == "/":
            path = "/index.html"
        target = (PUBLIC / path.lstrip("/")).resolve()
        if not str(target).startswith(str(PUBLIC.resolve())) or not target.exists():
            if not path.startswith("/api/"):
                target = PUBLIC / "index.html"
            else:
                self.send_error(404)
                return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(target.suffix, "application/octet-stream"))
        self.send_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        payload = read_payload(include_catalog=parsed.path == "/api/catalog")

        if parsed.path == "/api/auth/me":
            user = self.current_user(payload)
            self.send_json({"authenticated": bool(user), "user": user, "setup_required": len(ensure_users(payload)) == 0})
            return

        if parsed.path == "/api/auth/logout":
            self.clear_session()
            self.send_response(302)
            self.send_header("Set-Cookie", "competidor_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            self.send_header("Location", "/#/dashboard")
            self.end_headers()
            return

        if parsed.path.startswith("/api/") and parsed.path not in {"/api/oauth/callback"}:
            if not self.require_auth(payload):
                return

        if parsed.path == "/api/meta":
            tenant = payload.get("tenant") or default_tenant()
            actor = self.current_user(payload)
            master_view = is_master(actor)
            self.send_json(
                {
                    "name": "CompeTIDOR",
                    "tenant": {
                        **tenant,
                        "official_accounts": len([account for account in payload.get("accounts", []) if account.get("official")]),
                        "connected_accounts": len([account for account in payload.get("accounts", []) if account.get("status") == "connected"]),
                    },
                    "meli": {
                        "client_configured": app_configured(),
                        "auth_url": MELI_AUTH_URL if master_view else "",
                        "token_url": MELI_TOKEN_URL if master_view else "",
                        "api_url": MELI_API_URL if master_view else "",
                        "redirect_uri": self.redirect_uri() if master_view else "",
                        "suggested_redirect_uri": self.suggested_redirect_uri() if master_view else "",
                        "oauth_issues": oauth_issues() if master_view else [],
                    },
                    "generated_at": int(time.time()),
                }
            )
            return
        if parsed.path == "/api/dashboard":
            notifications_migrated = migrate_legacy_notifications(payload)
            alerts_enriched = enrich_product_alerts(payload)
            if notifications_migrated or alerts_enriched:
                write_payload(payload)
            self.send_json(public_payload(payload, self.current_user(payload), include_catalog=False))
            return
        if parsed.path == "/api/catalog":
            catalog = payload.get("catalog", [])
            self.send_json(
                {
                    "catalog": catalog,
                    "item_logs": payload.get("item_logs", [])[:500],
                    "catalog_counts": {
                        "total": len(catalog),
                        "winning": len([item for item in catalog if item.get("status") == "winning"]),
                        "losing": len([item for item in catalog if item.get("status") == "losing"]),
                        "sharing": len([item for item in catalog if item.get("status") == "sharing"]),
                        "paused": len([item for item in catalog if item.get("status") == "paused" or item.get("meli_status") == "paused"]),
                    },
                }
            )
            return
        if parsed.path == "/api/accounts":
            self.send_json([public_account(account) for account in payload["accounts"]])
            return
        if parsed.path == "/api/meli/config":
            user = self.current_user(payload)
            if not is_master(user):
                self.send_json({"error": "Apenas usuário master pode ver ou editar Client ID, Client Secret e Redirect URI."}, status=403)
                return
            config = meli_config()
            self.send_json(
                {
                    "client_id": config["client_id"],
                    "client_secret_set": bool(config["client_secret"]),
                    "redirect_uri": config["redirect_uri"],
                    "suggested_redirect_uri": self.suggested_redirect_uri(),
                    "issues": oauth_issues(),
                }
            )
            return
        if parsed.path == "/api/items":
            query = parse_qs(parsed.query)
            account_name = query.get("account", [""])[0]
            items = payload["catalog"]
            if account_name:
                items = [item for item in items if item["account"] == account_name]
            self.send_json(items)
            return
        if parsed.path == "/api/oauth/login":
            query = parse_qs(parsed.query)
            switch_account = query.get("switch_account", ["0"])[0] == "1"
            issues = oauth_issues()
            if issues:
                self.send_json(
                    {
                        "error": "Corrija a configuração OAuth antes de conectar.",
                        "issues": issues,
                        "hint": "O MELI_REDIRECT_URI precisa ser exatamente igual ao Redirect URI cadastrado na aplicação do Mercado Livre.",
                    },
                    status=400,
                )
                return
            state = str(uuid.uuid4())
            states = read_json("oauth_states.json", [])
            states.append({"state": state, "created_at": int(time.time())})
            write_json("oauth_states.json", states[-20:])
            self.send_json(
                {
                    "authorization_url": MercadoLivreClient().auth_url(state, self.redirect_uri(), switch_account),
                    "state": state,
                    "switch_account": switch_account,
                }
            )
            return
        if parsed.path == "/api/oauth/start":
            query = parse_qs(parsed.query)
            switch_account = query.get("switch_account", ["0"])[0] == "1"
            issues = oauth_issues()
            if issues:
                self.send_response(302)
                self.send_header("Location", "/#/contas?oauth=config")
                self.end_headers()
                return
            state = str(uuid.uuid4())
            states = read_json("oauth_states.json", [])
            states.append({"state": state, "created_at": int(time.time())})
            write_json("oauth_states.json", states[-20:])
            self.send_response(302)
            self.send_header("Location", MercadoLivreClient().auth_url(state, self.redirect_uri(), switch_account))
            self.end_headers()
            return
        if parsed.path == "/api/oauth/callback":
            query = parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            state = query.get("state", [""])[0]
            states = read_json("oauth_states.json", [])
            valid_state = any(item.get("state") == state for item in states)
            if not code or not valid_state:
                self.send_response(302)
                self.send_header("Location", "/#/contas?oauth=erro")
                self.end_headers()
                return
            try:
                token = MercadoLivreClient().exchange_code(code, self.redirect_uri())
                client = MercadoLivreClient(token.get("access_token"))
                me = client.me()
                existing_account = next(
                    (item for item in payload.get("accounts", []) if str(item.get("seller_id")) == str(me.get("id"))),
                    None,
                )
                account = {
                    "id": f"meli-{me.get('id')}",
                    "tenant_id": (payload.get("tenant") or default_tenant())["id"],
                    "nickname": me.get("nickname") or me.get("first_name") or "Conta Mercado Livre",
                    "seller_id": str(me.get("id")),
                    "site_id": me.get("site_id") or "MLB",
                    "status": "connected",
                    "official": True,
                    "color": "#2563eb",
                    "last_sync": now_label(),
                    "access_token": token.get("access_token"),
                    "refresh_token": token.get("refresh_token"),
                    "expires_in": token.get("expires_in"),
                    "token_created_at": int(time.time()),
                    "permalink": me.get("permalink", ""),
                }
                add_or_update_account(payload, account)
                oauth_status = "updated" if existing_account else "connected"
            except Exception as exc:
                oauth_status = "error"
                add_or_update_account(
                    payload,
                    {
                        "id": f"meli-{state[:8]}",
                        "nickname": "Conta Mercado Livre com erro OAuth",
                        "seller_id": "Não sincronizado",
                        "site_id": "MLB",
                        "status": "oauth_error",
                        "official": False,
                        "color": "#dc2626",
                        "last_sync": now_label(),
                        "error": str(exc),
                    },
                )
            write_payload(payload)
            self.send_response(302)
            self.send_header("Location", f"/#/contas?oauth={oauth_status}")
            self.end_headers()
            return
        if parsed.path == "/api/notifications/meli":
            self.send_json({"ok": True})
            return
        if not parsed.path.startswith("/api/") and "." not in Path(parsed.path).name:
            self.serve_file("/")
            return
        self.serve_file(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/notifications/meli":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                event = json.loads(body or "{}")
                configured_app_id = str(meli_config().get("client_id") or "")
                event_app_id = str(event.get("application_id") or "")
                if configured_app_id and event_app_id and configured_app_id != event_app_id:
                    self.send_json({"ok": False, "error": "Aplicação de origem inválida."}, status=403)
                    return
                MELI_NOTIFICATION_QUEUE.put_nowait(event)
                self.send_json({"ok": True}, status=200)
            except queue.Full:
                self.send_json({"ok": False, "error": "Fila temporariamente cheia."}, status=503)
            except Exception:
                self.send_json({"ok": True}, status=200)
            return
        if not self.validate_same_origin():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        request = json.loads(body or "{}")
        lightweight_paths = {
            "/api/auth/setup-master",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/users",
            "/api/users/update",
            "/api/meli/config",
            "/api/notifications/config",
            "/api/notifications/test",
        }
        payload = read_payload(include_catalog=parsed.path not in lightweight_paths)

        if parsed.path == "/api/auth/setup-master":
            users = ensure_users(payload)
            if users:
                self.send_json({"error": "O usuário master inicial já foi criado."}, status=409)
                return
            name = (request.get("name") or "").strip()
            email = (request.get("email") or "").strip().lower()
            password = request.get("password") or ""
            if not name or not email or not password:
                self.send_json({"error": "Informe nome, e-mail e senha para criar o master."}, status=400)
                return
            if len(password) < 8:
                self.send_json({"error": "A senha do master precisa ter pelo menos 8 caracteres."}, status=400)
                return
            salt, password_hash = hash_password(password)
            user = {
                "id": f"user-{uuid.uuid4().hex[:8]}",
                "tenant_id": (payload.get("tenant") or default_tenant())["id"],
                "name": name,
                "email": email,
                "role": "master",
                "status": "ativo",
                "password_salt": salt,
                "password_hash": password_hash,
                "created_at": now_label(),
            }
            payload["users"] = [user]
            payload.setdefault("user_notifications", {})[user["id"]] = blank_notifications()
            write_payload(payload)
            token = self.create_session(user)
            self.send_json(
                {"ok": True, "user": public_user(user)},
                status=201,
                headers={"Set-Cookie": f"competidor_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}"},
            )
            return

        if parsed.path == "/api/auth/login":
            users = ensure_users(payload)
            write_payload(payload)
            if not users:
                self.send_json({"error": "Crie o usuário master no primeiro acesso.", "setup_required": True}, status=409)
                return
            email = (request.get("email") or "").strip().lower()
            password = request.get("password") or ""
            user = next((item for item in users if (item.get("email") or "").lower() == email), None)
            if not user or not verify_password(password, user) or user.get("status") not in ("ativo", "active"):
                self.send_json({"error": "E-mail ou senha inválidos."}, status=401)
                return
            token = self.create_session(user)
            self.send_json(
                {"ok": True, "user": public_user(user)},
                headers={"Set-Cookie": f"competidor_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}"},
            )
            return

        if parsed.path == "/api/auth/logout":
            self.clear_session()
            self.send_json(
                {"ok": True},
                headers={"Set-Cookie": "competidor_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"},
            )
            return

        if parsed.path.startswith("/api/") and not parsed.path.startswith("/api/auth/"):
            if not self.require_auth(payload):
                return

        if parsed.path == "/api/alerts/read":
            alert_id = request.get("id")
            for alert in payload["alerts"]:
                if alert["id"] == alert_id:
                    alert["read"] = True
            write_payload(payload)
            self.send_json({"ok": True, "alerts": payload["alerts"]})
            return

        if parsed.path == "/api/users":
            actor = self.current_user(payload)
            if not can_manage_users(actor):
                self.send_json({"error": "Seu usuário não tem permissão para criar usuários."}, status=403)
                return
            name = (request.get("name") or "").strip()
            email = (request.get("email") or "").strip().lower()
            role = (request.get("role") or "operator").strip()
            password = request.get("password") or ""
            if not name or not email or not password:
                self.send_json({"error": "Informe nome, e-mail e senha para criar o usuário."}, status=400)
                return
            if len(password) < 6:
                self.send_json({"error": "A senha precisa ter pelo menos 6 caracteres."}, status=400)
                return
            if role == "master" and not is_master(actor):
                self.send_json({"error": "Apenas o usuário master pode criar outro master."}, status=403)
                return
            users = payload.get("users") or default_users(payload.get("tenant") or default_tenant())
            if any((user.get("email") or "").lower() == email for user in users):
                self.send_json({"error": "Já existe um usuário com este e-mail."}, status=400)
                return
            salt, password_hash = hash_password(password)
            user = {
                "id": f"user-{uuid.uuid4().hex[:8]}",
                "tenant_id": (payload.get("tenant") or default_tenant())["id"],
                "name": name,
                "email": email,
                "role": role if role in {"master", "admin", "manager", "operator", "viewer"} else "operator",
                "status": "ativo",
                "password_salt": salt,
                "password_hash": password_hash,
                "created_at": now_label(),
            }
            users.append(user)
            payload["users"] = users
            write_payload(payload)
            self.send_json({"ok": True, "user": public_user(user), "users": visible_users_for(actor, users)}, status=201)
            return

        if parsed.path == "/api/users/update":
            actor = self.current_user(payload)
            if not can_manage_users(actor):
                self.send_json({"error": "Seu usuário não tem permissão para editar usuários."}, status=403)
                return
            user_id = request.get("id")
            users = ensure_users(payload)
            user = next((item for item in users if item.get("id") == user_id), None)
            if not user:
                self.send_json({"error": "Usuário não encontrado."}, status=404)
                return
            if user.get("role") == "master" and not is_master(actor):
                self.send_json({"error": "Administradores não podem ver nem editar usuários master."}, status=403)
                return
            name = (request.get("name") or "").strip()
            email = (request.get("email") or "").strip().lower()
            role = (request.get("role") or user.get("role") or "operator").strip()
            status = (request.get("status") or user.get("status") or "ativo").strip()
            password = request.get("password") or ""
            if not name or not email:
                self.send_json({"error": "Informe nome e e-mail para editar o usuário."}, status=400)
                return
            if any(item.get("id") != user_id and (item.get("email") or "").lower() == email for item in users):
                self.send_json({"error": "Já existe outro usuário com este e-mail."}, status=400)
                return
            if password and len(password) < 6:
                self.send_json({"error": "A nova senha precisa ter pelo menos 6 caracteres."}, status=400)
                return
            if role == "master" and not is_master(actor):
                self.send_json({"error": "Apenas o usuário master pode conceder perfil master."}, status=403)
                return
            current_masters = [item for item in users if item.get("role") == "master" and item.get("status") in {"ativo", "active"}]
            if user.get("role") == "master" and (role != "master" or status in {"inativo", "inactive"}) and len(current_masters) <= 1:
                self.send_json({"error": "Mantenha pelo menos um usuário master ativo."}, status=400)
                return
            user["name"] = name
            user["email"] = email
            user["role"] = role if role in {"master", "admin", "manager", "operator", "viewer"} else "operator"
            user["status"] = status if status in {"ativo", "inativo", "active", "inactive"} else "ativo"
            if password:
                salt, password_hash = hash_password(password)
                user["password_salt"] = salt
                user["password_hash"] = password_hash
            user["updated_at"] = now_label()
            payload["users"] = users
            write_payload(payload)
            self.send_json({"ok": True, "user": public_user(user), "users": visible_users_for(actor, users)})
            return

        if parsed.path == "/api/meli/config":
            actor = self.current_user(payload)
            if not is_master(actor):
                self.send_json({"error": "Apenas usuário master pode alterar Client ID, Client Secret e Redirect URI."}, status=403)
                return
            settings = app_settings()
            current = settings.get("meli", {})
            client_secret = request.get("client_secret", "")
            settings["meli"] = {
                "client_id": request.get("client_id", "").strip(),
                "client_secret": client_secret.strip() if client_secret and client_secret != "********" else current.get("client_secret", ""),
                "redirect_uri": request.get("redirect_uri", "").strip(),
            }
            write_json("settings.json", settings)
            self.send_json(
                {
                    "ok": True,
                    "config": {
                        "client_id": settings["meli"]["client_id"],
                        "client_secret_set": bool(settings["meli"]["client_secret"]),
                        "redirect_uri": settings["meli"]["redirect_uri"],
                        "issues": oauth_issues(),
                    },
                }
            )
            return

        if parsed.path == "/api/notifications/config":
            actor = self.current_user(payload)
            notifications = user_notifications(payload, actor, create=True)
            if "telegram" in request:
                telegram_request = dict(request["telegram"])
                telegram_request.pop("status", None)
                if not telegram_request.get("bot_token") or telegram_request.get("bot_token") == "********":
                    telegram_request.pop("bot_token", None)
                notifications["telegram"] = {**notifications.get("telegram", {}), **telegram_request}
                telegram = notifications["telegram"]
                if telegram.get("enabled") and telegram.get("bot_token") and telegram.get("chat_id"):
                    telegram["status"] = "Telegram pronto para envio via Bot API"
                elif telegram.get("enabled"):
                    telegram["status"] = "Telegram ativado, mas falta token ou Chat ID"
                else:
                    telegram["status"] = "Telegram desativado"
            payload.setdefault("user_notifications", {})[actor["id"]] = notifications
            write_payload(payload)
            safe = json.loads(json.dumps(notifications))
            if safe.get("telegram", {}).get("bot_token"):
                safe["telegram"]["bot_token"] = "********"
            self.send_json({"ok": True, "notifications": safe})
            return

        if parsed.path == "/api/notifications/test":
            text = request.get("message") or "Teste de alerta do CompeTIDOR"
            channels = ["telegram"]
            results = {}
            actor = self.current_user(payload)
            notifier = Notifier(user_notifications(payload, actor, create=False))
            for channel in channels:
                try:
                    if channel == "telegram":
                        results[channel] = notifier.send_telegram(text)
                except Exception as exc:
                    results[channel] = {"ok": False, "error": str(exc)}
            self.send_json({"ok": True, "results": results})
            return

        if parsed.path == "/api/notifications/telegram/updates":
            try:
                actor = self.current_user(payload)
                notifier = Notifier(user_notifications(payload, actor, create=False))
                updates = notifier.telegram_updates()
                chats = []
                seen = set()
                for update in updates.get("result", []) or []:
                    message = update.get("message") or update.get("channel_post") or update.get("my_chat_member", {})
                    chat = message.get("chat") if isinstance(message, dict) else None
                    if not chat:
                        continue
                    chat_id = str(chat.get("id"))
                    if not chat_id or chat_id in seen:
                        continue
                    seen.add(chat_id)
                    chats.append(
                        {
                            "id": chat_id,
                            "type": chat.get("type", ""),
                            "title": chat.get("title") or " ".join([chat.get("first_name", ""), chat.get("last_name", "")]).strip() or chat.get("username", ""),
                            "username": chat.get("username", ""),
                        }
                    )
                self.send_json({"ok": True, "chats": chats, "raw_count": len(updates.get("result", []) or [])})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/scan/items":
            name = (request.get("name") or "").strip()
            url = (request.get("url") or "").strip()
            minimum_price = request.get("minimum_price") or 0
            item_id = extract_meli_item_id(url)
            if not name or not url or not item_id:
                self.send_json({"error": "Informe nome do produto e link ou código MLB válido para criar o scan."}, status=400)
                return
            scan = {
                "id": f"scan-{uuid.uuid4().hex[:8]}",
                "name": name,
                "url": url,
                "item_id": item_id,
                "minimum_price": float(minimum_price or 0),
                "history": [],
                "created_at": now_label(),
                "status": "active",
            }
            payload.setdefault("scan_items", []).insert(0, scan)
            write_payload(payload)
            self.send_json({"ok": True, "scan": scan, "scan_items": payload["scan_items"]}, status=201)
            return

        if parsed.path == "/api/scan/run":
            try:
                scan, entry = scan_meli_item(payload, request.get("id", ""))
                write_payload(payload)
                self.send_json({"ok": True, "scan": scan, "entry": entry, "scan_items": payload.get("scan_items", [])})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/scan/update":
            scan_id = request.get("id", "")
            scans = payload.setdefault("scan_items", [])
            scan = next((item for item in scans if item.get("id") == scan_id), None)
            if not scan:
                self.send_json({"error": "Produto de scan não encontrado."}, status=404)
                return
            try:
                minimum_price = float(request.get("minimum_price") or 0)
            except (TypeError, ValueError):
                self.send_json({"error": "Informe um preço mínimo válido."}, status=400)
                return
            scan["minimum_price"] = max(0, minimum_price)
            scan["last_alert_signature"] = ""
            scan["updated_at"] = now_label()
            write_payload(payload)
            self.send_json({"ok": True, "scan": scan, "scan_items": scans})
            return

        if parsed.path == "/api/competitors/scan":
            try:
                competitor = scan_competitor_profile(payload, request.get("seller_id", ""), request.get("limit", 50))
                write_payload(payload)
                self.send_json({"ok": True, "competitor": competitor, "competitors": payload.get("competitors", [])})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/sync":
            try:
                raw_limit = request.get("limit", "all")
                limit = None if raw_limit in ("all", "", None) else max(1, int(raw_limit))
                if limit is None or limit > int(os.getenv("MELI_SYNC_INLINE_LIMIT", "200")):
                    result = enqueue_official_sync(request.get("account_id", ""), limit, "manual")
                    self.send_json({"ok": True, **result}, status=202)
                else:
                    result = sync_official_account(payload, request.get("account_id", ""), limit)
                    write_payload(payload)
                    self.send_json({"ok": True, **result})
            except Exception as exc:
                message = str(exc)
                if "PA_UNAUTHORIZED_RESULT_FROM_POLICIES" in message or "HTTP 403" in message:
                    message = (
                        "A conta OAuth está conectada, mas a aplicação ainda não tem permissão/política "
                        "para ler anúncios. Habilite as permissões de anúncios/vendas no painel do Mercado Livre "
                        "e refaça o login desta conta."
                    )
                self.send_json({"error": message}, status=400)
            return

        if parsed.path == "/api/meli/unlink":
            try:
                account = unlink_official_account(payload, request.get("account_id", ""))
                write_payload(payload, replace_collections={"accounts"})
                self.send_json({"ok": True, "account": account})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/item/update":
            try:
                user = self.current_user(payload)
                item_id = request.get("item_id", "")
                account_id = request.get("account_id", "")
                account = next((item for item in payload.get("accounts", []) if item.get("id") == account_id or item.get("nickname") == request.get("account")), None)
                if not account or not account.get("official"):
                    raise RuntimeError("Conta oficial não encontrada para atualizar o anúncio.")
                update = {}
                for key in ["price", "available_quantity", "title"]:
                    if key in request and request[key] not in ("", None):
                        update[key] = request[key]
                if request.get("status_action") == "pause":
                    update["status"] = "paused"
                if request.get("status_action") == "activate":
                    update["status"] = "active"
                dimension_attrs = []
                expected_package_values = {}
                dimension_map = {
                    "package_weight": "SELLER_PACKAGE_WEIGHT",
                    "package_height": "SELLER_PACKAGE_HEIGHT",
                    "package_width": "SELLER_PACKAGE_WIDTH",
                    "package_length": "SELLER_PACKAGE_LENGTH",
                }
                for request_key, attr_id in dimension_map.items():
                    if clean_attribute_value(request.get(request_key)):
                        api_value = seller_package_api_value(request_key, request.get(request_key))
                        expected_package_values[request_key] = api_value
                        dimension_attrs.append({"id": attr_id, "value_name": api_value})
                if dimension_attrs:
                    update["attributes"] = dimension_attrs
                if not update:
                    raise RuntimeError("Nenhum campo informado para atualizar.")
                client = account_client(account)
                official = client.update_item(item_id, update)
                verified_item = verify_package_update(client, item_id, expected_package_values) if expected_package_values else {}
                verified_package_values = package_values_from_item(verified_item) if verified_item else {}
                stock_transition = None
                for item in payload.get("catalog", []):
                    if item.get("id") == item_id:
                        changes = {}
                        if "price" in update:
                            changes["price"] = {"from": item.get("price"), "to": update["price"]}
                            item["price"] = update["price"]
                        if "available_quantity" in update:
                            changes["stock"] = {"from": item.get("stock"), "to": update["available_quantity"]}
                            stock_transition = (int(item.get("stock") or 0), int(update["available_quantity"] or 0), item)
                            item["stock"] = update["available_quantity"]
                        if "title" in update:
                            changes["title"] = {"from": item.get("title"), "to": update["title"]}
                            item["title"] = update["title"]
                        for request_key in dimension_map:
                            if clean_attribute_value(request.get(request_key)):
                                normalized_value = verified_package_values.get(request_key) or normalize_package_value(
                                    expected_package_values[request_key], [dimension_map[request_key]]
                                )
                                changes[request_key] = {"from": item.get(request_key), "to": normalized_value}
                                item[request_key] = normalized_value
                        if update.get("status") == "paused":
                            changes["status"] = {"from": item.get("meli_status"), "to": "paused"}
                            item["status"] = "paused"
                            item["meli_status"] = "paused"
                        if update.get("status") == "active":
                            changes["status"] = {"from": item.get("meli_status"), "to": "active"}
                            item["status"] = "sharing" if item.get("catalog_product_id") not in ("", "-") else "winning"
                            item["meli_status"] = "active"
                        item["updated_at"] = now_label()
                        append_item_log(payload, item, user, "Atualização manual", changes)
                if stock_transition and stock_transition[0] > 0 and stock_transition[1] == 0:
                    old_stock, new_stock, changed_item = stock_transition
                    alert_id = f"stock-{account.get('id')}-{item_id}-{uuid.uuid4().hex[:8]}"
                    alert = stock_alert(alert_id, account, changed_item)
                    payload.setdefault("alerts", []).insert(0, alert)
                    notify_alert(payload, alert)
                write_payload(payload)
                self.send_json({"ok": True, "official": official, "catalog": public_payload(payload, user)["catalog"]})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/item/description":
            try:
                item_id = request.get("item_id", "")
                account_id = request.get("account_id", "")
                account = next((item for item in payload.get("accounts", []) if item.get("id") == account_id or item.get("nickname") == request.get("account")), None)
                if not account or not account.get("official"):
                    raise RuntimeError("Conta oficial não encontrada para ler a descrição.")
                description = account_client(account).item_description(item_id)
                text = description.get("plain_text") or description.get("text") or ""
                self.send_json({"ok": True, "description": text})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/item/win_catalog":
            try:
                user = self.current_user(payload)
                item_id = request.get("item_id", "")
                item = next((row for row in payload.get("catalog", []) if row.get("id") == item_id), None)
                if not item:
                    raise RuntimeError("Anúncio não encontrado.")
                if not item.get("price_to_win"):
                    raise RuntimeError("O Mercado Livre não retornou preço sugerido para ganhar este catálogo.")
                account = next((row for row in payload.get("accounts", []) if row.get("id") == item.get("account_id")), None)
                if not account:
                    raise RuntimeError("Conta oficial não encontrada.")
                official = account_client(account).update_item(item_id, {"price": item["price_to_win"]})
                old_price = item.get("price")
                item["price"] = item["price_to_win"]
                item["updated_at"] = now_label()
                append_item_log(payload, item, user, "Ganhar catálogo", {"price": {"from": old_price, "to": item["price_to_win"]}})
                write_payload(payload)
                self.send_json({"ok": True, "official": official, "item": item})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/item/remove_flex":
            try:
                user = self.current_user(payload)
                item_id = request.get("item_id", "")
                account_id = request.get("account_id", "")
                item = next((row for row in payload.get("catalog", []) if row.get("id") == item_id), None)
                if not item:
                    raise RuntimeError("Anúncio não encontrado.")
                account = next(
                    (row for row in payload.get("accounts", []) if row.get("id") == account_id or row.get("id") == item.get("account_id")),
                    None,
                )
                if not account or not account.get("official"):
                    raise RuntimeError("Conta oficial não encontrada para remover o Mercado Envios Flex.")
                update = {"shipping": {"mode": item.get("shipping_mode") or "me2", "logistic_type": "drop_off"}}
                official = account_client(account).update_item(item_id, update)
                old_logistic_type = item.get("shipping_logistic_type") or ""
                item["shipping_mode"] = update["shipping"]["mode"]
                item["shipping_logistic_type"] = update["shipping"]["logistic_type"]
                item["updated_at"] = now_label()
                append_item_log(
                    payload,
                    item,
                    user,
                    "Remoção Mercado Envios Flex",
                    {"shipping_logistic_type": {"from": old_logistic_type, "to": "drop_off"}},
                )
                write_payload(payload)
                self.send_json({"ok": True, "official": official, "item": item})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/meli/item/activate_flex":
            try:
                user = self.current_user(payload)
                item_id = request.get("item_id", "")
                account_id = request.get("account_id", "")
                item = next((row for row in payload.get("catalog", []) if row.get("id") == item_id), None)
                if not item:
                    raise RuntimeError("Anúncio não encontrado.")
                account = next(
                    (row for row in payload.get("accounts", []) if row.get("id") == account_id or row.get("id") == item.get("account_id")),
                    None,
                )
                if not account or not account.get("official"):
                    raise RuntimeError("Conta oficial não encontrada para ativar o Mercado Envios Flex.")
                update = {"shipping": {"mode": item.get("shipping_mode") or "me2", "logistic_type": "self_service"}}
                official = account_client(account).update_item(item_id, update)
                old_logistic_type = item.get("shipping_logistic_type") or ""
                item["shipping_mode"] = update["shipping"]["mode"]
                item["shipping_logistic_type"] = update["shipping"]["logistic_type"]
                item["updated_at"] = now_label()
                append_item_log(
                    payload,
                    item,
                    user,
                    "Ativação Mercado Envios Flex",
                    {"shipping_logistic_type": {"from": old_logistic_type, "to": "self_service"}},
                )
                write_payload(payload)
                self.send_json({"ok": True, "official": official, "item": item})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/clone/preview":
            try:
                item_ids = request.get("item_ids") or []
                edits = request.get("edits") or {}
                if isinstance(item_ids, str):
                    item_ids = [item.strip() for item in item_ids.split(",") if item.strip()]
                item_ids = [str(item_id).strip() for item_id in item_ids if str(item_id).strip()]
                source = (request.get("source") or "").strip()
                target = (request.get("target") or "").strip()
                if not source or not target:
                    self.send_json({"error": "Informe conta origem e conta destino."}, status=400)
                    return
                source_account = official_account_by_name(payload, source)
                target_account = official_account_by_name(payload, target)
                if not source_account or not source_account.get("official") or not source_account.get("access_token"):
                    self.send_json({"error": "A conta origem não está conectada oficialmente. Reautentique a conta e tente novamente."}, status=400)
                    return
                if not target_account or not target_account.get("official") or not target_account.get("access_token"):
                    self.send_json({"error": "A conta destino não está conectada oficialmente. Reautentique a conta e tente novamente."}, status=400)
                    return
                if not item_ids:
                    self.send_json({"error": "Selecione ao menos um anúncio específico."}, status=400)
                    return
                if len(item_ids) > 1:
                    self.send_json({"error": "A cópia permite apenas um anúncio por vez."}, status=400)
                    return
                catalog = payload.setdefault("catalog", [])
                source_items = [
                    item
                    for item in catalog
                    if (
                        item.get("account_id") == source_account.get("id")
                        or item.get("account") == source_account.get("nickname")
                    ) and item.get("id") in item_ids
                ]
                found_ids = {item.get("id") for item in source_items}
                missing_ids = [item_id for item_id in item_ids if item_id not in found_ids]
                if missing_ids:
                    self.send_json(
                        {
                            "error": "Alguns anúncios selecionados não foram encontrados na conta origem.",
                            "missing": missing_ids,
                        },
                        status=400,
                    )
                    return
                preflight_errors = clone_preflight_pending_fields(payload, source_account.get("id"), item_ids, edits)
                job = {
                    "id": f"clone-{uuid.uuid4().hex[:8]}",
                    "source": source_account.get("nickname"),
                    "target": target_account.get("nickname"),
                    "source_account_id": source_account.get("id"),
                    "target_account_id": target_account.get("id"),
                    "item_ids": item_ids,
                    "items": len(item_ids),
                    "status": "review_required" if preflight_errors else "preview_ready",
                    "edits": edits,
                    "errors": preflight_errors,
                    "note": "Preview criado para anúncios específicos. Campos opcionais em branco mantêm as informações originais; o tipo pode ser mantido, Clássico ou Premium.",
                }
                jobs = payload.get("clone_jobs")
                if not isinstance(jobs, list):
                    jobs = []
                    payload["clone_jobs"] = jobs
                jobs.insert(0, job)
                write_payload(payload)
                self.send_json(job, status=201)
            except Exception as exc:
                self.send_json({"error": f"Não foi possível gerar o preview: {exc}"}, status=400)
            return

        if parsed.path == "/api/clone/execute":
            try:
                job_id = request.get("job_id")
                jobs = payload.get("clone_jobs")
                if not isinstance(jobs, list):
                    jobs = []
                    payload["clone_jobs"] = jobs
                job = next((item for item in jobs if item.get("id") == job_id), None)
                if not job:
                    self.send_json({"error": "Preview não encontrado."}, status=404)
                    return
                incoming_answers = request.get("field_answers") or {}
                if isinstance(incoming_answers, dict) and incoming_answers:
                    saved_answers = job.setdefault("field_answers", {})
                    for item_id, answers in incoming_answers.items():
                        if isinstance(answers, dict):
                            saved_answers.setdefault(item_id, {}).update({key: value for key, value in answers.items() if value not in (None, "")})
                catalog = payload.setdefault("catalog", [])
                existing_ids = {item.get("id") for item in catalog if item.get("id")}
                copied = []
                errors = []
                created_details = []
                edits = job.get("edits") or {}
                for item_id in job.get("item_ids", []):
                    source_item = next((item for item in catalog if item.get("id") == item_id), None)
                    if not source_item:
                        continue
                    try:
                        created, official_source, verified_item = create_official_clone(payload, job, item_id, edits)
                    except Exception as exc:
                        pending_fields = getattr(exc, "pending_fields", [])
                        if pending_fields:
                            errors.append(
                                {
                                    "item_id": item_id,
                                    "error": str(exc),
                                    "pending_fields": pending_fields,
                                    "original_error": getattr(exc, "original_error", ""),
                                }
                            )
                        else:
                            errors.append({"item_id": item_id, "error": friendly_clone_error(exc)})
                        continue
                    new_id = created.get("id") or f"COPY-{uuid.uuid4().hex[:6].upper()}"
                    if new_id in existing_ids:
                        continue
                    existing_ids.add(new_id)
                    target_account = official_account_by_name(payload, job.get("target_account_id") or job.get("target")) or {}
                    official_sku = item_sku(official_source)
                    source_sku = official_sku if official_sku and official_sku != "-" else source_item.get("sku") or source_item.get("id") or "SEM-SKU"
                    official_created_item = {**official_source, **created, **verified_item}
                    new_item = synced_catalog_item(target_account, official_created_item)
                    display_sku = item_sku(official_created_item) or f"{source_sku}{edits.get('sku_suffix') or ''}"
                    new_item.update(
                        {
                            "id": new_id,
                            "account": job.get("target"),
                            "account_id": target_account.get("id"),
                            "sku": display_sku,
                            "price": official_created_item.get("price") or float(edits.get("price") or source_item.get("price") or 0),
                            "stock": item_available_quantity(official_created_item) or int(float(edits.get("stock") or item_available_quantity(official_source) or source_item.get("stock") or 0)),
                            "status": "sharing",
                            "share": 0,
                            "clone_source_item_id": item_id,
                            "description_override": edits.get("description", ""),
                            "permalink": official_created_item.get("permalink") or created.get("permalink") or "",
                            "meli_status": official_created_item.get("status") or created.get("status") or new_item.get("meli_status"),
                            "verification_warning": created.get("_verification_warning", ""),
                            "action": f"Anúncio criado oficialmente no Mercado Livre a partir de {source_item.get('id')}. Novo código: {new_id}.",
                        }
                    )
                    catalog.append(new_item)
                    copied.append(new_item)
                    created_details.append(
                        {
                            "source_item_id": item_id,
                            "item_id": new_id,
                            "title": new_item.get("title"),
                            "sku": new_item.get("sku"),
                            "status": new_item.get("meli_status") or "-",
                            "permalink": new_item.get("permalink") or "",
                            "verification_warning": new_item.get("verification_warning") or "",
                        }
                    )
                has_pending = any(row.get("pending_fields") for row in errors)
                job["status"] = "copied" if copied and not errors else "review_required" if has_pending else "partial_error" if copied else "error"
                job["copied_items"] = [item.get("id") for item in copied]
                job["created_details"] = created_details
                job["errors"] = errors
                created_codes = ", ".join(item.get("item_id") for item in created_details if item.get("item_id"))
                job["note"] = f"{len(copied)} anúncio(s) criados oficialmente no Mercado Livre para {job.get('target')}."
                if created_codes:
                    job["note"] += f" Códigos criados: {created_codes}."
                if errors:
                    job["note"] += f" {len(errors)} anúncio(s) falharam; veja erros no job."
                write_payload(payload)
                self.send_json({"ok": True, "job": job, "copied": copied})
            except Exception as exc:
                self.send_json({"error": f"Não foi possível copiar os anúncios: {exc}"}, status=400)
            return

        self.send_json({"error": "Endpoint não encontrado"}, status=404)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    http_server = ThreadingHTTPServer(("127.0.0.1", port), App)

    cert_file, key_file = ensure_dev_certificate()
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    https_server = ThreadingHTTPServer(("127.0.0.1", https_port()), App)
    https_server.socket = tls_context.wrap_socket(https_server.socket, server_side=True)

    threading.Thread(target=https_server.serve_forever, daemon=True).start()
    threading.Thread(target=meli_notification_loop, daemon=True).start()
    threading.Thread(target=auto_scan_loop, daemon=True).start()
    threading.Thread(target=auto_official_sync_loop, daemon=True).start()
    print(f"CompeTIDOR rodando em http://127.0.0.1:{port}")
    print(f"Callback HTTPS em https://127.0.0.1:{https_port()}/api/oauth/callback")
    http_server.serve_forever()




