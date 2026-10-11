"""Bibliothèque privée, cartes de révision et tableau des abonnements."""
import json
import re
from datetime import datetime, timedelta, timezone

from flask import jsonify, redirect, render_template, request, session, url_for

MAX_ITEMS = 200


def ensure_study_tables(connection, core):
    core.ensure_billing_tables(connection)
    connection.execute("""CREATE TABLE IF NOT EXISTS reviai_library (
        user_id TEXT NOT NULL REFERENCES reviai_accounts(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, item_id))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS reviai_study_folders (
        user_id TEXT NOT NULL REFERENCES reviai_accounts(id) ON DELETE CASCADE,
        id TEXT NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY (user_id, id), UNIQUE (user_id, name))""")
    # L'éditeur n'est pas un compte élève. Sa bibliothèque a son propre espace.
    connection.execute("""CREATE TABLE IF NOT EXISTS reviai_study_editors (
        id TEXT PRIMARY KEY)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS reviai_editor_library (
        user_id TEXT NOT NULL REFERENCES reviai_study_editors(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, item_id))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS reviai_editor_study_folders (
        user_id TEXT NOT NULL REFERENCES reviai_study_editors(id) ON DELETE CASCADE,
        id TEXT NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY (user_id, id), UNIQUE (user_id, name))""")


def library_tables(owner):
    # Noms SQL constants : aucun nom de table ne vient de la requête.
    if owner.get("editor"):
        return "reviai_editor_library", "reviai_editor_study_folders", "reviai_study_editors"
    return "reviai_library", "reviai_study_folders", "reviai_accounts"


