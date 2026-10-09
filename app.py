import os
import random
import re
import secrets
import time
import json
import hashlib
import hmac
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

try:
    import psycopg
except ModuleNotFoundError:  # Le mode fichier local fonctionne sans PostgreSQL.
    psycopg = None
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
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

# Un seul traitement IA à la fois sur le petit serveur gratuit.
EXECUTOR = ThreadPoolExecutor(max_workers=1)


class FeedbackStorageError(Exception):
    """Signale une erreur de lecture ou d'écriture des signalements."""


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


@app.after_request
def protect_admin_responses(response):
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


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
    
    
