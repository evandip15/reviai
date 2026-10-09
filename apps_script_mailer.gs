function doPost(e) {
  try {
    const expectedToken = PropertiesService.getScriptProperties().getProperty("REVIAI_MAIL_TOKEN");
    const body = JSON.parse(e && e.postData ? e.postData.contents : "{}");

    if (!expectedToken || !body.token || body.token !== expectedToken) {
      return jsonResponse({ ok: false, error: "unauthorized" });
    }

    const recipient = String(body.to || "").trim();
    const subject = String(body.subject || "").trim();
    const html = String(body.html || "");
    const text = String(body.text || "");

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient) || recipient.length > 254) {
      return jsonResponse({ ok: false, error: "invalid_recipient" });
    }
    if (!subject || subject.length > 200 || html.length > 40000 || text.length > 40000) {
      return jsonResponse({ ok: false, error: "invalid_message" });
    }
    if (MailApp.getRemainingDailyQuota() < 1) {
      return jsonResponse({ ok: false, error: "daily_quota_reached" });
    }

    MailApp.sendEmail({
      to: recipient,
      subject: subject,
      body: text || "Consulte le message dans un client e-mail compatible HTML.",
      htmlBody: html || undefined,
      name: "RéviAI"
    });
    return jsonResponse({ ok: true });
  } catch (error) {
    return jsonResponse({ ok: false, error: "send_failed" });
  }
}

function jsonResponse(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
