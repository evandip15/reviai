const form = document.getElementById("uploadForm");
const filesInput = document.getElementById("files");
const fileList = document.getElementById("fileList");
const generateButton = document.getElementById("generateButton");
const statusBox = document.getElementById("status");
const resultCard = document.getElementById("resultCard");
const resultContent = document.getElementById("resultContent");
const copyButton = document.getElementById("copyButton");
const dropZone = document.getElementById("dropZone");
const quizButton = document.getElementById("quizButton");
const quizCard = document.getElementById("quizCard");
const quizResultCard = document.getElementById("quizResultCard");
const quizCounter = document.getElementById("quizCounter");
const quizProgress = document.getElementById("quizProgress");
const quizQuestion = document.getElementById("quizQuestion");
const quizAnswers = document.getElementById("quizAnswers");
const quizFeedback = document.getElementById("quizFeedback");
const quizScore = document.getElementById("quizScore");
const nextQuizButton = document.getElementById("nextQuizButton");
const quizFinalScore = document.getElementById("quizFinalScore");
const quizFinalMessage = document.getElementById("quizFinalMessage");
const retryQuizButton = document.getElementById("retryQuizButton");
const backToRevisionButton = document.getElementById("backToRevisionButton");

let selectedFiles = [];
let sessionId = "";
let quizQuestions = [];
let quizIndex = 0;
let quizScoreValue = 0;
let answered = false;

function setStatus(message, error = false) {
    statusBox.textContent = message;
    statusBox.classList.toggle("error", error);
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function inlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>");
}

function markdownToHtml(markdown) {
    markdown = typeof markdown === "string" ? markdown : "";
    const lines = markdown.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const out = [];
    let listOpen = false;
    let paragraph = [];

    function closeList() {
        if (listOpen) { out.push("</ul>"); listOpen = false; }
    }
    function flushParagraph() {
        if (paragraph.length) { out.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`); paragraph = []; }
    }

    for (const raw of lines) {
        const line = raw.trim();
        if (!line) { flushParagraph(); closeList(); continue; }
        if (line.startsWith("# ")) { flushParagraph(); closeList(); out.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`); continue; }
        if (line.startsWith("## ")) { flushParagraph(); closeList(); out.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`); continue; }
        if (line.startsWith("### ")) { flushParagraph(); closeList(); out.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`); continue; }
        const bullet = line.match(/^[-*]\s+(.+)$/);
        if (bullet) {
            flushParagraph();
            if (!listOpen) { out.push("<ul>"); listOpen = true; }
            out.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
            continue;
        }
        paragraph.push(line);
    }
    flushParagraph();
    closeList();
    return out.join("");
}

function renderFiles() {
    fileList.innerHTML = selectedFiles.map(file =>
        `<div class="file-item">📄 ${escapeHtml(file.name)} <span style="margin-left:auto;opacity:.7">${Math.ceil(file.size / 1024)} Ko</span></div>`
    ).join("");
}

filesInput.addEventListener("change", () => {
    selectedFiles = Array.from(filesInput.files || []);
    renderFiles();
});

["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => {
    event.preventDefault();
    dropZone.classList.add("dragover");
}));
["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
}));
dropZone.addEventListener("drop", event => {
    selectedFiles = Array.from(event.dataTransfer.files || []);
    renderFiles();
});

async function waitForGeneration(jobId) {
    for (;;) {
        const response = await fetch(`/api/generate-status/${encodeURIComponent(jobId)}`);
        const state = await response.json();

        if (!response.ok) {
            throw new Error(state.error || "Impossible de suivre la génération.");
        }

        setStatus(state.message || "Traitement en cours…");

        if (state.status === "done") {
            return state.result;
        }

        if (state.status === "error") {
            throw new Error(state.message || "Erreur pendant la génération.");
        }

        await new Promise(resolve => setTimeout(resolve, 900));
    }
}

