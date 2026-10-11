
## Fonctionnalités
- Génération de fiches de révision à partir de PDF, DOCX, ODT, PPTX, TXT et photos.
- Résolution d'exercices à partir d'une photo ou d'un document PDF, DOCX, ODT, PPTX ou TXT, avec choix du numéro et chat de suivi.
- Repérage préalable des exercices numérotés, sélection des questions à traiter et vérification que la réponse correspond au numéro demandé.
- Trois formes d'accompagnement pour les exercices : indice, aide pas à pas ou correction complète avec vérification.
- Signalement volontaire des erreurs de lecture, de calcul ou d'explication pour examen par l'éditeur.
- Quiz générés à partir du cours, avec 4 réponses, correction et nouvelles questions.
- Contrôles blancs à partir du cours ou d’une fiche, réponses rédigées ou copie photographiée, barème sur 20, bilan et export PDF.
- Limites gratuites configurables et appliquées côté serveur : fiches, repérages et corrections d’exercices, messages de suivi et quiz.
- Bibliothèque personnelle locale pour enregistrer fiches, corrections et scores de quiz sans créer de compte.
- Comptes Plus avec adresse confirmée, mot de passe sécurisé, réinitialisation par e-mail et gestion personnelle de l’abonnement.
- Offre RéviAI Plus à 1,99 €/mois, renouvellement mensuel et résiliation depuis le compte ; l’accès reste ouvert jusqu’à la fin de la période réglée.
- Accès éditeur sans limites quotidiennes via la connexion `/admin`.
- Statistiques quotidiennes agrégées et accessibles dans l’espace éditeur.
- Comptes gratuits, liste privée des inscrits et dernière connexion dans `/admin/comptes`.
- Pass VIP individuels offerts, à usage unique et révocables, donnant un accès sans limite quotidienne.
- Formules lisibles dans les questions, réponses et corrections des quiz, comme dans les fiches.
- Interface responsive.
- Déploiement conteneurisé avec Docker/Render.
- Guides pédagogiques publics, FAQ et pages À propos et confidentialité.
- Sitemap et robots.txt générés à partir du domaine public configuré.
- Publicités désactivées par défaut et réservées aux pages de guides.

## Mode Contrôle blanc

Ouvrir `/controle-blanc` depuis l’accueil, la navigation ou **Créer un contrôle blanc** sous une fiche. Aucun nouveau compte de service, aucune nouvelle clé et aucune dépendance ne sont requis : les appels passent par le fournisseur IA déjà configuré.

1. Coller un chapitre (80 à 60 000 caractères), importer ses documents ou choisir une fiche de la bibliothèque. PDF, DOCX, ODT, PPTX, TXT, JPG/JPEG, PNG et WEBP sont acceptés, dans la limite totale `MAX_UPLOAD_MB` habituelle. Plusieurs documents sont lus dans l’ordre, sans couper silencieusement un cours trop long.
2. Choisir le niveau et la durée : 15 minutes donnent 4 questions ; 30 minutes, 6 questions ; 45, 60 ou 90 minutes, 8 questions. Chaque sujet totalise exactement 20 points. Le corrigé et les critères détaillés restent côté serveur avant remise.
3. Cliquer sur **Commencer mon contrôle**. La durée est calculée avec l’horloge du serveur ; le chronomètre se recalcule après changement d’onglet et se fige à la remise. Un dépassement est indiqué sans pénalité.
4. Rédiger les réponses ou envoyer les pages de la copie. **Lire ma copie** permet de relire et corriger sa transcription ; la confirmation de relecture est requise avant correction de cette transcription. Au plus trois envois de lecture sont autorisés par essai. Les originaux sont supprimés après lecture.
5. Rendre la copie : le serveur applique les critères, borne chaque crédit à son maximum et additionne les points. Les réponses absentes sans copie commune valent zéro. Si l’IA signale une lecture, un énoncé ou un barème incertain, la question reste à vérifier et aucune note globale n’est attribuée ; les points évaluables sont affichés séparément. Une correction reste une estimation pédagogique et peut être erronée.
6. Enregistrer le bilan dans un dossier, le copier, le télécharger en texte ou l’exporter en PDF via l’impression. Un bilan enregistré a le type `exam` et une taille maximale de 120 000 caractères. Il s’ouvre dans la bibliothèque sans remplacer une fiche ni entrer dans la moyenne des QCM. Les bilans des comptes et de l’éditeur utilisent leurs espaces synchronisés existants ; les bilans anonymes restent locaux. Aucune nouvelle table de bibliothèque n’est ajoutée.

