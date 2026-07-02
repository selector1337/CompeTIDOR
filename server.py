from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse
import json
import html
import hashlib
import hmac
import os
import re
import secrets
import ssl
import time
import threading
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
CERTS = DATA / "certs"
CERTS.mkdir(exist_ok=True)
APP_DATA_FILE = "app.json"
SYNC_LOCK = threading.Lock()
ACTIVE_SYNC_ACCOUNTS = set()

MELI_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
MELI_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
MELI_API_URL = "https://api.mercadolibre.com"
TELEGRAM_API_URL = "https://api.telegram.org"
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
    re.compile(r"^/items/[^/]+/price_to_win(\?|$)"),
    re.compile(r"^/products/[^/]+(\?|$)"),
    re.compile(r"^/products/[^/]+/items(\?|$)"),
    re.compile(r"^/orders/search(\?|$)"),
)


def read_json(name, fallback):
    path = DATA / name
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(name, payload):
    (DATA / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_payload():
    return read_json(APP_DATA_FILE, empty_payload())


def write_payload(payload):
    write_json(APP_DATA_FILE, payload)


def now_label():
    return time.strftime("%Y-%m-%d %H:%M")


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


def public_payload(payload, actor=None):
    clean = json.loads(json.dumps(payload))
    clean.setdefault("scan_items", [])
    clean.setdefault("recent_sales", [])
    clean["accounts"] = [public_account(account) for account in clean.get("accounts", [])]
    clean["notifications"] = public_notifications(user_notifications(payload, actor, create=False))
    clean["operations"] = build_operations(clean)
    clean["users"] = visible_users_for(actor, ensure_users(clean))
    return clean


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
    now = datetime.now()
    return f"{now.year:04d}-{now.month:02d}"


def current_month_window():
    now = datetime.now()
    start = datetime(now.year, now.month, 1)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1)
    else:
        end = datetime(now.year, now.month + 1, 1)
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
    for index, item in enumerate(catalog):
        row = {
            "id": item.get("id"),
            "title": item.get("title"),
            "account": item.get("account"),
            "sku": item.get("sku"),
            "stock": item.get("stock"),
            "price": item.get("price"),
            "occurred_at": item.get("updated_at") or fallback_time(index + 1),
        }
        if item.get("stock") == 0:
            stock.append(row)
        if item.get("status") == "losing" or item.get("competition_status") in {"competing", "sharing"} and item.get("winner_name") not in ("", None, item.get("account")):
            catalog_attention.append({**row, "winner_name": item.get("winner_name"), "winner_price": item.get("winner_price")})

    stock.sort(key=lambda item: item["occurred_at"], reverse=True)
    catalog_attention.sort(key=lambda item: item["occurred_at"], reverse=True)

    details_by_account = {}
    for detail in payload.get("claim_details", []):
        details_by_account.setdefault(detail.get("account"), []).append(detail)
    claims = payload.get("claims") or [
        {"account": account.get("nickname"), "open": 0, "mediations": 0, "updated_at": now_label(), "details": details_by_account.get(account.get("nickname"), [])}
        for account in accounts
        if account.get("official")
    ]
    for claim in claims:
        claim["details"] = claim.get("details") or details_by_account.get(claim.get("account"), [])
    shipments = payload.get("pending_shipments") or [
        {
            "account": account.get("nickname"),
            "order_id": "-",
            "buyer": "A sincronizar",
            "deadline": "Aguardando pedidos oficiais",
            "time_left": "-",
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


def request_json(url, method="GET", payload=None, headers=None):
    body = None
    request_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


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

    def product(self, catalog_product_id):
        return self.get(f"/products/{catalog_product_id}")

    def price_to_win(self, item_id):
        return self.get(f"/items/{item_id}/price_to_win?version=v2")

    def product_winners(self, catalog_product_id):
        return self.get(f"/products/{catalog_product_id}/items?site_id=MLB")

    def create_item(self, payload):
        return self.post("/items", payload)

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
    for attribute in item.get("attributes", []) or []:
        if attribute.get("id") in {"SELLER_SKU", "SKU", "MODEL"} and attribute.get("value_name"):
            return attribute["value_name"]
    return "-"


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
                    "available_quantity": item.get("available_quantity") or 0,
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
    interval = max(300, int(os.getenv("AUTO_SYNC_INTERVAL_SECONDS", "900")))
    startup_delay = max(20, int(os.getenv("AUTO_SYNC_STARTUP_DELAY_SECONDS", "45")))
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
            for account in accounts:
                try:
                    result = sync_official_account(payload, account.get("id"), "all")
                    account["auto_sync_status"] = f"Sincronização automática OK: {result.get('items', 0)} anúncios"
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


def product_public_urls(client, catalog_product_id, item=None):
    urls = []
    item_permalink = (item or {}).get("permalink") or ""
    if item_permalink:
        urls.append(item_permalink)
    try:
        product = client.product(catalog_product_id)
        permalink = product.get("permalink") or product.get("url")
        if permalink:
            urls.append(permalink)
        product_title = product.get("name") or product.get("title") or ""
    except Exception:
        product_title = ""
    urls.append(f"https://www.mercadolivre.com.br/p/{catalog_product_id}")
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
    with urllib.request.urlopen(req, timeout=15) as response:
        html_text = response.read().decode("utf-8", errors="replace")
        final_url = response.geturl() or url
    lines = visible_page_lines(html_text)
    seller_name = ""
    price = None
    for index, line in enumerate(lines):
        if "Vendido por" in line:
            tail = line.split("Vendido por", 1)[1].strip(" :·-")
            if tail:
                seller_name = tail
            for candidate in lines[index + 1 : index + 5]:
                if candidate and not re.search(r"mercadolíder|mercado líder|vendas|devolução|garantia|compra garantida", candidate, re.I):
                    seller_name = seller_name or candidate
                    break
            break
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
    price_matches = [match.group(0) for match in re.finditer(r"R\$\s*[\d\.]+(?:,\d{2})?", main_area)]
    for raw_price in price_matches:
        if re.search(r"x\s*" + re.escape(raw_price), main_area):
            continue
        price = parse_brl_price(raw_price)
        if price:
            break
    if not price:
        price = parse_brl_price(search_text)
    if not seller_name and "Vendido por" in search_text:
        seller_name = "Vendedor informado na página pública"
    if not price and not seller_name:
        raise RuntimeError("Não foi possível extrair buybox da página pública do Mercado Livre.")
    return {
        "seller_name": seller_name or "Vendedor da buybox",
        "price": price,
        "url": final_url,
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
        if str(price.get("winner", "")).lower() == "true" or price.get("status") in {"winning", "winner"}:
            data["winner_seller_id"] = account.get("seller_id", "")
            data["winner_name"] = account.get("nickname", "Sua conta")
            data["winner_price"] = price.get("current_price")
            data["winner_confirmed"] = True
            data["winner_source"] = "price_to_win"
    except Exception as exc:
        data["competition_reason"] = str(exc)

    if catalog_product_id:
        try:
            winners = client.product_winners(catalog_product_id)
            rows = winners.get("results") if isinstance(winners, dict) else winners
            if isinstance(rows, dict):
                rows = rows.get("results") or rows.get("items") or []
            if rows:
                candidates = catalog_offer_candidates(rows, sort_by_price=False)
                live_candidates = live_catalog_offers({"accounts": [account]}, candidates, sort_by_price=False)
                if live_candidates:
                    candidates = live_candidates
                reference = candidates[0] if candidates else {"raw": rows[0], "price": None, "item_id": "", "seller_id": ""}
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
                if reference.get("is_winner") and not data.get("winner_confirmed"):
                    data["winner_seller_id"] = reference_seller_id
                    data["winner_price"] = reference_price
                    data["winner_confirmed"] = True
                    data["winner_source"] = "products_items_winner_marker"
                    if reference_seller_id and reference_seller_id == str(account.get("seller_id")):
                        data["winner_name"] = account.get("nickname", "Sua conta")
                    else:
                        data["winner_name"] = data.get("catalog_reference_name") or first_present(raw_reference, ["seller.nickname", "nickname"], "Vendedor do catálogo")
                elif not data.get("winner_confirmed") and not data.get("competition_reason"):
                    data["competition_reason"] = "A API oficial não confirmou o vencedor da buy box; lista de ofertas do catálogo não será usada como vencedor."
        except Exception as exc:
            if not data["competition_reason"]:
                data["competition_reason"] = str(exc)

    if catalog_product_id and not data.get("winner_confirmed"):
        try:
            public_buybox = fetch_public_buybox(client, catalog_product_id, item)
            data["winner_name"] = public_buybox.get("seller_name") or "Vendedor da buybox"
            data["winner_price"] = public_buybox.get("price")
            data["winner_confirmed"] = True
            data["winner_source"] = "public_product_page"
            data["competition_status"] = data["competition_status"] if data["competition_status"] != "not_checked" else "public_buybox"
            data["competition_reason"] = "Buybox verificada na página pública do Mercado Livre."
        except Exception as exc:
            data["public_buybox_error"] = str(exc)

    if not data.get("winner_confirmed") and (data.get("catalog_reference_price") or data.get("catalog_reference_name") or data.get("catalog_reference_seller_id")):
        data["winner_seller_id"] = data.get("catalog_reference_seller_id") or ""
        data["winner_name"] = data.get("catalog_reference_name") or (
            f"Seller {data.get('catalog_reference_seller_id')}" if data.get("catalog_reference_seller_id") else "Vendedor da buybox"
        )
        data["winner_price"] = data.get("catalog_reference_price")
        data["winner_confirmed"] = True
        data["winner_source"] = "catalog_reference"
        data["competition_status"] = data["competition_status"] if data["competition_status"] != "not_checked" else "catalog_reference"
        data["competition_reason"] = "Buybox operacional baseada na primeira oferta elegível retornada pelo catálogo Mercado Livre."

    if not data.get("winner_confirmed"):
        data["winner_seller_id"] = ""
        data["winner_name"] = "Aguardando atualização"
        data["winner_price"] = None
    if data["competition_status"] == "not_listed" and not data.get("winner_seller_id"):
        data["winner_name"] = "Sem vencedor disponível"
    return data


def synced_catalog_item(account, item, competition=None):
    stock = item.get("available_quantity") or 0
    catalog_product_id = item.get("catalog_product_id") or "-"
    listing_type_id = item.get("listing_type_id") or first_present(item, ["listing_type.id", "listing_type"], "")
    shipping_logistic_type = first_present(item, ["shipping.logistic_type"], "")
    shipping_mode = first_present(item, ["shipping.mode"], "")
    is_catalog = bool(item.get("catalog_listing") or item.get("catalog_product_id"))
    competition = competition or {}
    if item.get("status") == "paused":
        status = "paused"
        action = "Anúncio pausado no Mercado Livre. Ative o anúncio e revise estoque, preço e políticas antes de disputar catálogo."
    elif stock == 0:
        status = "paused"
        action = "Anúncio importado sem estoque disponível. Pode estar pausado ou em risco de pausa por estoque zerado."
    elif is_catalog:
        status = "sharing"
        action = "Anúncio de catálogo importado da API oficial. A etapa seguinte é consultar concorrência e posição no catálogo."
    else:
        status = "winning"
        action = "Anúncio importado da API oficial. Sem vínculo de catálogo identificado neste retorno."
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
    return rows


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
    item_ids = client.seller_all_items(account["seller_id"], max_items=None if limit in (None, "all") else int(limit))
    imported = []
    for item_id in item_ids:
        try:
            item = client.item(item_id)
            competition = normalize_competition(client, account, item)
            imported.append(synced_catalog_item(account, item, competition))
        except Exception:
            continue

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
    add_stock_alerts(payload, account, imported)
    sync_recent_sales(payload, account, client)
    return {"account": public_account(account), "items": len(imported), "catalog": imported}


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
        sessions = [item for item in read_json("sessions.json", []) if item.get("expires_at", 0) > now]
        sessions.append({"token": token, "user_id": user["id"], "created_at": now, "expires_at": now + SESSION_SECONDS})
        write_json("sessions.json", sessions[-50:])
        return token

    def clear_session(self):
        token = self.cookie_value("competidor_session")
        if token:
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
        payload = read_payload()

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
            stock_created = ensure_stock_alerts(payload, notify=False)
            alerts_enriched = enrich_product_alerts(payload)
            if notifications_migrated or stock_created or alerts_enriched:
                write_payload(payload)
            self.send_json(public_payload(payload, self.current_user(payload)))
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
        if not self.validate_same_origin():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        request = json.loads(body or "{}")
        payload = read_payload()

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
                write_payload(payload)
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
                if not update:
                    raise RuntimeError("Nenhum campo informado para atualizar.")
                client = account_client(account)
                official = client.update_item(item_id, update)
                for item in payload.get("catalog", []):
                    if item.get("id") == item_id:
                        changes = {}
                        if "price" in update:
                            changes["price"] = {"from": item.get("price"), "to": update["price"]}
                            item["price"] = update["price"]
                        if "available_quantity" in update:
                            changes["stock"] = {"from": item.get("stock"), "to": update["available_quantity"]}
                            item["stock"] = update["available_quantity"]
                        if "title" in update:
                            changes["title"] = {"from": item.get("title"), "to": update["title"]}
                            item["title"] = update["title"]
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
                ensure_stock_alerts(payload, notify=True)
                write_payload(payload)
                self.send_json({"ok": True, "official": official, "catalog": public_payload(payload, user)["catalog"]})
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
                if not item_ids:
                    self.send_json({"error": "Selecione ao menos um anúncio específico."}, status=400)
                    return
                catalog = payload.setdefault("catalog", [])
                source_items = [
                    item
                    for item in catalog
                    if item.get("account") == source and item.get("id") in item_ids
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
                job = {
                    "id": f"clone-{uuid.uuid4().hex[:8]}",
                    "source": source,
                    "target": target,
                    "item_ids": item_ids,
                    "items": len(item_ids),
                    "status": "preview_ready",
                    "edits": edits,
                "note": "Preview criado para anúncios específicos. A cópia pode ser feita para outra conta ou para a mesma conta, usando novo ID interno e SKU com sufixo para evitar conflito.",
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
                catalog = payload.setdefault("catalog", [])
                existing_ids = {item.get("id") for item in catalog if item.get("id")}
                copied = []
                edits = job.get("edits") or {}
                for item_id in job.get("item_ids", []):
                    source_item = next((item for item in catalog if item.get("id") == item_id), None)
                    if not source_item:
                        continue
                    new_id = f"COPY-{uuid.uuid4().hex[:6].upper()}"
                    while new_id in existing_ids:
                        new_id = f"COPY-{uuid.uuid4().hex[:6].upper()}"
                    existing_ids.add(new_id)
                    source_sku = source_item.get("sku") or source_item.get("seller_sku") or source_item.get("id") or "SEM-SKU"
                    new_item = {
                        **source_item,
                        "id": new_id,
                        "account": job.get("target"),
                        "title": edits.get("title") or source_item.get("title") or source_item.get("id") or new_id,
                        "sku": f"{source_sku}{edits.get('sku_suffix') or '-COPIA'}",
                        "price": float(edits.get("price") or source_item.get("price") or 0),
                        "stock": int(float(edits.get("stock") or source_item.get("stock") or 0)),
                        "status": "sharing",
                        "share": 0,
                        "description_override": edits.get("description", ""),
                        "action": f"Anúncio copiado de {source_item.get('id')} para {job.get('target')}. Revise estoque, preço e políticas antes de publicar oficialmente.",
                    }
                    catalog.append(new_item)
                    copied.append(new_item)
                job["status"] = "copied"
                job["copied_items"] = [item.get("id") for item in copied]
                job["note"] = f"{len(copied)} anúncios copiados para {job.get('target')} no ambiente do CompeTIDOR."
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
    threading.Thread(target=auto_scan_loop, daemon=True).start()
    threading.Thread(target=auto_official_sync_loop, daemon=True).start()
    print(f"CompeTIDOR rodando em http://127.0.0.1:{port}")
    print(f"Callback HTTPS em https://127.0.0.1:{https_port()}/api/oauth/callback")
    http_server.serve_forever()




