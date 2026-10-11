(() => {
    "use strict";
    const panel = document.getElementById("statsConsent");
    const settings = document.getElementById("statsSettings");
    if (!panel || !settings) return;
    const close = document.getElementById("statsConsentClose");
    const status = document.getElementById("statsConsentStatus");
    const buttons = Array.from(panel.querySelectorAll("[data-stats-choice]"));
    const show = () => {
        panel.hidden = false;
        settings.setAttribute("aria-expanded", "true");
        buttons[0].focus();
    };
    const hide = () => {
        panel.hidden = true;
        settings.setAttribute("aria-expanded", "false");
    };
    settings.addEventListener("click", show);
    close.addEventListener("click", () => { hide(); settings.focus(); });
    panel.addEventListener("keydown", event => {
        if (event.key === "Escape" && panel.dataset.choice !== "pending") {
            hide(); settings.focus();
        }
    });
    function source() {
        const allowed = new Set(["tiktok", "instagram", "youtube", "google", "direct"]);
        const tagged = (new URL(window.location.href).searchParams.get("utm_source") || "").toLowerCase();
        if (tagged) return allowed.has(tagged) ? tagged : "other";
        try {
            if (!document.referrer) return "direct";
            const hostname = new URL(document.referrer).hostname.toLowerCase();
            if (hostname === window.location.hostname) return "direct";
            for (const [domain, label] of [["tiktok.com", "tiktok"], ["instagram.com", "instagram"], ["youtube.com", "youtube"], ["google.com", "google"], ["google.fr", "google"]]) {
                if (hostname === domain || hostname.endsWith(`.${domain}`)) return label;
            }
        } catch (_) { return "other"; }
        return "other";
    }
    let saving = false;
    buttons.forEach(button => button.addEventListener("click", async () => {
        if (saving) return;
        saving = true;
        buttons.forEach(item => { item.disabled = true; });
        status.textContent = "Enregistrement de ton choix…";
        try {
            const response = await fetch("/api/statistiques/choix", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ choice: button.dataset.statsChoice, csrf_token: panel.dataset.csrf,
                    source: source(), page: window.location.pathname === "/plus" ? "plus" : "other" })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Impossible d’enregistrer ton choix.");
            panel.dataset.choice = result.choice;
            close.hidden = false;
            status.textContent = result.choice === "declined"
                ? "Statistiques refusées. Les données de suivi de ce navigateur ont été supprimées."
                : "Statistiques acceptées. Tu peux changer d’avis à tout moment.";
            settings.textContent = result.choice === "declined" ? "Statistiques refusées · modifier" : "Statistiques acceptées · modifier";
            hide();
            settings.focus();
        } catch (error) {
            status.textContent = error.message || "Réessaie plus tard. Le site reste accessible.";
        } finally {
            saving = false;
            buttons.forEach(item => { item.disabled = false; });
        }
    }));
})();