La génération, la lecture de copie et la correction sont des tâches asynchrones dans l’exécuteur IA existant. Les erreurs permettent jusqu’à trois tentatives de génération et de correction pour le même essai. Une création décompte le quota quotidien ; une relance technique du même essai et sa correction ne décomptent pas un nouveau contrôle. La variable facultative `FREE_MOCK_EXAMS_PER_DAY` vaut **1** par défaut, indépendamment du quota de quiz. Plus, VIP et éditeur restent sans limite quotidienne de création, avec les bornes de taille, de concurrence et de tentatives par essai. Les appels utilisent et consomment les quotas éventuels du fournisseur IA existant.

Les contrôles en cours sont privés et temporaires en mémoire pendant trois heures à partir de leur création, dans le processus unique configuré par le Dockerfile. Ils peuvent disparaître au redémarrage ; aucun stockage durable des essais en cours n’est promis. Les routes vérifient le propriétaire et un jeton CSRF pour les actions. Le cookie de quota identifie aussi les essais anonymes. Le brouillon des réponses et l’identifiant de reprise sont conservés dans le stockage de session de l’onglet, séparés par compte ou navigateur. Après un changement de connexion, rouvrir la page ; connecter son compte avant de commencer facilite l’enregistrement synchronisé du bilan. Supprimer un compte retire aussi ses essais temporaires.

À publier ensemble pour cette fonction : `app.py`, `study.py`, les nouveaux `mock_exam.py` et `exam_ai.py`, `static/exam.js`, `static/exam.css`, `static/script.js`, `static/study.js`, `static/style.css`, `templates/mock_exam.html`, `templates/index.html`, `templates/public_base.html`, `templates/admin_analytics.html`, `templates/plus.html`, `templates/faq.html`, `templates/privacy.html`, `templates/terms.html`. Le README, le rapport et `.env.example` documentent la fonction. Aucun changement Render n’est obligatoire ; `FREE_MOCK_EXAMS_PER_DAY` est optionnel.

## Tableau de suivi dans l’espace éditeur

Après connexion à `/admin`, ouvrir **Statistiques** (`/admin/statistiques`). Choisir 7, 30 ou 90 jours pour voir :

- Les comptes créés et confirmés, les accès Plus actuellement actifs et les résiliations programmées, à partir de la base de gestion du service.
- Les navigateurs actifs et les fiches réellement terminées, uniquement avec accord. Les essais gratuits décomptés restent dans un tableau distinct sur 30 jours.
- Les étapes des navigateurs dont le suivi commence sur la période : inscription, confirmation, première fiche, consultation de Plus, ouverture de Stripe, activation réelle de Plus et retour un autre jour.
- Les origines simplifiées et des pistes d’amélioration pour les parcours incomplets. Pour TikTok, partager `https://reviai.onrender.com/?utm_source=tiktok`.

Le suivi commence à cette version : aucun ancien parcours n’est reconstitué. Les lignes d’étapes sont indépendantes et ne constituent pas un entonnoir strict ; une fiche peut précéder l’inscription. Les pourcentages portent sur un même groupe de nouveaux navigateurs ayant accepté. Un navigateur n’est pas une personne ; les retours sont mesurés sur des jours UTC différents. Les fiches et visites ne sont pas réunies entre appareils. Les confirmations et activations peuvent compléter un parcours associé au compte. Les paiements ouverts sans activation observée peuvent correspondre à des utilisateurs revenant plus tard, à des essais ou à des paiements de test.

`site_analytics.py` crée automatiquement `reviai_stats_visitors`, `reviai_stats_visits` et `reviai_stats_sheets` dans la base `DATABASE_URL` existante. Aucune nouvelle variable Render, dépendance ou service externe n’est nécessaire. Le rôle PostgreSQL doit pouvoir créer les tables et les index. La table des comptes doit également rester disponible pour ce tableau. Une panne des statistiques ne bloque pas les outils, les comptes ni les notifications Stripe.

Le composant `templates/analytics_consent.html` et `static/analytics.js` demandent un accord facultatif avant le suivi des visites. Le cookie signé `reviai_stats` est distinct du cookie de quota gratuit. Le choix se modifie en bas de page. Refuser supprime le parcours de ce navigateur ; la suppression d’un compte supprime les parcours qui lui sont liés. Les données de suivi sont purgées après 90 jours depuis le début du parcours, au prochain accès. Les réponses HTML contenant un choix personnel ne sont pas mises en cache partagé. Les visites en mode éditeur sont exclues.

