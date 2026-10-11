# Vérification de RéviAI — 11 octobre 2026

## Ajouts postérieurs : bibliothèque, entraînement et abonnements

Les six améliorations sont implémentées localement : bibliothèque liée au compte et dossiers, cartes avec rappels espacés, aide guidée, quiz ciblés sur les erreurs, export A4 via l’enregistrement PDF du navigateur et page privée `/admin/abonnements`.

Contrôles effectués sur ces ajouts : analyse de syntaxe de `app.py`, `ai.py` et `study.py`, analyse des 18 modèles Jinja et validation de syntaxe de `static/script.js` et `static/study.js`. Aucun test fonctionnel supplémentaire ni essai en production n’a été exécuté pour ces nouvelles fonctions. Les 32 tests décrits plus bas concernent la version précédente, avant ces six ajouts.

La synchronisation utilise les tables `reviai_library` et `reviai_study_folders`, créées automatiquement dans la base existante à la première utilisation. Les migrations PostgreSQL, la synchronisation réelle entre appareils, les appels IA de cartes et quiz ciblés, l’impression PDF et les permissions de lecture des mouvements Stripe restent à vérifier après déploiement. Aucun service externe supplémentaire, variable secrète ou dépendance de production n’a été ajouté.

Publier les fichiers Python, le dossier `static` et le dossier `templates`, notamment les nouveaux fichiers `study.py`, `static/study.js` et `templates/admin_subscriptions.html`. Les anciennes instructions de publication limitée à deux fichiers ci-dessous concernent uniquement la correction de résiliation précédente.

## Résultats observés sur le site en ligne

Les 22 pages et ressources contrôlées sur `https://reviai.onrender.com` répondent en HTTP 200 : accueil, Plus, inscription, connexion, mot de passe oublié, guides et leurs cinq articles, À propos, FAQ, conditions, confidentialité, robots.txt, sitemap, ads.txt, état du serveur, quotas, JavaScript et CSS.

Avec des données fictives uniquement :

- Une fiche a été produite à partir d’un fichier TXT.
- Le quiz a renvoyé 10 questions.
- Le repérage a identifié les exercices 62 et 63.
- Le choix de l’exercice 63 a renvoyé les résultats attendus (12 et 25) sans mentionner l’exercice voisin.
- Le chat a répondu à une question de suivi.
- Deux images PNG contenant un exercice réparti sur deux pages ont été traitées ensemble. La correction a donné l’aire 15 et le périmètre 16 attendus, sans traiter le numéro 62 voisin.

Le formulaire d’inscription est disponible. L’accès anonyme à `/admin/comptes` redirige vers la connexion éditeur. Le webhook Stripe rejette une notification sans signature (HTTP 400). L’accueil a été inspecté à une largeur de 390 pixels, sans débordement horizontal. Aucun avertissement ou erreur JavaScript n’a été relevé dans le navigateur lors des interactions contrôlées. robots.txt autorise l’exploration ; cela ne prouve pas l’indexation par Google.

## Tests locaux

32 tests Python réussis, plus les tests JavaScript des quiz. Ils couvrent notamment :

- Comptes gratuits, confirmation d’adresse, connexions enregistrées et réinitialisation à usage unique.
- Création, activation, usage unique et révocation des pass VIP ; protection des pages éditeur et des formulaires.
- E-mails visibles uniquement dans la liste privée, recherche et pagination.
- Lecture réelle de documents TXT, PDF, DOCX, ODT et PPTX de démonstration.
- Plusieurs fichiers, suppression des fichiers temporaires, formats refusés, document vide, taille maximale et quotas.
- Sélection de l’exercice, refus d’une correction portant un autre numéro, chat et signalement sans doublon.
- Prix de 199 centimes EUR par mois, résiliation à échéance et maintien de l’accès pendant la période payée.
- Résiliation selon l’état réellement retourné par Stripe : actif, essai, paiement en retard, incomplet, impayé, en pause, déjà résilié ou expiré.
- Bouton de résiliation visible malgré une échéance locale périmée, y compris avec un pass VIP ; demandes répétées sans modification supplémentaire.
- Résiliation et portail de facturation indépendants de la configuration d’e-mail et du secret webhook ; session et protection du formulaire obligatoires, abonnement choisi depuis le compte connecté.
- Pas de confirmation de réussite en cas d’échec Stripe ; message de synchronisation distinct si Stripe confirme mais que l’enregistrement local échoue. Fin de l’accès après la période payée.
- Notifications Stripe signées et traitement sans doublon, avec le SDK Stripe 15.6.1 réellement installé pour les tests.
- Symboles mathématiques des questions, réponses et corrections, et échappement HTML.

La base des tests est SQLite en mémoire avec une adaptation des requêtes courantes. Les migrations et les verrous PostgreSQL ne sont pas validés par ces tests. Les services IA, de paiement et d’e-mail sont simulés dans les tests locaux ; les essais IA réels sont ceux détaillés plus haut.

## Corrections apportées pendant la vérification

Dans `app.py` :

1. Lecture des objets du SDK Stripe par clé. Les objets récents ne possèdent pas `dict.get()` ; l’ancien code pouvait accepter un webhook sans enregistrer l’état de l’abonnement.
2. Rejet des corps JSON de type incorrect sur les API quiz, chat et signalement (HTTP 400 au lieu d’une exception).
3. Comparaison des mots de passe éditeur et des jetons de formulaire avec encodage UTF-8, pour éviter une exception sur les caractères accentués.
4. Gestion d’un abonnement existant indépendante de la configuration d’e-mail et du secret webhook. La résiliation consulte l’état Stripe, accepte les demandes répétées et vérifie la confirmation avant d’annoncer la réussite.

Dans `templates/account.html`, la résiliation reste proposée pour tout abonnement enregistré non terminé, même si l’accès Plus a expiré localement ou qu’un pass VIP est actif. Le renouvellement arrêté, la fin de période et les erreurs sont indiqués séparément.

Ces corrections sont locales : publier `app.py` et `templates/account.html` sur GitHub puis déployer sur Render. Les tests et ce rapport peuvent aussi être conservés dans le dépôt. Aucune nouvelle dépendance de production ni variable secrète n’a été ajoutée.

## Points non confirmés en production

- Création d’un compte réel, réception de l’e-mail et confirmation depuis une boîte de réception.
- Paiement Stripe réel, activation Plus après ce paiement et résiliation d’un abonnement réel.
- Création et activation d’un vrai pass VIP sur Neon : seul le parcours local et la protection de sa page en ligne ont été contrôlés.
- Migration réelle des tables PostgreSQL sur Neon et permissions de la clé Stripe configurée dans Render.
- État de la rotation du secret webhook précédemment partagé.

Aucun paiement, envoi d’e-mail à une personne, création de compte réel ou changement de configuration Render/Stripe n’a été effectué pendant cette vérification.

## Rejouer les vérifications

Après installation des dépendances de production, les tests de documents utilisent aussi `reportlab` (dépendance de test uniquement) :

```text
python -m unittest discover -s tests -v
node tests/quiz_math.test.cjs
```

`python tests/check_live_site.py` contrôle les pages publiques et le rejet d’un webhook non signé. L’option `--ai` effectue un parcours IA avec un cours fictif. L’option `--images` utilise deux images fictives ; elle nécessite Pillow et la police Arial à l’emplacement Windows indiqué dans le script. Ces deux options consomment des requêtes IA et des quotas gratuits.
