const form = document.getElementById("uploadForm");
const filesInput = document.getElementById("files");
const fileList = document.getElementById("fileList");
const generateButton = document.getElementById("generateButton");
const statusBox = document.getElementById("status");

const resultCard = document.getElementById("resultCard");
const resultContent = document.getElementById("resultContent");
const copyButton = document.getElementById("copyButton");

const dropZone = document.getElementById("dropZone");
const revisionModeButton = document.getElementById("revisionModeButton");
const exerciseModeButton = document.getElementById("exerciseModeButton");
const revisionFormCard = document.getElementById("revisionFormCard");
const exerciseFormCard = document.getElementById("exerciseFormCard");
const exerciseForm = document.getElementById("exerciseForm");
const exercisePhotoInput = document.getElementById("exercisePhoto");
const exerciseInstructionInput = document.getElementById("exerciseInstruction");
const exerciseDropZone = document.getElementById("exerciseDropZone");
const exercisePreview = document.getElementById("exercisePreview");
const exerciseFileName = document.getElementById("exerciseFileName");
const exerciseCropSection = document.getElementById("exerciseCropSection");
const exerciseCropCanvas = document.getElementById("exerciseCropCanvas");
const resetExerciseCropButton = document.getElementById("resetExerciseCrop");
const confirmExerciseCropButton = document.getElementById("confirmExerciseCrop");
const exerciseCropStatus = document.getElementById("exerciseCropStatus");
const solveExerciseButton = document.getElementById("solveExerciseButton");
const exerciseStatusBox = document.getElementById("exerciseStatus");
const exerciseResultCard = document.getElementById("exerciseResultCard");
const exerciseSolutionContent = document.getElementById("exerciseSolutionContent");
const copyExerciseButton = document.getElementById("copyExerciseButton");
const exerciseChatForm = document.getElementById("exerciseChatForm");
const exerciseChatInput = document.getElementById("exerciseChatInput");
const exerciseChatMessages = document.getElementById("exerciseChatMessages");
const exerciseChatStatus = document.getElementById("exerciseChatStatus");
const sendExerciseChatButton = document.getElementById("sendExerciseChatButton");

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


/* =========================
   STATUT
========================= */

function setStatus(message, error = false) {
    statusBox.textContent = message;
    statusBox.classList.toggle("error", error);
}


/* =========================
   SECURITE HTML
========================= */

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


/* =========================
   MARKDOWN -> HTML
========================= */

function inlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>");
}

function markdownToHtml(markdown) {
    markdown = typeof markdown === "string" ? markdown : "";

    const lines = markdown
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n")
        .split("\n");

    const out = [];

    let listTag = "";
    let paragraph = [];

    function closeList() {
        if (listTag) {
            out.push(`</${listTag}>`);
            listTag = "";
        }
    }

    function flushParagraph() {
        if (paragraph.length) {
            out.push(
                `<p>${inlineMarkdown(paragraph.join(" "))}</p>`
            );
            paragraph = [];
        }
    }

    for (const raw of lines) {
        const line = raw.trim();

        if (!line) {
            flushParagraph();
            closeList();
            continue;
        }

        if (line.startsWith("# ")) {
            flushParagraph();
            closeList();

            out.push(
                `<h1>${inlineMarkdown(line.slice(2))}</h1>`
            );

            continue;
        }

        if (line.startsWith("## ")) {
            flushParagraph();
            closeList();

            out.push(
                `<h2>${inlineMarkdown(line.slice(3))}</h2>`
            );

            continue;
        }

        if (line.startsWith("### ")) {
            flushParagraph();
            closeList();

            out.push(
                `<h3>${inlineMarkdown(line.slice(4))}</h3>`
            );

            continue;
        }

        const listItem = line.match(/^([-*]|\d+[.)])\s+(.+)$/);

        if (listItem) {
            flushParagraph();
            const nextListTag = /^\d/.test(listItem[1]) ? "ol" : "ul";

            if (listTag !== nextListTag) {
                closeList();
                out.push(`<${nextListTag}>`);
                listTag = nextListTag;
            }

            out.push(
                `<li>${inlineMarkdown(listItem[2])}</li>`
            );

            continue;
        }

        paragraph.push(line);
    }

    flushParagraph();
    closeList();

    return out.join("");
}


