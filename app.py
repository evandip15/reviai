import os
import random
import re
import secrets
import time
import json
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

try:
    import psycopg
except ModuleNotFoundError:  # Le mode fichier local fonctionne sans PostgreSQL.
    psycopg = None
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import stripe
except ModuleNotFoundError:  # La version locale peut fonctionner sans l'option Plus.
    stripe = None

from ai import (
    extract_image_text_gemini,
    extract_document_text_gemini,
    generate_quiz,
    generate_revision,
    chat_about_exercise,
    solve_exercise_images,
    solve_exercise_text,
)
from lecteur import read_file

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".odt", ".pptx", ".txt", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
}
MAX_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
SESSION_TTL_SECONDS = 60 * 60
DEFAULT_PUBLIC_BASE_URL = "https://reviai.onrender.com"

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

STATE_LOCK = Lock()
FEEDBACK_LOCK = Lock()
LATEST_BY_SESSION = {}
JOBS = {}
EXERCISE_SESSIONS = {}
EXERCISE_DETECTIONS = {}
FEEDBACK_PATH = BASE_DIR / "data" / "exercise_feedback.jsonl"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
FEEDBACK_DATABASE_REQUIRED = os.getenv("FEEDBACK_DATABASE_REQUIRED", "false").lower() == "true"
FEEDBACK_TABLE = "reviai_exercise_feedback"
USAGE_TABLE = "reviai_daily_usage"
ACCOUNT_TABLE = "reviai_accounts"
STRIPE_EVENTS_TABLE = "reviai_stripe_events"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()
APPS_SCRIPT_MAIL_URL = os.getenv("APPS_SCRIPT_MAIL_URL", "").strip()
APPS_SCRIPT_MAIL_TOKEN = os.getenv("APPS_SCRIPT_MAIL_TOKEN", "").strip()
PLUS_PRICE_CENTS = 199
TERMS_VERSION = "2026-10-09"
USAGE_LOCK = Lock()
LOCAL_DAILY_USAGE = {}
LAST_USAGE_CLEANUP = ""
LAST_BILLING_CLEANUP = ""


def configured_limit(name, default):
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


DAILY_LIMITS = {
    "revision": configured_limit("FREE_GENERATIONS_PER_DAY", 3),
    "detection": configured_limit("FREE_DETECTIONS_PER_DAY", 6),
    "exercise": configured_limit("FREE_EXERCISES_PER_DAY", 3),
    "chat": configured_limit("FREE_CHAT_MESSAGES_PER_DAY", 10),
    "quiz": configured_limit("FREE_QUIZZES_PER_DAY", 3),
}

# Un seul traitement IA à la fois sur le petit serveur gratuit.
EXECUTOR = ThreadPoolExecutor(max_workers=1)


class FeedbackStorageError(Exception):
    """Signale une erreur de lecture ou d'écriture des signalements."""


def billing_database_ready():
    return bool(DATABASE_URL and psycopg is not None)


def billing_ready():
    return bool(
        billing_database_ready()
        and stripe is not None
        and STRIPE_SECRET_KEY
        and STRIPE_WEBHOOK_SECRET
        and mail_is_configured()
    )


