import os
import random
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

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
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

STATE_LOCK = Lock()
LATEST_BY_SESSION = {}
JOBS = {}
EXERCISE_SESSIONS = {}

# Un seul traitement IA à la fois sur le petit serveur gratuit.
EXECUTOR = ThreadPoolExecutor(max_workers=1)


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
    for sessions in (LATEST_BY_SESSION, EXERCISE_SESSIONS):
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


def process_exercise(job_id, saved_files, instruction=""):
    temp_paths = [item[0] for item in saved_files]
    original_names = [item[1] for item in saved_files]
    exercise_text = ""
    try:
        set_job(job_id, status="reading", message="Lecture des photos ou documents…")

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
            )
        else:
            extracted_pages = []
            requested_number = _requested_exercise_number(instruction)
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
                        # Le recadrage OCR par numéro est utile pour une image
                        # seule. En lot, chaque image peut être une page de
                        # continuation sans répéter le numéro de l'exercice.
                        target_number = (
                            requested_number
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
            solution = solve_exercise_text(exercise_text, instruction=instruction)

        if not solution.strip():
            raise RuntimeError("L'IA n'a pas généré de résolution.")

        requested_number = _requested_exercise_number(instruction)
        answer_number = _answer_exercise_number(solution)
        if requested_number is not None and answer_number is not None and answer_number != requested_number:
            raise RuntimeError(
                f"La réponse désigne l’exercice {answer_number} alors que tu as demandé le {requested_number}. "
                "Je l’ai bloquée pour éviter de t’afficher le mauvais exercice."
            )

        session_key = make_id()
        with STATE_LOCK:
            expire_old_sessions_locked()
            EXERCISE_SESSIONS[session_key] = {
                "context": exercise_text or solution,
                "instruction": instruction,
                "initial_solution": solution,
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
            {"heading": "Avant l’envoi", "paragraphs": ["Pose la page à plat, évite l’ombre portée et garde les bords de l’exercice visibles. Vérifie que les indices, signes, fractions, unités et lettres du schéma sont lisibles.", "Si l’énoncé couvre plusieurs pages, sélectionne toutes les photos ensemble dans l’ordre de lecture : elles seront envoyées comme un seul exercice. S’il y a plusieurs exercices sur la page, indique par exemple « exercice 63 uniquement » et précise les questions voulues. Le recadrage est facultatif et n’apparaît que pour une photo seule."]},
            {"heading": "Formats pris en charge", "paragraphs": ["Le mode exercice accepte les images JPG, JPEG, PNG et WEBP, ainsi que les documents PDF, DOCX, ODT, PPTX et TXT. Les documents doivent contenir du texte exploitable. Un PDF composé uniquement de pages scannées peut nécessiter une photo de la page ou la lecture PDF par Gemini lorsque cette option est configurée."]},
            {"heading": "Lire la réponse avec méthode", "paragraphs": ["Commence par vérifier que le numéro et la consigne repris par la réponse correspondent à ta demande. Compare ensuite les données de départ, les formules utilisées et les unités ou coefficients.", "Dans un exercice de maths, demande à l’IA d’expliquer une étape ou une autre méthode au lieu de recopier une réponse sans la comprendre. Tu peux poursuivre la conversation dans le mode exercice."]},
            {"heading": "Que faire si l’énoncé est mal lu ?", "paragraphs": ["Corrige les caractères ambigus dans ta consigne (par exemple AB ou AD, un signe moins, un exposant ou un point décimal) et renvoie une image plus nette si une donnée manque. Une solution ne doit pas inventer le contenu illisible.", "Les réponses sont générées automatiquement : compare-les au cours et aux attentes de ton enseignant. RéviAI sert d’aide à l’apprentissage, pas de validation officielle."]},
        ],
    },
}


def public_base_url():
    return os.getenv("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")


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
    return render_template("index.html")


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
        "/", "/guides", "/guides/reviser-efficacement",
        "/guides/resoudre-exercice", "/a-propos", "/faq", "/privacy", "/terms",
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


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


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


@app.route("/api/solve-exercise", methods=["POST"])
def solve_exercise():
    uploaded_files = request.files.getlist("photos")
    if not uploaded_files:
        uploaded_files = request.files.getlist("photo")
    uploaded_files = [item for item in uploaded_files if item and item.filename]
    if not uploaded_files:
        return jsonify({"error": "Choisis au moins une photo ou un document de ton exercice."}), 400

    instruction = str(request.form.get("instruction", "")).strip()[:1000]
    if not instruction:
        return jsonify({"error": "Précise le numéro ou le nom de l'exercice à résoudre."}), 400

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


@app.route("/api/quiz", methods=["POST"])
def quiz():
    payload = request.get_json(silent=True) or {}
    session_key = str(payload.get("session_id", "")).strip()

    with STATE_LOCK:
        expire_old_sessions_locked()
        state = LATEST_BY_SESSION.get(session_key)

    if not state:
        return jsonify({"error": "Génère d'abord une fiche de révision."}), 400

    try:
        result = generate_quiz(
            state["course"],
            recent_questions=state["recent_questions"],
            count=10,
        )
    except Exception as exc:
        return jsonify({"error": f"Erreur pendant la création du quiz : {exc}"}), 500

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