/* =========================
   FICHIERS
========================= */

function isSameFile(fileA, fileB) {
    return (
        fileA.name === fileB.name &&
        fileA.size === fileB.size &&
        fileA.lastModified === fileB.lastModified
    );
}

function addFiles(files) {
    const newFiles = Array.from(files || []);

    for (const file of newFiles) {
        const alreadyAdded = selectedFiles.some(
            existing => isSameFile(existing, file)
        );

        if (!alreadyAdded) {
            selectedFiles.push(file);
        }
    }

    renderFiles();
}

function removeFile(index) {
    selectedFiles.splice(index, 1);
    renderFiles();
}

function renderFiles() {
    if (!selectedFiles.length) {
        fileList.innerHTML = "";
        return;
    }

    fileList.innerHTML = selectedFiles.map((file, index) => {
        const sizeKo = Math.ceil(file.size / 1024);

        return `
            <div class="file-item">
                <span>
                    📄 ${escapeHtml(file.name)}
                </span>

                <span style="margin-left:auto;opacity:.7;">
                    ${sizeKo} Ko
                </span>

                <button
                    type="button"
                    class="remove-file"
                    data-index="${index}"
                    aria-label="Supprimer ${escapeHtml(file.name)}"
                    style="
                        margin-left:10px;
                        border:none;
                        background:none;
                        cursor:pointer;
                        font-size:16px;
                    "
                >
                    ✕
                </button>
            </div>
        `;
    }).join("");

    document.querySelectorAll(".remove-file").forEach(button => {
        button.addEventListener("click", () => {
            const index = Number(button.dataset.index);

            if (!Number.isNaN(index)) {
                removeFile(index);
            }
        });
    });
}


/* =========================
   SELECTION DE FICHIERS
========================= */

filesInput.addEventListener("change", () => {
    addFiles(filesInput.files);

    // Permet de sélectionner à nouveau exactement le même fichier
    filesInput.value = "";
});


/* =========================
   DRAG & DROP
========================= */

["dragenter", "dragover"].forEach(type => {
    dropZone.addEventListener(type, event => {
        event.preventDefault();
        event.stopPropagation();

        dropZone.classList.add("dragover");
    });
});

["dragleave", "drop"].forEach(type => {
    dropZone.addEventListener(type, event => {
        event.preventDefault();
        event.stopPropagation();

        dropZone.classList.remove("dragover");
    });
});

dropZone.addEventListener("drop", event => {
    addFiles(event.dataTransfer.files);
});


/* =========================
   SUIVI DE GENERATION
========================= */

async function waitForGeneration(jobId) {
    for (;;) {
        const response = await fetch(
            `/api/generate-status/${encodeURIComponent(jobId)}`
        );

        const state = await response.json();

        if (!response.ok) {
            throw new Error(
                state.error ||
                "Impossible de suivre la génération."
            );
        }

        setStatus(
            state.message ||
            "Traitement en cours…"
        );

        if (state.status === "done") {
            return state.result;
        }

        if (state.status === "error") {
            throw new Error(
                state.message ||
                "Erreur pendant la génération."
            );
        }

        await new Promise(resolve =>
            setTimeout(resolve, 900)
        );
    }
}


/* =========================
   GENERATION DE LA FICHE
========================= */