def lock_library(connection, owner):
    _, _, owners = library_tables(owner)
    if owner.get("editor"):
        connection.execute("INSERT INTO reviai_study_editors (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (owner["id"],))
    connection.execute(f"SELECT id FROM {owners} WHERE id = %s FOR UPDATE", (owner["id"],)).fetchone()


def clean_item(raw, item_id):
    if not isinstance(raw, dict) or raw.get("type") not in {"revision", "exercise", "quiz", "flashcards"}:
        raise ValueError("Choisis un élément de révision valide.")
    if len(json.dumps(raw, ensure_ascii=False)) > 180000:
        raise ValueError("Cet élément est trop volumineux pour être enregistré.")
    clean = {"id": item_id, "type": raw["type"]}
    for key, limit in (("title", 180), ("content", 30000), ("folder", 100), ("sourceContent", 30000), ("sourceId", 100)):
        clean[key] = str(raw.get(key) or "")[:limit]
    for key in ("createdAt", "updatedAt"):
        clean[key] = datetime.now(timezone.utc).isoformat()
    if raw.get("createdAt"):
        try:
            clean["createdAt"] = datetime.fromisoformat(str(raw["createdAt"]).replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    if raw["type"] == "quiz":
        questions = raw.get("questions", [])
        if not isinstance(questions, list) or len(questions) > 30:
            raise ValueError("Ce résultat de quiz est invalide.")
        clean["questions"] = []
        for question in questions:
            if not isinstance(question, dict) or not isinstance(question.get("answers"), list) or len(question["answers"]) != 4:
                raise ValueError("Une question du quiz est invalide.")
            if type(question.get("correct")) is not int or question["correct"] not in range(4) or type(question.get("selected")) is not int or question["selected"] not in range(4):
                raise ValueError("Une réponse du quiz est invalide.")
            clean["questions"].append({
                "question": str(question.get("question", ""))[:2000],
                "answers": [str(answer)[:2000] for answer in question["answers"]],
                "correct": question["correct"], "selected": question["selected"],
                "explanation": str(question.get("explanation", ""))[:4000],
            })
        if questions:
            clean["total"] = len(questions)
            clean["score"] = sum(q["selected"] == q["correct"] for q in clean["questions"])
        else:  # Import des anciens résultats, qui n'avaient pas les questions.
            clean["total"] = min(30, max(0, int(raw.get("total") or 0)))
            clean["score"] = min(clean["total"], max(0, int(raw.get("score") or 0)))
    if raw["type"] == "flashcards":
        cards = raw.get("cards")
        if not isinstance(cards, list) or not 1 <= len(cards) <= 50:
            raise ValueError("Un jeu contient entre 1 et 50 cartes.")
        clean["cards"] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict) or not str(card.get("front", "")).strip() or not str(card.get("back", "")).strip():
                raise ValueError("Chaque carte doit avoir une question et une réponse.")
            due = datetime.fromisoformat(str(card.get("due") or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
            clean["cards"].append({"id": str(card.get("id") or index)[:100],
                "front": str(card["front"])[:2000], "back": str(card["back"])[:5000],
                "due": due.isoformat(), "interval": min(60, max(0, int(card.get("interval") or 0))),
                "reviews": min(100000, max(0, int(card.get("reviews") or 0)))})
    return clean


def register_study_routes(app, core):
    def account_required():
        if core.ADMIN_PASSWORD and session.get("admin_authenticated"):
            if request.method != "GET" and not core.valid_admin_csrf(request.headers.get("X-CSRF-Token", "")):
                return None, (jsonify(error="Session expirée. Recharge la page."), 400)
            return {"id": "editor", "editor": True}, None
        account = core.billing_account(session.get("user_id"))
        if not account or not account["email_verified"]:
            return None, (jsonify(error="Connecte-toi avec un compte confirmé pour retrouver ta bibliothèque."), 401)
        if request.method != "GET" and not core.valid_account_csrf(request.headers.get("X-CSRF-Token", "")):
            return None, (jsonify(error="Session expirée. Recharge la page."), 400)
        return account, None

    @app.route("/api/library", methods=["GET", "DELETE"])
    def study_library():
        if not session.get("user_id") and not session.get("admin_authenticated") and request.method == "GET":
            return jsonify(authenticated=False, items=[], folders=[])
        account, error = account_required()
        if error:
            return error
        items_table, folders_table, _ = library_tables(account)
        try:
            with core.feedback_database_connection() as connection:
                ensure_study_tables(connection, core)
                if request.method == "DELETE":
                    lock_library(connection, account)
                    connection.execute(f"DELETE FROM {items_table} WHERE user_id = %s", (account["id"],))
                    connection.execute(f"DELETE FROM {folders_table} WHERE user_id = %s", (account["id"],))
                    return jsonify(success=True)
                rows = connection.execute(f"SELECT payload, version FROM {items_table} WHERE user_id = %s ORDER BY updated_at DESC", (account["id"],)).fetchall()
                folders = connection.execute(f"SELECT id, name FROM {folders_table} WHERE user_id = %s ORDER BY name", (account["id"],)).fetchall()
            return jsonify(authenticated=True, account_id=account["id"], library_mode="editor" if account.get("editor") else "account",
                csrf_token=core.admin_csrf_token() if account.get("editor") else core.account_csrf_token(),
                items=[{**json.loads(row[0]), "version": row[1]} for row in rows],
                folders=[{"id": row[0], "name": row[1]} for row in folders], limit=MAX_ITEMS)
        except Exception:
            app.logger.exception("Bibliothèque du compte indisponible.")
            return jsonify(error="La bibliothèque de ton compte est indisponible. Réessaie plus tard."), 503

    @app.route("/api/library/items/<item_id>", methods=["PUT", "DELETE"])
    def study_library_item(item_id):
        account, error = account_required()
        if error:
            return error
        items_table, folders_table, _ = library_tables(account)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", item_id):
            return jsonify(error="Identifiant invalide."), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or type(payload.get("expected_version")) is not int or payload["expected_version"] < 0:
            return jsonify(error="Version de l’élément manquante. Recharge ta bibliothèque."), 400
        try:
            item = clean_item(payload.get("item"), item_id) if request.method == "PUT" else None
            with core.feedback_database_connection() as connection:
                ensure_study_tables(connection, core)
                # Verrou du compte : limite et modification atomiques, même entre appareils.
                lock_library(connection, account)
                row = connection.execute(f"SELECT version FROM {items_table} WHERE user_id = %s AND item_id = %s", (account["id"], item_id)).fetchone()
                version = row[0] if row else 0
                if version != payload["expected_version"]:
                    return jsonify(error="Cet élément a changé sur un autre appareil. Recharge la bibliothèque avant de le modifier."), 409
                if request.method == "DELETE":
                    connection.execute(f"DELETE FROM {items_table} WHERE user_id = %s AND item_id = %s", (account["id"], item_id))
                    return jsonify(success=True)
                if item["folder"] and not connection.execute(f"SELECT id FROM {folders_table} WHERE user_id = %s AND id = %s", (account["id"], item["folder"])).fetchone():
                    raise ValueError("Ce dossier n’existe plus. Recharge ta bibliothèque.")
                if not row and connection.execute(f"SELECT COUNT(*) FROM {items_table} WHERE user_id = %s", (account["id"],)).fetchone()[0] >= MAX_ITEMS:
                    return jsonify(error=f"Ta bibliothèque contient déjà {MAX_ITEMS} éléments. Supprime ceux dont tu n’as plus besoin."), 409
                connection.execute(f"""INSERT INTO {items_table} (user_id, item_id, payload, version)
                    VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, item_id) DO UPDATE
                    SET payload = EXCLUDED.payload, version = EXCLUDED.version, updated_at = CURRENT_TIMESTAMP""",
                    (account["id"], item_id, json.dumps(item, ensure_ascii=False), version + 1))
            return jsonify(success=True, item={**item, "version": version + 1})
        except (ValueError, TypeError, OverflowError):
            return jsonify(error="L’élément ou son dossier est invalide. Recharge ta bibliothèque et réessaie."), 400
        except Exception:
            app.logger.exception("Modification de la bibliothèque impossible.")
            return jsonify(error="Enregistrement impossible pour le moment. Ton contenu reste affiché."), 503

    @app.route("/api/library/folders", methods=["POST"])
    def study_create_folder():
        account, error = account_required()
        if error:
            return error
        _, folders_table, _ = library_tables(account)
        payload = request.get_json(silent=True)
        name = str(payload.get("name", "")).strip() if isinstance(payload, dict) else ""
        if not name or len(name) > 80:
            return jsonify(error="Donne un nom de 1 à 80 caractères au dossier."), 400
        try:
            with core.feedback_database_connection() as connection:
                ensure_study_tables(connection, core)
                lock_library(connection, account)
                existing = connection.execute(f"SELECT id FROM {folders_table} WHERE user_id = %s AND name = %s", (account["id"], name)).fetchone()
                if existing:
                    return jsonify(folder={"id": existing[0], "name": name})
                if connection.execute(f"SELECT COUNT(*) FROM {folders_table} WHERE user_id = %s", (account["id"],)).fetchone()[0] >= 50:
                    return jsonify(error="Tu peux créer jusqu’à 50 dossiers."), 409
                folder_id = core.make_id()
                connection.execute(f"INSERT INTO {folders_table} (user_id, id, name) VALUES (%s, %s, %s)", (account["id"], folder_id, name))
            return jsonify(folder={"id": folder_id, "name": name})
        except Exception:
            app.logger.exception("Création du dossier impossible.")
            return jsonify(error="Le dossier n’a pas pu être créé."), 503

    @app.route("/api/flashcards", methods=["POST"])
    def study_flashcards():
        payload = request.get_json(silent=True)
        course = str(payload.get("course", "")).strip()[:30000] if isinstance(payload, dict) else ""
        if not course:
            return jsonify(error="Ouvre une fiche avant de créer des cartes."), 400
        allowed, _, limit = core.consume_daily_usage("quiz")
        if not allowed:
            return jsonify(error=f"Tes {limit} créations de quiz ou cartes gratuites ont été utilisées aujourd’hui."), 429
        try:
            from ai import generate_flashcards
            return jsonify(cards=generate_flashcards(course))
        except Exception:
            app.logger.exception("Création des cartes impossible.")
            return jsonify(error="Les cartes n’ont pas pu être créées. Réessaie plus tard."), 503

    @app.route("/admin/abonnements", methods=["GET", "POST"])
    def admin_subscriptions():
        if not core.ADMIN_PASSWORD:
            return render_template("admin_login.html", configured=False), 503
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login"))
        if request.method == "POST" and not core.valid_admin_csrf(request.form.get("csrf_token", "")):
            return "Session expirée. Recharge la page.", 400
        accounts, error, finance, finance_error = [], "", None, ""
        counts = {"active": 0, "scheduled": 0, "canceled": 0, "past_due": 0}
        try:
            with core.feedback_database_connection() as connection:
                core.ensure_billing_tables(connection)
                rows = connection.execute("""SELECT email, subscription_status, cancel_at_period_end,
                    subscription_period_end FROM reviai_accounts WHERE stripe_subscription_id IS NOT NULL
                    ORDER BY email""").fetchall()
            now = datetime.now(timezone.utc)
            for row in rows:
                end = row[3]
                active = row[1] in {"active", "trialing"} and (end is None or end > now)
                counts["active"] += int(active)
                counts["scheduled"] += int(bool(row[2]) and row[1] not in {"canceled", "incomplete_expired"})
                counts["canceled"] += int(row[1] in {"canceled", "incomplete_expired"})
                counts["past_due"] += int(row[1] in {"past_due", "unpaid"})
                if len(accounts) < 200:
                    accounts.append({"email": row[0], "status": row[1], "scheduled": row[2], "end": end})
            total = len(rows)
        except Exception:
            app.logger.exception("Suivi des abonnements indisponible.")
            total, error = 0, "Les abonnements sont indisponibles pour le moment."
        if request.method == "POST":
            try:
                core.stripe_configure()
                since = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
                currencies, processed, truncated = {}, 0, False
                # Les mouvements réels intègrent les frais et remboursements, pas les prévisions.
                transactions = core.stripe.BalanceTransaction.list(created={"gte": since}, limit=100)
                for tx in transactions.auto_paging_iter():
                    if processed >= 2000:
                        truncated = True
                        break
                    processed += 1
                    kind = core.stripe_field(tx, "type")
                    if kind not in {"charge", "payment", "refund", "payment_refund", "payment_failure_refund", "refund_failure", "adjustment", "stripe_fee"}:
                        continue
                    currency = str(core.stripe_field(tx, "currency", "eur")).upper()
                    sums = currencies.setdefault(currency, {"gross": 0, "refunds": 0, "fees": 0, "net": 0})
                    amount = int(core.stripe_field(tx, "amount", 0))
                    sums["gross"] += amount if kind in {"charge", "payment"} else 0
                    sums["refunds"] += amount if "refund" in kind else 0
                    sums["fees"] += int(core.stripe_field(tx, "fee", 0))
                    if kind == "stripe_fee":
                        sums["fees"] -= amount
                    sums["net"] += int(core.stripe_field(tx, "net", 0))
                finance = {"currencies": currencies, "truncated": truncated,
                    "test_mode": "_test_" in core.STRIPE_SECRET_KEY,
                    "checked_at": datetime.now(timezone.utc)}
            except Exception:
                app.logger.exception("Lecture des mouvements Stripe impossible.")
                finance_error = "Stripe n’a pas renvoyé les montants. Vérifie que la clé Stripe autorise la lecture du solde et des mouvements de solde."
        return render_template("admin_subscriptions.html", accounts=accounts, counts=counts,
            total=total, storage_error=error, finance=finance, finance_error=finance_error,
            money=lambda amount, currency: f"{amount / 100:.2f}" if currency == "EUR" else f"{amount} unités mineures Stripe",
            csrf_token=core.admin_csrf_token())
