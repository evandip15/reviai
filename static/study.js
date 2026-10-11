/* Bibliothèque du compte, entraînement et export. Aucun contenu privé en cache partagé. */
(() => {
    "use strict";
    const el = id => document.getElementById(id);
    const cloud = document.body.dataset.accountSignedIn === "true";
    const editor = document.body.dataset.libraryMode === "editor";
    const storageName = editor ? "ton espace éditeur" : "ton compte";
    const folderKey = "reviai-folders-v1";
    const localItems = () => {
        try { const rows = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) || "[]"); return Array.isArray(rows) ? rows.filter(x => x && typeof x === "object") : []; }
        catch { return []; }
    };
    const localFolders = () => {
        try { const rows = JSON.parse(localStorage.getItem(folderKey) || "[]"); return Array.isArray(rows) ? rows.filter(x => x && typeof x.id === "string" && typeof x.name === "string") : []; }
        catch { return []; }
    };
    let csrf = "", queue = Promise.resolve(), pending = 0, refreshRevision = 0;
    const conflicted = new Set();
    let deck = null, cardQueue = [], shownCard = null, reviewing = false;
    const study = window.ReviStudy = {items: cloud ? [] : localItems(), folders: cloud ? [] : localFolders(), ready: !cloud};
    const id = prefix => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
    const clone = value => JSON.parse(JSON.stringify(value));
    function notice(text, error = false) {
        el("librarySyncStatus").textContent = text;
        el("librarySyncStatus").classList.toggle("error", error);
    }
    async function api(path, method = "GET", body) {
        const response = await fetch(path, {method, cache: "no-store", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
        const data = await response.json();
        if (!response.ok) { const error = new Error(data.error || "La demande n’a pas abouti. Réessaie."); error.status = response.status; throw error; }
        return data;
    }
    function serialize(operation) {
        pending += 1;
        refreshRevision += 1;
        const result = queue.then(operation).catch(error => { notice(error.message, true); alert(error.message); return false; }).finally(() => { pending -= 1; });
        queue = result.then(() => undefined);
        return result;
    }
    function requireReady() {
        if (!study.ready) throw new Error("La bibliothèque du compte est indisponible. Clique sur Actualiser avant d’enregistrer.");
    }
    function renderFolders() {
        const select = el("libraryFolderFilter"), value = select.value;
        select.replaceChildren(new Option("Tous les dossiers", "all"), new Option("Sans dossier", ""));
        for (const folder of study.folders) select.add(new Option(folder.name, folder.id));
        select.value = [...select.options].some(option => option.value === value) ? value : "all";
        syncSaveFolders();
    }
    function syncSaveFolders() {
        for (const [key, value] of [["revisionSaveFolder", currentRevisionFolder], ["exerciseSaveFolder", currentExerciseFolder]]) {
            const select = el(key);
            select.replaceChildren(new Option("Sans dossier", ""));
            for (const folder of study.folders) select.add(new Option(folder.name, folder.id));
            // Conserver le choix même pendant un chargement ou une erreur de synchronisation.
            if (value && !study.folders.some(folder => folder.id === value)) select.add(new Option("Dossier à actualiser", value));
            select.value = value || "";
        }
    }
    study.syncSaveFolders = syncSaveFolders;
    function chooseNewItemFolder() {
        const folder = el("libraryFolderFilter").value;
        if (folder === "all") return;
        // Un filtre ne doit pas déplacer une fiche déjà enregistrée.
        if (!study.items.some(item => item.id === currentRevisionLibraryId)) currentRevisionFolder = folder;
        if (!study.items.some(item => item.id === currentExerciseLibraryId)) currentExerciseFolder = folder;
        syncSaveFolders();
    }
    el("revisionSaveFolder").addEventListener("change", () => {
        currentRevisionFolder = el("revisionSaveFolder").value;
        saveRevisionButton.textContent = "＋ Enregistrer";
    });
    el("exerciseSaveFolder").addEventListener("change", () => {
        currentExerciseFolder = el("exerciseSaveFolder").value;
        saveExerciseButton.textContent = "＋ Enregistrer";
    });
    async function refresh() {
        if (pending) { notice("Un enregistrement est en cours…"); return; }
        if (!cloud) { study.items = localItems(); study.folders = localFolders(); renderFolders(); renderLibrary(); return; }
        const revision = ++refreshRevision;
        el("refreshLibraryButton").disabled = true;
        notice(`Chargement de la bibliothèque de ${storageName}…`);
        try {
            const data = await api("/api/library");
            if (revision !== refreshRevision) return;
            if (!data.authenticated) throw new Error("Ta session a expiré. Connecte-toi à nouveau avant d’enregistrer.");
            if (data.account_id !== document.body.dataset.accountId || data.library_mode !== document.body.dataset.libraryMode) { study.ready = false; window.location.reload(); return; }
            csrf = data.csrf_token;
            study.items = data.items;
            study.folders = data.folders;
            study.ready = true;
            renderFolders(); renderLibrary();
            notice(`Bibliothèque de ${storageName} à jour · jusqu’à 200 éléments et 50 dossiers.`);
        } catch (error) { if (revision === refreshRevision) { study.ready = false; notice(error.message, true); } }
        finally { el("refreshLibraryButton").disabled = false; }
    }
    async function persist(item) {
        requireReady();
        if (conflicted.has(item.id)) throw new Error("Cet élément a changé ailleurs. Actualise la bibliothèque, puis ouvre sa dernière version avant de le modifier.");
        const previous = study.items.find(entry => entry.id === item.id);
        let saved = item;
        if (cloud) {
            try {
                const result = await api(`/api/library/items/${encodeURIComponent(item.id)}`, "PUT", {item, expected_version: Number.isInteger(item.version) ? item.version : previous?.version || 0});
                saved = result.item;
            } catch (error) { if (error.status === 409 && previous) conflicted.add(item.id); throw error; }
        } else {
            if (!previous && study.items.length >= 200) throw new Error("Ta bibliothèque contient déjà 200 éléments. Supprime ceux dont tu n’as plus besoin.");
            const next = [item, ...study.items.filter(entry => entry.id !== item.id)];
            try { localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(next)); }
            catch { throw new Error("Le stockage de ce navigateur est plein. Libère de la place ou utilise un compte."); }
        }
        study.items = [saved, ...study.items.filter(entry => entry.id !== saved.id)];
        renderLibrary(); notice(cloud ? `Enregistré dans ${storageName} ✓` : "Enregistré dans ce navigateur ✓");
        return saved;
    }
    study.save = (type, title, content, itemId, score, total, extra = {}) => serialize(async () => {
        const previous = study.items.find(item => item.id === itemId);
        return persist({...previous, ...extra, id: itemId || id("item"), type, title: String(title || "Révision").slice(0, 180), content: String(content || "").slice(0, 30000), score, total,
            folder: extra.folder ?? previous?.folder ?? (el("libraryFolderFilter").value === "all" ? "" : el("libraryFolderFilter").value),
            createdAt: previous?.createdAt || new Date().toISOString(), updatedAt: new Date().toISOString()});
    });
    study.remove = itemId => serialize(async () => {
        requireReady();
        const item = study.items.find(entry => entry.id === itemId);
        if (!item || !confirm(`Supprimer « ${item.title} » ${cloud ? "de ton compte et de tous tes appareils" : "de ce navigateur"} ?`)) return false;
        if (cloud) await api(`/api/library/items/${encodeURIComponent(itemId)}`, "DELETE", {expected_version: item.version});
        else localStorage.setItem(LIBRARY_STORAGE_KEY, JSON.stringify(study.items.filter(entry => entry.id !== itemId)));
        study.items = study.items.filter(entry => entry.id !== itemId);
        if (currentRevisionLibraryId === itemId) currentRevisionLibraryId = "";
        if (currentExerciseLibraryId === itemId) currentExerciseLibraryId = "";
        if (deck?.id === itemId) { deck = null; el("flashcardsPanel").classList.add("hidden"); }
        renderLibrary(); notice("Élément supprimé."); return true;
    });
    study.clear = () => serialize(async () => {
        requireReady();
        if (!confirm(cloud ? "Effacer définitivement tous les éléments et dossiers de ton compte, sur tous tes appareils ?" : "Effacer la bibliothèque de ce navigateur ?")) return false;
        if (cloud) await api("/api/library", "DELETE");
        else { localStorage.removeItem(LIBRARY_STORAGE_KEY); localStorage.removeItem(folderKey); }
        study.items = []; study.folders = []; deck = null;
        currentRevisionLibraryId = ""; currentExerciseLibraryId = ""; currentRevisionFolder = ""; currentExerciseFolder = "";
        el("flashcardsPanel").classList.add("hidden"); renderFolders(); renderLibrary(); notice("Bibliothèque effacée."); return true;
    });
    study.matches = item => {
        const folder = el("libraryFolderFilter").value;
        return (folder === "all" || (item.folder || "") === folder) && String(item.title || "").toLocaleLowerCase("fr").includes(el("librarySearch").value.toLocaleLowerCase("fr"));
    };
    study.opened = item => conflicted.delete(item.id);
    function makeButton(label, callback) {
        const button = document.createElement("button"); button.type = "button"; button.className = "button button-secondary"; button.textContent = label;
        button.addEventListener("click", callback); return button;
    }
    study.decorate = (card, item, actions) => {
        const label = document.createElement("label"); label.className = "library-folder-label"; label.textContent = "Dossier ";
        const select = document.createElement("select"); select.setAttribute("aria-label", `Dossier de ${item.title}`); select.add(new Option("Sans dossier", ""));
        for (const folder of study.folders) select.add(new Option(folder.name, folder.id));
        select.value = item.folder || "";
        select.addEventListener("change", () => serialize(async () => {
            select.disabled = true;
            try {
                const saved = await persist({...item, folder: select.value, updatedAt: new Date().toISOString()});
                if (currentRevisionLibraryId === item.id) currentRevisionFolder = saved.folder;
                if (currentExerciseLibraryId === item.id) currentExerciseFolder = saved.folder;
                syncSaveFolders();
                if (deck?.id === item.id) deck = clone(saved);
            } catch (error) { select.value = item.folder || ""; throw error; }
            finally { select.disabled = false; }
        }));
        label.append(select); card.insertBefore(label, actions);
        if (item.type === "quiz" && (item.questions || []).some(q => q.selected !== q.correct) && item.sourceContent) {
            actions.prepend(makeButton("Retravailler mes erreurs", () => retryMistakes(item)));
        }
        if (item.type === "quiz" && item.questions?.length) actions.prepend(makeButton("Voir la correction", () => {
            const dialog = document.createElement("dialog"); dialog.className = "study-review-dialog";
            const heading = document.createElement("h2"); heading.textContent = item.title;
            const content = document.createElement("article"); content.className = "revision";
            content.innerHTML = markdownToHtml(item.questions.map((q, i) => `## ${i + 1}. ${q.question}\n\nTa réponse : ${q.answers[q.selected]}\n\nBonne réponse : ${q.answers[q.correct]}\n\n${q.explanation}`).join("\n\n"));
            dialog.append(heading, content, makeButton("Fermer", () => dialog.close()));
            dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog); dialog.showModal();
        }));
        if (item.type === "flashcards") {
            const due = (item.cards || []).filter(c => Date.parse(c.due) <= Date.now()).length;
            card.querySelector(".library-preview").textContent = `${(item.cards || []).length} cartes · ${due} à revoir maintenant`;
        }
    };
    study.renderProgress = items => {
        const root = el("studyProgress"); root.replaceChildren();
        const groups = new Map();
        for (const item of items.filter(x => x.type === "quiz" && Number(x.total) > 0)) {
            const key = item.folder || "", group = groups.get(key) || {score:0, total:0, attempts:0};
            group.score += Number(item.score || 0); group.total += Number(item.total); group.attempts += 1; groups.set(key, group);
        }
        for (const [key, group] of groups) {
            const row = document.createElement("p"), percentage = Math.round(100 * group.score / group.total);
            row.textContent = `${study.folders.find(f => f.id === key)?.name || "Sans dossier"} · ${percentage} % · ${group.attempts} quiz enregistré(s)`;
            root.append(row);
        }
        if (!groups.size) root.textContent = "Ta progression par dossier apparaîtra après ton premier quiz enregistré.";
        const visible = items.filter(study.matches).length;
        libraryEmptyState.classList.toggle("hidden", visible > 0);
        if (!visible) libraryEmptyState.textContent = items.length ? "Aucun élément ne correspond à cette recherche." : "Enregistre une fiche, un exercice ou un jeu de cartes pour commencer.";
    };
    async function createFolder(name) {
        requireReady(); name = String(name || "").trim().slice(0, 80);
        if (!name) return null;
        let folder = study.folders.find(f => f.name === name);
        if (folder) return folder;
        if (study.folders.length >= 50) throw new Error("Tu peux créer jusqu’à 50 dossiers.");
        folder = cloud ? (await api("/api/library/folders", "POST", {name})).folder : {id:id("folder"), name};
        const next = [...study.folders, folder];
        if (!cloud) localStorage.setItem(folderKey, JSON.stringify(next));
        study.folders = next; renderFolders(); return folder;
    }
    el("createFolderButton").addEventListener("click", () => {
        const name = prompt("Nom du dossier ou de la matière (ex. Maths — Terminale)");
        if (name?.trim()) serialize(async () => { const folder = await createFolder(name); el("libraryFolderFilter").value = folder.id; chooseNewItemFolder(); renderLibrary(); });
    });
    el("libraryFolderFilter").addEventListener("change", () => { chooseNewItemFolder(); renderLibrary(); });
    el("librarySearch").addEventListener("input", renderLibrary);
    el("refreshLibraryButton").addEventListener("click", refresh);
    libraryToggleButton.addEventListener("click", () => { if (!libraryPanel.classList.contains("hidden")) refresh(); });
    el("importLocalLibraryButton").classList.toggle("hidden", !cloud || (!localItems().length && !localFolders().length));
    el("importLocalLibraryButton").textContent = "Récupérer les anciennes révisions de ce navigateur";
    el("importLocalLibraryButton").addEventListener("click", () => serialize(async () => {
        requireReady();
        if (!confirm(`Copier la bibliothèque de ce navigateur dans ${storageName} ? Vérifie qu’elle t’appartient. Les éléments déjà enregistrés en ligne seront conservés.`)) return;
        const mapping = new Map();
        for (const folder of localFolders()) mapping.set(folder.id, (await createFolder(folder.name)).id);
        let imported = 0;
        for (const item of localItems()) {
            if (study.items.some(existing => existing.id === item.id)) continue;
            await persist({...item, id: /^[a-zA-Z0-9_-]{1,100}$/.test(item.id) ? item.id : id("import"), version: 0, folder: mapping.get(item.folder) || ""}); imported += 1;
        }
        notice(`${imported} élément(s) importé(s). Les originaux restent dans ce navigateur.`);
    }));
    // Les résultats détaillés permettent de créer de nouvelles questions sur les difficultés.
    function mistakes(questions) { return (questions || []).filter(q => q.selected !== q.correct); }
    function retryMistakes(item) {
        if (!item.sourceContent) { alert("Ouvre la fiche d’origine pour retravailler ce quiz."); return; }
        currentRevisionText = item.sourceContent; currentRevisionTitle = String(item.title || "Révision").replace(/^Résultat du quiz — /, "");
        currentRevisionLibraryId = item.sourceId || ""; currentRevisionFolder = item.folder || ""; sessionId = "";
        syncSaveFolders();
        resultContent.innerHTML = markdownToHtml(currentRevisionText); resultCard.classList.remove("hidden");
        loadQuiz({focus: mistakes(item.questions).map(q => `${q.question}\nExplication : ${q.explanation}`)});
    }
    study.showQuizMistakes = () => {
        const questions = quizQuestions.map((q, index) => ({...q, selected:quizSelections[index]}));
        const count = mistakes(questions).length;
        el("quizMistakesSummary").textContent = count ? `${count} question(s) à retravailler. Ton prochain quiz peut cibler ces difficultés.` : "Aucune erreur sur ce quiz. Tu peux vérifier tes acquis avec un nouveau quiz.";
        el("retryMistakesButton").disabled = !count;
    };
    el("retryMistakesButton").addEventListener("click", () => retryMistakes({sourceContent:quizRunSource, sourceId:quizRunSourceId, title:quizRunTitle, folder:quizRunFolder, questions:quizQuestions.map((q, index) => ({...q, selected:quizSelections[index]}))}));

    // Cartes : nouveau rappel à 10 minutes, puis 1, 3, 7, 14, 30, 60 jours.
    function cardNotice(text, error = false) { el("flashcardsStatus").textContent = text; el("flashcardsStatus").classList.toggle("error", error); }
    function displayCard() {
        shownCard = deck?.cards.find(card => card.id === cardQueue[0]) || null;
        el("flashcardFront").innerHTML = shownCard ? markdownToHtml(shownCard.front) : "<p>Ta séance est terminée. Reviens aux prochaines dates de révision.</p>";
        el("flashcardBack").classList.add("hidden");
        el("flipFlashcardButton").classList.toggle("hidden", !shownCard);
        for (const key of ["flashcardAgainButton", "flashcardKnownButton"]) el(key).classList.add("hidden");
        el("editFlashcardButton").classList.toggle("hidden", !shownCard);
        el("reviewAllCardsButton").classList.toggle("hidden", Boolean(shownCard));
        el("editFlashcardForm").classList.add("hidden");
        el("flashcardCounter").textContent = shownCard ? `${cardQueue.length} carte(s) restante(s) · ${deck.cards.length} dans ce jeu` : `${deck?.cards.length || 0} cartes dans ce jeu`;
        if (!shownCard && deck?.cards.length) {
            const next = Math.min(...deck.cards.map(card => Date.parse(card.due)).filter(Number.isFinite));
            if (Number.isFinite(next)) el("flashcardCounter").textContent += ` · prochain rappel : ${new Date(next).toLocaleString("fr-FR")}`;
        }
    }
    study.openDeck = item => {
        conflicted.delete(item.id);
        deck = clone(item); cardQueue = deck.cards.filter(card => Date.parse(card.due) <= Date.now()).map(card => card.id);
        el("flashcardsTitle").textContent = deck.title;
        el("flashcardsPanel").classList.remove("hidden");
        cardNotice("Les dates de révision sont enregistrées quand tu évalues une carte."); displayCard();
        el("flashcardsPanel").scrollIntoView({behavior:"smooth", block:"start"});
    };
    el("flashcardsButton").addEventListener("click", async () => {
        if (!currentRevisionText.trim()) return;
        const button = el("flashcardsButton"); button.disabled = true;
        const source = currentRevisionText, title = currentRevisionTitle, sourceId = currentRevisionLibraryId, folder = currentRevisionFolder;
        el("flashcardsPanel").classList.remove("hidden"); cardNotice("Création des cartes à partir de ta fiche…");
        try {
            const result = await api("/api/flashcards", "POST", {course:source});
            study.openDeck({id:id("cards"), type:"flashcards", title:`Cartes — ${title}`.slice(0,180), content:"", sourceId, folder, cards:result.cards.map(card => ({...card, id:id("card"), due:new Date().toISOString(), interval:0, reviews:0}))});
            cardNotice("Cartes prêtes. Enregistre ton jeu pour le retrouver plus tard. Tu peux corriger une carte avant de réviser.");
        } catch (error) { cardNotice(error.message, true); }
        finally { button.disabled = false; refreshUsageSummary(); }
    });
    async function saveDeck(next = deck) {
        if (!next) return false;
        const saved = await study.save("flashcards", next.title, "", next.id, null, null, {cards:next.cards, folder:next.folder, sourceId:next.sourceId || "", version:next.version || 0});
        if (saved && deck?.id === next.id) deck = clone(saved);
        if (deck?.id === next.id) cardNotice(saved ? (cloud ? `Jeu et progression enregistrés dans ${storageName} ✓` : "Jeu et progression enregistrés dans ce navigateur ✓") : "L’enregistrement a échoué. Ta progression reste affichée ; réessaie.", !saved);
        return saved;
    }
    el("saveDeckButton").addEventListener("click", async () => {
        if (reviewing) return;
        reviewing = true; el("saveDeckButton").disabled = true;
        try { await saveDeck(); } finally { reviewing = false; el("saveDeckButton").disabled = false; }
    });
    el("flipFlashcardButton").addEventListener("click", () => {
        if (!shownCard) return;
        el("flashcardBack").innerHTML = markdownToHtml(shownCard.back); el("flashcardBack").classList.remove("hidden");
        el("flipFlashcardButton").classList.add("hidden");
        el("flashcardAgainButton").classList.remove("hidden"); el("flashcardKnownButton").classList.remove("hidden");
        const days = [1,3,7,14,30,60].find(value => value > (shownCard.interval || 0)) || 60;
        el("flashcardKnownButton").textContent = `Je savais · dans ${days} jour(s)`;
    });
    async function gradeCard(known) {
        if (!shownCard || reviewing) return;
        reviewing = true;
        const next = clone(deck), card = next.cards.find(c => c.id === shownCard.id);
        card.interval = known ? ([1,3,7,14,30,60].find(value => value > (card.interval || 0)) || 60) : 0;
        card.due = new Date(Date.now() + (known ? card.interval * 86400000 : 600000)).toISOString(); card.reviews = (card.reviews || 0) + 1;
        const saved = await saveDeck(next);
        if (saved && deck?.id === next.id) { cardQueue.shift(); displayCard(); }
        reviewing = false;
    }
    el("flashcardAgainButton").addEventListener("click", () => gradeCard(false));
    el("flashcardKnownButton").addEventListener("click", () => gradeCard(true));
    el("reviewAllCardsButton").addEventListener("click", () => { if (deck) { cardQueue = deck.cards.map(card => card.id); displayCard(); } });
    el("editFlashcardButton").addEventListener("click", () => {
        if (!shownCard) return;
        el("editCardFront").value = shownCard.front; el("editCardBack").value = shownCard.back; el("editFlashcardForm").classList.remove("hidden"); el("editCardFront").focus();
    });
    el("cancelEditCardButton").addEventListener("click", () => el("editFlashcardForm").classList.add("hidden"));
    el("editFlashcardForm").addEventListener("submit", async event => {
        event.preventDefault(); if (!shownCard || reviewing) return;
        const front = el("editCardFront").value.trim(), back = el("editCardBack").value.trim();
        if (!front || !back) return;
        reviewing = true;
        const next = clone(deck), card = next.cards.find(c => c.id === shownCard.id); card.front = front; card.back = back;
        if (await saveDeck(next) && deck?.id === next.id) displayCard();
        reviewing = false;
    });
    // Une page A4 dédiée conserve la mise en forme et les formules du navigateur.
    function exportPdf(title, content) {
        if (!String(content || "").trim()) return;
        const popup = window.open("", "_blank", "width=900,height=850");
        if (!popup) { alert("Autorise l’ouverture de la fenêtre d’export pour enregistrer ton PDF."); return; }
        popup.document.open();
        popup.document.write(`<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>
            @page{size:A4;margin:18mm}*{box-sizing:border-box}body{font:11pt/1.65 Arial,sans-serif;color:#202337;max-width:180mm;margin:24px auto;padding:12px}header{border-bottom:2px solid #5b4cf6;margin-bottom:24px;padding-bottom:12px}header strong{color:#5b4cf6}h1{font-size:22pt}h2{font-size:16pt;margin-top:24px}h3{font-size:13pt}h1,h2,h3{break-after:avoid}p,li{orphans:3;widows:3;overflow-wrap:anywhere}ul,ol{padding-left:24px}hr{border:0;border-top:1px solid #ddd;margin:20px 0}sub,sup{font-size:.78em}.vector-notation{position:relative;display:inline-block;padding-top:.2em;line-height:1}.vector-notation:before{content:'→';position:absolute;left:0;right:0;top:-.48em;text-align:center;font-size:.68em}.export-tools{padding:16px;background:#f2efff;border-radius:12px;margin-bottom:24px}.export-tools button{font:inherit;padding:10px;cursor:pointer}@media print{body{max-width:none;margin:0;padding:0}.export-tools{display:none}}
            </style></head><body><div class="export-tools"><p>Choisis « Enregistrer au format PDF » dans la fenêtre d’impression. Tu peux aussi imprimer cette fiche.</p><button id="printPdf" type="button">Enregistrer en PDF / Imprimer</button></div><header><strong>RéviAI</strong> · ${new Date().toLocaleDateString("fr-FR")}<h1>${escapeHtml(title)}</h1></header><main>${markdownToHtml(content)}</main></body></html>`);
        popup.document.close();
        popup.document.getElementById("printPdf").addEventListener("click", () => popup.print());
        popup.focus();
    }
    study.exportPdf = exportPdf;
    el("examFromRevisionButton").addEventListener("click", () => {
        if (!currentRevisionText.trim()) return;
        try {
            sessionStorage.setItem("reviai-exam-course", JSON.stringify({content:currentRevisionText, title:currentRevisionTitle, scope:document.body.dataset.accountId || ""}));
            window.location.href = "/controle-blanc";
        } catch (_) { alert("Le transfert de la fiche est bloqué par ce navigateur. Ouvre « Contrôle blanc » et colle le texte de ta fiche."); }
    });
    el("exportRevisionButton").addEventListener("click", () => exportPdf(currentRevisionTitle, currentRevisionText));
    el("exportExerciseButton").addEventListener("click", () => exportPdf(currentExerciseTitle, currentExerciseText));
    const guided = el("guidedExerciseToggle");
    guided.addEventListener("change", () => {
        exerciseHelpMode.value = guided.checked ? "hint" : "complete";
        solveExerciseButton.textContent = guided.checked ? "💡 Commencer avec un indice" : "✨ Résoudre mon exercice";
        exerciseChatInput.placeholder = guided.checked ? "Écris ton essai ou l’étape qui te bloque…" : "Exemple : pourquoi as-tu utilisé cette formule ?";
    });
    exerciseHelpMode.addEventListener("change", () => { guided.checked = exerciseHelpMode.value !== "complete"; });
    el("libraryStorageNote").textContent = editor
        ? "La bibliothèque de l’éditeur est sauvegardée en ligne. Connecte-toi à l’espace éditeur sur tes autres appareils pour la retrouver. Pour tes anciennes fiches, utilise le bouton de récupération dans leur navigateur d’origine."
        : cloud ? "Tes fiches, exercices, cartes et quiz enregistrés sont accessibles sur tes autres appareils après connexion au même compte."
        : "Tes révisions restent dans ce navigateur. Crée un compte gratuit pour les retrouver sur tes autres appareils.";
    renderFolders(); renderLibrary(); if (cloud) refresh();
    window.addEventListener("storage", event => { if (!cloud && [LIBRARY_STORAGE_KEY,folderKey].includes(event.key)) refresh(); });
    window.addEventListener("focus", () => { if (cloud && !pending && !libraryPanel.classList.contains("hidden")) refresh(); });
})();
