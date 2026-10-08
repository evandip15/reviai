import base64
import json
import os
import re
import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_revision_package(course_text: str, images: list[dict]):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY manquante")

    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()

    system_prompt = """
Tu es RéviAI, un assistant de révision scolaire en français.

À partir des documents fournis, produis UNE fiche de révision complète ET un quiz.
Tu dois rester strictement fidèle au contenu fourni : n'invente aucun fait absent des documents.
La sortie doit être entièrement en français, même si le document source est en anglais ou en italien,
sauf pour les mots, expressions, citations ou notions étrangères qui doivent être conservés quand ils sont utiles.

FICHE :
- Structure claire avec titres, sous-titres et listes.
- Garde les définitions importantes, idées, arguments, exemples, auteurs, œuvres, dates, notions, figures de style et chiffres.
- Pour un texte littéraire, indique les mouvements/axes, procédés et leur effet lorsque ces éléments sont présents dans le document.
- Pour les sciences, conserve précisément les unités, valeurs et relations importantes.
- Reformule pour faciliter l'apprentissage mais sans déformer le cours.
- Ajoute une section "À retenir" à la fin.

QUIZ :
- 10 questions différentes basées uniquement sur le contenu fourni.
- 4 réponses plausibles par question.
- Une seule bonne réponse.
- Les mauvaises réponses doivent être crédibles et proches du cours pour éviter la déduction facile.
- Évite les questions de type "complète la phrase".
- Varie les types de questions : distinction de notions, application, lien entre idées, dates/chiffres, raisonnement, identification d'un exemple ou d'un procédé.
- L'ordre des bonnes réponses doit être mélangé.
- Fournis une explication courte pour chaque correction.
- Le quiz doit être différent d'un quiz précédent quand une valeur de variation est fournie.

RENVOIE UNIQUEMENT UN OBJET JSON VALIDE, SANS MARKDOWN AUTOUR, avec exactement cette forme :
{
  "fiche_markdown": "...",
  "quiz": [
    {
      "question": "...",
      "options": ["...", "...", "...", "..."],
      "answer": 0,
      "explanation": "..."
    }
  ]
}
"""

    variation = os.urandom(8).hex()
    parts = [
        {"text": system_prompt},
        {"text": f"Variation de ce test : {variation}"},
    ]

    if course_text:
        # Avoid excessively huge prompts while retaining the beginning and end.
        max_chars = 90000
        if len(course_text) > max_chars:
            half = max_chars // 2
            course_text = course_text[:half] + "\n\n[CONTENU CENTRAL RÉDUIT]\n\n" + course_text[-half:]
        parts.append({"text": "CONTENU DES DOCUMENTS :\n" + course_text})

    for image in images:
        parts.append({
            "inline_data": {
                "mime_type": image["mime_type"],
                "data": image["base64"]
            }
        })
        parts.append({"text": f"Photo associée au fichier : {image['filename']}. Analyse son contenu scolaire."})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
            "maxOutputTokens": 7000
        }
    }

    url = API_URL.format(model=model)
    response = requests.post(
        url,
        params={"key": api_key},
        json=payload,
        timeout=180
    )
    if not response.ok:
        try:
            details = response.json().get("error", {})
            msg = details.get("message", response.text)
        except Exception:
            msg = response.text
        raise RuntimeError(f"Gemini {response.status_code}: {msg}")

    data = response.json()
    text = ""
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                text += part["text"]

    if not text.strip():
        raise RuntimeError("Gemini n'a renvoyé aucun contenu.")

    result = _extract_json(text)
    if not isinstance(result, dict):
        raise RuntimeError("Réponse Gemini invalide.")

    quiz = result.get("quiz", [])
    if not isinstance(quiz, list):
        quiz = []

    clean_quiz = []
    for item in quiz[:10]:
        if not isinstance(item, dict):
            continue
        options = item.get("options", [])
        answer = item.get("answer", 0)
        try:
            answer = int(answer)
        except Exception:
            answer = 0
        if not isinstance(options, list) or len(options) != 4 or answer not in range(4):
            continue
        clean_quiz.append({
            "question": str(item.get("question", "")),
            "options": [str(x) for x in options],
            "answer": answer,
            "explanation": str(item.get("explanation", ""))
        })

    return {
        "fiche_markdown": str(result.get("fiche_markdown", "")),
        "quiz": clean_quiz
    }