form.addEventListener("submit", async event => {
    event.preventDefault();

    if (!selectedFiles.length) {
        setStatus(
            "Sélectionne au moins un fichier.",
            true
        );
        return;
    }

    generateButton.disabled = true;

    setStatus("Envoi des fichiers…");

    const data = new FormData();

    selectedFiles.forEach(file => {
        data.append("files", file);
    });

    try {
        const response = await fetch("/api/generate", {
            method: "POST",
            body: data
        });

        const started = await response.json();

        if (!response.ok) {
            throw new Error(
                started.error ||
                "Erreur inconnue."
            );
        }

        setStatus("Lecture des fichiers…");

        const result = await waitForGeneration(
            started.job_id
        );

        if (!result || typeof result !== "object") {
            throw new Error(
                "Le serveur n'a renvoyé aucun résultat."
            );
        }

        const content =
            typeof result.content === "string"
                ? result.content
                : (
                    typeof result.revision === "string"
                        ? result.revision
                        : ""
                );

        if (!content.trim()) {
            throw new Error(
                "La fiche n'a pas été reçue par le navigateur."
            );
        }

        sessionId =
            typeof result.session_id === "string"
                ? result.session_id
                : "";

        resultContent.innerHTML =
            markdownToHtml(content);

        resultCard.classList.remove("hidden");

        quizCard.classList.add("hidden");
        quizResultCard.classList.add("hidden");

        const sourceCount =
            Array.isArray(result.sources)
                ? result.sources.length
                : selectedFiles.length;

        setStatus(
            `Fiche créée à partir de ${sourceCount} fichier(s).`
        );

        resultCard.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

    } catch (error) {
        console.error(error);

        setStatus(
            error.message ||
            "Une erreur est survenue.",
            true
        );

    } finally {
        generateButton.disabled = false;
    }
});


/* =========================
   COPIER LA FICHE
========================= */

copyButton.addEventListener("click", async () => {
    try {
        const text = resultContent.innerText;

        await navigator.clipboard.writeText(text);

        copyButton.textContent = "Copié ✓";

        setTimeout(() => {
            copyButton.textContent = "Copier";
        }, 1200);

    } catch (error) {
        console.error(error);

        setStatus(
            "Impossible de copier la fiche.",
            true
        );
    }
});


/* =========================
   GENERATION DU QUIZ
========================= */

async function loadQuiz() {
    if (!sessionId) {
        return;
    }

    quizButton.disabled = true;
    quizButton.textContent = "Génération…";

    try {
        const response = await fetch("/api/quiz", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                session_id: sessionId
            })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(
                result.error ||
                "Le quiz n'a pas pu être créé."
            );
        }

        quizQuestions =
            Array.isArray(result.questions)
                ? result.questions
                : [];

        if (!quizQuestions.length) {
            throw new Error(
                "Aucune question n'a été générée."
            );
        }

        quizIndex = 0;
        quizScoreValue = 0;

        quizResultCard.classList.add("hidden");
        quizCard.classList.remove("hidden");

        renderQuizQuestion();

        quizCard.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });

    } catch (error) {
        console.error(error);

        alert(
            error.message ||
            "Impossible de créer le quiz."
        );

    } finally {
        quizButton.disabled = false;
        quizButton.textContent = "🧠 Lancer le quiz";
    }
}


/* =========================
   AFFICHAGE QUESTION
========================= */

function renderQuizQuestion() {
    const item = quizQuestions[quizIndex];

    if (!item) {
        finishQuiz();
        return;
    }

    answered = false;

    quizCounter.textContent =
        `Question ${quizIndex + 1} / ${quizQuestions.length}`;

    quizProgress.style.width =
        `${(quizIndex / quizQuestions.length) * 100}%`;

    quizScore.textContent =
        `Score : ${quizScoreValue}`;

    quizQuestion.textContent =
        item.question || "";

    quizAnswers.innerHTML = "";

    quizFeedback.classList.add("hidden");
    quizFeedback.textContent = "";

    nextQuizButton.disabled = true;

    const answers =
        Array.isArray(item.answers)
            ? item.answers
            : [];

    answers.forEach((answer, index) => {
        const button =
            document.createElement("button");

        button.type = "button";
        button.className = "quiz-answer";
        button.textContent = answer;

        button.addEventListener("click", () => {
            answerQuiz(index);
        });

        quizAnswers.appendChild(button);
    });
}


/* =========================
   REPONSE AU QUIZ
========================= */