Les fiches sont comptées côté serveur après une génération réussie, avec l’identifiant du traitement pour éviter les doublons. Les activations du parcours sont prises dans les objets d’abonnement lus chez Stripe, au retour authentifié ou via le webhook signé existant : uniquement statut `active` et `livemode=true`. Les renouvellements ne créent pas une deuxième première activation ; les essais, tests et pass VIP n’en créent pas. Ce compteur ne constitue pas un montant de revenus. Le tableau des accès Plus actuellement actifs est distinct et inclut, comme indiqué à l’écran, les essais et les abonnements de test connus.

À publier ensemble : `app.py`, `site_analytics.py`, `static/analytics.js`, `static/style.css`, `templates/analytics_consent.html`, `templates/admin_analytics.html`, `templates/public_base.html`, `templates/index.html` et `templates/privacy.html`.

## Lancer localement

```bash
pip install -r requirements.txt
python app.py
```

Ouvre ensuite `http://127.0.0.1:5000`.

## Comptes gratuits et pass VIP

La page d’accueil propose de créer un compte gratuit avec une adresse e-mail confirmée. Les outils gratuits restent utilisables sans inscription. Les nouvelles colonnes de connexion et la table des pass VIP sont créées automatiquement dans la base PostgreSQL existante : aucune variable d’environnement supplémentaire n’est nécessaire.

1. Connecte-toi à `/admin`, puis ouvre **Comptes et pass VIP** (ou `/admin/comptes`).
2. Donne un nom au pass et clique sur **Créer un pass VIP**. Copie immédiatement le code affiché : seul son hash est conservé, il ne pourra pas être réaffiché.
3. Ton ami crée un compte, confirme son e-mail, puis saisit le code dans **Mon compte → Activer un pass VIP**.
4. Le pass donne un accès gratuit sans limite quotidienne, sans date de fin. Chaque code ne peut être activé qu’une fois. Tu peux le révoquer depuis cette même page éditeur ; un code utilisé reste inutilisable même si le compte bénéficiaire est supprimé.

La liste privée affiche l’e-mail, la confirmation, l’offre, l’inscription, la dernière connexion réussie et le nombre de connexions enregistrées. La confirmation d’adresse ouvre une session et compte comme une connexion. Les dates sont affichées en UTC ; les connexions antérieures à cette version ne sont pas reconstituées. Les comptes et les pass sont paginés. La politique de confidentialité décrit ces données. L’activation d’un pass VIP ne résilie pas un abonnement payant existant ; le bénéficiaire peut le résilier depuis son compte.

## RéviAI Plus, paiement et comptes

Le parcours Plus utilise Stripe Checkout et son portail de facturation. Le prix récurrent est créé par le serveur à 1,99 € par mois. Les comptes gratuits nécessitent PostgreSQL et un service d’envoi d’e-mails ; Stripe est nécessaire uniquement pour les abonnements payants.

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

Le site publie `/privacy`, `/terms`, `/a-propos`, `/faq`, `/guides`, `/robots.txt` et `/sitemap.xml`. Les outils gratuits fonctionnent sans compte ; un compte e-mail confirmé permet de synchroniser la bibliothèque. Un cookie fonctionnel aléatoire sert aux quotas gratuits et n'enregistre ni nom, ni e-mail, ni adresse IP dans leur table.

## Important pour un utilisateur mineur
Google indique qu'un compte AdSense doit être détenu par une personne d'au moins 18 ans. Pour un mineur, un parent ou représentant légal peut s'inscrire avec son propre compte si le site est accepté, et les paiements sont alors versés à l'adulte responsable.

Les informations bancaires ne sont nécessaires qu'au moment de configurer un mode de paiement pour recevoir les revenus ; RéviAI peut rester entièrement gratuit et fonctionner sans renseigner ces informations.

## Publication

`render.yaml` et `Dockerfile` permettent de déployer le site sur un hébergeur compatible Docker. Ajoute les variables d'environnement dans le tableau de bord de l'hébergeur.

## Données
Les fichiers originaux sont supprimés à la fin du traitement. Les contenus temporaires des fiches, quiz et chats restent en mémoire une heure. Sans compte, la bibliothèque reste dans le navigateur. Avec un compte confirmé, les éléments enregistrés, dossiers, cartes et résultats détaillés des quiz sont conservés dans PostgreSQL, jusqu’à leur suppression ou celle du compte (clés étrangères avec suppression en cascade). Les quotas gardent un identifiant aléatoire haché, la fonction et la date pendant 90 jours. Les signalements restent dans PostgreSQL ou, en développement local, dans `data/exercise_feedback.jsonl`. Ne publie ni données locales ni secrets.

