
## Fonctionnalités
- Génération de fiches de révision à partir de PDF, DOCX, ODT, PPTX, TXT et photos.
- Résolution d'exercices à partir d'une photo ou d'un document PDF, DOCX, ODT, PPTX ou TXT, avec choix du numéro et chat de suivi.
- Repérage préalable des exercices numérotés, sélection des questions à traiter et vérification que la réponse correspond au numéro demandé.
- Trois formes d'accompagnement pour les exercices : indice, aide pas à pas ou correction complète avec vérification.
- Signalement volontaire des erreurs de lecture, de calcul ou d'explication pour examen par l'éditeur.
- Quiz générés à partir du cours, avec 4 réponses, correction et nouvelles questions.
- Limites gratuites configurables et appliquées côté serveur : fiches, repérages et corrections d’exercices, messages de suivi et quiz.
- Bibliothèque personnelle locale pour enregistrer fiches, corrections et scores de quiz sans créer de compte.
- Comptes Plus avec adresse confirmée, mot de passe sécurisé, réinitialisation par e-mail et gestion personnelle de l’abonnement.
- Offre RéviAI Plus à 1,99 €/mois, renouvellement mensuel et résiliation depuis le compte ; l’accès reste ouvert jusqu’à la fin de la période réglée.
- Accès éditeur sans limites quotidiennes via la connexion `/admin`.
- Statistiques quotidiennes agrégées et accessibles dans l’espace éditeur.
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

## RéviAI Plus, paiement et comptes

Le parcours Plus utilise Stripe Checkout et son portail de facturation. Le prix récurrent est créé par le serveur à 1,99 € par mois. La création de comptes et le paiement restent désactivés tant que PostgreSQL, Stripe et un service d’envoi d’e-mails ne sont pas configurés.

Dans Render, ajoute ces variables secrètes :

```text
STRIPE_SECRET_KEY=sk_test_...   # commence par les clés de test
STRIPE_WEBHOOK_SECRET=whsec_...
PUBLIC_BASE_URL=https://reviai.onrender.com
DATABASE_URL=...                # base PostgreSQL persistante
```

Pour les e-mails de confirmation et de réinitialisation, choisis une seule solution : Resend avec un domaine expéditeur vérifié (`RESEND_API_KEY` et `MAIL_FROM`), ou Google Apps Script sans domaine personnalisé (`APPS_SCRIPT_MAIL_URL` et `APPS_SCRIPT_MAIL_TOKEN`). Avec Apps Script, les messages partiront de l’adresse Gmail du compte qui possède le script, sous le nom d’expéditeur RéviAI. Un compte Gmail personnel est limité à 100 destinataires par jour par Apps Script ; Google peut modifier ce quota.

Dans Stripe, configure un endpoint webhook public vers `https://reviai.onrender.com/stripe/webhook` et sélectionne les événements `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated` et `customer.subscription.deleted`. Le secret de signature de cet endpoint est la valeur `STRIPE_WEBHOOK_SECRET`. Active aussi le portail client Stripe pour que les abonnés puissent gérer leurs moyens de paiement et consulter leurs factures ; garde la résiliation dans le compte RéviAI afin qu’elle prenne toujours effet à la fin de la période payée. Le bouton RéviAI arrête le prochain renouvellement et l’accès reste ouvert jusqu’à cette échéance.

Avec Resend, ajoute et vérifie un domaine expéditeur, crée une clé API, puis indique l’adresse d’expédition dans `MAIL_FROM`. Elle sert aux liens de confirmation de compte et de réinitialisation du mot de passe. Ne publie jamais ces clés dans GitHub et ne me les envoie pas.

### Configurer l’envoi gratuit avec Google Apps Script

