
## Fonctionnalités
- Génération de fiches de révision à partir de PDF, DOCX, ODT, PPTX, TXT et photos.
- Quiz générés à partir du cours, avec 4 réponses, correction et nouvelles questions.
- Limites gratuites configurables côté serveur.
- Interface responsive.
- Déploiement conteneurisé avec Docker/Render.
- Emplacements publicitaires prévus, désactivés par défaut.

## Lancer localement

```bash
pip install -r requirements.txt
python app.py
```

Ouvre ensuite `http://127.0.0.1:5000`.

### IA locale
```text
AI_PROVIDER=ollama
REVIAI_MODEL=llama3.2
```

### IA cloud
La V5 peut aussi utiliser Gemini côté serveur avec une clé placée dans les variables d'environnement, pas dans le code.

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=...
```

## Publicité

Par défaut :
```text
ADS_ENABLED=false
```

Le site fonctionne normalement sans publicité. Aucun compte bancaire ni moyen de paiement n'est demandé par RéviAI lui-même.

Pour activer plus tard les emplacements AdSense, il faut obtenir un compte éditeur approuvé et renseigner côté serveur :
```text
ADS_ENABLED=true
ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXXXXXXXX
ADSENSE_SLOT_ID=XXXXXXXXXX
ADSENSE_PUBLISHER_ID=pub-XXXXXXXXXXXXXXXX
```

Ne mets jamais ces valeurs directement dans Git ou dans `index.html`.

## Important pour un utilisateur mineur
Google indique qu'un compte AdSense doit être détenu par une personne d'au moins 18 ans. Pour un mineur, un parent ou représentant légal peut s'inscrire avec son propre compte si le site est accepté, et les paiements sont alors versés à l'adulte responsable.

Les informations bancaires ne sont nécessaires qu'au moment de configurer un mode de paiement pour recevoir les revenus ; RéviAI peut rester entièrement gratuit et fonctionner sans renseigner ces informations.

## Publication

`render.yaml` et `Dockerfile` permettent de déployer le site sur un hébergeur compatible Docker. Ajoute les variables d'environnement dans le tableau de bord de l'hébergeur.

## Données
Les fichiers importés sont lus temporairement pour générer le contenu puis supprimés par l'application. Pour un lancement public, ajoute une vraie base de données et une politique de conservation des données adaptée si tu veux conserver des comptes utilisateurs ou l'historique.


## Images prises avec un téléphone

Les formats JPG/JPEG, PNG et WEBP sont acceptés. Les photos sont automatiquement orientées correctement et redimensionnées avant OCR lorsqu’elles sont très grandes.
