import json
import os
import random
import re
import urllib.error
import urllib.request
from fractions import Fraction
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
- Écris les formules dans une notation directement lisible : H₂O, CO₂, x², 10⁻³, → et ≈. N'utilise pas les délimiteurs LaTeX $...$ ni les commandes comme \\rightarrow ou \\frac, car elles s'afficheraient comme du texte.
- Pour les calculs, garde le calcul et le résultat.
- Pour l'analyse littéraire, conserve les mouvements, procédés, images et effets présents dans le cours.
- Ne crée ni quiz ni questions dans la fiche.

Commence par :
# Fiche de révision

Retourne uniquement la fiche finale.
""".strip()


EXERCISE_TEXT_PROMPT = """
MODE DE RÉPONSE : si la consigne demande un indice ou une aide pas à pas, elle prime sur les règles de correction complète plus bas. Garde d'abord l'en-tête exact de l'exercice choisi. Pour un indice, ajoute seulement le titre « ## Indice » et un seul indice. Pour l'aide pas à pas, ajoute seulement le titre « ## Étape suivante », donne la prochaine étape puis invite l'élève à continuer.
Le numéro et la sélection exacte des sous-questions transmis par l'élève priment sur les règles générales ci-dessous.
Tu es le tuteur pédagogique de RéviAI. Résous l'exercice fourni en français.

Consignes :
- Résous EXCLUSIVEMENT l'exercice désigné par l'élève. Ne donne aucune solution, réponse ou remarque sur les exercices voisins.
- Résous les sous-questions demandées par l'élève (toutes si aucune sélection n'est précisée), mais aucune partie appartenant à un autre exercice.
- Garde l'ordre et le numéro de chaque sous-question. Réponds à chacune une seule fois, sans répéter la résolution.
- Commence par indiquer le numéro ou le titre de l'exercice choisi et reformule uniquement son énoncé.
- Si tu ne peux pas distinguer avec certitude le bon exercice dans le texte OCR, ne résous rien : demande une photo recadrée ou une précision.
- Pour un exercice de mathématiques, écris les étapes de calcul une par une et justifie les formules, théorèmes et transformations utilisés. Ne saute pas les calculs intermédiaires.
- Pour un cube ABCDEFGH standard, si la base demandée est (AB, AD, AE), travaille avec les coordonnées A=(0,0,0), B=(1,0,0), D=(0,1,0), E=(0,0,1), G=(1,1,1). Un vecteur XY a pour coordonnées celles de Y moins celles de X. En particulier EG=AB+AD, EB=AB-AE, et si AK=pAB+qAD+rAE alors EK=pAB+qAD+(r-1)AE.
- Dans une égalité de vecteurs, compare les coefficients de AB, AD et AE. Ne confonds jamais un vecteur avec sa longueur : n'utilise pas de racine carrée ni le théorème de Pythagore pour exprimer un vecteur.
- Si l'on demande EK=xEG+yEB, déduis x et y en comparant les trois coefficients, puis vérifie les trois égalités.
- Une relation EK=xEG+yEB montre que les trois vecteurs sont coplanaires; avec l'origine E, cela signifie que les points E, G, B et K sont coplanaires. Ne suppose pas que K appartient à la surface du cube.
- En spécialité mathématiques au lycée, explicite les conditions de validité (ensemble de définition, signe, cas possibles, hypothèses) quand elles s'appliquent.
- Garde les valeurs exactes, les signes, les exposants, les fractions et les unités. Ne remplace pas une valeur exacte par une approximation sans le signaler.
- Vérifie le résultat par substitution, dérivation inverse, calcul indépendant ou contrôle adapté au problème.
- Si l'OCR a pu confondre un signe, un exposant, une fraction ou une racine, indique précisément l'ambiguïté et demande confirmation au lieu de deviner.
- Présente la réponse avec les sections : « Énoncé choisi », « Méthode », « Résolution détaillée », « Vérification » et « Réponse ».
- Adapte l'explication au niveau et à la matière indiqués par l'élève. Écris les expressions mathématiques dans une notation textuelle lisible (par exemple x^2, sqrt(x), a/b) et utilise un Markdown lisible.
- Chaque section demandée apparaît une seule fois. N'ajoute pas une deuxième solution ou un second bloc « Réponse ».