function answerQuiz(index) {
    if (answered) {
        return;
    }

    answered = true;

    const item = quizQuestions[quizIndex];

    const buttons = [
        ...quizAnswers.querySelectorAll("button")
    ];

    buttons.forEach(button => {
        button.disabled = true;
    });

    if (index === item.correct) {
        quizScoreValue += 1;

        if (buttons[index]) {
            buttons[index].classList.add("correct");
        }

        quizFeedback.textContent =
            `✅ Bonne réponse ! ${item.explanation || ""}`;

    } else {
        if (buttons[index]) {
            buttons[index].classList.add("wrong");
        }

        if (
            typeof item.correct === "number" &&
            buttons[item.correct]
        ) {
            buttons[item.correct].classList.add("correct");
        }

        quizFeedback.textContent =
            `❌ Mauvaise réponse. ${item.explanation || ""}`;
    }

    quizFeedback.classList.remove("hidden");

    quizScore.textContent =
        `Score : ${quizScoreValue}`;

    quizProgress.style.width =
        `${((quizIndex + 1) / quizQuestions.length) * 100}%`;

    nextQuizButton.disabled = false;
}


/* =========================
   QUESTION SUIVANTE
========================= */

function nextQuizQuestion() {
    if (!answered) {
        return;
    }

    quizIndex += 1;

    if (quizIndex >= quizQuestions.length) {
        finishQuiz();
    } else {
        renderQuizQuestion();
    }
}


/* =========================
   FIN DU QUIZ
========================= */

function finishQuiz() {
    quizCard.classList.add("hidden");
    quizResultCard.classList.remove("hidden");

    quizFinalScore.textContent =
        `${quizScoreValue} / ${quizQuestions.length}`;

    const ratio =
        quizQuestions.length > 0
            ? quizScoreValue / quizQuestions.length
            : 0;

    if (ratio === 1) {
        quizFinalMessage.textContent =
            "Excellent, sans-faute !";
    } else if (ratio >= 0.8) {
        quizFinalMessage.textContent =
            "Très bon résultat !";
    } else if (ratio >= 0.6) {
        quizFinalMessage.textContent =
            "Bien joué, continue tes révisions.";
    } else {
        quizFinalMessage.textContent =
            "Refais un quiz pour renforcer tes connaissances.";
    }

    quizResultCard.scrollIntoView({
        behavior: "smooth",
        block: "center"
    });
}


/* =========================
   BOUTONS QUIZ
========================= */

quizButton.addEventListener(
    "click",
    loadQuiz
);

nextQuizButton.addEventListener(
    "click",
    nextQuizQuestion
);

retryQuizButton.addEventListener(
    "click",
    loadQuiz
);

backToRevisionButton.addEventListener(
    "click",
    () => {
        resultCard.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    }
);


/* =========================
   MODE EXERCICE
========================= */

function setExerciseStatus(message, error = false) {
    exerciseStatusBox.textContent = message;
    exerciseStatusBox.classList.toggle("error", error);
}

function setActiveMode(mode) {
    const exerciseMode = mode === "exercise";
    revisionFormCard.classList.toggle("hidden", exerciseMode);
    exerciseFormCard.classList.toggle("hidden", !exerciseMode);
    revisionModeButton.classList.toggle("active", !exerciseMode);
    exerciseModeButton.classList.toggle("active", exerciseMode);
    revisionModeButton.setAttribute("aria-pressed", String(!exerciseMode));
    exerciseModeButton.setAttribute("aria-pressed", String(exerciseMode));
}

revisionModeButton.addEventListener("click", () => setActiveMode("revision"));
exerciseModeButton.addEventListener("click", () => setActiveMode("exercise"));

let selectedExercisePhoto = null;
let selectedExerciseFiles = [];
let exerciseOriginalPhoto = null;
let exercisePreviewUrls = [];
let exerciseSessionId = "";
let exerciseSourceImage = null;
let exerciseCropStart = null;
let exerciseCropSelection = null;
let exerciseCropEncoding = false;