1. Connecte-toi au compte Gmail depuis lequel RéviAI enverra les e-mails, puis crée un projet sur [script.google.com](https://script.google.com/).
2. Remplace le contenu de `Code.gs` par celui du fichier `apps_script_mailer.gs` du dépôt.
3. Dans **Paramètres du projet** (icône engrenage), ajoute une propriété de script nommée `REVIAI_MAIL_TOKEN`. Saisis un secret aléatoire long et garde-le privé.
4. Clique sur **Déployer → Nouveau déploiement**, choisis **Application Web**, sélectionne **Exécuter en tant que : Moi**, puis **Tout le monde** comme accès si cette option est proposée. Autorise le script avec ton compte Google et copie l’URL de déploiement terminant par `/exec`.
5. Dans Render, ajoute `APPS_SCRIPT_MAIL_URL` avec cette URL et `APPS_SCRIPT_MAIL_TOKEN` avec le même secret que dans les propriétés du script, puis redéploie.

Cette option évite l’achat d’un domaine et l’abonnement à un service d’e-mail, mais son quota est limité. Stripe prélève ses frais de traitement sur chaque paiement réussi ; son offre standard n’a pas de frais fixes de démarrage ni d’abonnement mensuel. Consulte ses [tarifs](https://stripe.com/fr/pricing) avant d’activer le mode réel.

Commence avec les clés Stripe de test et vérifie le parcours avant de remplacer les clés de test par les clés réelles. Le propriétaire garde un accès illimité en se connectant à `/admin` avec `ADMIN_PASSWORD`. Les abonnés ont leur tableau de bord sur `/compte`. La suppression d’un compte résilie immédiatement tout abonnement encore actif.

Avant l’ouverture au public, vérifie dans Stripe et auprès de l’adulte responsable ou de ton conseiller les conditions de vente, les taxes applicables et les conditions d’encaissement. Le site ne calcule pas automatiquement les taxes dans Checkout.

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

### Signalements d'erreurs

L'espace éditeur est disponible à l'adresse `/admin`. Définis `ADMIN_PASSWORD` dans les variables d'environnement de l'hébergeur avant de l'utiliser. Les statistiques sont dans `/admin/statistiques` et n'affichent que des nombres d'utilisation agrégés par jour et fonction. Pour conserver les retours et compteurs après les redémarrages et mises en veille, configure `DATABASE_URL` avec l'URL d'une base PostgreSQL persistante. En développement local sans base, les signalements utilisent `data/exercise_feedback.jsonl` et les compteurs restent temporaires. Ne publie jamais l'URL de base ou le mot de passe dans GitHub.

Configure `PUBLIC_BASE_URL=https://ton-domaine.fr` pour que le sitemap et `robots.txt` utilisent le domaine publié.

Le site publie `/privacy`, `/terms`, `/a-propos`, `/faq`, `/guides`, `/robots.txt` et `/sitemap.xml`. Aucun nom ni adresse e-mail n'est demandé pour utiliser le site. La bibliothèque de révision reste dans le stockage du navigateur ; un cookie fonctionnel aléatoire sert à appliquer les quotas gratuits et n'enregistre ni nom, ni e-mail, ni adresse IP dans la table de quota.

## Important pour un utilisateur mineur
Google indique qu'un compte AdSense doit être détenu par une personne d'au moins 18 ans. Pour un mineur, un parent ou représentant légal peut s'inscrire avec son propre compte si le site est accepté, et les paiements sont alors versés à l'adulte responsable.

Les informations bancaires ne sont nécessaires qu'au moment de configurer un mode de paiement pour recevoir les revenus ; RéviAI peut rester entièrement gratuit et fonctionner sans renseigner ces informations.

## Publication

`render.yaml` et `Dockerfile` permettent de déployer le site sur un hébergeur compatible Docker. Ajoute les variables d'environnement dans le tableau de bord de l'hébergeur.

## Données
Les fichiers importés sont supprimés à la fin du traitement. Les contenus servant à la fiche, au quiz et au chat sont conservés en mémoire du serveur pendant une heure pour permettre le suivi. La bibliothèque garde les résultats enregistrés et les scores de quiz dans le navigateur de l'élève ; elle ne synchronise pas entre appareils. Les quotas enregistrent un identifiant aléatoire haché, la fonction utilisée et la date, pendant 90 jours, uniquement pour limiter l'usage gratuit et afficher des statistiques quotidiennes. Les signalements volontaires sont enregistrés dans la base PostgreSQL configurée ou, en développement local, dans `data/exercise_feedback.jsonl`. Ne publie ni le fichier local ni les secrets de connexion. La politique de confidentialité du site décrit ces traitements et les fournisseurs d'IA configurés.


## Images prises avec un téléphone

Les formats JPG/JPEG, PNG et WEBP sont acceptés. Les photos sont automatiquement orientées correctement et redimensionnées avant OCR lorsqu’elles sont très grandes.

### OCR local sur Windows

Avec `AI_PROVIDER=ollama`, RéviAI utilise Tesseract OCR pour lire le texte des photos. Installe Tesseract avec les données de langue française en suivant le [guide officiel d'installation Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html). Le chemin Windows standard est détecté automatiquement. Si tu l'as installé ailleurs, indique son emplacement avant de lancer l'application :

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
python app.py
```

Le chemin peut varier selon l'emplacement choisi lors de l'installation. L'image du conteneur Docker installe déjà Tesseract et les langues française et anglaise.