Retourne uniquement la résolution.
""".strip()


EXERCISE_IMAGE_PROMPT = """
MODE DE RÉPONSE : si la consigne demande un indice ou une aide pas à pas, elle prime sur les règles de correction complète plus bas. Garde d'abord l'en-tête exact de l'exercice choisi. Pour un indice, ajoute seulement le titre « ## Indice » et un seul indice. Pour l'aide pas à pas, ajoute seulement le titre « ## Étape suivante », donne la prochaine étape puis invite l'élève à continuer.
Le numéro et la sélection exacte des sous-questions transmis par l'élève priment sur les règles générales ci-dessous.
Tu es le tuteur pédagogique de RéviAI. Lis l'exercice visible sur les images jointes et résous-le en français.

Consignes :
- Si plusieurs images sont jointes, lis-les ensemble dans l'ordre d'envoi : elles peuvent montrer les pages successives du même exercice.
- Résous EXCLUSIVEMENT l'exercice désigné par l'élève. Ne donne aucune solution, réponse ou remarque sur les exercices voisins.
- Résous les sous-questions demandées par l'élève (toutes si aucune sélection n'est précisée), mais aucune partie appartenant à un autre exercice.
- Garde l'ordre et le numéro de chaque sous-question. Réponds à chacune une seule fois, sans répéter la résolution.
- Commence par indiquer le numéro ou le titre de l'exercice choisi et retranscris uniquement son énoncé.
- Si tu ne peux pas distinguer avec certitude le bon exercice sur la photo, ne résous rien : demande une photo recadrée ou une précision.
- Pour un exercice de mathématiques, écris les étapes de calcul une par une et justifie les formules, théorèmes et transformations utilisés. Ne saute pas les calculs intermédiaires.
- Pour un cube ABCDEFGH standard, si la base demandée est (AB, AD, AE), travaille avec les coordonnées A=(0,0,0), B=(1,0,0), D=(0,1,0), E=(0,0,1), G=(1,1,1). Un vecteur XY a pour coordonnées celles de Y moins celles de X. En particulier EG=AB+AD, EB=AB-AE, et si AK=pAB+qAD+rAE alors EK=pAB+qAD+(r-1)AE.
- Dans une égalité de vecteurs, compare les coefficients de AB, AD et AE. Ne confonds jamais un vecteur avec sa longueur : n'utilise pas de racine carrée ni le théorème de Pythagore pour exprimer un vecteur.
- Si l'on demande EK=xEG+yEB, déduis x et y en comparant les trois coefficients, puis vérifie les trois égalités.
- Une relation EK=xEG+yEB montre que les trois vecteurs sont coplanaires; avec l'origine E, cela signifie que les points E, G, B et K sont coplanaires. Ne suppose pas que K appartient à la surface du cube.
- En spécialité mathématiques au lycée, explicite les conditions de validité (ensemble de définition, signe, cas possibles, hypothèses) quand elles s'appliquent.
- Garde les valeurs exactes, les signes, les exposants, les fractions et les unités. Ne remplace pas une valeur exacte par une approximation sans le signaler.
- Vérifie le résultat par substitution, dérivation inverse, calcul indépendant ou contrôle adapté au problème.
- Si un signe, un exposant, une fraction ou une racine est illisible, dis exactement ce qui est ambigu et demande confirmation au lieu de deviner.
- Présente la réponse avec les sections : « Énoncé choisi », « Méthode », « Résolution détaillée », « Vérification » et « Réponse ».
- Adapte l'explication au niveau et à la matière indiqués par l'élève. Écris les expressions mathématiques dans une notation textuelle lisible (par exemple x^2, sqrt(x), a/b) et utilise un Markdown lisible.
- Chaque section demandée apparaît une seule fois. N'ajoute pas une deuxième solution ou un second bloc « Réponse ».