function drawExerciseCrop() {
    if (!exerciseSourceImage) return;
    const context = exerciseCropCanvas.getContext("2d");
    context.clearRect(0, 0, exerciseCropCanvas.width, exerciseCropCanvas.height);
    context.drawImage(exerciseSourceImage, 0, 0);

    if (exerciseCropSelection) {
        const { x, y, width, height } = exerciseCropSelection;
        context.fillStyle = "rgba(17, 18, 35, 0.48)";
        context.fillRect(0, 0, exerciseCropCanvas.width, exerciseCropCanvas.height);
        context.clearRect(x, y, width, height);
        context.drawImage(exerciseSourceImage, x, y, width, height, x, y, width, height);
        context.strokeStyle = "#5b4cf6";
        context.lineWidth = Math.max(3, exerciseCropCanvas.width / 500);
        context.strokeRect(x, y, width, height);
    }
}

function resetExerciseCrop() {
    exerciseCropSelection = null;
    exerciseCropStart = null;
    exerciseCropEncoding = false;
    selectedExercisePhoto = exerciseOriginalPhoto;
    selectedExerciseFiles = exerciseOriginalPhoto ? [exerciseOriginalPhoto] : [];
    confirmExerciseCropButton.disabled = true;
    resetExerciseCropButton.disabled = false;
    exerciseCropStatus.textContent = exerciseOriginalPhoto
        ? "La photo complète sera utilisée. Le recadrage est facultatif."
        : "La photo complète sera utilisée.";
    drawExerciseCrop();
}

function exerciseCropPoint(event) {
    const bounds = exerciseCropCanvas.getBoundingClientRect();
    return {
        x: Math.max(0, Math.min(exerciseCropCanvas.width, (event.clientX - bounds.left) * exerciseCropCanvas.width / bounds.width)),
        y: Math.max(0, Math.min(exerciseCropCanvas.height, (event.clientY - bounds.top) * exerciseCropCanvas.height / bounds.height)),
    };
}

function exerciseFileIsImage(file) {
    const extension = file.name.split(".").pop().toLowerCase();
    return ["jpg", "jpeg", "png", "webp"].includes(extension)
        || ["image/jpeg", "image/png", "image/webp"].includes(file.type);
}

function renderExerciseFileList(files) {
    exerciseFileName.replaceChildren();
    files.forEach((file, index) => {
        const item = document.createElement("span");
        item.className = "exercise-file-item";
        const order = files.length > 1 ? `${index + 1}. ` : "";
        item.textContent = `${order}${file.name} · ${Math.max(1, Math.ceil(file.size / 1024))} Ko`;
        exerciseFileName.appendChild(item);
    });
    exercisePreview.classList.toggle("hidden", files.length === 0);
}

