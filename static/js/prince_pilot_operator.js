/* Prince pilot intake: no customer private information in browser storage or URLs. */
(() => {
  "use strict";
  const form = document.getElementById("prince-intake-form");
  if (!form) return;
  const submit = document.getElementById("submit-intake");
  const feedback = document.getElementById("intake-message");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  let submitting = false;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || !form.reportValidity()) return;
    if (!csrf || !form.dataset.intakeUrl) {
      feedback.textContent = "The secure form is unavailable. Refresh and try again.";
      return;
    }
    const data = new FormData(form);
    const payload = {
      full_name: String(data.get("full_name") || "").trim(),
      privacy_notice_version: String(data.get("privacy_notice_version") || ""),
      participation_confirmed: data.get("participation_confirmed") === "on",
    };
    for (const field of ["phone_number", "email", "vehicle_brand", "vehicle_model", "reported_concern"]) {
      const value = String(data.get(field) || "").trim();
      if (value) payload[field] = value;
    }
    const yearValue = String(data.get("vehicle_year") || "").trim();
    if (yearValue) {
      if (!/^\d{4}$/.test(yearValue)) {
        feedback.textContent = "Please use a valid four-digit vehicle year.";
        return;
      }
      payload.vehicle_year = Number(yearValue);
    }
    submitting = true;
    submit.disabled = true;
    feedback.textContent = "Saving securely…";
    try {
      const response = await fetch(form.dataset.intakeUrl, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("save-failed");
      // Clear sensitive form fields before navigating; do not put PII in URLs.
      form.reset();
      for (const field of form.querySelectorAll("input:not([type=hidden]), textarea")) {
        if (field.type !== "checkbox") field.value = "";
      }
      const url = new URL(window.location.href);
      url.search = "?saved=1";
      url.hash = "assigned-jobs";
      window.location.replace(url.toString());
    } catch {
      feedback.textContent = "Could not save. Check the form or ask the pilot administrator. No receipt or job approval was issued.";
      submit.disabled = false;
      submitting = false;
    }
  });
})();
