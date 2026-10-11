"""Contrôles blancs privés et temporaires, avec travail IA en arrière-plan."""

import copy
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from threading import Lock

from flask import jsonify, render_template, request, session

from exam_ai import correct_exam, generate_exam


class MockExams:
    TTL = 3 * 60 * 60
    LEVELS = {"Collège", "Seconde", "Première", "Terminale", "Études supérieures"}
    DURATIONS = {15, 30, 45, 60, 90}
    BUSY = {"generating", "reading", "grading"}

    def __init__(self, app, core):
        self.app, self.core = app, core
        self.lock, self.exams = Lock(), {}
        app.add_url_rule("/controle-blanc", "mock_exam_page", self.page)
        app.add_url_rule("/api/controle-blanc", "mock_exam_create", self.create, methods=["GET", "POST"])
        app.add_url_rule("/api/controle-blanc/<exam_id>", "mock_exam_status", self.status, methods=["GET", "DELETE"])
        app.add_url_rule("/api/controle-blanc/<exam_id>/commencer", "mock_exam_start", self.start, methods=["POST"])
        app.add_url_rule("/api/controle-blanc/<exam_id>/lire-copie", "mock_exam_read", self.read_copy, methods=["POST"])
        app.add_url_rule("/api/controle-blanc/<exam_id>/corriger", "mock_exam_grade", self.grade, methods=["POST"])
        app.after_request(self.private_responses)

    def owner(self):
        if session.get("admin_authenticated"):
            return "editor"
        if session.get("user_id"):
            return "account:" + str(session["user_id"])
        return "browser:" + self.core.browser_usage_hash()

    def csrf(self):
        return self.core.admin_csrf_token() if session.get("admin_authenticated") else self.core.account_csrf_token()

    def valid_csrf(self):
        token = request.headers.get("X-CSRF-Token", "")
        return self.core.valid_admin_csrf(token) if session.get("admin_authenticated") else self.core.valid_account_csrf(token)

    def private_responses(self, response):
        if request.path.startswith("/api/controle-blanc") or request.path == "/controle-blanc":
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/controle-blanc"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    def page(self):
        secret = self.app.secret_key
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        scope = hmac.new(secret, self.owner().encode("utf-8"), hashlib.sha256).hexdigest()
        return render_template("mock_exam.html", exam_csrf=self.csrf(), exam_scope=scope, max_upload_mb=self.core.MAX_MB,
                               exam_cloud=bool(session.get("user_id") or session.get("admin_authenticated")))

    def prune(self):
        now = time.time()
        for key in [key for key, state in self.exams.items() if state["expires_at"] <= now]:
            self.exams.pop(key, None)

    def forget_account(self, user_id):
        with self.lock:
            for key in [key for key, state in self.exams.items() if state["owner"] == "account:" + str(user_id)]:
                self.exams.pop(key, None)

    def find(self, exam_id):
        # Appeler sous verrou. Ne jamais retourner un état d'un autre propriétaire.
        self.prune()
        state = self.exams.get(exam_id)
        return state if state and hmac.compare_digest(state["owner"], self.owner()) else None

    def public(self, state):
        result = {key: copy.deepcopy(state.get(key)) for key in (
            "id", "status", "message", "phase", "level", "topic", "duration", "expires_at", "started_at",
            "deadline", "submitted_at", "transcript", "read_version", "copy_error", "result", "attempts", "grade_attempts", "reads")}
        result["server_now"] = time.time()
        result["course_available"] = bool(state.get("course_ready"))
        result["submitted_answers"] = copy.deepcopy(state.get("answers", {}))
        subject = state.get("subject")
        if subject:
            result["title"] = subject["title"]
            result["instructions"] = subject["instructions"]
            result["question_count"] = len(subject["questions"])
            if state.get("started_at"):
                result["questions"] = [{key: q[key] for key in ("id", "prompt", "points", "kind")} for q in subject["questions"]]
        return result

    @staticmethod
    def error(text, code=400):
        return jsonify(error=text), code

    def uploads(self, field):
        saved = []
        try:
            for uploaded in request.files.getlist(field):
                if not uploaded or not uploaded.filename:
                    continue
                suffix = Path(uploaded.filename).suffix.lower()
                if suffix not in self.core.ALLOWED_EXTENSIONS:
                    suffix = self.core.MIME_TO_EXTENSION.get((uploaded.mimetype or "").lower(), "")
                if suffix not in self.core.ALLOWED_EXTENSIONS:
                    raise ValueError("Format non accepté. Utilise PDF, DOCX, ODT, PPTX, TXT, JPG, PNG ou WEBP.")
                destination = self.core.UPLOAD_DIR / ("exam_" + secrets.token_hex(18) + suffix)
                # Ajouter avant save() pour nettoyer aussi un fichier partiellement écrit.
                saved.append((destination, str(uploaded.filename)[:180]))
                uploaded.save(destination)
            return saved
        except Exception:
            self.cleanup(saved)
            raise

    def cleanup(self, files):
        self.core.cleanup_temp([path for path, _name in files])

    def extract(self, files, maximum):
        parts = []
        size = 0
        provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
        vision = provider != "ollama" and bool(os.getenv("GEMINI_API_KEY", "").strip())
        for index, (path, _name) in enumerate(files, 1):
            if path.suffix.lower() in self.core.IMAGE_EXTENSIONS and vision:
                text = self.core.extract_image_text_gemini(path, mime_type=self.core.uploaded_mime_from_extension(path.suffix))
            else:
                text = self.core.read_file(path)
                if path.suffix.lower() == ".pdf" and len(text.strip()) < 80 and vision:
                    text = self.core.extract_document_text_gemini(path)
            if not text.strip():
                raise ValueError(f"Le document {index} ne contient pas de texte lisible. Renvoie une version plus nette ou colle son texte.")
            size += len(text)
            if size > maximum:
                raise ValueError("Le contenu est trop long pour ce contrôle. Envoie uniquement le chapitre concerné.")
            parts.append(f"[Document {index}]\n{text.strip()}")
        combined = "\n\n".join(parts)
        if len(combined) > maximum:
            raise ValueError("Le contenu est trop long. Réduis le nombre de chapitres envoyés.")
        return combined

    def create(self):
        if request.method == "GET":
            with self.lock:
                self.prune()
                rows = [s for s in self.exams.values() if s["owner"] == self.owner()]
                return jsonify(self.public(max(rows, key=lambda s: s["expires_at"])) if rows else {"id": None})
        if not self.valid_csrf():
            return self.error("Ta session a expiré. Recharge la page avant de continuer.")
        course = request.form.get("course", "").strip()
        topic = request.form.get("topic", "").strip()
        level = request.form.get("level", "Terminale")
        try:
            duration = int(request.form.get("duration", "30"))
        except ValueError:
            return self.error("Choisis une durée proposée.")
        if level not in self.LEVELS or duration not in self.DURATIONS or len(topic) > 120 or len(course) > 60000:
            return self.error("Vérifie le niveau, la durée, le thème et la longueur du cours (60 000 caractères maximum).")
        files = []
        reserved = None
        try:
            files = self.uploads("files")
            retry_id = request.form.get("retry_id", "")
            if retry_id and not course and not files:
                with self.lock:
                    previous = self.find(retry_id)
                    if previous and previous.get("course_ready"):
                        course = previous["course"]
            if not files and len(course) < 80:
                return self.error("Importe ton cours ou colle au moins 80 caractères de cours.")
            with self.lock:
                self.prune()
                retry = self.find(retry_id) if retry_id else None
                if retry_id and (not retry or retry["status"] != "error" or retry["phase"] != "generation" or retry["attempts"] >= 3):
                    self.cleanup(files)
                    return self.error("Ce contrôle ne peut plus être relancé. Crée un nouveau contrôle.", 409)
                if any(s["owner"] == self.owner() and s["status"] in self.BUSY for s in self.exams.values()):
                    self.cleanup(files)
                    return self.error("Un traitement de contrôle blanc est déjà en cours. Attends sa fin.", 409)
                if sum(s["status"] in self.BUSY for s in self.exams.values()) >= 8 or (not retry and len(self.exams) >= 100):
                    self.cleanup(files)
                    return self.error("Beaucoup de contrôles sont en cours. Réessaie dans quelques minutes.", 503)
                exam_id = retry_id if retry else self.core.make_id()
                state = {"id": exam_id, "owner": self.owner(), "status": "generating", "phase": "generation",
                         "message": "Lecture du cours et préparation du sujet…", "course": course, "topic": topic,
                         "level": level, "duration": duration, "subject": None, "course_ready": False, "result": None, "started_at": None,
                         "deadline": None, "transcript": "", "read_version": 0, "copy_error": "",
                         "attempts": retry["attempts"] + 1 if retry else 1, "grade_attempts": 0, "reads": 0,
                         "expires_at": retry["expires_at"] if retry else time.time() + self.TTL}
                self.exams[exam_id] = state
                reserved = state
            if not retry:
                allowed, _used, limit = self.core.consume_daily_usage("exam")
                if not allowed:
                    with self.lock:
                        self.exams.pop(exam_id, None)
                    self.cleanup(files)
                    return self.error(f"Tu as utilisé ta limite de {limit} contrôle(s) blanc(s) gratuit(s) aujourd’hui. Réessaie demain ou découvre Plus.", 429)
            self.core.EXECUTOR.submit(self.generate_worker, exam_id, course, files, level, topic, duration)
            return jsonify(id=exam_id, status="generating"), 202
        except Exception as exc:
            self.cleanup(files)
            if reserved:
                self.fail(reserved["id"], "generation", exc)
            return self.error(str(exc) if isinstance(exc, ValueError) else "Impossible de lancer le contrôle. Réessaie.", 400 if isinstance(exc, ValueError) else 503)

    def update(self, exam_id, **values):
        with self.lock:
            state = self.exams.get(exam_id)
            if state:
                state.update(values)

    def fail(self, exam_id, phase, error):
        self.app.logger.warning("Traitement de contrôle blanc indisponible (%s).", phase)
        messages = {
            "generation": "Impossible de préparer un sujet valide. Vérifie que le cours est lisible puis relance la création.",
            "correction": "La correction n’a pas abouti. Tes réponses sont conservées ; tu peux réessayer.",
        }
        self.update(exam_id, status="error", phase=phase, message=str(error) if isinstance(error, ValueError) else messages[phase])

    def generate_worker(self, exam_id, course, files, level, topic, duration):
        try:
            extracted = self.extract(files, 60000) if files else ""
            combined = "\n\n".join(value for value in (course, extracted) if value)
            if not 80 <= len(combined) <= 60000:
                raise ValueError("Le cours doit contenir entre 80 et 60 000 caractères. Envoie un chapitre lisible.")
            self.update(exam_id, course=combined, course_ready=True, message="Création des questions et vérification du barème sur 20…")
            subject = generate_exam(combined, level, topic, duration)
            self.update(exam_id, subject=subject, status="ready", message="Ton sujet est prêt. Le chronomètre démarrera quand tu commenceras.")
        except Exception as exc:
            self.fail(exam_id, "generation", exc)
        finally:
            self.cleanup(files)

    def status(self, exam_id):
        if request.method == "DELETE" and not self.valid_csrf():
            return self.error("Session expirée. Recharge la page.")
        with self.lock:
            state = self.find(exam_id)
            if not state:
                return self.error("Ce contrôle est introuvable ou a expiré. Les contrôles temporaires durent au maximum 3 heures.", 404)
            if request.method == "DELETE":
                self.exams.pop(exam_id, None)
                return jsonify(success=True)
            return jsonify(self.public(state))

    def start(self, exam_id):
        if not self.valid_csrf():
            return self.error("Session expirée. Recharge la page.")
        with self.lock:
            state = self.find(exam_id)
            if not state:
                return self.error("Ce contrôle a expiré. Crée un nouveau sujet.", 404)
            if state["status"] == "ready":
                now = time.time()
                state.update(status="started", phase="attempt", started_at=now, deadline=now + state["duration"] * 60,
                             message="Réponds aux questions, puis rends ta copie.")
            elif state["status"] != "started":
                return self.error("Le contrôle ne peut pas être commencé dans cet état.", 409)
            return jsonify(self.public(state))

    def read_copy(self, exam_id):
        if not self.valid_csrf():
            return self.error("Session expirée. Recharge la page.")
        with self.lock:
            state = self.find(exam_id)
            if not state:
                return self.error("Ce contrôle a expiré.", 404)
            if not self.gradeable(state) or state["reads"] >= 3:
                return self.error("Attends la fin du traitement. La lecture de copie est limitée à trois envois par contrôle.", 409)
        files = []
        reserved = False
        try:
            files = self.uploads("copy_files")
            if not files:
                return self.error("Sélectionne au moins une photo ou un document de ta copie.")
            with self.lock:
                state = self.find(exam_id)
                if not state or not self.gradeable(state) or state["reads"] >= 3:
                    self.cleanup(files)
                    return self.error("La copie est déjà en cours de traitement.", 409)
                state.update(status="reading", phase="attempt", message="Lecture de ta copie…", copy_error="", reads=state["reads"] + 1)
                reserved = True
            self.core.EXECUTOR.submit(self.read_worker, exam_id, files)
            return jsonify(status="reading"), 202
        except Exception as exc:
            self.cleanup(files)
            if reserved:
                self.update(exam_id, status="started", copy_error="La lecture n’a pas pu démarrer. Réessaie ou saisis tes réponses.")
            return self.error(str(exc) if isinstance(exc, ValueError) else "Impossible de lire la copie. Réessaie.", 400)

    def read_worker(self, exam_id, files):
        try:
            transcript = self.extract(files, 24000)
            with self.lock:
                state = self.exams.get(exam_id)
                if state:
                    state.update(status="started", transcript=transcript, read_version=state["read_version"] + 1,
                                 message="Relis la transcription, corrige les signes ou numéros mal lus, puis rends ta copie.", copy_error="")
        except Exception as exc:
            self.update(exam_id, status="started", copy_error=str(exc) if isinstance(exc, ValueError) else "Lecture impossible. Envoie une photo plus nette ou saisis tes réponses.")
        finally:
            self.cleanup(files)

    @staticmethod
    def gradeable(state):
        return state["status"] == "started" or (state["status"] == "error" and state["phase"] == "correction")

    def grade(self, exam_id):
        if not self.valid_csrf():
            return self.error("Session expirée. Recharge la page avant de rendre ta copie.")
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict) or not isinstance(data.get("transcript", ""), str):
            return self.error("Les réponses envoyées sont invalides.")
        transcript = data.get("transcript", "").strip()
        if len(transcript) > 24000:
            return self.error("La transcription est trop longue (24 000 caractères maximum).")
        with self.lock:
            state = self.find(exam_id)
            if not state:
                return self.error("Ce contrôle a expiré.", 404)
            if state["status"] == "graded":
                return jsonify(self.public(state))
            if not self.gradeable(state) or state["grade_attempts"] >= 3:
                return self.error("La correction est déjà en cours ou la limite de trois tentatives a été atteinte.", 409)
            questions = state["subject"]["questions"]
            if set(data["answers"]) - {q["id"] for q in questions}:
                return self.error("Une réponse ne correspond pas à ce sujet.")
            answers = {}
            for question in questions:
                value = data["answers"].get(question["id"], "")
                if not isinstance(value, str) or len(value) > 4000:
                    return self.error("Chaque réponse doit contenir au maximum 4 000 caractères.")
                answers[question["id"]] = value.strip()
            if state["read_version"] and transcript and data.get("transcript_reviewed") is not True:
                return self.error("Confirme que tu as relu la transcription de ta copie.")
            state.update(status="grading", phase="correction", message="Correction question par question et application du barème…",
                         grade_attempts=state["grade_attempts"] + 1, submitted_at=time.time(), answers=answers, transcript=transcript)
            course, subject = state["course"], copy.deepcopy(state["subject"])
        try:
            self.core.EXECUTOR.submit(self.grade_worker, exam_id, course, subject, answers, transcript)
        except Exception as exc:
            self.fail(exam_id, "correction", exc)
            return self.error("La correction ne peut pas démarrer. Réessaie.", 503)
        return jsonify(status="grading"), 202

    def grade_worker(self, exam_id, course, subject, answers, transcript):
        try:
            result = correct_exam(course, subject, answers, transcript)
            with self.lock:
                state = self.exams.get(exam_id)
                if not state:
                    return
                result["transcript"] = transcript
                result["title"] = subject["title"]
                result["duration"] = state["duration"]
                result["elapsed_seconds"] = max(0, round(state["submitted_at"] - state["started_at"]))
                result["overtime"] = state["submitted_at"] > state["deadline"]
                result["report"] = self.report(result)
                # Libérer le cours après correction ; le sujet reste disponible pour l'export.
                state.update(result=result, status="graded", phase="result", course="", message="Ta correction est prête.")
        except Exception as exc:
            self.fail(exam_id, "correction", exc)

    @staticmethod
    def report(result):
        score = str(result["score"]).replace(".", ",") + " / 20" if result["score"] is not None else "Non attribuée : des questions nécessitent une vérification"
        lines = [f"# {result['title']}", "", f"Note indicative : {score}",
                 f"Points évalués : {result['graded_points']} / {result['graded_out_of']}",
                 f"Durée prévue : {result['duration']} min · temps écoulé : {result['elapsed_seconds'] // 60} min",
                 "Temps prévu dépassé." if result["overtime"] else "Copie rendue dans le temps prévu.",
                 "Correction automatique d’entraînement, à vérifier avec ton cours."]
        if result["transcript"]:
            lines.extend(["", "## Transcription de la copie relue", result["transcript"]])
        for index, question in enumerate(result["questions"], 1):
            awarded = question["earned"] if question["earned"] is not None else "À vérifier"
            lines.extend(["", f"## Question {index} · {awarded} / {question['points']}", question["prompt"],
                          "", "### Ta réponse saisie", question["answer"] or "Voir la transcription commune, si présente.",
                          "", "### Retour sur ta réponse", question["feedback"], "", "### Correction attendue", question["correction"], "", "### Barème détaillé"])
            for criterion in question["criteria"]:
                earned = criterion["earned"] if criterion["earned"] is not None else "À vérifier"
                lines.append(f"- {criterion['description']} : {earned} / {criterion['points']} — {criterion['feedback']}")
        for title, key in (("Points forts", "strengths"), ("À retravailler", "next_steps")):
            lines.extend(["", f"## {title}", *["- " + text for text in result[key]]])
        return "\n".join(lines)