function setExercisePhotos(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    exercisePreviewUrls.forEach(url => URL.revokeObjectURL(url));
    exercisePreviewUrls = [];
    const documentExtensions = ["pdf", "docx", "odt", "pptx", "txt"];
    const invalidFile = files.find(file => {
        const extension = file.name.split(".").pop().toLowerCase();
        return !exerciseFileIsImage(file) && !documentExtensions.includes(extension);
    });
    if (invalidFile) {
        selectedExercisePhoto = null;
        selectedExerciseFiles = [];
        exerciseOriginalPhoto = null;
        exerciseSourceImage = null;
        exerciseCropSelection = null;
        exercisePreview.classList.add("hidden");
        exerciseCropSection.classList.add("hidden");
        setExerciseStatus(`Format non pris en charge : ${invalidFile.name}. Choisis des photos, PDF, DOCX, ODT, PPTX ou TXT.`, true);
        return;
    }

    selectedExerciseFiles = files;
    exerciseOriginalPhoto = null;
    exerciseSourceImage = null;
    exerciseCropSelection = null;
    exerciseCropStart = null;
    exerciseCropEncoding = false;
    renderExerciseFileList(files);

    if (files.length !== 1 || !exerciseFileIsImage(files[0])) {
        selectedExercisePhoto = files.length === 1 ? files[0] : null;
        exerciseCropSection.classList.add("hidden");
        if (files.length > 1) {
            setExerciseStatus(`${files.length} fichiers ajoutés dans l’ordre sélectionné. Pour plusieurs photos, choisis les pages du même exercice dans l’ordre.`);
        } else {
            setExerciseStatus("Document ajouté. Indique le numéro de l’exercice à résoudre.");
        }
        return;
    }

    const file = files[0];
    exerciseOriginalPhoto = file;
    selectedExercisePhoto = file;
    exercisePreviewUrls = [URL.createObjectURL(file)];
    exerciseCropSection.classList.remove("hidden");
    const sourceImage = new Image();
    exerciseSourceImage = sourceImage;
    sourceImage.onload = () => {
        if (exerciseSourceImage !== sourceImage) return;
        exerciseCropCanvas.width = sourceImage.naturalWidth;
        exerciseCropCanvas.height = sourceImage.naturalHeight;
        resetExerciseCrop();
        setExerciseStatus("Photo ajoutée : le recadrage est facultatif.");
    };
    sourceImage.onerror = () => {
        if (exerciseSourceImage !== sourceImage) return;
        exerciseSourceImage = null;
        selectedExercisePhoto = null;
        selectedExerciseFiles = [];
        exerciseOriginalPhoto = null;
        exerciseCropSection.classList.add("hidden");
        setExerciseStatus("Impossible d’ouvrir cette photo. Essaie avec une autre image.", true);
    };
    sourceImage.src = exercisePreviewUrls[0];
    exerciseCropStatus.textContent = "La photo complète sera utilisée. Le recadrage est facultatif.";
    setExerciseStatus("");
}

exerciseCropCanvas.addEventListener("pointerdown", event => {
    if (!exerciseSourceImage || exerciseCropEncoding) return;
    event.preventDefault();
    exerciseCropCanvas.setPointerCapture(event.pointerId);
    selectedExercisePhoto = exerciseOriginalPhoto;
    selectedExerciseFiles = [exerciseOriginalPhoto];
    exerciseCropStart = exerciseCropPoint(event);
    exerciseCropSelection = { x: exerciseCropStart.x, y: exerciseCropStart.y, width: 0, height: 0 };
    confirmExerciseCropButton.disabled = true;
    exerciseCropStatus.textContent = "Continue à faire glisser pour encadrer l’exercice et toutes ses questions.";
    drawExerciseCrop();
});

exerciseCropCanvas.addEventListener("pointermove", event => {
    if (!exerciseCropStart) return;
    const point = exerciseCropPoint(event);
    exerciseCropSelection = {
        x: Math.min(exerciseCropStart.x, point.x),
        y: Math.min(exerciseCropStart.y, point.y),
        width: Math.abs(point.x - exerciseCropStart.x),
        height: Math.abs(point.y - exerciseCropStart.y),
    };
    drawExerciseCrop();
});

function finishExerciseCropSelection() {
    if (!exerciseCropStart) return;
    exerciseCropStart = null;
    const selection = exerciseCropSelection;
    if (!selection || selection.width < 20 || selection.height < 20) {
        exerciseCropSelection = null;
        confirmExerciseCropButton.disabled = true;
        exerciseCropStatus.textContent = "Cadre trop petit. La photo complète reste sélectionnée.";
        drawExerciseCrop();
        return;
    }
    confirmExerciseCropButton.disabled = false;
    exerciseCropStatus.textContent = "Vérifie que le numéro et toutes les questions sont dans le cadre, puis valide si tu veux recadrer.";
    drawExerciseCrop();
}

exerciseCropCanvas.addEventListener("pointerup", finishExerciseCropSelection);
exerciseCropCanvas.addEventListener("pointercancel", finishExerciseCropSelection);
resetExerciseCropButton.addEventListener("click", resetExerciseCrop);