Retourne uniquement la résolution.
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


def _add_exercise_instruction(prompt, instruction):
    instruction = str(instruction or "").strip()
    if not instruction:
        return prompt
    return (
        prompt
        + "\n\nCONSIGNE DE L'ÉLÈVE :\n"
        + instruction
        + "\nRésous seulement l'exercice correspondant à cette demande."
    )


def _exercise_request_instruction(
    instruction="",
    exercise_number=None,
    selected_questions=None,
    help_mode="complete",
):
    parts = []
    if exercise_number is not None:
        parts.append(
            f"L'élève a choisi exclusivement l'exercice {exercise_number}. "
            f"Commence par l'en-tête exact « # Exercice {exercise_number} ». "
            "N'en traite aucun autre, même s'il est visible dans la source."
        )
    questions = [str(item).strip() for item in (selected_questions or []) if str(item).strip()]
    if questions:
        parts.append(
            "Traite uniquement les sous-questions suivantes : "
            + ", ".join(questions)
            + ". N'ajoute aucune autre sous-question."
        )
    else:
        parts.append("Traite toutes les sous-questions de l'exercice choisi, une seule fois chacune.")

    if help_mode == "hint":
        parts.append(
            "Mode indice : donne seulement un premier indice utile, sans calculer la réponse finale "
            "ni révéler toute la méthode. Garde l'en-tête exact de l'exercice, puis réponds avec le titre « ## Indice » et termine en invitant l'élève à essayer. "
            "Cette consigne remplace le format de correction complète demandé ailleurs."
        )
    elif help_mode == "step_by_step":
        parts.append(
            "Mode pas à pas : guide l'élève avec une étape à la fois, explique la prochaine action "
            "et laisse-lui une courte question pour qu'il poursuive. Garde l'en-tête exact de l'exercice, puis réponds sous le titre « ## Étape suivante ». "
            "Ne donne pas toute la correction; cette consigne remplace le format de correction complète demandé ailleurs."
        )
    else:
        parts.append(
            "Mode correction complète : explique la méthode, détaille les calculs, vérifie le résultat "
            "et termine par une réponse claire."
        )

    extra = str(instruction or "").strip()
    if extra:
        parts.append("Matière, niveau ou précision de l'élève : " + extra)
    return "\n".join(parts)


def solve_exercise_text(
    exercise_text,
    instruction="",
    *,
    exercise_number=None,
    selected_questions=None,
    help_mode="complete",
):
    exercise_text = str(exercise_text or "").strip()
    if not exercise_text:
        raise RuntimeError("Aucun énoncé lisible n'a été trouvé dans la photo.")
    task_instruction = _exercise_request_instruction(
        instruction, exercise_number, selected_questions, help_mode
    )
    if help_mode == "complete" and not selected_questions:
        cube_solution = _solve_cube_vector_exercise(exercise_text, task_instruction)
        if cube_solution:
            return cube_solution
    answer = generate_text(
        _add_exercise_instruction(
            EXERCISE_TEXT_PROMPT + "\n\nÉNONCÉ DE L'EXERCICE :\n\n" + exercise_text,
            task_instruction,
        ),
        temperature=0,
        num_predict=5000,
    )
    return _keep_first_exercise_answer(answer)


def _format_fraction(value):
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _format_vector_combination(terms):
    parts = []
    for coefficient, vector in terms:
        coefficient = Fraction(coefficient)
        if coefficient == 0:
            continue
        sign = "-" if coefficient < 0 else "+"
        magnitude = abs(coefficient)
        value = "" if magnitude == 1 else _format_fraction(magnitude) + " "
        body = value + vector
        if not parts:
            parts.append(("-" if sign == "-" else "") + body)
        else:
            parts.append(f" {sign} {body}")
    return "".join(parts) or "0"


