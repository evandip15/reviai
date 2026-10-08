# RéviAI

RéviAI transforme des cours en fiches de révision et en quiz en français.

## Formats

PDF, DOCX, ODT, PPTX, TXT, JPG, JPEG, PNG et WEBP.

## Variables d'environnement

- `GEMINI_API_KEY` : clé API Gemini.
- `GEMINI_MODEL` : modèle Gemini utilisé.

## Déploiement Render

Le dépôt contient un `Dockerfile` et un `render.yaml`.

Ajoute `GEMINI_API_KEY` dans les variables d'environnement du service Render. La clé ne doit jamais être écrite dans le code ou le dépôt GitHub.

## AdSense

Le script AdSense est déjà présent dans `templates/index.html` et le fichier `ads.txt` est présent à la racine du projet.

La route `/ads.txt` sert le fichier directement. Après déploiement, vérifie :

`https://reviai.onrender.com/ads.txt`
