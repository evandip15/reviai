import json
import os
import random
import re
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.getenv("REVIAI_MODEL", "llama3.2")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
PROVIDER = os.getenv("AI_PROVIDER", "auto").lower().strip()

REVISION_PROMPT = """
Tu es RéviAI, une IA de révision scolaire.

Transforme le cours fourni en fiche de révision claire, détaillée et fidèle.

RÈGLES ABSOLUES
- Utilise uniquement les informations présentes dans le cours.
- N'ajoute aucune connaissance extérieure.
- N'invente aucun nom, auteur, œuvre, date, chiffre, formule, citation ou exemple.
- Conserve exactement les chiffres, unités, calculs et formules utiles.
- Tu peux corriger une formulation maladroite sans changer le sens.
- Conserve les informations importantes pour un contrôle.
- Regroupe les idées proches sans supprimer les informations utiles.
- Utilise des titres et sous-titres adaptés au contenu réel.
- Mets les mots-clés, chiffres et formules importants en **gras**.
- Pour les calculs, garde le calcul et le résultat.
- Pour l'analyse littéraire, conserve les mouvements, procédés, images et effets présents dans le cours.
- Ne crée ni quiz ni questions dans la fiche.

Commence par :
# Fiche de révision

Retourne uniquement la fiche finale.
""".strip()


def _ollama(prompt, *, json_mode=False, temperature=0.05, num_predict=5000):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if json_mode:
        payload["format"] = "json"

    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama est inaccessible. Vérifie qu'Ollama est lancé et que le modèle '{OLLAMA_MODEL}' est installé."
        ) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Réponse invalide reçue depuis Ollama.") from exc

    result = str(parsed.get("response", "")).strip()
    if not result:
        raise RuntimeError("Ollama n'a renvoyé aucun contenu.")
    return result


def _gemini_config(*, json_mode=False, max_output_tokens=5000, temperature=0.05):
    from google.genai import types
    kwargs = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    return types.GenerateContentConfig(**kwargs)


def _gemini(prompt, *, json_mode=False, temperature=0.05, max_output_tokens=5000):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY manque dans la configuration du site.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Dépendance google-genai manquante. Lance pip install -r requirements.txt") from exc

    client = genai.Client(api_key=key)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=_gemini_config(
                json_mode=json_mode,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            ),
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini n'a pas pu générer la réponse : {exc}") from exc

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini n'a renvoyé aucun contenu.")
    return text.strip()


def _read_binary(path):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Impossible de lire le fichier : {exc}") from exc


def _gemini_file_part(path, mime_type):
    from google.genai import types
    return types.Part.from_bytes(data=_read_binary(path), mime_type=mime_type)


def extract_image_text_gemini(image_path, mime_type=None):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY manque dans la configuration du site.")

    if not mime_type:
        mime_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(Path(image_path).suffix.lower(), "image/jpeg")

    prompt = """
Tu es le module de lecture de RéviAI.

Transcris fidèlement cette photo de cours ou de cahier.
- Recopie tout le texte lisible dans l'ordre de lecture.
- Conserve titres, sous-titres, listes, numéros, dates, chiffres, unités, formules et exemples.
- Ne résume pas.
- N'invente aucun contenu.
- Si un mot est réellement illisible, écris [illisible].
- Réponds uniquement avec la transcription.
""".strip()

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Dépendance google-genai manquante.") from exc

    try:
        client = genai.Client(api_key=key)
        part = _gemini_file_part(image_path, mime_type)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, part],
            config=_gemini_config(max_output_tokens=6000, temperature=0),
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini n'a pas pu lire la photo : {exc}") from exc

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise RuntimeError("Gemini n'a trouvé aucun texte lisible dans la photo.")
    return text.strip()


def extract_document_text_gemini(pdf_path):
    """Lecture de secours des PDF avec la vision native de Gemini."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY manque dans la configuration du site.")

    prompt = """
Tu es le module de lecture documentaire de RéviAI.