def _solve_cube_vector_exercise(exercise_text, instruction=""):
    """Résout exactement le format cube/coordonnées vectorielles reconnu dans le texte."""
    text = str(exercise_text or "").upper()
    for source, replacement in (("Ë", "E"), ("É", "E"), ("È", "E"), ("−", "-"), ("–", "-"), ("—", "-")):
        text = text.replace(source, replacement)

    if "CUBE" not in text and "ABCDEFGH" not in text:
        return None
    if not all(re.search(rf"\b{vector}\b", text) for vector in ("EG", "EB", "EK")):
        return None
    if not re.search(r"EK\s*=\s*X\s*EG\s*\+\s*Y\s*EB", text):
        return None

    equation = re.search(r"AK\s*=\s*([^\r\n;]*?A\s*E)\b", text)
    if not equation:
        return None
    expression = re.sub(r"[.!?]+\s*$", "", equation.group(1)).replace(" ", "").replace("*", "")
    term_pattern = re.compile(r"([+-]?)(?:(\d+/\d+|\d+(?:[.,]\d*)?|[.,]\d+))?A([BDE])")
    coefficients = {"B": Fraction(0), "D": Fraction(0), "E": Fraction(0)}
    position = 0
    found_terms = 0
    while position < len(expression):
        match = term_pattern.match(expression, position)
        if not match:
            return None
        sign, magnitude, direction = match.groups()
        try:
            coefficient = Fraction((magnitude or "1").replace(",", "."))
        except (ValueError, ZeroDivisionError):
            return None
        if sign == "-":
            coefficient = -coefficient
        coefficients[direction] += coefficient
        found_terms += 1
        position = match.end()
    if found_terms < 2:
        return None

    p, q, r = coefficients["B"], coefficients["D"], coefficients["E"]
    ek_e = r - 1
    x, y = q, 1 - r
    has_solution = p == x + y

    exercise_match = re.search(
        r"\b(?:exercice|exo|num[eé]ro)\s*(?:n\s*[°o.]?\s*)?(\d{1,3})\b|^\s*(?:le\s+|n\s*[°o.]?\s*|#)?(\d{1,3})(?=\s|$|[,;—-])",
        str(instruction or ""),
        flags=re.IGNORECASE,
    )
    exercise_number = next((group for group in exercise_match.groups() if group), None) if exercise_match else None
    title = f"Exercice {exercise_number}" if exercise_number else "Exercice demandé"

    ak_expression = _format_vector_combination(((p, "AB"), (q, "AD"), (r, "AE")))
    ek_expression = _format_vector_combination(((p, "AB"), (q, "AD"), (ek_e, "AE")))
    output = [
        f"# {title}",
        "",
        "## 1. Exprimer EG, EB et EK",
        "Dans le cube standard, les coordonnées des sommets sont prises dans la base vectorielle (AB, AD, AE). Ainsi, E=(0,0,1), B=(1,0,0) et G=(1,1,1). Un vecteur XY se calcule en faisant coordonnées de Y moins coordonnées de X.",
        "",
        "- EG = G - E = (1,1,0) = AB + AD.",
        "- EB = B - E = (1,0,-1) = AB - AE.",
        f"- Par Chasles, EK = EA + AK = -AE + ({ak_expression}) = {ek_expression}.",
        "",
        "## 2. Déterminer x et y",
        f"On cherche x et y tels que EK = xEG + yEB. Or xEG + yEB = (x+y)AB + xAD - yAE.",
        f"En comparant avec EK = {ek_expression}, le coefficient de AD donne x = {_format_fraction(q)} et celui de AE donne y = {_format_fraction(y)}.",
    ]
    if has_solution:
        output.extend([
            f"Le coefficient de AB est bien vérifié : x+y = {_format_fraction(x)} + {_format_fraction(y)} = {_format_fraction(p)}.",
            "",
            "## 3. Ce qu’on en déduit pour les vecteurs",
            f"EK = {_format_fraction(x)}EG + {_format_fraction(y)}EB. Le vecteur EK est donc une combinaison linéaire de EG et EB : les trois vecteurs sont coplanaires (linéairement dépendants).",
            "",
            "## 4. Ce qu’on en déduit pour les points",
            "Comme les vecteurs EB, EG et EK ont la même origine E et sont coplanaires, les points B, E, G et K sont coplanaires.",
            "",
            "## Vérification",
            f"{_format_fraction(x)}EG + {_format_fraction(y)}EB = {_format_fraction(x)}(AB + AD) + {_format_fraction(y)}(AB - AE) = {ek_expression} = EK.",
            "",
            f"## Réponse\n**x = {_format_fraction(x)} et y = {_format_fraction(y)}. Les points B, E, G et K sont coplanaires.**",
        ])
    else:
        output.extend([
            f"Le coefficient de AB imposerait x+y = {_format_fraction(p)}, mais les deux autres coefficients donnent x+y = {_format_fraction(x + y)}. Ces valeurs sont différentes.",
            "",
            "## 3. Ce qu’on en déduit pour les vecteurs",
            "Il n’existe pas de relation EK = xEG + yEB. Le vecteur EK n’appartient donc pas au plan engendré par EG et EB; les trois vecteurs ne sont pas coplanaires.",
            "",
            "## 4. Ce qu’on en déduit pour les points",
            "Les points B, E, G et K ne sont pas coplanaires.",
            "",
            "## Réponse\n**Il n’existe pas de réels x et y satisfaisant l’égalité.**",
        ])
    return "\n".join(output)


