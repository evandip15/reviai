"""Statistiques internes facultatives ; aucun contenu de cours n'est enregistré."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock

from flask import jsonify, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


class SiteAnalytics:
    COOKIE = "reviai_stats"
    MAX_AGE = 90 * 24 * 60 * 60
    VISITORS = "reviai_stats_visitors"
    VISITS = "reviai_stats_visits"
    SHEETS = "reviai_stats_sheets"
    SOURCES = {
        "direct": "Accès direct / origine inconnue", "tiktok": "TikTok",
        "instagram": "Instagram", "youtube": "YouTube", "google": "Google",
        "other": "Autre origine",
    }
    MILESTONES = {"signed_up_at", "verified_at", "plus_view_at", "checkout_at"}

    def __init__(self, app, host):
        self.app, self.host = app, host
        self.signer = URLSafeTimedSerializer(app.secret_key, salt="reviai-stats-v1")
        self.lock, self.ready, self.cleanup_day = Lock(), False, None
        app.context_processor(self.template_context)
        app.after_request(self.observe_page)
        app.add_url_rule("/api/statistiques/choix", "analytics_choice", self.choice, methods=["POST"])

    def enabled(self):
        return bool(self.host.DATABASE_URL and self.host.psycopg is not None)

    def consent(self):
        value = request.cookies.get(self.COOKIE, "")
        if value == "no":
            return "declined", None
        try:
            payload = self.signer.loads(value, max_age=self.MAX_AGE)
            identifier = payload.get("id", "")
            if len(identifier) == 64 and all(c in "0123456789abcdef" for c in identifier):
                secret = self.app.secret_key
                if isinstance(secret, str):
                    secret = secret.encode("utf-8")
                digest = hmac.new(secret, identifier.encode("ascii"), hashlib.sha256).hexdigest()
                return "accepted", digest
        except (BadSignature, SignatureExpired, AttributeError, TypeError):
            pass
        return "pending", None

    def visitor(self):
        if not self.enabled() or session.get("admin_authenticated"):
            return None
        return self.consent()[1]

    def template_context(self):
        visible = self.enabled() and not request.path.startswith("/admin") and not session.get("admin_authenticated")
        return {
            "stats_available": visible,
            "stats_choice": self.consent()[0] if visible else "declined",
            "stats_csrf": self.host.account_csrf_token() if visible else "",
        }

    def prepare(self):
        """Créer les tables une fois par processus, purger au premier accès du jour."""
        today = datetime.now(timezone.utc).date()
        if self.ready and self.cleanup_day == today:
            return
        with self.lock:
            if self.ready and self.cleanup_day == today:
                return
            with self.host.feedback_database_connection() as connection:
                if not self.ready:
                    self.host.ensure_billing_tables(connection)
                    connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.VISITORS} (
                        visitor_hash TEXT PRIMARY KEY,
                        first_seen TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        source TEXT NOT NULL DEFAULT 'direct',
                        account_id TEXT REFERENCES {self.host.ACCOUNT_TABLE}(id) ON DELETE CASCADE,
                        signed_up_at TIMESTAMPTZ, verified_at TIMESTAMPTZ,
                        first_sheet_at TIMESTAMPTZ, plus_view_at TIMESTAMPTZ,
                        checkout_at TIMESTAMPTZ, plus_at TIMESTAMPTZ
                    )""")
                    connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.VISITS} (
                        visitor_hash TEXT REFERENCES {self.VISITORS}(visitor_hash) ON DELETE CASCADE,
                        visit_date DATE NOT NULL DEFAULT ((CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date),
                        PRIMARY KEY (visitor_hash, visit_date)
                    )""")
                    connection.execute(f"""CREATE TABLE IF NOT EXISTS {self.SHEETS} (
                        job_id TEXT PRIMARY KEY,
                        visitor_hash TEXT NOT NULL REFERENCES {self.VISITORS}(visitor_hash) ON DELETE CASCADE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )""")
                    connection.execute(f"CREATE INDEX IF NOT EXISTS reviai_stats_account_idx ON {self.VISITORS}(account_id)")
                    connection.execute(f"CREATE INDEX IF NOT EXISTS reviai_stats_seen_idx ON {self.VISITORS}(first_seen)")
                    connection.execute(f"CREATE INDEX IF NOT EXISTS reviai_stats_visit_day_idx ON {self.VISITS}(visit_date)")
                    connection.execute(f"CREATE INDEX IF NOT EXISTS reviai_stats_sheet_day_idx ON {self.SHEETS}(created_at)")
                connection.execute(f"DELETE FROM {self.VISITORS} WHERE first_seen < CURRENT_TIMESTAMP - INTERVAL '90 days'")
            self.ready, self.cleanup_day = True, today

    def save_visit(self, visitor_hash, source="direct", create=False):
        self.prepare()
        with self.host.feedback_database_connection() as connection:
            if create:
                connection.execute(
                    f"INSERT INTO {self.VISITORS} (visitor_hash, source) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (visitor_hash, source if source in self.SOURCES else "other"),
                )
            connection.execute(
                f"INSERT INTO {self.VISITS} (visitor_hash) SELECT visitor_hash FROM {self.VISITORS} WHERE visitor_hash = %s ON CONFLICT DO NOTHING", (visitor_hash,),
            )

    def observe_page(self, response):
        # Uniquement des pages effectivement affichées : ni API, ni robots, ni erreurs.
        public_html = response.status_code == 200 and response.mimetype == "text/html" and not request.path.startswith("/admin")
        if public_html and self.enabled():
            response.headers["Cache-Control"] = "no-store, private"
            visitor_hash = self.visitor()
            if visitor_hash and request.method == "GET":
                try:
                    self.save_visit(visitor_hash)
                    if request.path == "/plus":
                        self.milestone("plus_view_at")
                except Exception:
                    self.app.logger.warning("Mesure facultative des visites indisponible.")
        return response

    def choice(self):
        response_error = lambda text, code: (jsonify({"error": text}), code)
        if not self.enabled() or session.get("admin_authenticated"):
            return response_error("Les statistiques ne sont pas disponibles ici.", 503)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not isinstance(data.get("csrf_token"), str) or not self.host.valid_account_csrf(data["csrf_token"]):
            return response_error("Recharge la page puis réessaie.", 400)
        choice = data.get("choice")
        if not isinstance(choice, str) or choice not in {"accepted", "declined"}:
            return response_error("Choix invalide.", 400)
        old_hash = self.consent()[1]
        try:
            self.prepare()
            if choice == "declined":
                if old_hash:
                    with self.host.feedback_database_connection() as connection:
                        connection.execute(f"DELETE FROM {self.VISITORS} WHERE visitor_hash = %s", (old_hash,))
                cookie_value = "no"
            elif old_hash:
                # Conserver la date d'expiration et le même navigateur en cas de double clic.
                cookie_value = request.cookies[self.COOKIE]
            else:
                cookie_value = self.signer.dumps({"id": secrets.token_hex(32)})
                payload = self.signer.loads(cookie_value)
                secret = self.app.secret_key
                if isinstance(secret, str):
                    secret = secret.encode("utf-8")
                visitor_hash = hmac.new(secret, payload["id"].encode("ascii"), hashlib.sha256).hexdigest()
                source = data.get("source", "direct")
                self.save_visit(visitor_hash, source if isinstance(source, str) else "other", create=True)
                # Accepter depuis /plus doit aussi compter la consultation de l'offre.
                if data.get("page") == "plus":
                    with self.host.feedback_database_connection() as connection:
                        connection.execute(f"UPDATE {self.VISITORS} SET plus_view_at = CURRENT_TIMESTAMP WHERE visitor_hash = %s", (visitor_hash,))
            response = jsonify({"choice": choice})
            response.headers["Cache-Control"] = "no-store"
            response.set_cookie(self.COOKIE, cookie_value, max_age=self.MAX_AGE, httponly=True,
                                secure=bool(self.app.config["SESSION_COOKIE_SECURE"] or request.is_secure),
                                samesite="Lax", path="/")
            return response
        except Exception:
            self.app.logger.warning("Enregistrement du choix de statistiques indisponible.")
            return response_error("Impossible d’enregistrer ton choix pour l’instant. Tu peux continuer à utiliser le site.", 503)

    def milestone(self, column, account_id=None):
        visitor_hash = self.visitor()
        if not visitor_hash or column not in self.MILESTONES:
            return
        try:
            self.prepare()
            with self.host.feedback_database_connection() as connection:
                # Associer seulement un compte créé ou authentifié, jamais un e-mail saisi.
                if column in {"signed_up_at", "checkout_at"} and account_id:
                    connection.execute(f"""UPDATE {self.VISITORS}
                        SET {column} = COALESCE({column}, CURRENT_TIMESTAMP), account_id = %s
                        WHERE visitor_hash = %s AND (account_id IS NULL OR account_id = %s)""",
                        (str(account_id), visitor_hash, str(account_id)))
                else:
                    connection.execute(f"UPDATE {self.VISITORS} SET {column} = COALESCE({column}, CURRENT_TIMESTAMP) WHERE visitor_hash = %s", (visitor_hash,))
        except Exception:
            self.app.logger.warning("Mesure facultative d'une étape indisponible.")

    def account_verified(self, account_id):
        # La confirmation peut arriver sur un autre appareil ; seul le parcours
        # ayant déjà reçu un accord et créé ce compte est mis à jour.
        self.account_event(account_id, "verified_at")

    def subscription_activated(self, account_id, subscription):
        # Seulement l'objet lu chez Stripe après signature du webhook ou retour
        # authentifié : ni paramètre d'URL, ni pass VIP, ni paiement de test.
        if (self.host.stripe_field(subscription, "livemode") is True
                and self.host.stripe_field(subscription, "status") == "active"):
            self.account_event(account_id, "plus_at")

    def account_event(self, account_id, column):
        if not self.enabled() or not account_id or column not in {"verified_at", "plus_at", "checkout_at"}:
            return
        try:
            self.prepare()
            with self.host.feedback_database_connection() as connection:
                connection.execute(f"UPDATE {self.VISITORS} SET {column} = COALESCE({column}, CURRENT_TIMESTAMP) WHERE account_id = %s", (str(account_id),))
        except Exception:
            self.app.logger.warning("Mesure facultative d'une activation indisponible.")

    def completed_sheet(self, visitor_hash, job_id):
        # Contexte minimal capturé lors de l'envoi, sans Flask dans le thread IA.
        # Une révocation d'accord supprime la ligne : le traitement ne la recrée pas.
        if not visitor_hash:
            return
        try:
            self.prepare()
            with self.host.feedback_database_connection() as connection:
                row = connection.execute(f"""UPDATE {self.VISITORS}
                    SET first_sheet_at = COALESCE(first_sheet_at, CURRENT_TIMESTAMP)
                    WHERE visitor_hash = %s RETURNING visitor_hash""", (visitor_hash,)).fetchone()
                if row:
                    connection.execute(f"INSERT INTO {self.SHEETS} (job_id, visitor_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING", (job_id, visitor_hash))
        except Exception:
            self.app.logger.warning("Mesure facultative d'une fiche terminée indisponible.")

    def dashboard(self, days):
        empty = {"available": False, "error": "", "days": days, "cards": [], "steps": [], "daily": [], "sources": [], "observations": []}
        if not self.enabled():
            return {**empty, "error": "Configure DATABASE_URL pour conserver les statistiques en ligne."}
        try:
            self.prepare()
            since_date = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
            since = datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)
            with self.host.feedback_database_connection() as connection:
                actual = connection.execute(f"""SELECT
                    COUNT(*) FILTER (WHERE created_at >= %s),
                    COUNT(*) FILTER (WHERE created_at >= %s AND email_verified),
                    COUNT(*) FILTER (WHERE subscription_status IN ('active', 'trialing')
                        AND (subscription_period_end IS NULL OR subscription_period_end > CURRENT_TIMESTAMP)),
                    COUNT(*) FILTER (WHERE subscription_status IN ('active', 'trialing') AND cancel_at_period_end
                        AND (subscription_period_end IS NULL OR subscription_period_end > CURRENT_TIMESTAMP))
                    FROM {self.host.ACCOUNT_TABLE}""", (since, since)).fetchone()
                totals = connection.execute(f"""SELECT COUNT(DISTINCT visitor_hash),
                    (SELECT COUNT(*) FROM {self.SHEETS} WHERE created_at >= %s)
                    FROM {self.VISITS} WHERE visit_date >= %s""", (since, since_date)).fetchone()
                cohort = connection.execute(f"""SELECT COUNT(*),
                    COUNT(signed_up_at), COUNT(verified_at), COUNT(first_sheet_at),
                    COUNT(plus_view_at), COUNT(checkout_at), COUNT(plus_at),
                    COUNT(*) FILTER (WHERE (SELECT COUNT(*) FROM {self.VISITS} d WHERE d.visitor_hash = v.visitor_hash) >= 2),
                    COUNT(*) FILTER (WHERE signed_up_at IS NOT NULL AND verified_at IS NULL),
                    COUNT(*) FILTER (WHERE signed_up_at IS NOT NULL AND first_sheet_at IS NULL),
                    COUNT(*) FILTER (WHERE checkout_at IS NOT NULL AND plus_at IS NULL)
                    FROM {self.VISITORS} v WHERE first_seen >= %s""", (since,)).fetchone()
                daily = connection.execute(f"""SELECT visit_date, COUNT(*) FROM {self.VISITS}
                    WHERE visit_date >= %s GROUP BY visit_date ORDER BY visit_date""", (since_date,)).fetchall()
                sources = connection.execute(f"""SELECT source, COUNT(*), COUNT(signed_up_at), COUNT(plus_at)
                    FROM {self.VISITORS} WHERE first_seen >= %s GROUP BY source ORDER BY COUNT(*) DESC""", (since,)).fetchall()
            visitors = int(cohort[0])
            labels = ["Nouveaux navigateurs mesurés", "Compte créé", "Confirmation de l’e-mail observée", "Première fiche terminée",
                      "Offre Plus consultée", "Paiement Stripe ouvert", "Plus activé en mode réel", "Retour un autre jour"]
            steps = [{"label": label, "count": int(cohort[i]),
                      "percent": round(100 * int(cohort[i]) / visitors, 1) if visitors else None}
                     for i, label in enumerate(labels)]
            max_daily = max((int(row[1]) for row in daily), default=1)
            return {
                **empty, "available": True,
                "cards": [
                    {"label": "Comptes créés", "count": int(actual[0]), "note": f"Sur {days} jours · tous les comptes"},
                    {"label": "Adresses confirmées", "count": int(actual[1]), "note": "Parmi ces nouveaux comptes, à ce jour"},
                    {"label": "Accès Plus actifs", "count": int(actual[2]), "note": "État actuel connu, essais et mode test inclus · VIP exclus"},
                    {"label": "Résiliations programmées", "count": int(actual[3]), "note": "Parmi les accès Plus encore actifs"},
                    {"label": "Navigateurs actifs mesurés", "count": int(totals[0]), "note": f"Avec accord · sur {days} jours"},
                    {"label": "Fiches terminées mesurées", "count": int(totals[1]), "note": "Avec accord · hors échecs et hors mode éditeur"},
                ],
                "steps": steps,
                "daily": [{"date": row[0].strftime("%d/%m"), "count": int(row[1]), "width": round(100 * int(row[1]) / max_daily)} for row in daily],
                "sources": [{"label": self.SOURCES.get(row[0], "Autre origine"), "visitors": int(row[1]), "signups": int(row[2]), "plus": int(row[3])} for row in sources],
                "observations": [
                    {"count": int(cohort[8]), "text": "compte(s) créé(s) sans confirmation observée. Vérifie la réception des e-mails et la clarté du message de confirmation."},
                    {"count": int(cohort[9]), "text": "inscription(s) sans fiche terminée sur ce navigateur. Rends le bouton d’import et les formats acceptés bien visibles."},
                    {"count": int(cohort[10]), "text": "ouverture(s) du paiement sans activation Plus observée. Vérifie les événements Stripe et explique clairement le prix et la résiliation."},
                ],
            }
        except Exception:
            self.app.logger.warning("Lecture du tableau des statistiques indisponible.")
            return {**empty, "error": "Les statistiques sont temporairement indisponibles. Vérifie la connexion à la base puis recharge la page."}