Lis ce PDF avec précision pour fournir une transcription exploitable par un autre module.
- Recopie tout le texte important, dans l'ordre naturel de lecture.
- Conserve titres, sous-titres, listes, tableaux, chiffres, dates, unités, formules et exemples.
- Prends également en compte le texte visible dans les pages scannées.
- Ne résume pas.
- N'invente rien.
- Si une partie est réellement illisible, écris [illisible].
- Réponds uniquement avec le contenu transcrit.
""".strip()

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Dépendance google-genai manquante.") from exc

    try:
        client = genai.Client(api_key=key)
        part = _gemini_file_part(pdf_path, "application/pdf")
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, part],
            config=_gemini_config(max_output_tokens=8000, temperature=0),
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini n'a pas pu lire le PDF : {exc}") from exc

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise RuntimeError("Gemini n'a trouvé aucun contenu exploitable dans le PDF.")
    return text.strip()


def generate_text(prompt, *, json_mode=False, temperature=0.05, num_predict=5000):
    if PROVIDER == "gemini":
        return _gemini(prompt, json_mode=json_mode, temperature=temperature, max_output_tokens=num_predict)
    if PROVIDER == "ollama":
        return _ollama(prompt, json_mode=json_mode, temperature=temperature, num_predict=num_predict)
    if os.getenv("GEMINI_API_KEY"):
        return _gemini(prompt, json_mode=json_mode, temperature=temperature, max_output_tokens=num_predict)
    return _ollama(prompt, json_mode=json_mode, temperature=temperature, num_predict=num_predict)


def generate_revision(course):
    return generate_text(
        REVISION_PROMPT + "\n\nCOURS À ANALYSER :\n\n" + course + "\n\nFIN DU COURS.\n",
        temperature=0.05,
        num_predict=4500,
    )


def _extract_json(text):
    cleaned = text.strip().lstrip("\ufeff")
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for idx, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[idx:])
                return value
            except json.JSONDecodeError:
                continue
    raise ValueError("JSON introuvable dans la réponse de l'IA.")


def _validate_questions(data, count):
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return []
    clean = []
    seen = set()
    for raw in data["questions"]:
        if not isinstance(raw, dict):
            continue
        question = str(raw.get("question", "")).strip()
        answers = raw.get("answers")
        explanation = str(raw.get("explanation", "")).strip()
        correct = raw.get("correct")
        if not question or not isinstance(answers, list) or len(answers) != 4:
            continue
        answers = [str(x).strip() for x in answers]
        if any(not x for x in answers):
            continue
        if len({x.casefold() for x in answers}) != 4:
            continue
        try:
            correct = int(correct)
        except (TypeError, ValueError):
            continue
        if correct not in range(4):
            continue
        key = re.sub(r"\s+", " ", question.casefold())
        if key in seen:
            continue
        seen.add(key)
        clean.append({
            "question": question,
            "answers": answers,
            "correct": correct,
            "explanation": explanation or "Cette réponse est fondée sur le cours fourni.",
        })
    return clean[:count]


def _quiz_prompt(course, banned, count):
    seed = random.randint(100000, 999999)
    return f"""
Tu es le générateur de quiz de RéviAI.

À partir UNIQUEMENT du cours ci-dessous, crée exactement {count} questions.

RÈGLES :
- Exactement 4 réponses par question.
- Une seule réponse correcte.
- Les 3 mauvaises réponses doivent être plausibles et liées au cours.
- Les questions doivent tester la compréhension et pas seulement la copie d'une phrase.
- Ne demande aucune information absente du cours.
- Mélange la position de la bonne réponse.
- Les questions doivent être différentes de celles déjà utilisées.
- Pas de markdown autour du JSON.
- "correct" est un entier 0, 1, 2 ou 3.
- "explanation" explique brièvement la bonne réponse à partir du cours.
- Variation : {seed}

QUESTIONS DÉJÀ UTILISÉES À ÉVITER :
{banned}

FORMAT EXACT :
{{
  "questions": [
    {{
      "question": "...",
      "answers": ["...", "...", "...", "..."],
      "correct": 0,
      "explanation": "..."
    }}
  ]
}}

COURS :
{course}

FIN DU COURS.
""".strip()


def generate_quiz(course, *, recent_questions, count=10):
    banned = "\n".join(f"- {q}" for q in recent_questions[-30:]) or "Aucune question précédente."
    errors = []
    for attempt in range(3):
        try:
            text = generate_text(
                _quiz_prompt(course, banned, count),
                json_mode=True,
                temperature=0.2 if attempt == 0 else 0.05,
                num_predict=4500,
            )
            data = _extract_json(text)
            questions = _validate_questions(data, count)
            if len(questions) >= min(8, count):
                random.shuffle(questions)
                return {"questions": questions[:count]}
            errors.append(f"tentative {attempt + 1}: {len(questions)} questions valides")
        except Exception as exc:
            errors.append(f"tentative {attempt + 1}: {exc}")
    raise RuntimeError("Impossible de générer un quiz valide. " + " | ".join(errors))