confirmExerciseCropButton.addEventListener("click", () => {
    if (!exerciseSourceImage || !exerciseOriginalPhoto || !exerciseCropSelection || exerciseCropEncoding) return;
    const originalPhoto = exerciseOriginalPhoto;
    const { x, y, width, height } = exerciseCropSelection;
    const outputCanvas = document.createElement("canvas");
    outputCanvas.width = Math.round(width);
    outputCanvas.height = Math.round(height);
    outputCanvas.getContext("2d").drawImage(
        exerciseSourceImage,
        Math.round(x), Math.round(y), outputCanvas.width, outputCanvas.height,
        0, 0, outputCanvas.width, outputCanvas.height,
    );
    exerciseCropEncoding = true;
    confirmExerciseCropButton.disabled = true;
    resetExerciseCropButton.disabled = true;
    exerciseCropStatus.textContent = "Préparation de l’image recadrée…";
    outputCanvas.toBlob(blob => {
        if (exerciseOriginalPhoto !== originalPhoto) return;
        exerciseCropEncoding = false;
        resetExerciseCropButton.disabled = false;
        if (!blob) {
            confirmExerciseCropButton.disabled = false;
            exerciseCropStatus.textContent = "Le recadrage a échoué. La photo complète reste sélectionnée.";
            return;
        }
        const originalName = originalPhoto.name || "exercice";
        const baseName = originalName.replace(/\.[^.]+$/, "");
        selectedExercisePhoto = new File([blob], `${baseName}-recadre.jpg`, { type: "image/jpeg" });
        selectedExerciseFiles = [selectedExercisePhoto];
        exerciseCropStatus.textContent = "Zone recadrée prête : seule cette zone sera envoyée.";
        setExerciseStatus("Cadrage prêt. Vérifie le numéro de l’exercice avant de lancer la résolution.");
    }, "image/jpeg", 0.94);
});

exercisePhotoInput.addEventListener("change", () => {
    setExercisePhotos(exercisePhotoInput.files);
});

["dragenter", "dragover"].forEach(type => {
    exerciseDropZone.addEventListener(type, event => {
        event.preventDefault();
        event.stopPropagation();
        exerciseDropZone.classList.add("dragover");
    });
});

["dragleave", "drop"].forEach(type => {
    exerciseDropZone.addEventListener(type, event => {
        event.preventDefault();
        event.stopPropagation();
        exerciseDropZone.classList.remove("dragover");
    });
});

exerciseDropZone.addEventListener("drop", event => {
    setExercisePhotos(event.dataTransfer.files);
});

async function waitForExercise(jobId) {
    for (;;) {
        const response = await fetch(`/api/exercise-status/${encodeURIComponent(jobId)}`);
        const state = await response.json();

        if (!response.ok) {
            throw new Error(state.error || "Impossible de suivre la résolution.");
        }

        setExerciseStatus(state.message || "Résolution en cours…");

        if (state.status === "done") return state.result;
        if (state.status === "error") {
            throw new Error(state.message || "Erreur pendant la résolution de l'exercice.");
        }

        await new Promise(resolve => setTimeout(resolve, 900));
    }
}

exerciseForm.addEventListener("submit", async event => {
    event.preventDefault();

    if (!selectedExerciseFiles.length) {
        setExerciseStatus("Ajoute une photo ou un document de ton exercice pour commencer.", true);
        return;
    }

    solveExerciseButton.disabled = true;
    solveExerciseButton.textContent = "Résolution en cours…";
    exerciseResultCard.classList.add("hidden");
    exerciseSessionId = "";
    setExerciseStatus(selectedExerciseFiles.length > 1 ? "Envoi des fichiers…" : "Envoi du fichier…");

    const data = new FormData();
    selectedExerciseFiles.forEach(file => data.append("photos", file));
    data.append("instruction", exerciseInstructionInput.value.trim());

    try {
        const response = await fetch("/api/solve-exercise", { method: "POST", body: data });
        const started = await response.json();
        if (!response.ok) throw new Error(started.error || "Les fichiers n'ont pas pu être envoyés.");

        const result = await waitForExercise(started.job_id);
        const solution = result && typeof result.solution === "string" ? result.solution : "";
        if (!solution.trim()) throw new Error("L'IA n'a renvoyé aucune explication.");

        exerciseSessionId = typeof result.session_id === "string" ? result.session_id : "";
        exerciseSolutionContent.innerHTML = markdownToHtml(solution);
        exerciseChatMessages.innerHTML = "";
        exerciseChatInput.value = "";
        exerciseChatStatus.textContent = "";
        exerciseChatStatus.classList.remove("error");
        exerciseResultCard.classList.remove("hidden");
        setExerciseStatus("Exercice résolu !");
        exerciseResultCard.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        console.error(error);
        setExerciseStatus(error.message || "Une erreur est survenue pendant la résolution.", true);
    } finally {
        solveExerciseButton.disabled = false;
        solveExerciseButton.textContent = "✨ Résoudre mon exercice";
    }
});