## Bibliothèque, cartes et suivi des abonnements

- `study.py` ajoute les routes de bibliothèque privée et `/admin/abonnements`. Il est indispensable au démarrage ; publier ce fichier avec `app.py`.
- Les tables `reviai_library` et `reviai_study_folders` sont créées à la première utilisation, dans la base `DATABASE_URL` existante. Aucun service supplémentaire ni nouvelle variable d’environnement n’est requis. Le rôle PostgreSQL doit pouvoir créer les tables, comme pour les comptes existants.
- L’espace éditeur dispose aussi d’une bibliothèque synchronisée dans cette même base, dans les tables séparées `reviai_study_editors`, `reviai_editor_library` et `reviai_editor_study_folders`. Elle est accessible uniquement après connexion éditeur, avec protection CSRF éditeur et contrôle de version. Se connecter à l’espace éditeur sur un autre appareil retrouve cette bibliothèque ; cela ne donne aucun accès aux bibliothèques des élèves.
- Avant cette correction, la bibliothèque de l’éditeur était locale. Sur chaque navigateur qui contient encore d’anciennes fiches, se connecter en éditeur, ouvrir « Ma bibliothèque », puis « Récupérer les anciennes révisions de ce navigateur ». L’import garde les originaux locaux et copie les dossiers. Les éléments déjà importés avec le même identifiant sont conservés sans doublon.
- Le champ « Enregistrer dans » près de la fiche ou de l’exercice indique le dossier de destination. Créer ou sélectionner un dossier dans la bibliothèque le propose pour les nouveaux contenus ; cela ne déplace pas les éléments déjà enregistrés. Pour déplacer un élément existant, utiliser son menu de dossier puis enregistrer si le changement est effectué depuis la fiche ouverte.
- Bibliothèque gratuite : 200 éléments et 50 dossiers par compte. Lecture et modification sont limitées au compte connecté, avec protection CSRF, verrou du compte et contrôle de version. Une modification concurrente renvoie HTTP 409. L’import des anciennes données locales est volontaire ; les données d’un compte connecté ne sont pas copiées dans le stockage local du navigateur.
- `static/study.js` apporte les dossiers, la recherche, les cartes modifiables, les rappels espacés, la progression par dossier, la lecture des corrections de quiz et l’entraînement ciblé sur les erreurs. Les cartes déjà créées ne consomment pas d’IA pendant la révision. La création de cartes utilise le quota `quiz` existant ; Plus, VIP et éditeur gardent leur accès sans limite quotidienne.
- L’export PDF ouvre une page A4 adaptée à l’impression. L’utilisateur choisit « Enregistrer au format PDF » dans le navigateur. Aucune dépendance PDF côté serveur ni aucun service payant supplémentaire n’est ajouté.
- « Aide-moi à comprendre » commence par un indice, conserve le contexte de l’exercice et invite l’élève à proposer son essai avant la prochaine étape.
- `/admin/abonnements` affiche l’état connu des abonnements RéviAI. Le bouton d’actualisation lit les mouvements réels du compte Stripe entier des 30 derniers jours (maximum 2 000 mouvements, avertissement si tronqué). La clé restreinte Stripe doit autoriser la lecture du solde et des mouvements de solde. Aucun nouveau webhook n’est nécessaire pour les montants. Ils distinguent paiements bruts, remboursements, frais et net des mouvements retenus ; il ne s’agit ni d’une prévision ni du solde bancaire ni d’un bénéfice fiscal. Les autres devises que l’euro gardent leurs unités mineures Stripe. Les clés de test sont signalées.
- Les montants financiers sont lus à la demande et ne sont pas stockés dans la bibliothèque. L’éditeur ne peut pas lire les bibliothèques des élèves depuis les pages d’administration.


## Images prises avec un téléphone

Les formats JPG/JPEG, PNG et WEBP sont acceptés. Les photos sont automatiquement orientées correctement et redimensionnées avant OCR lorsqu’elles sont très grandes.

### OCR local sur Windows

Avec `AI_PROVIDER=ollama`, RéviAI utilise Tesseract OCR pour lire le texte des photos. Installe Tesseract avec les données de langue française en suivant le [guide officiel d'installation Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html). Le chemin Windows standard est détecté automatiquement. Si tu l'as installé ailleurs, indique son emplacement avant de lancer l'application :

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
python app.py
```

Le chemin peut varier selon l'emplacement choisi lors de l'installation. L'image du conteneur Docker installe déjà Tesseract et les langues française et anglaise.