form.addEventListener("submit", async event => {
    event.preventDefault();

    if (!selectedFiles.length) {
        setStatus("Sélectionne au moins un fichier.", true);
        return;
    }

    generateButton.disabled = true;
    setStatus("Envoi des fichiers…");

    const data = new FormData();
    selectedFiles.forEach(file => data.append("files", file));

    try {
        const response = await fetch("/api/generate", {
            method: "POST",
            body: data
        });

        const started = await response.json();

        if (!response.ok) {
            throw new Error(started.error || "Erreur inconnue.");
        }

        setStatus("Lecture des fichiers…");

        const result = await waitForGeneration(started.job_id);

        if (!result || typeof result !== "object") {
            throw new Error("Le serveur n'a renvoyé aucun résultat.");
        }

        const content = typeof result.content === "string"
            ? result.content
            : (typeof result.revision === "string" ? result.revision : "");

        if (!content.trim()) {
            throw new Error("La fiche n'a pas été reçue par le navigateur.");
        }

        sessionId = typeof result.session_id === "string" ? result.session_id : "";
        resultContent.innerHTML = markdownToHtml(content);
        resultCard.classList.remove("hidden");
        quizCard.classList.add("hidden");
        quizResultCard.classList.add("hidden");
        setStatus(`Fiche créée à partir de ${result.sources.length} fichier(s).`);
        resultCard.scrollIntoView({ behavior: "smooth", block: "start" });

    } catch (error) {
        setStatus(error.message, true);
    } finally {
        generateButton.disabled = false;
    }
});

copyButton.addEventListener("click", async () => {
    const text = resultContent.innerText;
    await navigator.clipboard.writeText(text);
    copyButton.textContent = "Copié ✓";
    setTimeout(() => { copyButton.textContent = "Copier"; }, 1200);
});

async function loadQuiz() {
    if (!sessionId) return;
    quizButton.disabled = true;
    quizButton.textContent = "Génération…";
    try {
        const response = await fetch("/api/quiz", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Le quiz n'a pas pu être créé.");
        quizQuestions = result.questions || [];
        quizIndex = 0;
        quizScoreValue = 0;
        quizResultCard.classList.add("hidden");
        quizCard.classList.remove("hidden");
        renderQuizQuestion();
        quizCard.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        alert(error.message);
    } finally {
        quizButton.disabled = false;
        quizButton.textContent = "🧠 Lancer le quiz";
    }
}

function renderQuizQuestion() {
    const item = quizQuestions[quizIndex];
    if (!item) return finishQuiz();
    answered = false;
    quizCounter.textContent = `Question ${quizIndex + 1} / ${quizQuestions.length}`;
    quizProgress.style.width = `${(quizIndex / quizQuestions.length) * 100}%`;
    quizScore.textContent = `Score : ${quizScoreValue}`;
    quizQuestion.textContent = item.question;
    quizAnswers.innerHTML = "";
    quizFeedback.classList.add("hidden");
    quizFeedback.textContent = "";
    nextQuizButton.disabled = true;
    item.answers.forEach((answer, index) => {
        const button = document.createElement("button");
        button.className = "quiz-answer";
        button.textContent = answer;
        button.addEventListener("click", () => answerQuiz(index));
        quizAnswers.appendChild(button);
    });
}

function answerQuiz(index) {
    if (answered) return;
    answered = true;
    const item = quizQuestions[quizIndex];
    const buttons = [...quizAnswers.querySelectorAll("button")];
    buttons.forEach(button => { button.disabled = true; });
    if (index === item.correct) {
        quizScoreValue += 1;
        buttons[index].classList.add("correct");
        quizFeedback.textContent = `✅ Bonne réponse ! ${item.explanation}`;
    } else {
        buttons[index].classList.add("wrong");
        buttons[item.correct].classList.add("correct");
        quizFeedback.textContent = `❌ Mauvaise réponse. ${item.explanation}`;
    }
    quizFeedback.classList.remove("hidden");
    quizScore.textContent = `Score : ${quizScoreValue}`;
    quizProgress.style.width = `${((quizIndex + 1) / quizQuestions.length) * 100}%`;
    nextQuizButton.disabled = false;
}

function nextQuizQuestion() {
    if (!answered) return;
    quizIndex += 1;
    if (quizIndex >= quizQuestions.length) finishQuiz();
    else renderQuizQuestion();
}

function finishQuiz() {
    quizCard.classList.add("hidden");
    quizResultCard.classList.remove("hidden");
    quizFinalScore.textContent = `${quizScoreValue} / ${quizQuestions.length}`;
    const ratio = quizScoreValue / quizQuestions.length;
    quizFinalMessage.textContent = ratio === 1 ? "Excellent, sans-faute !" : ratio >= .8 ? "Très bon résultat !" : ratio >= .6 ? "Bien joué, continue tes révisions." : "Refais un quiz pour renforcer tes connaissances.";
    quizResultCard.scrollIntoView({ behavior: "smooth", block: "center" });
}

quizButton.addEventListener("click", loadQuiz);
nextQuizButton.addEventListener("click", nextQuizQuestion);
retryQuizButton.addEventListener("click", loadQuiz);
backToRevisionButton.addEventListener("click", () => resultCard.scrollIntoView({ behavior: "smooth" }));

