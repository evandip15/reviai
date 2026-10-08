const form = document.getElementById("uploadForm");
const input = document.getElementById("files");
const selectedFiles = document.getElementById("selectedFiles");
const statusBox = document.getElementById("statusBox");
const resultCard = document.getElementById("resultCard");
const quizCard = document.getElementById("quizCard");
const ficheBox = document.getElementById("fiche");
const quizBox = document.getElementById("quiz");
const quizScore = document.getElementById("quizScore");
const newQuizBtn = document.getElementById("newQuizBtn");

let currentQuiz = [];
let lastCourseFiles = [];
let userAnswers = [];

input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    lastCourseFiles = files;
    if (!files.length) {
        selectedFiles.textContent = "Aucun fichier sélectionné.";
        return;
    }
    selectedFiles.textContent = `${files.length} fichier(s) : ${files.map(f => f.name).join(", ")}`;
});

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function inlineMarkdown(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    return s;
}

function markdownToHtml(markdown) {
    const lines = String(markdown ?? "").split(/\r?\n/);
    let html = "";
    let inUl = false;
    let inOl = false;
    let inQuote = false;

    const closeLists = () => {
        if (inUl) { html += "</ul>"; inUl = false; }
        if (inOl) { html += "</ol>"; inOl = false; }
    };
    const closeQuote = () => {
        if (inQuote) { html += "</blockquote>"; inQuote = false; }
    };

    for (const rawLine of lines) {
        const line = rawLine.trimEnd();
        if (!line.trim()) {
            closeLists();
            closeQuote();
            continue;
        }

        if (line.startsWith(">")) {
            closeLists();
            if (!inQuote) { html += "<blockquote>"; inQuote = true; }
            html += `${inlineMarkdown(line.replace(/^>\s?/, ""))}<br>`;
            continue;
        }

        closeQuote();

        if (line.startsWith("### ")) {
            closeLists();
            html += `<h3>${inlineMarkdown(line.slice(4))}</h3>`;
        } else if (line.startsWith("## ")) {
            closeLists();
            html += `<h2>${inlineMarkdown(line.slice(3))}</h2>`;
        } else if (line.startsWith("# ")) {
            closeLists();
            html += `<h1>${inlineMarkdown(line.slice(2))}</h1>`;
        } else if (/^[-*]\s+/.test(line)) {
            if (!inUl) { closeLists(); html += "<ul>"; inUl = true; }
            html += `<li>${inlineMarkdown(line.replace(/^[-*]\s+/, ""))}</li>`;
        } else if (/^\d+\.\s+/.test(line)) {
            if (!inOl) { closeLists(); html += "<ol>"; inOl = true; }
            html += `<li>${inlineMarkdown(line.replace(/^\d+\.\s+/, ""))}</li>`;
        } else {
            closeLists();
            html += `<p>${inlineMarkdown(line)}</p>`;
        }
    }

    closeLists();
    closeQuote();
    return html;
}

function setStatus(message, isError = false) {
    statusBox.classList.remove("hidden");
    statusBox.textContent = message;
    statusBox.style.background = isError ? "#fef2f2" : "#f3f4f6";
    statusBox.style.color = isError ? "#991b1b" : "#374151";
}

function renderQuiz(quiz) {
    currentQuiz = Array.isArray(quiz) ? quiz : [];
    userAnswers = Array(currentQuiz.length).fill(null);
    quizBox.innerHTML = "";
    quizScore.textContent = "";
    newQuizBtn.classList.add("hidden");

    if (!currentQuiz.length) {
        quizBox.innerHTML = "<p>Le quiz n'a pas pu être généré.</p>";
        return;
    }

    currentQuiz.forEach((item, index) => {
        const wrapper = document.createElement("div");
        wrapper.className = "quiz-question";
        wrapper.innerHTML = `<h3>${index + 1}. ${escapeHtml(item.question)}</h3>`;

        item.options.forEach((option, optionIndex) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "option";
            button.textContent = option;
            button.addEventListener("click", () => answerQuestion(wrapper, index, optionIndex));
            wrapper.appendChild(button);
        });
        quizBox.appendChild(wrapper);
    });
}

function answerQuestion(wrapper, questionIndex, chosenIndex) {
    const item = currentQuiz[questionIndex];
    if (!item) return;
    const options = Array.from(wrapper.querySelectorAll(".option"));
    if (options.some(btn => btn.disabled)) return;

    options.forEach(btn => btn.disabled = true);
    options[item.answer]?.classList.add("correct");
    if (chosenIndex !== item.answer) options[chosenIndex]?.classList.add("wrong");
    userAnswers[questionIndex] = chosenIndex;

    const explanation = document.createElement("div");
    explanation.className = "explanation";
    explanation.innerHTML = `<strong>${chosenIndex === item.answer ? "✅ Bonne réponse" : "❌ Mauvaise réponse"}</strong><br>${escapeHtml(item.explanation)}`;
    wrapper.appendChild(explanation);

    const done = userAnswers.filter(answer => answer !== null).length;
    if (done >= currentQuiz.length) {
        const finalScore = currentQuiz.reduce((total, question, index) => {
            return total + (userAnswers[index] === question.answer ? 1 : 0);
        }, 0);
        quizScore.textContent = `Score : ${finalScore} / ${currentQuiz.length}`;
        newQuizBtn.classList.remove("hidden");
    }
}

async function generateAgain() {
    if (!lastCourseFiles.length) return;
    form.dispatchEvent(new Event("submit", {cancelable: true}));
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = Array.from(input.files || []);
    if (!files.length) {
        setStatus("Choisis au moins un fichier.", true);
        return;
    }

    const formData = new FormData();
    files.forEach(file => formData.append("files", file));

    resultCard.classList.add("hidden");
    quizCard.classList.add("hidden");
    setStatus("Analyse des fichiers et génération de la fiche + du quiz…");

    try {
        const response = await fetch("/api/generate-from-files", {
            method: "POST",
            body: formData
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
            throw new Error(data.error || "Une erreur est survenue.");
        }

        ficheBox.innerHTML = markdownToHtml(data.fiche || "");
        renderQuiz(data.quiz || []);
        resultCard.classList.remove("hidden");
        quizCard.classList.remove("hidden");
        setStatus(`✅ ${data.files_count || files.length} fichier(s) traité(s).`);
        window.scrollTo({ top: resultCard.offsetTop - 20, behavior: "smooth" });
    } catch (error) {
        setStatus(`❌ ${error.message}`, true);
    }
});

newQuizBtn.addEventListener("click", generateAgain);