def _keep_first_exercise_answer(answer):
    """Évite qu'un modèle qui boucle renvoie plusieurs fois la même solution."""
    text = str(answer or "").strip()
    starts = list(re.finditer(r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?Énoncé choisi\b", text))
    if len(starts) > 1:
        text = text[:starts[1].start()].rstrip()
    return text


def solve_exercise_images(
    images,
    instruction="",
    *,
    exercise_number=None,
    selected_questions=None,
    help_mode="complete",
    selected_statement="",
):
    """Résout un exercice montré sur une ou plusieurs images ordonnées."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY manque dans la configuration du site.")
    if not images:
        raise RuntimeError("Aucune photo n'a été envoyée.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Dépendance google-genai manquante.") from exc

    try:
        client = genai.Client(api_key=key)
        parts = []
        for image_path, mime_type in images:
            if not mime_type:
                mime_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                }.get(Path(image_path).suffix.lower(), "image/jpeg")
            parts.append(_gemini_file_part(image_path, mime_type))
        task_instruction = _exercise_request_instruction(
            instruction, exercise_number, selected_questions, help_mode
        )
        prompt = _add_exercise_instruction(EXERCISE_IMAGE_PROMPT, task_instruction)
        if selected_statement:
            prompt += (
                "\n\nÉNONCÉ TRANSCRIT ET SÉLECTIONNÉ PAR L'ÉLÈVE :\n"
                "Utilise cet énoncé comme référence pour identifier précisément l'exercice et ses questions. "
                "Les photos peuvent contenir des exercices voisins : ignore-les. Les photos servent aussi "
                "à lire les schémas et les données visuelles.\n\n"
                + str(selected_statement)[:12000]
            )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt, *parts],
            config=_gemini_config(max_output_tokens=6000, temperature=0),
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini n'a pas pu résoudre les photos : {exc}") from exc

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise RuntimeError("L'IA n'a pas pu lire ou résoudre l'exercice sur les photos.")
    return _keep_first_exercise_answer(text)


def solve_exercise_image(image_path, mime_type=None, instruction=""):
    """Compatibilité pour un exercice envoyé sur une seule photo."""
    return solve_exercise_images([(image_path, mime_type)], instruction=instruction)


def chat_about_exercise(context, initial_solution, history, message, instruction=""):
    """Répond à une question de suivi en gardant le contexte de la photo."""
    history_text = "\n".join(
        f"{('Élève' if item.get('role') == 'user' else 'RéviAI')}: {str(item.get('content', ''))[:3000]}"
        for item in (history or [])[-12:]
        if isinstance(item, dict)
    ) or "Aucun échange précédent."

    prompt = f"""
Si l'élève demande un indice, ne révèle pas la solution complète. S'il demande l'étape suivante, donne uniquement la prochaine étape puis laisse-lui essayer.
Tu es le tuteur pédagogique de RéviAI. Continue la conversation en français.
- Réponds précisément à la dernière demande de l'élève.
- Ne traite qu'un seul exercice par réponse. Si un numéro est demandé, ne résous pas les exercices voisins.
- Garde le contexte de la photo et de l'exercice choisi ; ne mélange pas les exercices.
- Si l'élève demande un autre numéro d'exercice, utilise l'énoncé disponible dans le contexte. S'il manque, demande une photo ou l'énoncé correspondant.
- Pour les mathématiques, détaille chaque calcul intermédiaire, justifie les propriétés utilisées et vérifie la réponse.
- Recalcule à partir de l'énoncé : la résolution précédente peut contenir une erreur. Corrige-la clairement si nécessaire.
- Pour un cube ABCDEFGH standard avec la base (AB, AD, AE), calcule les vecteurs par différence de coordonnées. En particulier EG=AB+AD, EB=AB-AE, et si AK=pAB+qAD+rAE alors EK=pAB+qAD+(r-1)AE. Compare les coefficients dans toute égalité de vecteurs; ne confonds jamais vecteurs et longueurs.
- Explique avec des étapes simples et n'invente aucune donnée.
- Si l'élève demande une correction ou reformulation, reprends la solution en conséquence.
- Utilise un Markdown lisible.

CHOIX INITIAL DE L'ÉLÈVE :
{instruction or 'Aucun numéro précisé.'}

TEXTE LU SUR LA PHOTO OU CONTEXTE DE L'EXERCICE :
{str(context or '')[:12000]}

PREMIÈRE RÉSOLUTION :
{str(initial_solution or '')[:12000]}

CONVERSATION JUSQU'ICI :
{history_text}

DERNIER MESSAGE DE L'ÉLÈVE :
{str(message or '').strip()[:1500]}

Réponds uniquement au dernier message.
""".strip()

    answer = generate_text(prompt, temperature=0, num_predict=3500)
    return _keep_first_exercise_answer(answer)


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
    banned_questions = list(recent_questions[-30:])
    questions = []
    errors = []

    for attempt in range(5):
        remaining = count - len(questions)

        if remaining <= 0:
            break

        banned = "\n".join(
            f"- {q}"
            for q in banned_questions
        ) or "Aucune question précédente."

        try:
            text = generate_text(
                _quiz_prompt(course, banned, remaining),
                json_mode=True,
                temperature=0.2 if attempt == 0 else 0.05,
                num_predict=4500,
            )

            data = _extract_json(text)
            new_questions = _validate_questions(data, remaining)

            added = 0

            for question in new_questions:
                question_text = re.sub(
                    r"\s+",
                    " ",
                    str(question.get("question", "")).casefold()
                )

                existing_questions = {
                    re.sub(
                        r"\s+",
                        " ",
                        str(item.get("question", "")).casefold()
                    )
                    for item in questions
                }

                if question_text in existing_questions:
                    continue

                questions.append(question)
                banned_questions.append(
                    question.get("question", "")
                )

                added += 1

                if len(questions) >= count:
                    break

            if added == 0:
                errors.append(
                    f"tentative {attempt + 1}: aucune nouvelle question"
                )
            else:
                errors.append(
                    f"tentative {attempt + 1}: +{added} question(s)"
                )

        except Exception as exc:
            errors.append(
                f"tentative {attempt + 1}: {exc}"
            )

    if len(questions) < count:
        raise RuntimeError(
            f"Impossible de générer exactement {count} questions "
            f"(seulement {len(questions)} valides). "
            + " | ".join(errors)
        )

    random.shuffle(questions)

    return {
        "questions": questions[:count]
    }
