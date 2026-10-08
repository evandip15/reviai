import os
import random
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from ai import extract_image_text_gemini, extract_document_text_gemini, generate_quiz, generate_revision
from lecteur import read_file

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
FICHES_DIR = BASE_DIR / "data" / "fiches"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FICHES_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".odt", ".pptx", ".txt", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_MIME_TO_EXTENSION = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))

app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

STATE_LOCK = Lock()
LATEST_BY_SESSION = {}
JOBS = {}

# Un seul traitement IA à la fois sur le petit serveur gratuit.
EXECUTOR = ThreadPoolExecutor(max_workers=1)


def save_revision(content: str) -> str:
    filename = f"revision_{int(time.time())}_{secrets.token_hex(4)}.md"
    path = FICHES_DIR / filename
    path.write_text(content, encoding="utf-8")
    return filename


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
        filename = save_revision(revision)

        with STATE_LOCK:
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
                "filename": filename,
                "session_id": session_key,
            },
        )

    except Exception as exc:
        set_job(job_id, status="error", message=str(exc))
    finally:
        cleanup_temp(temp_paths)


@app.route("/")
def home():
    ads_enabled = os.getenv("ADS_ENABLED", "false").strip().lower() == "true"
    ads_client_id = os.getenv("ADSENSE_CLIENT_ID", "").strip()
    ads_slot_id = os.getenv("ADSENSE_SLOT_ID", "").strip()
    return render_template(
        "index.html",
        ads_enabled=ads_enabled and bool(ads_client_id),
        ads_client_id=ads_client_id,
        ads_slot_id=ads_slot_id,
    )


@app.route("/ads.txt")
def ads_txt():
    publisher = os.getenv("ADSENSE_PUBLISHER_ID", "pub-3292344498702156").strip()
    return (
        f"google.com, {publisher}, DIRECT, f08c47fec0942fa0\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


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
                mime_extension = IMAGE_MIME_TO_EXTENSION.get((uploaded.mimetype or "").lower())
                if mime_extension:
                    extension = mime_extension

            if extension not in ALLOWED_EXTENSIONS:
                cleanup_temp([item[0] for item in saved_files])
                return jsonify({
                    "error": (
                        f"Format non supporté : {extension or 'inconnu'}. "
                        "Les images acceptées sont JPG/JPEG, PNG et WEBP."
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
def generation_status(job_id):
    with STATE_LOCK:
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


@app.route("/api/quiz", methods=["POST"])
def quiz():
    payload = request.get_json(silent=True) or {}
    session_key = str(payload.get("session_id", "")).strip()

    with STATE_LOCK:
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
