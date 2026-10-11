/* Contrôle blanc : aucun corrigé n'est envoyé avant la remise de la copie. */
(() => {
    "use strict";
    const app = document.getElementById("examApp");
    if (!app) return;
    const el = id => document.getElementById(id);
    const base = "/api/controle-blanc";
    const cloud = app.dataset.cloud === "true";
    const prefix = `reviai-exam:${app.dataset.scope}:`;
    const maxBytes = Number(app.dataset.maxMb) * 1024 * 1024;
    const busyStatuses = new Set(["generating", "reading", "grading"]);
    let state = null, renderedId = "", renderedResult = "", lastRead = 0;
    let polling = null, ticking = null, pollFailures = 0, requesting = false, offset = 0;
    let courseFiles = [], copyFiles = [], library = null, saved = false, pendingCourse = null;
    function readSession(key, fallback = null) {
        try { return JSON.parse(sessionStorage.getItem(prefix + key) || "null") ?? fallback; }
        catch (_) { return fallback; }
    }
    function writeSession(key, value) {
        try { sessionStorage.setItem(prefix + key, JSON.stringify(value)); }
        catch (_) { /* Le contrôle reste utilisable si le stockage de l'onglet est bloqué. */ }
    }
    function removeSession(key) { try { sessionStorage.removeItem(prefix + key); } catch (_) {} }
    function notice(message, error = false) {
        el("examStatus").textContent = message;
        el("examStatus").classList.toggle("error", error);
    }
    const number = value => Number(value).toLocaleString("fr-FR", {maximumFractionDigits: 2});
    function element(tag, text, className = "") {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    async function api(path, method = "GET", body) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), method === "GET" ? 20000 : 90000);
        const headers = {"X-CSRF-Token": app.dataset.csrf};
        const options = {method, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal};
        if (body instanceof FormData) options.body = body;
        else if (body !== undefined) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
        try {
            const response = await fetch(path, options);
            let data;
            try { data = await response.json(); }
            catch (_) { throw new Error("Le serveur n’a pas répondu correctement. Réessaie dans un instant."); }
            if (!response.ok) {
                const error = new Error(data.error || "La demande n’a pas abouti.");
                error.status = response.status; throw error;
            }
            return data;
        } catch (error) {
            if (error.name === "AbortError") throw new Error("La connexion prend trop de temps. Reprends le suivi sans renvoyer ta copie.");
            throw error;
        } finally { clearTimeout(timeout); }
    }
    function draft() {
        const answers = {};
        el("examQuestions").querySelectorAll("textarea[data-question]").forEach(input => { answers[input.dataset.question] = input.value; });
        return {answers, transcript: el("examTranscript").value, reviewed: el("examTranscriptReviewed").checked, readVersion: lastRead};
    }
    function saveDraft() {
        if (!state?.id || state.status === "graded") return;
        writeSession(`draft:${state.id}`, draft());
        const filled = Object.values(draft().answers).filter(answer => answer.trim()).length;
        el("examProgress").value = filled;
        el("examProgressText").textContent = `${filled} / ${state.questions?.length || 0} réponses saisies${el("examTranscript").value.trim() ? " · copie transcrite ajoutée" : ""}`;
    }
    function renderQuestions(next) {
        if (renderedId === next.id || !next.questions) return;
        renderedId = next.id;
        const stored = readSession(`draft:${next.id}`, {});
        lastRead = Number(stored.readVersion || 0);
        el("examQuestions").replaceChildren();
        next.questions.forEach((question, index) => {
            const section = element("section", undefined, "exam-question");
            const heading = element("div", undefined, "exam-question-heading");
            heading.append(element("h3", `Question ${index + 1}`), element("span", `${question.points} points`, "exam-points"));
            const input = document.createElement("textarea");
            input.id = `examAnswer${index}`; input.dataset.question = question.id; input.rows = 5; input.maxLength = 4000;
            input.placeholder = "Rédige ta réponse, les étapes de calcul et les justifications…";
            input.value = stored.answers?.[question.id] ?? next.submitted_answers?.[question.id] ?? "";
            input.addEventListener("input", saveDraft);
            const label = element("label", `Ta réponse à la question ${index + 1}`); label.htmlFor = input.id;
            section.append(heading, element("p", question.prompt, "exam-prose"), label, input, element("div", "", "exam-paper-lines"));
            el("examQuestions").append(section);
        });
        el("examProgress").max = next.questions.length;
        el("examTranscript").value = stored.transcript ?? next.transcript ?? "";
        el("examTranscriptReviewed").checked = Boolean(stored.reviewed);
        el("examTranscriptPanel").hidden = !el("examTranscript").value;
        saveDraft();
    }
    function tick() {
        if (!state?.deadline || !state.started_at) return;
        const now = state.status === "grading" && state.submitted_at ? state.submitted_at : Date.now() / 1000 + offset;
        const remaining = Math.max(0, Math.ceil(state.deadline - now));
        el("examTimer").textContent = `${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`;
        el("examTimer").closest(".exam-clock").classList.toggle("overdue", remaining === 0);
        el("examTimeNotice").textContent = state.status === "grading" ? "Copie remise · correction en cours" : remaining === 0 ? "Temps prévu dépassé. Tu peux encore rendre ta copie." : "Le chrono n’enlève pas de points.";
    }
    function setDisabled(formId, disabled) {
        el(formId).querySelectorAll("input, select, textarea, button").forEach(input => { input.disabled = disabled; });
    }
    function endRequest() {
        requesting = false;
        setDisabled("examCreateForm", state?.status === "generating");
        if (state?.status === "error" && state.phase === "generation" && state.attempts >= 3) el("examCreate").disabled = true;
        el("examStart").disabled = false;
        el("examReadCopy").disabled = Boolean(state && (["reading", "grading", "graded"].includes(state.status) || state.reads >= 3));
        el("examSubmit").disabled = Boolean(state && (busyStatuses.has(state.status) || state.status === "graded" || state.grade_attempts >= 3));
    }
    function render(next) {
        state = next; offset = next.server_now - Date.now() / 1000;
        writeSession("current", next.id);
        el("examRecovery").hidden = true;
        const creation = next.status === "generating";
        const generationError = next.status === "error" && next.phase === "generation";
        const attempting = Boolean(next.started_at) && next.status !== "graded";
        el("examSetup").hidden = !(creation || generationError);
        el("examReady").hidden = next.status !== "ready";
        el("examAttempt").hidden = !attempting;
        el("examResults").hidden = next.status !== "graded";
        el("examNew").hidden = false;
        setDisabled("examCreateForm", creation || requesting);
        el("examCreate").textContent = generationError ? "Réessayer la création du sujet" : "Créer mon contrôle blanc →";
        if (generationError && next.attempts >= 3) el("examCreate").disabled = true;
        el("examTopic").value = next.topic || "";
        el("examLevel").value = next.level;
        el("examDuration").value = String(next.duration);
        notice(next.message || "", next.status === "error");
        if (next.status === "ready") {
            el("examReadyTitle").textContent = next.title;
            el("examReadyDuration").textContent = `${next.duration} minutes`;
            el("examReadyCount").textContent = `${next.question_count} questions rédigées`;
        }
        if (next.started_at) {
            renderQuestions(next);
            el("examAttemptTitle").textContent = next.title;
            el("examInstructions").textContent = next.instructions;
            if (next.read_version > lastRead) {
                el("examTranscript").value = next.transcript;
                el("examTranscriptReviewed").checked = false;
                el("examTranscriptPanel").hidden = false;
                lastRead = next.read_version; saveDraft();
            }
            el("examCopyStatus").textContent = next.copy_error || (next.status === "reading" ? "Lecture en cours. Tes réponses saisies restent modifiables." : next.read_version ? "Relis les numéros, les signes et les formules avant de rendre ta copie." : "");
            el("examCopyStatus").classList.toggle("error", Boolean(next.copy_error));
            const grading = next.status === "grading" || next.status === "graded";
            el("examQuestions").querySelectorAll("textarea").forEach(input => { input.disabled = grading; });
            el("examTranscript").disabled = grading;
            el("examTranscriptReviewed").disabled = grading;
            el("examCopyFiles").disabled = grading || next.status === "reading";
            el("examReadCopy").disabled = grading || next.status === "reading" || next.reads >= 3 || requesting;
            el("examReadCopy").textContent = next.reads >= 3 ? "Trois lectures de copie utilisées" : "Lire ma copie";
            el("examSubmit").disabled = busyStatuses.has(next.status) || grading || next.grade_attempts >= 3 || requesting;
            el("examSubmit").textContent = next.status === "error" ? "Réessayer la correction de ma copie" : "Rendre ma copie et voir la correction →";
            tick();
        }
        clearInterval(ticking);
        if (attempting && next.status !== "grading") ticking = setInterval(tick, 1000);
        if (next.status === "graded") {
            renderResult(next);
            removeSession(`draft:${next.id}`);
        }
        clearTimeout(polling);
        if (busyStatuses.has(next.status)) polling = setTimeout(poll, 1400);
    }
    async function poll() {
        if (!state?.id) return;
        try {
            const next = await api(`${base}/${encodeURIComponent(state.id)}`);
            pollFailures = 0; render(next);
        } catch (error) {
            notice(error.message, true);
            if (error.status === 404) {
                clearTimeout(polling); clearInterval(ticking);
                el("examRecovery").hidden = false;
                el("examRecovery").querySelector("p").textContent = "Ce contrôle n’est plus disponible sur le serveur. Copie tes réponses si tu veux les conserver, puis crée un nouveau sujet.";
                return;
            }
            pollFailures += 1;
            el("examRecovery").hidden = false;
            if (pollFailures < 4) polling = setTimeout(poll, Math.min(15000, 2500 * pollFailures));
        }
    }
    async function recoverLatest() {
        try {
            const latest = await api(state?.id ? `${base}/${encodeURIComponent(state.id)}` : base);
            if (latest.id) { render(latest); return true; }
        } catch (_) {}
        return false;
    }
    async function usage() {
        try {
            const data = await api("/api/usage");
            if (data.unlimited) el("examUsage").textContent = `Accès ${data.plan} : contrôles blancs sans limite quotidienne.`;
            else {
                const limit = data.limits.exam || {};
                el("examUsage").textContent = limit.limit === null ? "Contrôles blancs sans limite quotidienne." : `Aujourd’hui : ${limit.remaining ?? 0} contrôle(s) blanc(s) gratuit(s) restant(s). La correction du sujet est incluse.`;
            }
        } catch (_) { el("examUsage").textContent = "Le contrôle blanc est disponible avec une limite quotidienne gratuite. Plus, VIP et éditeur ont un accès illimité."; }
    }
    function filesList(kind) {
        const list = kind === "course" ? courseFiles : copyFiles;
        const root = el(kind === "course" ? "examCourseFileList" : "examCopyFileList");
        root.replaceChildren();
        list.forEach((file, index) => {
            const row = element("li"); const remove = element("button", "Retirer"); remove.type = "button";
            remove.setAttribute("aria-label", `Retirer ${file.name}`);
            remove.addEventListener("click", () => { list.splice(index, 1); filesList(kind); });
            row.append(element("span", `${index + 1}. ${file.name} · ${Math.ceil(file.size / 1024)} Ko`), remove); root.append(row);
        });
    }
    function bindFiles(inputId, kind) {
        el(inputId).addEventListener("change", event => {
            const list = kind === "course" ? courseFiles : copyFiles;
            const incoming = Array.from(event.target.files || []).filter(file => !list.some(old => old.name === file.name && old.size === file.size && old.lastModified === file.lastModified));
            if ([...list, ...incoming].reduce((total, file) => total + file.size, 0) > maxBytes) notice(`L’envoi dépasse ${app.dataset.maxMb} Mo. Retire quelques fichiers ou réduis leur taille.`, true);
            else { list.push(...incoming); filesList(kind); }
            event.target.value = "";
        });
    }
    bindFiles("examCourseFiles", "course"); bindFiles("examCopyFiles", "copy");
    el("examCreateForm").addEventListener("submit", async event => {
        event.preventDefault(); if (requesting) return;
        const course = el("examCourse").value.trim();
        const retry = state?.status === "error" && state.phase === "generation";
        if (!courseFiles.length && course.length < 80 && !(retry && state.course_available && !course)) { notice("Importe ton cours ou colle au moins 80 caractères.", true); return; }
        const body = new FormData();
        for (const [key, id] of [["course", "examCourse"], ["topic", "examTopic"], ["level", "examLevel"], ["duration", "examDuration"]]) body.set(key, el(id).value);
        courseFiles.forEach(file => body.append("files", file));
        if (retry) body.set("retry_id", state.id);
        requesting = true; setDisabled("examCreateForm", true); notice("Envoi de ton cours…");
        try {
            const created = await api(base, "POST", body);
            writeSession("current", created.id); renderedId = ""; renderedResult = ""; saved = false;
            requesting = false;
            render(await api(`${base}/${encodeURIComponent(created.id)}`));
            usage();
        } catch (error) {
            notice(error.message, true);
            if (!error.status || error.status >= 500) await recoverLatest();
        } finally { endRequest(); }
    });
    el("examStart").addEventListener("click", async () => {
        if (requesting || !state) return;
        requesting = true; el("examStart").disabled = true;
        try { const next = await api(`${base}/${state.id}/commencer`, "POST", {}); requesting = false; render(next); el("examAttempt").scrollIntoView({behavior:"smooth", block:"start"}); }
        catch (error) { notice(error.message, true); if (!error.status) await recoverLatest(); }
        finally { endRequest(); }
    });
    el("examReadCopy").addEventListener("click", async () => {
        if (requesting || !state) return;
        if (!copyFiles.length) { el("examCopyStatus").textContent = "Choisis d’abord les pages de ta copie."; return; }
        if (el("examTranscript").value.trim() && !confirm("Remplacer la transcription actuelle par la nouvelle lecture de ces pages ?")) return;
        const body = new FormData(); copyFiles.forEach(file => body.append("copy_files", file));
        saveDraft(); requesting = true; el("examReadCopy").disabled = true; el("examSubmit").disabled = true;
        try {
            await api(`${base}/${state.id}/lire-copie`, "POST", body);
            requesting = false; await poll();
        } catch (error) { notice(error.message, true); if (!error.status) await recoverLatest(); }
        finally { endRequest(); }
    });
    el("examTranscript").addEventListener("input", () => { el("examTranscriptReviewed").checked = false; saveDraft(); });
    el("examTranscriptReviewed").addEventListener("change", saveDraft);
    el("examAnswerForm").addEventListener("submit", async event => {
        event.preventDefault(); if (requesting || !state || busyStatuses.has(state.status)) return;
        const copy = draft();
        if (copy.transcript.trim() && !copy.reviewed) { notice("Relis la transcription puis coche la case de confirmation.", true); el("examTranscriptReviewed").focus(); return; }
        const blanks = Object.values(copy.answers).filter(value => !value.trim()).length;
        if (blanks && !copy.transcript.trim() && !confirm(`${blanks} question(s) n’ont pas de réponse. Rendre quand même ta copie ?`)) return;
        if (copyFiles.length && !copy.transcript.trim() && !confirm("Les fichiers sélectionnés ne seront pas corrigés sans lecture. Clique sur « Lire ma copie » pour les utiliser. Rendre uniquement tes réponses saisies ?")) return;
        saveDraft(); requesting = true; el("examSubmit").disabled = true;
        try {
            await api(`${base}/${state.id}/corriger`, "POST", {answers:copy.answers, transcript:copy.transcript, transcript_reviewed:copy.reviewed});
            requesting = false; await poll();
        } catch (error) { notice(error.message, true); if (!error.status) await recoverLatest(); }
        finally { endRequest(); }
    });
    function list(id, values, empty) {
        const root = el(id); root.replaceChildren();
        (values.length ? values : [empty]).forEach(value => root.append(element("li", value)));
    }
    function renderResult(next) {
        if (renderedResult === next.id) return;
        renderedResult = next.id;
        const result = next.result;
        el("examResultTitle").textContent = result.title;
        el("examScore").textContent = result.score === null ? "À vérifier" : `${number(result.score)} / 20`;
        el("examPartialScore").textContent = result.score === null ? `${number(result.graded_points)} points obtenus sur ${number(result.graded_out_of)} points évalués. Pas de note globale tant que des questions restent incertaines.` : "Calculée à partir des critères du barème";
        const elapsed = Math.floor(result.elapsed_seconds / 60);
        el("examResultTime").textContent = `${elapsed} min ${result.elapsed_seconds % 60} s · durée prévue ${result.duration} min · ${result.overtime ? "temps prévu dépassé" : "copie rendue dans le temps prévu"}`;
        list("examStrengths", result.strengths, "Consulte les retours question par question pour faire le point.");
        list("examNextSteps", result.next_steps, "Refais les questions en expliquant chaque étape sans regarder la correction.");
        el("examCommonCopy").hidden = !result.transcript;
        el("examCommonCopyText").textContent = result.transcript || "";
        el("examCorrections").replaceChildren();
        result.questions.forEach((question, index) => {
            const section = element("section", undefined, "exam-correction");
            const heading = element("div", undefined, "exam-question-heading");
            heading.append(element("h3", `Question ${index + 1}`), element("span", question.earned === null ? "À vérifier" : `${number(question.earned)} / ${question.points}`, "exam-points"));
            section.append(heading, element("p", question.prompt, "exam-prose"), element("h4", "Ta réponse saisie"),
                element("p", question.answer || (result.transcript ? "Voir ta copie transcrite ci-dessus." : "Aucune réponse remise."), "exam-prose"),
                element("p", question.feedback, "exam-prose exam-answer-feedback"));
            const details = element("details"); details.open = true;
            details.append(element("summary", "Correction et barème détaillé"));
            const content = element("div"); content.append(element("p", question.correction, "exam-prose"));
            const table = element("table", undefined, "exam-criteria");
            const head = element("thead"); const headings = element("tr");
            headings.append(element("th", "Critère et retour"), element("th", "Points")); head.append(headings);
            const tbody = element("tbody");
            question.criteria.forEach(criterion => {
                const row = element("tr"), cell = element("td", criterion.description);
                cell.append(element("small", criterion.feedback));
                row.append(cell, element("td", `${criterion.earned === null ? "À vérifier" : number(criterion.earned)} / ${number(criterion.points)}`)); tbody.append(row);
            });
            table.append(head, tbody); content.append(table); details.append(content); section.append(details); el("examCorrections").append(section);
        });
        loadFolders().catch(error => { el("examSaveStatus").textContent = error.message; });
        el("examResults").scrollIntoView({behavior:"smooth", block:"start"});
    }
    function localRows(key) {
        const parsed = JSON.parse(localStorage.getItem(key) || "[]");
        return Array.isArray(parsed) ? parsed.filter(row => row && typeof row === "object") : [];
    }
    async function getLibrary() {
        const remote = await api("/api/library");
        if (Boolean(remote.authenticated) !== cloud || (cloud && remote.account_id !== app.dataset.libraryOwner)) throw new Error("Le compte connecté a changé. Copie ou télécharge ton bilan, puis recharge la page.");
        if (cloud) library = remote;
        else {
            try { library = {items:localRows("reviai-library-v1"), folders:localRows("reviai-folders-v1")}; }
            catch (_) { throw new Error("La bibliothèque de ce navigateur est inaccessible. Télécharge ton bilan pour le conserver."); }
        }
        return library;
    }
    async function loadFolders() {
        const data = await getLibrary();
        const previous = data.items.find(item => item.id === `exam-${state?.id}`);
        const selected = el("examSaveFolder").value || previous?.folder || "";
        const select = el("examSaveFolder"); select.replaceChildren(new Option("Sans dossier", ""));
        data.folders.forEach(folder => select.add(new Option(folder.name, folder.id)));
        select.value = data.folders.some(folder => folder.id === selected) ? selected : "";
        if (previous) { saved = true; el("examSave").textContent = "Bilan enregistré ✓"; }
    }
    el("examLoadLibrary").addEventListener("click", async () => {
        const button = el("examLoadLibrary"); button.disabled = true;
        try {
            const data = await getLibrary();
            const revisions = data.items.filter(item => item.type === "revision" && typeof item.content === "string" && item.content.trim());
            const select = el("examLibrarySource"); select.replaceChildren(new Option("Choisir une fiche…", ""));
            revisions.forEach(item => select.add(new Option(item.title || "Fiche", item.id)));
            el("examLibraryLabel").hidden = !revisions.length;
            el("examLibraryStatus").textContent = revisions.length ? "Le sujet utilisera le contenu de la fiche sélectionnée. Le cours original est préférable s’il contient plus de détails." : "Aucune fiche enregistrée. Importe ton cours ou colle son texte.";
        } catch (error) { el("examLibraryStatus").textContent = error.message; }
        finally { button.disabled = false; }
    });
    el("examLibrarySource").addEventListener("change", () => {
        const item = library?.items.find(row => row.id === el("examLibrarySource").value);
        if (!item) return;
        el("examCourse").value = item.content.slice(0, 60000);
        el("examTopic").value = String(item.title || "").slice(0, 120);
    });
    el("examSave").addEventListener("click", async () => {
        if (!state?.result) return;
        const button = el("examSave"); button.disabled = true;
        const folder = el("examSaveFolder").value;
        try {
            const data = await getLibrary();
            if (folder && !data.folders.some(row => row.id === folder)) throw new Error("Ce dossier n’existe plus. Actualise les dossiers puis choisis-en un autre.");
            const id = `exam-${state.id}`;
            const previous = data.items.find(item => item.id === id);
            if (!previous && data.items.length >= 200) throw new Error("Ta bibliothèque contient déjà 200 éléments. Supprime un ancien élément ou télécharge ce bilan.");
            if (state.result.report.length > 120000) throw new Error("Ce bilan dépasse la taille de la bibliothèque. Télécharge-le ou exporte-le en PDF pour le conserver intégralement.");
            const item = {id, type:"exam", title:`Contrôle blanc — ${state.result.title}`.slice(0,180), content:state.result.report,
                score:state.result.score, total:20, folder, createdAt:previous?.createdAt || new Date().toISOString(), updatedAt:new Date().toISOString()};
            if (cloud) {
                const response = await fetch(`/api/library/items/${encodeURIComponent(id)}`, {method:"PUT", credentials:"same-origin", headers:{"Content-Type":"application/json", "X-CSRF-Token":data.csrf_token}, body:JSON.stringify({item, expected_version:previous?.version || 0})});
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "L’enregistrement n’a pas abouti.");
            } else localStorage.setItem("reviai-library-v1", JSON.stringify([item,...data.items.filter(row => row.id !== id)]));
            saved = true; button.textContent = "Bilan enregistré ✓";
            el("examSaveStatus").textContent = cloud ? "Ton bilan est enregistré dans ta bibliothèque et disponible sur tes autres appareils." : "Ton bilan est enregistré dans la bibliothèque de ce navigateur.";
        } catch (error) { el("examSaveStatus").textContent = error.message || "L’enregistrement n’a pas abouti. Télécharge ton bilan pour le conserver."; }
        finally { button.disabled = false; }
    });
    el("examCopyResult").addEventListener("click", async () => {
        if (!state?.result) return;
        try { await navigator.clipboard.writeText(state.result.report); el("examSaveStatus").textContent = "Bilan copié."; }
        catch (_) { el("examSaveStatus").textContent = "La copie n’est pas autorisée dans ce navigateur. Utilise « Télécharger le bilan »."; }
    });
    el("examDownload").addEventListener("click", () => {
        if (!state?.result) return;
        const url = URL.createObjectURL(new Blob([state.result.report], {type:"text/plain;charset=utf-8"}));
        const link = document.createElement("a"); link.href = url; link.download = "ReviAI-controle-blanc.txt";
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    });
    function print(mode) {
        if (!state?.started_at) return;
        document.body.dataset.examPrint = mode;
        document.querySelectorAll(".exam-correction details").forEach(details => { details.open = true; });
        window.print();
    }
    el("examPrintSubject").addEventListener("click", () => print("subject"));
    el("examPrintResult").addEventListener("click", () => print("result"));
    el("examNew").addEventListener("click", async () => {
        if (requesting) return;
        if (state && !confirm(saved ? "Créer un nouveau contrôle ? Ton bilan enregistré restera dans ta bibliothèque." : "Créer un nouveau contrôle et supprimer cet essai temporaire ? Télécharge ou enregistre d’abord ton bilan si tu veux le garder.")) return;
        requesting = true;
        try {
            if (state) {
                try { await api(`${base}/${state.id}`, "DELETE"); } catch (error) { if (error.status !== 404) throw error; }
                removeSession(`draft:${state.id}`);
            }
            clearTimeout(polling); clearInterval(ticking); removeSession("current");
            state = null; renderedId = ""; renderedResult = ""; lastRead = 0; saved = false;
            copyFiles = []; filesList("copy"); el("examTranscript").value = ""; el("examTranscriptReviewed").checked = false;
            ["examReady", "examAttempt", "examResults", "examRecovery", "examNew"].forEach(id => { el(id).hidden = true; });
            el("examSetup").hidden = false; setDisabled("examCreateForm", false); el("examCreate").textContent = "Créer mon contrôle blanc →";
            el("examSave").textContent = "Enregistrer mon bilan"; el("examSaveStatus").textContent = "";
            if (pendingCourse) {
                el("examCourse").value = pendingCourse.content.slice(0,60000);
                el("examTopic").value = String(pendingCourse.title || "").slice(0,120);
                courseFiles = []; filesList("course"); pendingCourse = null;
            }
            notice(""); usage(); el("examSetup").scrollIntoView({behavior:"smooth", block:"start"});
        } catch (error) { notice(error.message, true); }
        finally { requesting = false; }
    });
    el("examReconnect").addEventListener("click", () => { pollFailures = 0; if (state) poll(); else recoverLatest(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && state && busyStatuses.has(state.status)) { clearTimeout(polling); poll(); } else tick(); });
    window.addEventListener("beforeunload", event => { if (state?.started_at && state.status !== "graded") { saveDraft(); event.preventDefault(); event.returnValue = ""; } });
    async function init() {
        usage();
        const previous = readSession("current");
        if (typeof previous === "string" && previous) {
            notice("Recherche de ton contrôle en cours…");
            try { render(await api(`${base}/${encodeURIComponent(previous)}`)); }
            catch (error) {
                notice(error.message, true);
                if (error.status === 404) removeSession("current");
                else el("examRecovery").hidden = false;
            }
        }
        try {
            const handoff = JSON.parse(sessionStorage.getItem("reviai-exam-course") || "null");
            sessionStorage.removeItem("reviai-exam-course");
            if (handoff && handoff.scope === app.dataset.libraryOwner && typeof handoff.content === "string") {
                if (state) {
                    pendingCourse = handoff;
                    notice("Tu as déjà un contrôle ouvert. Utilise « Nouveau contrôle blanc » pour préparer un sujet depuis la fiche que tu viens de choisir.");
                } else {
                    el("examCourse").value = handoff.content.slice(0,60000);
                    el("examTopic").value = String(handoff.title || "").slice(0,120);
                }
            }
        } catch (_) {}
    }
    init();
})();