def ensure_billing_tables(connection):
    global LAST_BILLING_CLEANUP
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ACCOUNT_TABLE} (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            email_verified BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            terms_accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            terms_version TEXT NOT NULL DEFAULT 'initial',
            subscription_consent_at TIMESTAMPTZ,
            verification_token_hash TEXT,
            verification_expires_at TIMESTAMPTZ,
            reset_token_hash TEXT,
            reset_expires_at TIMESTAMPTZ,
            stripe_customer_id TEXT UNIQUE,
            stripe_subscription_id TEXT,
            subscription_status TEXT NOT NULL DEFAULT 'inactive',
            subscription_period_end TIMESTAMPTZ,
            cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    connection.execute(f"ALTER TABLE {ACCOUNT_TABLE} ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP")
    connection.execute(f"ALTER TABLE {ACCOUNT_TABLE} ADD COLUMN IF NOT EXISTS terms_version TEXT NOT NULL DEFAULT 'initial'")
    connection.execute(f"ALTER TABLE {ACCOUNT_TABLE} ADD COLUMN IF NOT EXISTS subscription_consent_at TIMESTAMPTZ")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STRIPE_EVENTS_TABLE} (
            event_id TEXT PRIMARY KEY,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cleanup_day = time.strftime("%Y-%m-%d")
    with USAGE_LOCK:
        if LAST_BILLING_CLEANUP != cleanup_day:
            connection.execute(
                f"DELETE FROM {STRIPE_EVENTS_TABLE} WHERE received_at < CURRENT_TIMESTAMP - INTERVAL '90 days'"
            )
            LAST_BILLING_CLEANUP = cleanup_day


def billing_account(user_id):
    if not billing_database_ready() or not user_id:
        return None
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            row = connection.execute(
                f"""SELECT id, email, email_verified, stripe_customer_id,
                           stripe_subscription_id, subscription_status,
                           subscription_period_end, cancel_at_period_end
                    FROM {ACCOUNT_TABLE} WHERE id = %s""",
                (str(user_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "email_verified": row[2],
            "stripe_customer_id": row[3], "stripe_subscription_id": row[4],
            "subscription_status": row[5], "subscription_period_end": row[6],
            "cancel_at_period_end": row[7],
        }
    except Exception:
        app.logger.exception("Impossible de lire le compte RéviAI.")
        return None


def account_has_plus(account):
    if not account or account.get("subscription_status") not in {"active", "trialing"}:
        return False
    period_end = account.get("subscription_period_end")
    return period_end is None or period_end > datetime.now(timezone.utc)


def has_unlimited_access():
    if session.get("admin_authenticated"):
        return True
    if getattr(g, "reviai_unlimited_access", None) is not None:
        return g.reviai_unlimited_access
    account = billing_account(session.get("user_id"))
    g.reviai_unlimited_access = bool(account and account.get("email_verified") and account_has_plus(account))
    return g.reviai_unlimited_access


def mail_is_configured():
    resend_ready = bool(RESEND_API_KEY and MAIL_FROM)
    apps_script_ready = bool(APPS_SCRIPT_MAIL_URL and APPS_SCRIPT_MAIL_TOKEN)
    return resend_ready or apps_script_ready


def send_account_email(recipient, subject, html, text):
    if not mail_is_configured():
        return False
    if not (RESEND_API_KEY and MAIL_FROM):
        payload = json.dumps({
            "token": APPS_SCRIPT_MAIL_TOKEN,
            "to": recipient,
            "subject": subject,
            "html": html,
            "text": text,
        }).encode("utf-8")
        req = urllib.request.Request(
            APPS_SCRIPT_MAIL_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
                return 200 <= response.status < 300 and result.get("ok") is True
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            app.logger.exception("L'envoi d'un e-mail via Google Apps Script a échoué.")
            return False

    payload = json.dumps({
        "from": MAIL_FROM,
        "to": [recipient],
        "subject": subject,
        "html": html,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        app.logger.exception("L'envoi d'un e-mail de compte a échoué.")
        return False


def account_link(endpoint, token):
    query = urllib.parse.urlencode({"token": token})
    return f"{public_base_url()}{url_for(endpoint)}?{query}"


def set_account_token(user_id, token_kind, token, expires_minutes):
    if token_kind not in {"verification", "reset"}:
        return False
    token_column = "verification_token_hash" if token_kind == "verification" else "reset_token_hash"
    expiry_column = "verification_expires_at" if token_kind == "verification" else "reset_expires_at"
    hashed = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            result = connection.execute(
                f"""UPDATE {ACCOUNT_TABLE}
                    SET {token_column} = %s,
                        {expiry_column} = CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute')
                    WHERE id = %s RETURNING email""",
                (hashed, expires_minutes, str(user_id)),
            ).fetchone()
        return result[0] if result else False
    except Exception:
        app.logger.exception("Impossible de préparer un e-mail de compte.")
        return False


def account_csrf_token():
    token = session.get("account_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["account_csrf_token"] = token
    return token


def valid_account_csrf(token):
    expected = session.get("account_csrf_token", "")
    return bool(expected and token and hmac.compare_digest(expected, token))


def send_verification_email(user_id, email):
    token = secrets.token_urlsafe(32)
    if not set_account_token(user_id, "verification", token, 60 * 24):
        return False
    link = account_link("verify_account", token)
    return send_account_email(
        email,
        "Confirme ton adresse e-mail pour RéviAI",
        f"<p>Pour activer ton compte RéviAI, confirme ton adresse dans les 24 heures :</p><p><a href=\"{link}\">Confirmer mon adresse e-mail</a></p><p>Si tu n'es pas à l'origine de cette demande, ignore ce message.</p>",
        f"Confirme ton adresse e-mail RéviAI dans les 24 heures : {link}\n\nSi tu n'es pas à l'origine de cette demande, ignore ce message.",
    )


def get_account_for_login(email):
    if not billing_database_ready():
        return None
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            row = connection.execute(
                f"""SELECT id, email, password_hash, email_verified
                    FROM {ACCOUNT_TABLE} WHERE email = %s""",
                (email,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "email": row[1], "password_hash": row[2], "email_verified": row[3]}
    except Exception:
        app.logger.exception("Impossible de rechercher un compte RéviAI.")
        return None


def stripe_configure():
    if stripe is None or not STRIPE_SECRET_KEY:
        raise RuntimeError("Le paiement n'est pas configuré.")
    stripe.api_key = STRIPE_SECRET_KEY


def stripe_field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    getter = getattr(value, "get", None)
    return getter(name, default) if getter else default


def stripe_id(value):
    return value if isinstance(value, str) else stripe_field(value, "id")


def stripe_subscription_period_end(subscription):
    period_end = stripe_field(subscription, "current_period_end")
    if period_end:
        return period_end
    items = stripe_field(subscription, "items", {}) or {}
    item_list = stripe_field(items, "data", []) or []
    if item_list:
        return stripe_field(item_list[0], "current_period_end")
    return None


def apply_subscription_update(subscription):
    metadata = stripe_field(subscription, "metadata", {}) or {}
    user_id = str(stripe_field(metadata, "reviai_user_id", "") or "")
    customer_id = stripe_id(stripe_field(subscription, "customer"))
    subscription_id = stripe_id(subscription)
    status = str(stripe_field(subscription, "status", "inactive"))
    period_end = stripe_subscription_period_end(subscription)
    period_end_at = datetime.fromtimestamp(int(period_end), timezone.utc) if period_end else None
    cancel_at_period_end = bool(stripe_field(subscription, "cancel_at_period_end", False))

    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            if not user_id and customer_id:
                row = connection.execute(
                    f"SELECT id FROM {ACCOUNT_TABLE} WHERE stripe_customer_id = %s",
                    (customer_id,),
                ).fetchone()
                user_id = str(row[0]) if row else ""
            if not user_id:
                return False
            connection.execute(
                f"""UPDATE {ACCOUNT_TABLE}
                    SET stripe_customer_id = COALESCE(%s, stripe_customer_id),
                        stripe_subscription_id = %s,
                        subscription_status = %s,
                        subscription_period_end = %s,
                        cancel_at_period_end = %s
                    WHERE id = %s""",
                (customer_id, subscription_id, status, period_end_at, cancel_at_period_end, user_id),
            )
        return True
    except Exception:
        app.logger.exception("Impossible de mettre à jour l'abonnement reçu de Stripe.")
        raise


def process_stripe_event(event):
    event_id = str(stripe_field(event, "id", ""))
    event_type = str(stripe_field(event, "type", ""))
    event_data = stripe_field(event, "data", {}) or {}
    event_object = stripe_field(event_data, "object", {}) or {}
    subscription = None

    if event_type == "checkout.session.completed":
        if stripe_field(event_object, "mode") == "subscription":
            subscription_id = stripe_id(stripe_field(event_object, "subscription"))
            if subscription_id:
                stripe_configure()
                subscription = stripe.Subscription.retrieve(subscription_id)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        subscription_id = stripe_id(event_object)
        if subscription_id:
            stripe_configure()
            subscription = stripe.Subscription.retrieve(subscription_id)
    else:
        return True

    if not event_id:
        return False
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            inserted = connection.execute(
                f"""INSERT INTO {STRIPE_EVENTS_TABLE} (event_id)
                    VALUES (%s) ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                (event_id,),
            ).fetchone()
            if not inserted:
                return True

            if event_type == "checkout.session.completed":
                user_id = str(stripe_field(event_object, "client_reference_id", "") or "")
                customer_id = stripe_id(stripe_field(event_object, "customer"))
                subscription_id = stripe_id(stripe_field(event_object, "subscription"))
                if user_id:
                    connection.execute(
                        f"""UPDATE {ACCOUNT_TABLE}
                            SET stripe_customer_id = COALESCE(%s, stripe_customer_id),
                                stripe_subscription_id = COALESCE(%s, stripe_subscription_id)
                            WHERE id = %s""",
                        (customer_id, subscription_id, user_id),
                    )
            if subscription is not None:
                metadata = stripe_field(subscription, "metadata", {}) or {}
                user_id = str(stripe_field(metadata, "reviai_user_id", "") or "")
                customer_id = stripe_id(stripe_field(subscription, "customer"))
                subscription_id = stripe_id(subscription)
                status = str(stripe_field(subscription, "status", "inactive"))
                period_end = stripe_subscription_period_end(subscription)
                period_end_at = datetime.fromtimestamp(int(period_end), timezone.utc) if period_end else None
                cancel_at_period_end = bool(stripe_field(subscription, "cancel_at_period_end", False))
                if not user_id and customer_id:
                    row = connection.execute(
                        f"SELECT id FROM {ACCOUNT_TABLE} WHERE stripe_customer_id = %s",
                        (customer_id,),
                    ).fetchone()
                    user_id = str(row[0]) if row else ""
                if user_id:
                    connection.execute(
                        f"""UPDATE {ACCOUNT_TABLE}
                            SET stripe_customer_id = COALESCE(%s, stripe_customer_id),
                                stripe_subscription_id = %s,
                                subscription_status = %s,
                                subscription_period_end = %s,
                                cancel_at_period_end = %s
                            WHERE id = %s""",
                        (customer_id, subscription_id, status, period_end_at, cancel_at_period_end, user_id),
                    )
        return True
    except Exception:
        app.logger.exception("Traitement d'un événement Stripe impossible.")
        raise


@app.before_request
def identify_browser_for_usage_limits():
    browser_id = request.cookies.get("reviai_browser", "")
    if not re.fullmatch(r"[a-f0-9]{64}", browser_id):
        browser_id = secrets.token_hex(32)
        g.reviai_browser_cookie_new = True
    else:
        g.reviai_browser_cookie_new = False
    g.reviai_browser_id = browser_id


def browser_usage_hash():
    browser_id = getattr(g, "reviai_browser_id", "")
    secret = app.secret_key
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, browser_id.encode("ascii"), hashlib.sha256).hexdigest()


def ensure_usage_table(connection):
    global LAST_USAGE_CLEANUP
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {USAGE_TABLE} (
            usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
            browser_hash TEXT NOT NULL,
            feature TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (usage_date, browser_hash, feature)
        )
        """
    )
    today = time.strftime("%Y-%m-%d")
    with USAGE_LOCK:
        if LAST_USAGE_CLEANUP != today:
            connection.execute(f"DELETE FROM {USAGE_TABLE} WHERE usage_date < CURRENT_DATE - 90")
            LAST_USAGE_CLEANUP = today


def consume_daily_usage(feature):
    if has_unlimited_access():
        return True, 0, None
    limit = DAILY_LIMITS.get(feature, 0)
    if limit <= 0:
        return True, 0, None

    browser_hash = browser_usage_hash()
    if DATABASE_URL and psycopg is not None:
        try:
            with feedback_database_connection() as connection:
                ensure_usage_table(connection)
                row = connection.execute(
                    f"""
                    INSERT INTO {USAGE_TABLE} (usage_date, browser_hash, feature, used)
                    VALUES (CURRENT_DATE, %s, %s, 1)
                    ON CONFLICT (usage_date, browser_hash, feature)
                    DO UPDATE SET used = {USAGE_TABLE}.used + 1
                    WHERE {USAGE_TABLE}.used < %s
                    RETURNING used
                    """,
                    (browser_hash, feature, limit),
                ).fetchone()
                if row:
                    return True, row[0], limit
                existing = connection.execute(
                    f"""SELECT used FROM {USAGE_TABLE}
                        WHERE usage_date = CURRENT_DATE AND browser_hash = %s AND feature = %s""",
                    (browser_hash, feature),
                ).fetchone()
                return False, existing[0] if existing else limit, limit
        except Exception:
            app.logger.exception("Le compteur journalier PostgreSQL est indisponible ; utilisation du compteur temporaire.")

    today = time.strftime("%Y-%m-%d")
    key = (today, browser_hash, feature)
    with USAGE_LOCK:
        used = LOCAL_DAILY_USAGE.get(key, 0)
        if used >= limit:
            return False, used, limit
        used += 1
        LOCAL_DAILY_USAGE[key] = used
        for old_key in [item for item in LOCAL_DAILY_USAGE if item[0] != today]:
            LOCAL_DAILY_USAGE.pop(old_key, None)
        return True, used, limit


def daily_usage_snapshot():
    if has_unlimited_access():
        return {
            feature: {"limit": None, "used": 0, "remaining": None}
            for feature in DAILY_LIMITS
        }
    browser_hash = browser_usage_hash()
    usage = {}
    if DATABASE_URL and psycopg is not None:
        try:
            with feedback_database_connection() as connection:
                ensure_usage_table(connection)
                rows = connection.execute(
                    f"""SELECT feature, used FROM {USAGE_TABLE}
                        WHERE usage_date = CURRENT_DATE AND browser_hash = %s""",
                    (browser_hash,),
                ).fetchall()
            usage = {row[0]: int(row[1]) for row in rows}
        except Exception:
            app.logger.exception("Impossible de lire les compteurs journaliers PostgreSQL.")

    today = time.strftime("%Y-%m-%d")
    with USAGE_LOCK:
        for feature in DAILY_LIMITS:
            if feature not in usage:
                usage[feature] = LOCAL_DAILY_USAGE.get((today, browser_hash, feature), 0)

    response = {}
    for feature, limit in DAILY_LIMITS.items():
        used = usage.get(feature, 0)
        response[feature] = {
            "limit": limit or None,
            "used": used,
            "remaining": max(0, limit - used) if limit else None,
        }
    return response


def recent_usage_metrics():
    if not DATABASE_URL or psycopg is None:
        return [], "Configure DATABASE_URL pour conserver les statistiques après un redémarrage."
    try:
        with feedback_database_connection() as connection:
            ensure_usage_table(connection)
            rows = connection.execute(
                f"""SELECT usage_date, feature, SUM(used)::integer, COUNT(*)::integer
                    FROM {USAGE_TABLE}
                    WHERE usage_date >= CURRENT_DATE - 29
                    GROUP BY usage_date, feature
                    ORDER BY usage_date DESC, feature ASC"""
            ).fetchall()
        return [
            {"date": row[0].isoformat(), "feature": row[1], "uses": row[2], "browsers": row[3]}
            for row in rows
        ], ""
    except Exception as exc:
        app.logger.exception("Impossible de charger les statistiques d'utilisation.")
        return [], "Impossible de lire les statistiques. Vérifie DATABASE_URL."


def feedback_database_connection():
    if psycopg is None:
        raise FeedbackStorageError("Le pilote PostgreSQL n'est pas installé.")
    database_url = DATABASE_URL
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    return psycopg.connect(database_url, connect_timeout=5)


def ensure_feedback_table(connection):
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {FEEDBACK_TABLE} (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            exercise_number TEXT,
            questions JSONB NOT NULL DEFAULT '[]'::jsonb,
            category TEXT NOT NULL,
            comment TEXT NOT NULL
        )
        """
    )


def legacy_feedback_id(report, raw_line):
    existing_id = report.get("id")
    if existing_id:
        return str(existing_id)
    digest = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()[:24]
    return f"legacy-{digest}"


def save_feedback_report(report):
    if DATABASE_URL:
        try:
            with feedback_database_connection() as connection:
                ensure_feedback_table(connection)
                connection.execute(
                    f"""
                    INSERT INTO {FEEDBACK_TABLE}
                        (id, created_at, exercise_number, questions, category, comment)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        report["id"],
                        report["created_at"],
                        str(report["exercise_number"]) if report.get("exercise_number") is not None else None,
                        json.dumps(report.get("questions", []), ensure_ascii=False),
                        report["category"],
                        report["comment"],
                    ),
                )
            return
        except Exception as exc:
            raise FeedbackStorageError("Impossible d'enregistrer le signalement dans la base de données.") from exc

    if FEEDBACK_DATABASE_REQUIRED:
        raise FeedbackStorageError("Le stockage permanent des signalements n'est pas encore configuré.")

    try:
        with FEEDBACK_LOCK:
            with FEEDBACK_PATH.open("a", encoding="utf-8") as feedback_file:
                feedback_file.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise FeedbackStorageError("Impossible d'enregistrer le signalement dans le fichier local.") from exc


def list_feedback_reports(limit=1000):
    if DATABASE_URL:
        try:
            with feedback_database_connection() as connection:
                ensure_feedback_table(connection)
                rows = connection.execute(
                    f"""
                    SELECT id, created_at, exercise_number, questions, category, comment
                    FROM {FEEDBACK_TABLE}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
            return [
                {
                    "id": row[0],
                    "created_at": row[1],
                    "exercise_number": row[2],
                    "questions": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                    "category": row[4],
                    "comment": row[5],
                }
                for row in rows
            ]
        except Exception as exc:
            raise FeedbackStorageError("Impossible de lire les signalements dans la base de données.") from exc

    reports = []
    try:
        with FEEDBACK_LOCK:
            with FEEDBACK_PATH.open("r", encoding="utf-8") as feedback_file:
                for raw_line in feedback_file:
                    try:
                        report = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(report, dict):
                        continue
                    report["id"] = legacy_feedback_id(report, raw_line.rstrip("\r\n"))
                    reports.append(report)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise FeedbackStorageError("Impossible de lire le fichier local des signalements.") from exc
    reports.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return reports[:limit]


def delete_feedback_report(report_id):
    if DATABASE_URL:
        try:
            with feedback_database_connection() as connection:
                ensure_feedback_table(connection)
                result = connection.execute(
                    f"DELETE FROM {FEEDBACK_TABLE} WHERE id = %s",
                    (report_id,),
                )
                return result.rowcount > 0
        except Exception as exc:
            raise FeedbackStorageError("Impossible de supprimer le signalement de la base de données.") from exc

    try:
        with FEEDBACK_LOCK:
            kept_lines = []
            found = False
            if FEEDBACK_PATH.exists():
                with FEEDBACK_PATH.open("r", encoding="utf-8") as feedback_file:
                    for raw_line in feedback_file:
                        try:
                            report = json.loads(raw_line)
                        except json.JSONDecodeError:
                            kept_lines.append(raw_line)
                            continue
                        if isinstance(report, dict) and legacy_feedback_id(report, raw_line.rstrip("\r\n")) == report_id:
                            found = True
                        else:
                            kept_lines.append(raw_line)
            if found:
                temporary_path = FEEDBACK_PATH.with_suffix(".tmp")
                with temporary_path.open("w", encoding="utf-8") as feedback_file:
                    feedback_file.writelines(kept_lines)
                os.replace(temporary_path, FEEDBACK_PATH)
            return found
    except OSError as exc:
        raise FeedbackStorageError("Impossible de supprimer le signalement du fichier local.") from exc


def admin_csrf_token():
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


def valid_admin_csrf(token):
    expected = session.get("admin_csrf_token", "")
    return bool(expected and token and hmac.compare_digest(expected, token))


def cleanup_temp(paths):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def make_id():
    return secrets.token_urlsafe(18)


def set_job(job_id, **updates):
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def expire_old_sessions_locked():
    """Évite de garder indéfiniment les cours et exercices en mémoire."""
    now = time.time()
    for sessions in (LATEST_BY_SESSION, EXERCISE_SESSIONS, EXERCISE_DETECTIONS):
        expired = [
            key for key, value in sessions.items()
            if now - value.get("created", now) > SESSION_TTL_SECONDS
        ]
        for key in expired:
            sessions.pop(key, None)
    expired_jobs = [
        key for key, job in JOBS.items()
        if job.get("status") in {"done", "error"}
        and now - job.get("created", now) > SESSION_TTL_SECONDS
    ]
    for key in expired_jobs:
        JOBS.pop(key, None)



def uploaded_mime_from_extension(extension):
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(str(extension).lower(), "image/jpeg")


def process_generation(job_id, saved_files):
    temp_paths = [item[0] for item in saved_files]
    try:
        set_job(job_id, status="reading", message="Lecture des fichiers…")

        texts = []
        sources = []

        for path, original_name in saved_files:
            try:
                suffix = path.suffix.lower()
                use_gemini = (
                    os.getenv("AI_PROVIDER", "auto").strip().lower() == "gemini"
                    and bool(os.getenv("GEMINI_API_KEY", "").strip())
                )

                is_image = suffix in {".jpg", ".jpeg", ".png", ".webp"}

                if is_image and use_gemini:
                    set_job(
                        job_id,
                        status="reading",
                        message=f"Lecture intelligente de la photo : {original_name}…",
                    )
                    extracted = extract_image_text_gemini(
                        path,
                        mime_type=uploaded_mime_from_extension(path.suffix),
                    ).strip()
                elif suffix == ".pdf" and use_gemini:
                    # Les PDF contenant des scans, tableaux ou schémas peuvent
                    # contenir très peu (ou pas) de texte extractible localement.
                    # On utilise d'abord pypdf pour les PDF textuels, puis Gemini
                    # en secours pour les PDF scannés.
                    extracted = read_file(path).strip()
                    if len(extracted) < 80:
                        set_job(
                            job_id,
                            status="reading",
                            message=f"Lecture intelligente du PDF : {original_name}…",
                        )
                        extracted = extract_document_text_gemini(path).strip()
                else:
                    extracted = read_file(path).strip()
            except Exception as exc:
                raise RuntimeError(f"Impossible de lire {original_name} : {exc}") from exc

            if extracted:
                texts.append(
                    f"===== SOURCE : {original_name} =====\n\n{extracted}"
                )
                sources.append(original_name)

        if not texts:
            raise RuntimeError("Aucun texte exploitable n'a été trouvé dans les fichiers.")

        course = "\n\n".join(texts)

        set_job(
            job_id,
            status="generating",
            message="Les fichiers sont lus. Génération de la fiche par l'IA…",
        )

        revision = generate_revision(course)
        if not revision.strip():
            raise RuntimeError("L'IA n'a généré aucune fiche.")

        session_key = make_id()

        with STATE_LOCK:
            expire_old_sessions_locked()
            LATEST_BY_SESSION[session_key] = {
                "course": course,
                "revision": revision,
                "recent_questions": [],
                "created": time.time(),
            }

        set_job(
            job_id,
            status="done",
            message="Fiche créée !",
            result={
                "success": True,
                "content": revision,
                "sources": sources,
                "session_id": session_key,
            },
        )

    except Exception as exc:
        set_job(job_id, status="error", message=str(exc))
    finally:
        cleanup_temp(temp_paths)


def _requested_exercise_number(instruction):
    instruction = str(instruction or "").strip()
    match = re.search(
        r"\b(?:exercice|exo|num[eé]ro)\s*(?:n\s*[°o.]?\s*)?(\d{1,3})\b",
        instruction,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(r"\b(?:le|n\s*[°o.]?)\s*(\d{1,3})\b|#\s*(\d{1,3})", instruction, flags=re.IGNORECASE)
    if not match:
        match = re.match(r"\s*(?:le\s+|n\s*[°o.]?\s*|#)?(\d{1,3})(?=\s|$|[,;—-])", instruction, flags=re.IGNORECASE)
    if not match:
        return None
    number = next((group for group in match.groups() if group), None)
    return int(number) if number else None


def _answer_exercise_number(solution):
    prefix = str(solution or "")[:500]
    explicit = re.search(
        r"\b(?:exercice|exo)\s*(?:n\s*[°o.]?\s*)?(\d{1,3})\b",
        prefix,
        flags=re.IGNORECASE,
    )
    if explicit:
        return int(explicit.group(1))

    chosen = re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?Énoncé choisi\b(?:\*\*)?\s*:?\s*(?P<title>[^\n]*)",
        prefix,
    )
    if chosen:
        title = chosen.group("title").strip() or prefix[chosen.end():].lstrip("* :\r\n")
        numbered_title = re.match(r"(?:#\s*)?(\d{1,3})\s*(?:[°.)\-:]|(?=\s+[A-Z]))", title)
        if numbered_title:
            return int(numbered_title.group(1))
    return None


def _split_detected_exercises(text):
    """Repère les exercices numérotés et garde leur énoncé jusqu'au suivant."""
    heading_pattern = re.compile(
        r"(?im)^[ \t]*(?:exercice|exo)\s*(?:n(?:um[eé]ro)?\s*[°o.]?\s*)?(\d{1,3})\b[^\n]*"
        r"|^[ \t]*(\d{2,3})(?:[ \t]+(?=[A-ZÀ-ÖØ-Þ«(\[])|[ \t]*$)[^\n]*"
    )
    headings = list(heading_pattern.finditer(str(text or "")))
    exercises = {}
    for index, heading in enumerate(headings):
        number_text = heading.group(1) or heading.group(2)
        if not number_text:
            continue
        number = int(number_text)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        statement = str(text[heading.start():end]).strip()
        if not statement:
            continue

        question_numbers = []
        for line in statement.splitlines()[1:]:
            question = re.match(r"^\s*(\d{1,2})\s*[.)]\s+\S", line)
            if question and question.group(1) not in question_numbers:
                question_numbers.append(question.group(1))

        if number in exercises:
            exercises[number]["text"] += "\n\n" + statement
            for question_number in question_numbers:
                if question_number not in exercises[number]["questions"]:
                    exercises[number]["questions"].append(question_number)
        else:
            exercises[number] = {
                "text": statement,
                "questions": question_numbers,
            }
    return exercises


def _read_exercise_source(path):
    extension = path.suffix.lower()
    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
    has_gemini_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    use_gemini = provider == "gemini" or (provider != "ollama" and has_gemini_key)

    if extension in IMAGE_EXTENSIONS and use_gemini:
        return extract_image_text_gemini(
            path,
            mime_type=uploaded_mime_from_extension(extension),
        ).strip()

    extracted = read_file(path).strip()
    if extension == ".pdf" and len(extracted) < 60 and has_gemini_key:
        extracted = extract_document_text_gemini(path).strip()
    return extracted


def process_exercise_detection(job_id, saved_files):
    temp_paths = [item[0] for item in saved_files]
    try:
        set_job(job_id, status="reading", message="Repérage des exercices dans les fichiers…")
        pages = []
        for index, (path, original_name) in enumerate(saved_files, start=1):
            try:
                extracted = _read_exercise_source(path)
            except Exception as exc:
                raise RuntimeError(f"Impossible de lire {original_name} : {exc}") from exc
            if extracted:
                pages.append(f"===== PAGE {index} : {original_name} =====\n\n{extracted}")

        full_text = "\n\n".join(pages).strip()
        if not full_text:
            raise RuntimeError("Aucun texte lisible n’a été trouvé dans les fichiers.")

        exercises = _split_detected_exercises(full_text)
        detection_id = make_id()
        with STATE_LOCK:
            expire_old_sessions_locked()
            EXERCISE_DETECTIONS[detection_id] = {
                "text": full_text,
                "exercises": exercises,
                "created": time.time(),
            }

        candidates = [
            {
                "number": number,
                "preview": re.sub(r"\s+", " ", exercise["text"])[:260],
                "questions": exercise["questions"],
            }
            for number, exercise in sorted(exercises.items())
        ]
        set_job(
            job_id,
            status="done",
            message=(
                f"{len(candidates)} exercice(s) repéré(s). Choisis celui que tu veux faire."
                if candidates
                else "Aucun numéro n’a été repéré automatiquement. Tu peux saisir le numéro manuellement."
            ),
            result={
                "success": True,
                "detection_id": detection_id,
                "exercises": candidates,
                "detected": bool(candidates),
            },
        )
    except Exception as exc:
        set_job(job_id, status="error", message=str(exc))
    finally:
        cleanup_temp(temp_paths)


def process_exercise(
    job_id,
    saved_files,
    instruction="",
    exercise_number=None,
    selected_questions=None,
    help_mode="complete",
    detection_id="",
):
    temp_paths = [item[0] for item in saved_files]
    original_names = [item[1] for item in saved_files]
    exercise_text = ""
    try:
        set_job(job_id, status="reading", message="Lecture des photos ou documents…")

        detection = None
        if detection_id:
            with STATE_LOCK:
                cached = EXERCISE_DETECTIONS.get(detection_id)
                detection = dict(cached) if cached else None
        selected_statement = ""
        solver_questions = list(selected_questions or [])
        if detection and exercise_number is not None:
            candidate = detection.get("exercises", {}).get(exercise_number)
            if candidate:
                selected_statement = candidate.get("text", "")
                detected_questions = set(candidate.get("questions", []))
                if detected_questions and set(solver_questions) == detected_questions:
                    solver_questions = []
        if selected_statement:
            exercise_text = selected_statement

        all_images = all(path.suffix.lower() in IMAGE_EXTENSIONS for path in temp_paths)
        provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
        has_gemini_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
        use_gemini_images = provider == "gemini" or (
            provider != "ollama" and has_gemini_key
        )

        if all_images and use_gemini_images:
            set_job(job_id, status="generating", message="Résolution de l'exercice à partir des photos…")
            solution = solve_exercise_images(
                [
                    (path, uploaded_mime_from_extension(path.suffix))
                    for path in temp_paths
                ],
                instruction=instruction,
                exercise_number=exercise_number,
                selected_questions=solver_questions,
                help_mode=help_mode,
                selected_statement=selected_statement,
            )
        elif selected_statement:
            set_job(job_id, status="generating", message="Résolution de l’exercice et des questions choisies…")
            solution = solve_exercise_text(
                selected_statement,
                instruction=instruction,
                exercise_number=exercise_number,
                selected_questions=solver_questions,
                help_mode=help_mode,
            )
        else:
            extracted_pages = []
            for index, (path, original_name) in enumerate(saved_files, start=1):
                extension = path.suffix.lower()
                is_image = extension in IMAGE_EXTENSIONS
                try:
                    if is_image and use_gemini_images:
                        extracted = extract_image_text_gemini(
                            path,
                            mime_type=uploaded_mime_from_extension(extension),
                        ).strip()
                    else:
                        target_number = (
                            exercise_number
                            if len(saved_files) == 1 and is_image
                            else None
                        )
                        extracted = read_file(path, exercise_number=target_number).strip()
                    if extension == ".pdf" and len(extracted) < 60 and has_gemini_key:
                        set_job(job_id, status="reading", message=f"Lecture du PDF : {original_name}…")
                        extracted = extract_document_text_gemini(path).strip()
                except Exception as exc:
                    raise RuntimeError(
                        f"Impossible de lire {original_name} : {exc}"
                    ) from exc

                if extracted:
                    extracted_pages.append(
                        f"===== PAGE {index} : {original_name} =====\n\n{extracted}"
                    )

            exercise_text = "\n\n".join(extracted_pages).strip()
            if not exercise_text:
                raise RuntimeError(
                    "Aucun texte exploitable n'a été trouvé dans les fichiers. "
                    "Pour un PDF scanné, envoie les pages en photo ou active la lecture PDF Gemini."
                )

            set_job(job_id, status="generating", message="Résolution de l'exercice par l'IA…")
            solution = solve_exercise_text(
                exercise_text,
                instruction=instruction,
                exercise_number=exercise_number,
                selected_questions=solver_questions,
                help_mode=help_mode,
            )

        if not solution.strip():
            raise RuntimeError("L'IA n'a pas généré de résolution.")

        answer_number = _answer_exercise_number(solution)
        if exercise_number is not None and answer_number != exercise_number:
            raise RuntimeError(
                f"La réponse n’a pas confirmé l’exercice {exercise_number} demandé. "
                "Je l’ai bloquée pour éviter de t’afficher une correction possiblement mélangée. "
                "Vérifie le numéro ou envoie une photo plus nette."
            )

        other_exercises = {
            int(number)
            for number in re.findall(r"(?i)\b(?:exercice|exo)\s*(?:n\s*[°o.]?\s*)?(\d{1,3})\b", solution)
        }
        if exercise_number is not None and any(number != exercise_number for number in other_exercises):
            raise RuntimeError(
                "La réponse a mentionné un exercice voisin. Je l’ai bloquée : relance avec une photo plus nette ou recadrée."
            )

        session_key = make_id()
        with STATE_LOCK:
            expire_old_sessions_locked()
            EXERCISE_SESSIONS[session_key] = {
                "context": exercise_text or solution,
                "instruction": instruction,
                "initial_solution": solution,
                "exercise_number": exercise_number,
                "selected_questions": list(selected_questions or []),
                "history": [],
                "created": time.time(),
            }

        set_job(
            job_id,
            status="done",
            message="Exercice résolu !",
            result={
                "success": True,
                "solution": solution,
                "source": ", ".join(original_names),
                "session_id": session_key,
                "exercise_number": exercise_number,
            },
        )
    except Exception as exc:
        set_job(job_id, status="error", message=str(exc))
    finally:
        cleanup_temp(temp_paths)


GUIDES = {
    "reviser-efficacement": {
        "title": "Comment réviser efficacement : une méthode simple en 5 étapes",
        "description": "Une méthode concrète pour transformer un cours en rappels actifs, espacés et vérifiables.",
        "intro": "Relire plusieurs fois donne souvent une impression de familiarité, sans garantir qu’on saura retrouver l’information le jour du contrôle. Une révision utile alterne compréhension, rappel sans le cours et vérification.",
        "sections": [
            {"heading": "1. Clarifier ce qu’il faut savoir", "paragraphs": ["Repère les objectifs du chapitre, les définitions, les méthodes et les exemples vus en classe. Sépare les idées centrales des détails : cela donne un plan de travail et évite de recopier tout le cours.", "Si une consigne ou une notion reste floue, note une question précise à poser ou à résoudre avant d’apprendre par cœur."]},
            {"heading": "2. Faire une fiche qui sert à se tester", "paragraphs": ["Pour chaque partie, écris une définition courte, une propriété ou méthode, puis un exemple qui montre comment l’utiliser. Une fiche utile rassemble les liens entre les notions sans recopier tout le chapitre.", "Ajoute quelques questions sans réponse : « Quelle condition faut-il vérifier ? », « Pourquoi cette étape est-elle valide ? », « Quel piège faut-il éviter ? »"]},
            {"heading": "3. Pratiquer le rappel actif", "paragraphs": ["Cache le cours et essaie de restituer une définition, un schéma ou les étapes d’une méthode. Compare ensuite avec la source et corrige ce qui manquait.", "Le rappel actif est plus exigeant qu’une relecture, mais il révèle les points à retravailler avant le contrôle."]},
            {"heading": "4. Espacer les séances", "paragraphs": ["Revois une notion peu après l’avoir étudiée, puis quelques jours plus tard et de nouveau avant l’évaluation. Si tu te trompes, rapproche la prochaine révision ; si tu réussis sans aide, espace-la davantage.", "Une séance courte et régulière est souvent plus facile à tenir qu’une longue séance la veille."]},
            {"heading": "5. Finir par des exercices", "paragraphs": ["Essaie sans regarder la correction, écris tes étapes, puis repère la première ligne où ton raisonnement diverge. Cela permet de distinguer une erreur de calcul d’une méthode mal choisie.", "Reprends ensuite les étapes difficiles avec une méthode plus simple ou demande une explication précise."]},
        ],
    },
    "resoudre-exercice": {
        "title": "Comment choisir et résoudre un exercice à partir d’une photo ou d’un fichier",
        "description": "Conseils pour envoyer un énoncé lisible, choisir un exercice précis et vérifier une correction étape par étape.",
        "intro": "Une page peut contenir plusieurs exercices et un énoncé peut continuer sur plusieurs pages. Donner un numéro précis et envoyer les photos dans l’ordre aide à isoler la bonne consigne ; une image lisible et une vérification des hypothèses restent essentielles.",
        "sections": [
            {"heading": "Avant l’envoi", "paragraphs": ["Pose la page à plat, évite l’ombre portée et garde les bords de l’exercice visibles. Vérifie que les indices, signes, fractions, unités et lettres du schéma sont lisibles.", "Si l’énoncé couvre plusieurs pages, sélectionne toutes les photos ensemble dans l’ordre de lecture : elles seront envoyées comme un seul exercice. Après l’analyse, choisis le numéro repéré et coche les questions à traiter. Si aucun numéro n’est reconnu, saisis-le manuellement. Le recadrage reste facultatif."]},
            {"heading": "Formats pris en charge", "paragraphs": ["Le mode exercice accepte les images JPG, JPEG, PNG et WEBP, ainsi que les documents PDF, DOCX, ODT, PPTX et TXT. Les documents doivent contenir du texte exploitable. Un PDF composé uniquement de pages scannées peut nécessiter une photo de la page ou la lecture PDF par Gemini lorsque cette option est configurée."]},
            {"heading": "Lire la réponse avec méthode", "paragraphs": ["Commence par vérifier que le numéro et la consigne repris par la réponse correspondent à ta demande. Compare ensuite les données de départ, les formules utilisées et les unités ou coefficients.", "Dans un exercice de maths, demande à l’IA d’expliquer une étape ou une autre méthode au lieu de recopier une réponse sans la comprendre. Tu peux poursuivre la conversation dans le mode exercice."]},
            {"heading": "Que faire si l’énoncé est mal lu ?", "paragraphs": ["Corrige les caractères ambigus dans ta consigne (par exemple AB ou AD, un signe moins, un exposant ou un point décimal) et renvoie une image plus nette si une donnée manque. Une solution ne doit pas inventer le contenu illisible.", "Les réponses sont générées automatiquement : compare-les au cours et aux attentes de ton enseignant. RéviAI sert d’aide à l’apprentissage, pas de validation officielle."]},
        ],
    },
    "reussir-un-quiz": {
        "title": "Réussir un quiz de révision et apprendre de ses erreurs",
        "description": "Une méthode pour répondre sans le cours, comprendre les erreurs et retenir les notions après un quiz.",
        "intro": "Un quiz est utile quand il révèle ce que tu sais rappeler seul et ce que tu dois revoir. Le score indique un point de départ ; l’analyse de chaque réponse transforme l’entraînement en apprentissage.",
        "sections": [
            {"heading": "Avant de commencer", "paragraphs": ["Choisis un chapitre précis et ferme ton cours. Réponds de mémoire, sans chercher une réponse au hasard en relisant les choix plusieurs fois.", "Pour chaque question, essaie de formuler ta réponse avant de regarder les propositions. Cette étape t’aide à distinguer un vrai rappel d’une réponse simplement familière."]},
            {"heading": "Après chaque réponse", "paragraphs": ["Lis l’explication même quand ta réponse est juste. Demande-toi quelle règle justifie la bonne réponse et pourquoi les autres choix ne conviennent pas.", "Quand tu te trompes, note la notion testée et la raison de l’erreur : définition oubliée, confusion entre deux idées ou étape de méthode manquante."]},
            {"heading": "Transformer le score en plan", "paragraphs": ["Classe les notions en trois groupes : maîtrisées, incertaines et à reprendre. Recommence par les notions incertaines plutôt que de relancer immédiatement le même quiz.", "Reviens ensuite aux questions ratées après une autre activité ou une pause. Essaie de justifier la réponse sans aide avant de considérer la notion acquise."]},
            {"heading": "Éviter les faux progrès", "paragraphs": ["Un score élevé sur les mêmes questions peut venir du souvenir des réponses. Pour vérifier ce que tu as retenu, reformule la notion, donne un exemple différent ou demande un nouveau quiz sur le même cours.", "Garde aussi une trace des résultats dans ta bibliothèque RéviAI sur cet appareil afin de voir si tes résultats progressent au fil des séances."]},
        ],
    },
    "organiser-ses-revisions": {
        "title": "Organiser ses révisions avec un planning simple",
        "description": "Découper un chapitre, prévoir des séances courtes et ajuster le planning selon les notions maîtrisées.",
        "intro": "Un planning de révision sert à décider quoi travailler avant d’ouvrir son cours. Il doit rester assez simple pour être suivi et assez souple pour laisser plus de temps aux notions difficiles.",
        "sections": [
            {"heading": "Faire l’inventaire", "paragraphs": ["Écris les matières, les chapitres et les dates des évaluations. Pour chaque chapitre, indique ce qu’il faut savoir : vocabulaire, dates, raisonnements, formules ou types d’exercices.", "Découpe les gros chapitres en petites tâches observables, comme « expliquer le cycle de l’eau sans le cours » ou « résoudre deux équations en justifiant chaque étape ». "]},
            {"heading": "Prévoir des séances réalisables", "paragraphs": ["Choisis quelques créneaux courts dans la semaine et donne à chacun un objectif précis. Alterne les matières lorsque plusieurs contrôles approchent pour éviter de passer toute la séance à relire un seul cours.", "Commence par une tâche importante mais assez petite pour être terminée. Prévois une marge pour un imprévu ou un exercice plus long que prévu."]},
            {"heading": "Ajuster après l’entraînement", "paragraphs": ["À la fin de la séance, essaie de restituer l’essentiel sans regarder puis vérifie avec le cours. Déplace vers une prochaine séance les notions qui restent difficiles.", "Si une notion est bien rappelée et appliquée, garde une vérification plus courte pour plus tard. Le planning doit suivre tes résultats, pas seulement le temps déjà passé à étudier."]},
            {"heading": "La veille du contrôle", "paragraphs": ["Utilise la dernière séance pour retrouver les idées principales et refaire quelques exercices représentatifs. Note les erreurs encore présentes et relis les explications correspondantes.", "Garde un horaire de fin réaliste. Une nuit écourtée pour relire tout le chapitre peut rendre la concentration et le rappel plus difficiles le lendemain."]},
        ],
    },
    "reviser-les-sciences": {
        "title": "Faire une fiche de sciences qui aide à comprendre un chapitre",
        "description": "Organiser les définitions, les observations, les mécanismes et les schémas d’un cours de SVT ou de physique-chimie.",
        "intro": "En sciences, une fiche utile montre les liens entre une observation, une explication et une conclusion. Une liste de mots-clés seule ne suffit pas toujours à reconstruire le raisonnement du cours.",
        "sections": [
            {"heading": "Séparer les éléments du raisonnement", "paragraphs": ["Pour chaque partie, distingue le fait observé, la méthode ou l’expérience qui le met en évidence, puis l’interprétation retenue dans le cours.", "Garde les conditions importantes : organisme étudié, milieu, température, unité, échelle ou limites de l’expérience. Elles peuvent changer la conclusion."]},
            {"heading": "Relier les étapes", "paragraphs": ["Présente un mécanisme dans l’ordre avec des flèches et des verbes précis : « augmente », « se transforme », « est transporté » ou « permet ». Vérifie que chaque flèche exprime une relation présente dans le cours.", "Si plusieurs causes contribuent à un phénomène, écris-les séparément avant de montrer leurs conséquences. Cela évite de réduire une explication à une seule cause."]},
            {"heading": "Garder les formules et les unités", "paragraphs": ["Recopie une formule avec ses symboles, ses unités et les conditions d’application données par le professeur. Ajoute une courte phrase qui explique ce que représente chaque grandeur.", "Pour une réaction chimique, vérifie le nombre d’atomes de chaque élément des deux côtés et distingue réactifs et produits."]},
            {"heading": "Se tester sans la fiche", "paragraphs": ["Cache une partie du schéma et essaie de retrouver les légendes. Explique ensuite le mécanisme à voix haute en reliant chaque étape à une observation ou à une donnée.", "Termine par une question de transfert : que changerait une condition différente ? Réponds seulement si le cours fournit les connaissances nécessaires pour le justifier."]},
        ],
    },
}


def public_base_url():
    configured_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    return (configured_url or DEFAULT_PUBLIC_BASE_URL).rstrip("/")


def ad_settings():
    enabled = (
        os.getenv("ADS_ENABLED", "false").strip().lower() == "true"
        and os.getenv("ADS_CONSENT_READY", "false").strip().lower() == "true"
    )
    client_id = os.getenv("ADSENSE_CLIENT_ID", "").strip()
    slot_id = os.getenv("ADSENSE_SLOT_ID", "").strip()
    publisher = os.getenv("ADSENSE_PUBLISHER_ID", "pub-3292344498702156").strip()
    client_match = re.fullmatch(r"ca-pub-(\d{16})", client_id)
    publisher_match = re.fullmatch(r"pub-(\d{16})", publisher)
    enabled = (
        enabled and bool(client_match) and bool(re.fullmatch(r"\d{6,12}", slot_id))
        and bool(publisher_match)
        and client_match.group(1) == publisher_match.group(1)
    )
    return enabled, client_id, slot_id


@app.route("/")
def home():
    # La page d'accueil sert à importer, générer des fiches et résoudre des
    # exercices ; les emplacements publicitaires sont réservés aux guides.
    account = billing_account(session.get("user_id"))
    return render_template("index.html", user_has_plus=account_has_plus(account))


@app.route("/ads.txt")
def ads_txt():
    publisher = os.getenv("ADSENSE_PUBLISHER_ID", "pub-3292344498702156").strip()
    if not re.fullmatch(r"pub-\d{16}", publisher):
        publisher = "pub-3292344498702156"
    return (
        f"google.com, {publisher}, DIRECT, f08c47fec0942fa0\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/robots.txt")
def robots_txt():
    return (
        f"User-agent: *\nAllow: /\nSitemap: {public_base_url()}/sitemap.xml\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/sitemap.xml")
def sitemap():
    pages = [
        "/", "/guides", *[f"/guides/{slug}" for slug in GUIDES],
        "/a-propos", "/faq", "/privacy", "/terms",
    ]
    urls = "".join(
        f"<url><loc>{public_base_url()}{path}</loc></url>" for path in pages
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + urls
        + "</urlset>",
        200,
        {"Content-Type": "application/xml; charset=utf-8"},
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/a-propos")
def about():
    return render_template("about.html")


@app.route("/guides")
def guides():
    return render_template("guides.html", guides=GUIDES)


@app.route("/guides/<slug>")
def guide(slug):
    guide_content = GUIDES.get(slug)
    if guide_content is None:
        return render_template("not_found.html"), 404
    ads_enabled, ads_client_id, ads_slot_id = ad_settings()
    return render_template(
        "guide.html",
        guide=guide_content,
        slug=slug,
        show_ads=True,
        ads_enabled=ads_enabled,
        ads_client_id=ads_client_id,
        ads_slot_id=ads_slot_id,
    )


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.after_request
def protect_admin_responses(response):
    if request.path.startswith(("/compte", "/connexion", "/inscription", "/mot-de-passe")):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["Referrer-Policy"] = "no-referrer"
    if request.path == "/stripe/webhook":
        response.headers["Cache-Control"] = "no-store"
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    if (
        getattr(g, "reviai_browser_cookie_new", False)
        and (request.path == "/" or request.path.startswith("/api/"))
    ):
        response.set_cookie(
            "reviai_browser",
            g.reviai_browser_id,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            secure=app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
            path="/",
        )
    return response


@app.context_processor
def inject_account_navigation():
    return {
        "account_signed_in": bool(session.get("user_id")),
        "admin_signed_in": bool(session.get("admin_authenticated")),
    }


@app.route("/plus")
def plus_page():
    account = billing_account(session.get("user_id"))
    return render_template(
        "plus.html", billing_ready=billing_ready(),
        user_has_plus=account_has_plus(account),
    )


@app.route("/inscription", methods=["GET", "POST"])
def register_account():
    if session.get("user_id"):
        return redirect(url_for("account_page"))
    error = ""
    success = ""
    if request.method == "POST":
        if not valid_account_csrf(request.form.get("csrf_token", "")):
            error = "La session a expiré. Recharge la page et réessaie."
        elif not billing_ready():
            error = "Les abonnements ne sont pas encore configurés par l’éditeur. Réessaie plus tard."
        else:
            email = str(request.form.get("email", "")).strip().lower()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            accepted = request.form.get("accept_terms") == "on"
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
                error = "Saisis une adresse e-mail valide."
            elif len(password) < 10 or len(password) > 128:
                error = "Choisis un mot de passe de 10 à 128 caractères."
            elif password != confirmation:
                error = "Les deux mots de passe ne correspondent pas."
            elif not accepted:
                error = "Accepte les conditions d’utilisation et la politique de confidentialité pour continuer."
            else:
                account_id = secrets.token_urlsafe(18)
                try:
                    with feedback_database_connection() as connection:
                        ensure_billing_tables(connection)
                        connection.execute(
                            f"""INSERT INTO {ACCOUNT_TABLE}
                                (id, email, password_hash, terms_accepted_at, terms_version)
                                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s)""",
                            (account_id, email, generate_password_hash(password), TERMS_VERSION),
                        )
                    if send_verification_email(account_id, email):
                        return render_template("account_message.html", title="Vérifie ton adresse e-mail", message="Nous venons d’envoyer un lien de confirmation valable 24 heures. Clique dessus avant de te connecter.", resend=True, csrf_token=account_csrf_token())
                    error = "Le compte a été créé, mais l’e-mail n’a pas pu être envoyé. Vérifie la configuration e-mail puis demande un nouvel envoi depuis la connexion."
                except Exception:
                    app.logger.exception("Création de compte RéviAI impossible.")
                    error = "Impossible de créer ce compte. Si tu en as déjà un, connecte-toi ou réinitialise ton mot de passe."
    return render_template(
        "account_form.html", mode="register", error=error, success=success,
        csrf_token=account_csrf_token(), billing_ready=billing_ready(),
    )


@app.route("/connexion", methods=["GET", "POST"])
def login_account():
    if session.get("user_id"):
        return redirect(url_for("account_page"))
    error = ""
    message = ""
    if request.args.get("status") == "verified":
        message = "Adresse confirmée. Tu peux maintenant te connecter."
    elif request.args.get("status") == "reset":
        message = "Mot de passe modifié. Connecte-toi avec le nouveau."
    elif request.args.get("status") == "resent":
        message = "Si un compte non confirmé correspond à cette adresse, un nouveau lien vient d’être envoyé."
    if request.method == "POST":
        email = str(request.form.get("email", "")).strip().lower()
        password = request.form.get("password", "")
        if not valid_account_csrf(request.form.get("csrf_token", "")):
            error = "La session a expiré. Recharge la page et réessaie."
        else:
            account = get_account_for_login(email)
            if account and check_password_hash(account["password_hash"], password):
                if not account["email_verified"]:
                    error = "Confirme ton adresse e-mail avant de te connecter. Tu peux demander un nouveau lien ci-dessous."
                else:
                    session.clear()
                    session["user_id"] = account["id"]
                    session["account_csrf_token"] = secrets.token_urlsafe(32)
                    session.permanent = True
                    return redirect(url_for("account_page"))
            else:
                error = "Adresse e-mail ou mot de passe incorrect."
    return render_template(
        "account_form.html", mode="login", error=error, success=message,
        csrf_token=account_csrf_token(), billing_ready=billing_ready(),
    )


@app.route("/compte/verifier")
def verify_account():
    token = str(request.args.get("token", ""))[:200]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    if not token_hash or not billing_database_ready():
        return render_template("account_message.html", title="Lien invalide", message="Ce lien est invalide ou a expiré. Demande un nouvel e-mail de confirmation.", resend=True, csrf_token=account_csrf_token()), 400
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            row = connection.execute(
                f"""UPDATE {ACCOUNT_TABLE}
                    SET email_verified = TRUE,
                        verification_token_hash = NULL,
                        verification_expires_at = NULL
                    WHERE verification_token_hash = %s
                      AND verification_expires_at > CURRENT_TIMESTAMP
                    RETURNING id""",
                (token_hash,),
            ).fetchone()
    except Exception:
        app.logger.exception("Confirmation d'adresse e-mail impossible.")
        row = None
    if not row:
        return render_template("account_message.html", title="Lien invalide", message="Ce lien est invalide ou a expiré. Demande un nouvel e-mail de confirmation.", resend=True, csrf_token=account_csrf_token()), 400
    session.clear()
    session["user_id"] = row[0]
    session["account_csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return redirect(url_for("account_page", status="verified"))


@app.route("/compte/verifier/renvoyer", methods=["POST"])
def resend_verification():
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    email = str(request.form.get("email", "")).strip().lower()
    account = get_account_for_login(email)
    if account and not account["email_verified"]:
        if send_verification_email(account["id"], email):
            return redirect(url_for("login_account", status="resent"))
    return redirect(url_for("login_account", status="resent"))


@app.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot_password():
    message = ""
    error = ""
    if request.method == "POST":
        if not valid_account_csrf(request.form.get("csrf_token", "")):
            error = "La session a expiré. Recharge la page et réessaie."
        else:
            email = str(request.form.get("email", "")).strip().lower()
            account = get_account_for_login(email)
            if account and account["email_verified"]:
                token = secrets.token_urlsafe(32)
                stored_email = set_account_token(account["id"], "reset", token, 30)
                if stored_email:
                    link = account_link("reset_password", token)
                    send_account_email(
                        email,
                        "Réinitialise ton mot de passe RéviAI",
                        f"<p>Pour choisir un nouveau mot de passe, utilise ce lien valable 30 minutes :</p><p><a href=\"{link}\">Réinitialiser mon mot de passe</a></p><p>Si tu n'es pas à l'origine de cette demande, ignore ce message.</p>",
                        f"Réinitialise ton mot de passe RéviAI dans les 30 minutes : {link}\n\nSi tu n'es pas à l'origine de cette demande, ignore ce message.",
                    )
            message = "Si un compte confirmé correspond à cette adresse, un lien de réinitialisation vient d’être envoyé."
    return render_template("account_form.html", mode="forgot", error=error, success=message, csrf_token=account_csrf_token(), billing_ready=billing_ready())


@app.route("/mot-de-passe-reinitialiser", methods=["GET", "POST"])
def reset_password():
    token = str(request.values.get("token", ""))[:200]
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""
    error = ""
    if request.method == "POST":
        if not valid_account_csrf(request.form.get("csrf_token", "")):
            error = "La session a expiré. Recharge la page et réessaie."
        else:
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            if len(password) < 10 or len(password) > 128:
                error = "Choisis un mot de passe de 10 à 128 caractères."
            elif password != confirmation:
                error = "Les deux mots de passe ne correspondent pas."
            elif not token_hash or not billing_database_ready():
                error = "Ce lien est invalide ou a expiré."
            else:
                try:
                    with feedback_database_connection() as connection:
                        ensure_billing_tables(connection)
                        row = connection.execute(
                            f"""UPDATE {ACCOUNT_TABLE}
                                SET password_hash = %s, reset_token_hash = NULL, reset_expires_at = NULL
                                WHERE reset_token_hash = %s AND reset_expires_at > CURRENT_TIMESTAMP
                                RETURNING id""",
                            (generate_password_hash(password), token_hash),
                        ).fetchone()
                    if row:
                        return redirect(url_for("login_account", status="reset"))
                    error = "Ce lien est invalide ou a expiré."
                except Exception:
                    app.logger.exception("Réinitialisation de mot de passe impossible.")
                    error = "Impossible de modifier le mot de passe pour le moment. Réessaie plus tard."
    if not token_hash:
        error = "Ce lien est invalide ou a expiré."
    return render_template("account_form.html", mode="reset", error=error, success="", csrf_token=account_csrf_token(), reset_token=token_hash and token or "", billing_ready=billing_ready())


@app.route("/compte")
def account_page():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login_account", next="/compte"))
    account = billing_account(user_id)
    if not account or not account["email_verified"]:
        session.clear()
        return redirect(url_for("login_account"))
    checkout_id = str(request.args.get("session_id", ""))
    if checkout_id and billing_ready():
        try:
            stripe_configure()
            checkout = stripe.checkout.Session.retrieve(checkout_id)
            if str(stripe_field(checkout, "client_reference_id", "")) == str(user_id):
                subscription_id = stripe_id(stripe_field(checkout, "subscription"))
                if subscription_id:
                    apply_subscription_update(stripe.Subscription.retrieve(subscription_id))
                    account = billing_account(user_id) or account
        except Exception:
            app.logger.exception("Vérification de la session de paiement impossible.")
    return render_template(
        "account.html", account=account, has_plus=account_has_plus(account),
        is_owner=bool(session.get("admin_authenticated")),
        csrf_token=account_csrf_token(), billing_ready=billing_ready(),
        status=request.args.get("status", ""), payment=request.args.get("payment", ""),
    )


@app.route("/compte/abonnement", methods=["POST"])
def start_subscription():
    if not session.get("user_id"):
        return redirect(url_for("login_account"))
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    if request.form.get("accept_subscription") != "on":
        return redirect(url_for("account_page", status="consent_required"))
    account = billing_account(session.get("user_id"))
    if not account or not account["email_verified"]:
        return redirect(url_for("login_account"))
    if account_has_plus(account):
        return redirect(url_for("account_page"))
    if account.get("stripe_subscription_id") and account.get("subscription_status") not in {"canceled", "incomplete_expired"}:
        return redirect(url_for("account_page", status="existing_subscription"))
    if not billing_ready():
        return redirect(url_for("plus_page", status="unavailable"))
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            connection.execute(
                f"UPDATE {ACCOUNT_TABLE} SET subscription_consent_at = CURRENT_TIMESTAMP, terms_version = %s WHERE id = %s",
                (TERMS_VERSION, str(account["id"])),
            )
        stripe_configure()
        checkout_args = {
            "mode": "subscription",
            "locale": "fr",
            "payment_method_types": ["card"],
            "line_items": [{
                "price_data": {
                    "currency": "eur",
                    "unit_amount": PLUS_PRICE_CENTS,
                    "recurring": {"interval": "month"},
                    "product_data": {"name": "RéviAI Plus"},
                },
                "quantity": 1,
            }],
            "client_reference_id": str(account["id"]),
            "metadata": {"reviai_user_id": str(account["id"])},
            "subscription_data": {"metadata": {"reviai_user_id": str(account["id"])}},
            "success_url": f"{public_base_url()}{url_for('account_page')}?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{public_base_url()}{url_for('plus_page')}?payment=cancelled",
        }
        if account.get("stripe_customer_id"):
            checkout_args["customer"] = account["stripe_customer_id"]
        else:
            checkout_args["customer_email"] = account["email"]
        checkout = stripe.checkout.Session.create(**checkout_args)
        return redirect(checkout.url, code=303)
    except Exception:
        app.logger.exception("Création de paiement Plus impossible.")
        return redirect(url_for("plus_page", status="payment_error"))


@app.route("/compte/abonnement/resilier", methods=["POST"])
def cancel_subscription():
    if not session.get("user_id"):
        return redirect(url_for("login_account"))
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    account = billing_account(session.get("user_id"))
    if not account or not account.get("stripe_subscription_id") or not billing_ready():
        return redirect(url_for("account_page", status="cancel_error"))
    try:
        stripe_configure()
        if account.get("subscription_status") in {"incomplete", "unpaid", "paused"}:
            updated = stripe.Subscription.cancel(account["stripe_subscription_id"])
        else:
            updated = stripe.Subscription.modify(
                account["stripe_subscription_id"], cancel_at_period_end=True,
            )
        apply_subscription_update(updated)
        result_status = "cancelled_now" if stripe_field(updated, "status") == "canceled" else "cancel_scheduled"
        return redirect(url_for("account_page", status=result_status))
    except Exception:
        app.logger.exception("Résiliation à échéance impossible.")
        return redirect(url_for("account_page", status="cancel_error"))


@app.route("/compte/facturation", methods=["POST"])
def billing_portal():
    if not session.get("user_id"):
        return redirect(url_for("login_account"))
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    account = billing_account(session.get("user_id"))
    if not account or not account.get("stripe_customer_id") or not billing_ready():
        return redirect(url_for("account_page", status="portal_error"))
    try:
        stripe_configure()
        portal = stripe.billing_portal.Session.create(
            customer=account["stripe_customer_id"],
            return_url=f"{public_base_url()}{url_for('account_page')}",
        )
        return redirect(portal.url, code=303)
    except Exception:
        app.logger.exception("Ouverture du portail de facturation impossible.")
        return redirect(url_for("account_page", status="portal_error"))


@app.route("/compte/deconnexion", methods=["POST"])
def logout_account():
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    session.clear()
    return redirect(url_for("home"))


@app.route("/compte/supprimer", methods=["POST"])
def delete_account():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login_account"))
    if not valid_account_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page puis réessaie.", 400
    password = request.form.get("password", "")
    try:
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            row = connection.execute(
                f"SELECT password_hash, stripe_subscription_id, subscription_status FROM {ACCOUNT_TABLE} WHERE id = %s",
                (str(user_id),),
            ).fetchone()
        if not row or not check_password_hash(row[0], password):
            return redirect(url_for("account_page", status="delete_error"))
        subscription_id = row[1]
        subscription_status = row[2]
        if subscription_id and subscription_status not in {"canceled", "incomplete_expired"}:
            if not billing_ready():
                return redirect(url_for("account_page", status="delete_error"))
            stripe_configure()
            stripe.Subscription.cancel(subscription_id)
        with feedback_database_connection() as connection:
            ensure_billing_tables(connection)
            connection.execute(f"DELETE FROM {ACCOUNT_TABLE} WHERE id = %s", (str(user_id),))
    except Exception:
        app.logger.exception("Suppression de compte impossible.")
        return redirect(url_for("account_page", status="delete_error"))
    session.clear()
    return render_template(
        "account_message.html", title="Compte supprimé",
        message="Ton compte RéviAI a été supprimé. Tout abonnement associé a été résilié immédiatement. Stripe peut conserver les factures et certaines données de transaction selon ses obligations.",
        resend=False,
    )


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    if stripe is None or not STRIPE_WEBHOOK_SECRET or not billing_database_ready():
        return jsonify({"error": "Webhook Stripe non configuré."}), 503
    try:
        stripe_configure()
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""),
            STRIPE_WEBHOOK_SECRET,
        )
    except Exception:
        return jsonify({"error": "Signature Stripe invalide."}), 400
    try:
        if not process_stripe_event(event):
            return jsonify({"error": "Événement Stripe invalide."}), 400
    except Exception:
        return jsonify({"error": "Traitement temporairement indisponible."}), 500
    return jsonify({"received": True})


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        return render_template("admin_login.html", configured=False), 503
    if session.get("admin_authenticated"):
        return redirect(url_for("admin_feedback"))

    error = ""
    status_code = 200
    if request.method == "POST":
        if not valid_admin_csrf(request.form.get("csrf_token", "")):
            error = "La session a expiré. Recharge la page puis réessaie."
            status_code = 400
        elif hmac.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin_authenticated"] = True
            session["admin_csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(url_for("admin_feedback"))
        else:
            error = "Mot de passe incorrect."
            status_code = 401

    return render_template(
        "admin_login.html",
        configured=True,
        error=error,
        csrf_token=admin_csrf_token(),
    ), status_code


@app.route("/admin/signalements")
def admin_feedback():
    if not ADMIN_PASSWORD:
        return render_template("admin_login.html", configured=False), 503
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))

    try:
        reports = list_feedback_reports()
        storage_error = ""
    except FeedbackStorageError:
        app.logger.exception("Lecture des signalements impossible")
        reports = []
        storage_error = "Impossible de charger les signalements. Vérifie la connexion au stockage configuré."

    return render_template(
        "admin_feedback.html",
        reports=reports,
        csrf_token=admin_csrf_token(),
        persistent_storage=bool(DATABASE_URL),
        database_required=FEEDBACK_DATABASE_REQUIRED,
        storage_error=storage_error,
        status=request.args.get("status", ""),
    )


@app.route("/admin/statistiques")
def admin_analytics():
    if not ADMIN_PASSWORD:
        return render_template("admin_login.html", configured=False), 503
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    metrics, metrics_error = recent_usage_metrics()
    return render_template(
        "admin_analytics.html",
        metrics=metrics,
        metrics_error=metrics_error,
        csrf_token=admin_csrf_token(),
    )


@app.route("/admin/signalements/<report_id>/supprimer", methods=["POST"])
def admin_delete_feedback(report_id):
    if not ADMIN_PASSWORD:
        return render_template("admin_login.html", configured=False), 503
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    if not valid_admin_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page et réessaie.", 400

    try:
        deleted = delete_feedback_report(report_id)
    except FeedbackStorageError:
        app.logger.exception("Suppression d'un signalement impossible")
        return redirect(url_for("admin_feedback", status="error"))
    return redirect(url_for("admin_feedback", status="deleted" if deleted else "missing"))


@app.route("/admin/deconnexion", methods=["POST"])
def admin_logout():
    if not valid_admin_csrf(request.form.get("csrf_token", "")):
        return "Session expirée. Recharge la page et réessaie.", 400
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/usage")
def usage():
    unlimited = has_unlimited_access()
    plan = "éditeur" if session.get("admin_authenticated") else ("plus" if unlimited else "gratuit")
    return jsonify({"limits": daily_usage_snapshot(), "unlimited": unlimited, "plan": plan})


@app.route("/api/generate", methods=["POST"])
def generate():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Aucun fichier n'a été sélectionné."}), 400

    saved_files = []

    try:
        for uploaded in files:
            if not uploaded or not uploaded.filename:
                continue

            original_name = uploaded.filename
            extension = Path(original_name).suffix.lower()

            # Certains navigateurs mobiles envoient correctement le type MIME
            # même lorsque le nom de fichier est atypique.
            if extension not in ALLOWED_EXTENSIONS:
                mime_extension = MIME_TO_EXTENSION.get((uploaded.mimetype or "").lower())
                if mime_extension:
                    extension = mime_extension

            if extension not in ALLOWED_EXTENSIONS:
                cleanup_temp([item[0] for item in saved_files])
                return jsonify({
                    "error": (
                        f"Format non supporté : {extension or 'inconnu'}. "
                        "Formats acceptés : PDF, DOCX, ODT, PPTX, TXT, JPG, JPEG, PNG et WEBP."
                    )
                }), 400

            safe_original = secure_filename(original_name)
            if not safe_original or Path(safe_original).suffix.lower() not in ALLOWED_EXTENSIONS:
                safe_original = f"document{extension}"

            filename = safe_original
            destination = UPLOAD_DIR / f"{secrets.token_hex(6)}_{filename}"
            uploaded.save(destination)
            saved_files.append((destination, original_name))

        if not saved_files:
            return jsonify({"error": "Aucun fichier valide n'a été envoyé."}), 400

        allowed, used, limit = consume_daily_usage("revision")
        if not allowed:
            cleanup_temp([item[0] for item in saved_files])
            return jsonify({
                "error": f"Tu as utilisé tes {limit} fiches gratuites aujourd’hui. Tu pourras en créer d’autres demain.",
                "limits": daily_usage_snapshot(),
            }), 429

        job_id = make_id()

        with STATE_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "message": "Préparation du traitement…",
                "created": time.time(),
                "result": None,
            }

        EXECUTOR.submit(process_generation, job_id, saved_files)

        return jsonify({"success": True, "job_id": job_id})

    except Exception as exc:
        cleanup_temp([item[0] for item in saved_files])
        return jsonify({"error": str(exc)}), 500


@app.route("/api/generate-status/<job_id>")
@app.route("/api/exercise-status/<job_id>")
def generation_status(job_id):
    with STATE_LOCK:
        expire_old_sessions_locked()
        job = JOBS.get(job_id)

    if not job:
        return jsonify({"error": "Traitement introuvable."}), 404

    response = {
        "status": job["status"],
        "message": job["message"],
    }

    if job["status"] == "done":
        response["result"] = job["result"]

    return jsonify(response)


@app.route("/api/detect-exercises", methods=["POST"])
def detect_exercises():
    uploaded_files = request.files.getlist("photos")
    uploaded_files = [item for item in uploaded_files if item and item.filename]
    if not uploaded_files:
        return jsonify({"error": "Ajoute au moins une photo ou un document à analyser."}), 400

    saved_files = []
    try:
        for uploaded in uploaded_files:
            original_name = uploaded.filename
            extension = Path(original_name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                extension = MIME_TO_EXTENSION.get((uploaded.mimetype or "").lower(), extension)
            if extension not in ALLOWED_EXTENSIONS:
                cleanup_temp([item[0] for item in saved_files])
                return jsonify({"error": f"Format non pris en charge : {original_name}."}), 400

            safe_name = secure_filename(original_name)
            if not safe_name or Path(safe_name).suffix.lower() not in ALLOWED_EXTENSIONS:
                safe_name = f"exercice{extension}"
            destination = UPLOAD_DIR / f"repere_{secrets.token_hex(6)}_{safe_name}"
            uploaded.save(destination)
            saved_files.append((destination, original_name))

        allowed, used, limit = consume_daily_usage("detection")
        if not allowed:
            cleanup_temp([item[0] for item in saved_files])
            return jsonify({
                "error": f"Tu as utilisé tes {limit} repérages gratuits aujourd’hui. Tu pourras en analyser d’autres demain.",
                "limits": daily_usage_snapshot(),
            }), 429

        job_id = make_id()
        with STATE_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "message": "Préparation du repérage…",
                "created": time.time(),
                "result": None,
            }
        EXECUTOR.submit(process_exercise_detection, job_id, saved_files)
        return jsonify({"success": True, "job_id": job_id})
    except Exception as exc:
        cleanup_temp([item[0] for item in saved_files])
        return jsonify({"error": str(exc)}), 500


@app.route("/api/solve-exercise", methods=["POST"])
def solve_exercise():
    uploaded_files = request.files.getlist("photos")
    if not uploaded_files:
        uploaded_files = request.files.getlist("photo")
    uploaded_files = [item for item in uploaded_files if item and item.filename]
    if not uploaded_files:
        return jsonify({"error": "Choisis au moins une photo ou un document de ton exercice."}), 400

    instruction = str(request.form.get("instruction", "")).strip()[:1000]
    raw_number = str(request.form.get("exercise_number", "")).strip()
    exercise_number = int(raw_number) if re.fullmatch(r"\d{1,3}", raw_number) else None
    if exercise_number is None:
        exercise_number = _requested_exercise_number(instruction)
    if exercise_number is None:
        return jsonify({"error": "Choisis un exercice détecté ou saisis son numéro."}), 400

    selected_questions = []
    for item in request.form.getlist("questions")[:20]:
        question = str(item).strip().lower()
        if re.fullmatch(r"\d{1,2}[a-z]?", question) and question not in selected_questions:
            selected_questions.append(question)
    help_mode = str(request.form.get("help_mode", "complete")).strip()
    if help_mode not in {"hint", "step_by_step", "complete"}:
        help_mode = "complete"
    detection_id = str(request.form.get("detection_id", "")).strip()[:100]

    saved_files = []
    try:
        for uploaded in uploaded_files:
            original_name = uploaded.filename
            extension = Path(original_name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                mime_extension = MIME_TO_EXTENSION.get((uploaded.mimetype or "").lower())
                if mime_extension:
                    extension = mime_extension
            if extension not in ALLOWED_EXTENSIONS:
                cleanup_temp([item[0] for item in saved_files])
                return jsonify({
                    "error": (
                        f"Format non supporté pour {original_name}. "
                        "Envoie un PDF, DOCX, ODT, PPTX, TXT ou une image JPG, PNG ou WEBP."
                    )
                }), 400

            safe_name = secure_filename(original_name)
            if not safe_name or Path(safe_name).suffix.lower() not in ALLOWED_EXTENSIONS:
                safe_name = f"exercice{extension}"
            destination = UPLOAD_DIR / f"exercice_{secrets.token_hex(6)}_{safe_name}"
            uploaded.save(destination)
            saved_files.append((destination, original_name))

        allowed, used, limit = consume_daily_usage("exercise")
        if not allowed:
            cleanup_temp([item[0] for item in saved_files])
            return jsonify({
                "error": f"Tu as utilisé tes {limit} corrections gratuites aujourd’hui. Tu pourras en résoudre d’autres demain.",
                "limits": daily_usage_snapshot(),
            }), 429

        job_id = make_id()
        with STATE_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "message": "Préparation de la résolution…",
                "created": time.time(),
                "result": None,
            }

        EXECUTOR.submit(
            process_exercise,
            job_id,
            saved_files,
            instruction,
            exercise_number,
            selected_questions,
            help_mode,
            detection_id,
        )
        return jsonify({"success": True, "job_id": job_id})
    except Exception as exc:
        cleanup_temp([item[0] for item in saved_files])
        return jsonify({"error": str(exc)}), 500


@app.route("/api/exercise-chat", methods=["POST"])
def exercise_chat():
    payload = request.get_json(silent=True) or {}
    session_key = str(payload.get("session_id", "")).strip()
    message = str(payload.get("message", "")).strip()

    if not session_key:
        return jsonify({"error": "Cette conversation d'exercice est introuvable."}), 400
    if not message:
        return jsonify({"error": "Écris une question avant de l'envoyer."}), 400
    if len(message) > 1500:
        return jsonify({"error": "Ton message est trop long (maximum 1 500 caractères)."}), 400

    with STATE_LOCK:
        expire_old_sessions_locked()
        session = EXERCISE_SESSIONS.get(session_key)
        session_data = dict(session) if session else None
        if session_data:
            session_data["history"] = list(session.get("history", []))

    if not session_data:
        return jsonify({"error": "Cette conversation a expiré. Renvoie la photo de l'exercice."}), 404

    allowed, used, limit = consume_daily_usage("chat")
    if not allowed:
        return jsonify({
            "error": f"Tu as utilisé tes {limit} messages de suivi gratuits aujourd’hui. Tu pourras reprendre la conversation demain.",
            "limits": daily_usage_snapshot(),
        }), 429

    try:
        answer = chat_about_exercise(
            session_data["context"],
            session_data["initial_solution"],
            session_data["history"],
            message,
            instruction=session_data["instruction"],
        )
    except Exception as exc:
        return jsonify({"error": f"L'IA n'a pas pu répondre : {exc}"}), 500

    with STATE_LOCK:
        session = EXERCISE_SESSIONS.get(session_key)
        if session is not None:
            session["history"].extend([
                {"role": "user", "content": message},
                {"role": "assistant", "content": answer},
            ])
            session["history"] = session["history"][-12:]
            session["created"] = time.time()

    return jsonify({"success": True, "response": answer})


@app.route("/api/exercise-feedback", methods=["POST"])
def exercise_feedback():
    payload = request.get_json(silent=True) or {}
    session_key = str(payload.get("session_id", "")).strip()
    category = str(payload.get("category", "")).strip()
    comment = str(payload.get("comment", "")).strip()
    allowed_categories = {
        "mauvaise_lecture",
        "mauvais_exercice",
        "erreur_de_calcul",
        "explication_incomplete",
        "autre",
    }
    if not session_key:
        return jsonify({"error": "La correction à signaler est introuvable."}), 400
    if category not in allowed_categories:
        return jsonify({"error": "Choisis le type de problème rencontré."}), 400
    if len(comment) > 1000:
        return jsonify({"error": "Le commentaire est trop long (maximum 1 000 caractères)."}), 400

    with STATE_LOCK:
        expire_old_sessions_locked()
        session = EXERCISE_SESSIONS.get(session_key)
        session_data = dict(session) if session else None
        if session is not None and session.get("feedback_submitted"):
            return jsonify({"error": "Un signalement a déjà été envoyé pour cette correction."}), 409
        if session is not None:
            session["feedback_submitted"] = True
    if not session_data:
        return jsonify({"error": "Cette correction a expiré. Renvoie l’exercice pour le signaler."}), 404

    report = {
        "id": secrets.token_urlsafe(18),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exercise_number": session_data.get("exercise_number"),
        "questions": session_data.get("selected_questions", []),
        "category": category,
        "comment": comment,
    }
    try:
        save_feedback_report(report)
    except FeedbackStorageError as exc:
        app.logger.exception("Enregistrement d'un signalement impossible")
        with STATE_LOCK:
            session = EXERCISE_SESSIONS.get(session_key)
            if session is not None:
                session["feedback_submitted"] = False
        return jsonify({"error": str(exc) or "Le signalement n’a pas pu être enregistré. Réessaie plus tard."}), 503

    return jsonify({"success": True, "message": "Merci, ton signalement a bien été enregistré."})


@app.route("/api/quiz", methods=["POST"])
def quiz():
    payload = request.get_json(silent=True) or {}
    session_key = str(payload.get("session_id", "")).strip()

    with STATE_LOCK:
        expire_old_sessions_locked()
        state = LATEST_BY_SESSION.get(session_key)

    revision_text = str(payload.get("revision_text", "")).strip()[:30000]
    if not state and not revision_text:
        return jsonify({"error": "Génère ou ouvre une fiche de révision avant de lancer un quiz."}), 400

    allowed, used, limit = consume_daily_usage("quiz")
    if not allowed:
        return jsonify({
            "error": f"Tu as utilisé tes {limit} quiz gratuits aujourd’hui. Tu pourras en lancer d’autres demain.",
            "limits": daily_usage_snapshot(),
        }), 429

    try:
        result = generate_quiz(
            state["course"] if state else revision_text,
            recent_questions=state["recent_questions"] if state else [],
            count=10,
        )
    except Exception as exc:
        return jsonify({"error": f"Erreur pendant la création du quiz : {exc}"}), 500

    if state:
        with STATE_LOCK:
            state["recent_questions"].extend(
                str(item.get("question", "")).strip()
                for item in result.get("questions", [])
                if item.get("question")
            )
            state["recent_questions"] = state["recent_questions"][-40:]

    return jsonify({"success": True, **result})


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": f"Fichier trop volumineux. Maximum : {MAX_MB} Mo."}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
    
    