copyExerciseButton.addEventListener("click", async () => {
    try {
        const text = exerciseSolutionContent.innerText.trim();
        if (!text) throw new Error("Il n’y a pas de réponse à copier.");

        let copied = false;
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
            try {
                await navigator.clipboard.writeText(text);
                copied = true;
            } catch (error) {
                // Le fallback ci-dessous fonctionne aussi si le navigateur
                // interdit l'API presse-papiers sur la page courante.
            }
        }

        if (!copied) {
            const fallback = document.createElement("textarea");
            fallback.value = text;
            fallback.setAttribute("readonly", "");
            fallback.style.position = "fixed";
            fallback.style.left = "-9999px";
            fallback.style.top = "0";
            document.body.appendChild(fallback);
            fallback.focus();
            fallback.select();
            fallback.setSelectionRange(0, fallback.value.length);
            try {
                copied = document.execCommand("copy");
            } finally {
                fallback.remove();
            }
        }

        if (!copied) throw new Error("Le navigateur a refusé la copie. Sélectionne le texte puis copie-le.");
        copyExerciseButton.textContent = "Copié ✓";
        setTimeout(() => { copyExerciseButton.textContent = "Copier"; }, 1200);
    } catch (error) {
        console.error(error);
        setExerciseStatus(error.message || "Impossible de copier la résolution.", true);
    }
});

function setExerciseChatStatus(message, error = false) {
    exerciseChatStatus.textContent = message;
    exerciseChatStatus.classList.toggle("error", error);
}

function appendExerciseChatMessage(role, content) {
    const message = document.createElement("div");
    message.className = `chat-message ${role}`;

    const label = document.createElement("strong");
    label.textContent = role === "user" ? "Toi" : "RéviAI";

    const body = document.createElement("div");
    body.className = "chat-message-content";
    if (role === "assistant") {
        body.innerHTML = markdownToHtml(content);
    } else {
        body.textContent = content;
    }

    message.append(label, body);
    exerciseChatMessages.appendChild(message);
    message.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

exerciseChatForm.addEventListener("submit", async event => {
    event.preventDefault();
    const message = exerciseChatInput.value.trim();

    if (!exerciseSessionId) {
        setExerciseChatStatus("Renvoie d'abord la photo pour ouvrir une conversation.", true);
        return;
    }
    if (!message) {
        setExerciseChatStatus("Écris une question avant de l'envoyer.", true);
        return;
    }

    sendExerciseChatButton.disabled = true;
    setExerciseChatStatus("RéviAI prépare sa réponse…");
    appendExerciseChatMessage("user", message);
    exerciseChatInput.value = "";

    try {
        const response = await fetch("/api/exercise-chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: exerciseSessionId, message })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "RéviAI n'a pas pu répondre.");

        appendExerciseChatMessage("assistant", result.response || "");
        setExerciseChatStatus("");
    } catch (error) {
        console.error(error);
        setExerciseChatStatus(error.message || "Impossible d'envoyer le message.", true);
    } finally {
        sendExerciseChatButton.disabled = false;
        exerciseChatInput.focus();
    }
});


/* =========================
   INITIALISATION
========================= */

renderFiles();
