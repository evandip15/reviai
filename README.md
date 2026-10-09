
## Fonctionnalités
- Génération de fiches de révision à partir de PDF, DOCX, ODT, PPTX, TXT et photos.
- Résolution d'exercices à partir d'une photo ou d'un document PDF, DOCX, ODT, PPTX ou TXT, avec choix du numéro et chat de suivi.
- Quiz générés à partir du cours, avec 4 réponses, correction et nouvelles questions.
- Limites gratuites configurables côté serveur.
- Interface responsive.
- Déploiement conteneurisé avec Docker/Render.
- Guides pédagogiques publics, FAQ et pages À propos et confidentialité.
- Sitemap et robots.txt générés à partir du domaine public configuré.
- Publicités désactivées par défaut et réservées aux pages de guides.

## Lancer localement

```bash
pip install -r requirements.txt
python app.py
```

Ouvre ensuite `http://127.0.0.1:5000`.

### Lancer depuis Thonny sous Windows

1. Dans Thonny, ouvre **Tools > Manage packages...** puis choisis l'installation depuis un fichier `requirements.txt`. Sélectionne le fichier du projet.
2. Ouvre `app.py` dans Thonny et lance **Run current script (F5)**.
3. Laisse le script tourner pendant que tu utilises le site sur `http://127.0.0.1:5000`.

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

## Publicité et préparation AdSense

Par défaut :
```text
ADS_ENABLED=false
```

Le site fonctionne normalement sans publicité. Les pages d'import, de résolution et de chat n'affichent pas de publicité ; les emplacements sont réservés aux guides publics.

Google examine le site et le compte éditeur. Les guides, la navigation, les pages de transparence et le sitemap améliorent la préparation du site, mais ne garantissent pas l'approbation : Google demande notamment du contenu original et utile, un site publié et une navigation claire.

Pour préparer une activation après approbation, configure côté hébergeur :
```text
ADS_ENABLED=true
ADS_CONSENT_READY=false
ADSENSE_CLIENT_ID=ca-pub-XXXXXXXXXXXXXXXX
ADSENSE_SLOT_ID=XXXXXXXXXX
ADSENSE_PUBLISHER_ID=pub-XXXXXXXXXXXXXXXX
```

Le client et l'identifiant éditeur doivent correspondre au même compte. Le fichier `/ads.txt` utilise l'identifiant éditeur. Vérifie-le dans ton compte AdSense.

Pour les annonces personnalisées en France et dans les autres pays concernés, configure d'abord une CMP certifiée par Google et compatible avec le TCF de l'IAB dans AdSense « Confidentialité et messages ». Le réglage `ADS_CONSENT_READY=true` ne crée pas la CMP : il signifie seulement que l'éditeur l'a déjà configurée et vérifiée. Garde-le à `false` tant que ce n'est pas fait.

Les identifiants ne doivent jamais être ajoutés au dépôt ou codés en dur dans les pages.

Configure `PUBLIC_BASE_URL=https://ton-domaine.fr` pour que le sitemap et `robots.txt` utilisent le domaine publié.

Le site publie `/privacy`, `/terms`, `/a-propos`, `/faq`, `/guides`, `/robots.txt` et `/sitemap.xml`. Aucun nom ni adresse e-mail n'est demandé pour utiliser le site. La politique explique le traitement des fichiers et l'usage éventuel des cookies publicitaires.

## Important pour un utilisateur mineur
Google indique qu'un compte AdSense doit être détenu par une personne d'au moins 18 ans. Pour un mineur, un parent ou représentant légal peut s'inscrire avec son propre compte si le site est accepté, et les paiements sont alors versés à l'adulte responsable.

Les informations bancaires ne sont nécessaires qu'au moment de configurer un mode de paiement pour recevoir les revenus ; RéviAI peut rester entièrement gratuit et fonctionner sans renseigner ces informations.

## Publication

`render.yaml` et `Dockerfile` permettent de déployer le site sur un hébergeur compatible Docker. Ajoute les variables d'environnement dans le tableau de bord de l'hébergeur.

## Données
Les fichiers importés sont supprimés à la fin du traitement. Les contenus servant à la fiche, au quiz et au chat sont conservés en mémoire du serveur pendant une heure pour permettre le suivi, sans enregistrement permanent dans une base de données. La politique de confidentialité du site décrit ces durées et les fournisseurs d'IA configurés.


## Images prises avec un téléphone

Les formats JPG/JPEG, PNG et WEBP sont acceptés. Les photos sont automatiquement orientées correctement et redimensionnées avant OCR lorsqu’elles sont très grandes.

### OCR local sur Windows

Avec `AI_PROVIDER=ollama`, RéviAI utilise Tesseract OCR pour lire le texte des photos. Installe Tesseract avec les données de langue française en suivant le [guide officiel d'installation Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html). Le chemin Windows standard est détecté automatiquement. Si tu l'as installé ailleurs, indique son emplacement avant de lancer l'application :

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
python app.py
```

Le chemin peut varier selon l'emplacement choisi lors de l'installation. L'image du conteneur Docker installe déjà Tesseract et les langues française et anglaise.
