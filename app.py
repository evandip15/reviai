import os
import uuid
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

from ai import generate_revision_package
from lecteur import lire_fichier

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

EXTENSIONS_AUTORISEES = {
    ".pdf", ".docx", ".doc", ".odt", ".pptx", ".txt",
    ".jpg", ".jpeg", ".png", ".webp"
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/ads.txt")
def ads_txt():
    return send_from_directory(BASE_DIR, "ads.txt", mimetype="text/plain")

@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "RéviAI"})

@app.post("/api/generate-from-files")
def generate_from_files():
    files = request.files.getlist("files")
    if not files or all(not f.filename for f in files):
        return jsonify({"error": "Aucun fichier reçu."}), 400

    extracted_parts = []
    image_parts = []
    temp_paths = []

    try:
        for file in files:
            if not file.filename:
                continue

            original_name = Path(file.filename).name
            suffix = Path(original_name).suffix.lower()
            if suffix not in EXTENSIONS_AUTORISEES:
                return jsonify({"error": f"Format non pris en charge : {suffix or 'inconnu'}"}), 400

            safe_name = f"{uuid.uuid4().hex}{suffix}"
            saved_path = UPLOAD_DIR / safe_name
            file.save(saved_path)
            temp_paths.append(saved_path)

            result = lire_fichier(saved_path)
            if result.get("type") == "image":
                image_parts.append({
                    "filename": original_name,
                    "mime_type": result["mime_type"],
                    "base64": result["base64"]
                })
            else:
                text = result.get("text", "").strip()
                if text:
                    extracted_parts.append(f"\n===== FICHIER : {original_name} =====\n{text}")

        course_text = "\n".join(extracted_parts).strip()
        if not course_text and not image_parts:
            return jsonify({"error": "Impossible d'extraire du contenu de ces fichiers."}), 422

        package = generate_revision_package(course_text, image_parts)

        return jsonify({
            "ok": True,
            "fiche": package.get("fiche_markdown", ""),
            "quiz": package.get("quiz", []),
            "files_count": len([f for f in files if f.filename])
        })

    except Exception as exc:
        message = str(exc)
        if "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
            message = "Le quota Gemini est temporairement atteint. Réessaie après la réinitialisation du quota."
        elif "API_KEY" in message or "api key" in message.lower():
            message = "La clé GEMINI_API_KEY est absente ou invalide dans les variables d'environnement."
        return jsonify({"error": message}), 500

    finally:
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
