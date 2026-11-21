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


/* Append-only, category-specific workshop notes. Never include private contacts. */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".job-entry-form")) {
    let saving = false;
    const feedback = form.querySelector(".job-entry-message");
    const button = form.querySelector('button[type="submit"]');
    const history = form.closest(".workshop-record")?.querySelector(".job-history-list");

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving || !form.reportValidity()) return;
      const category = String(form.querySelector('select[name="category"]')?.value || "");
      const body = String(form.querySelector('textarea[name="body"]')?.value || "").trim();
      if (!csrf || !form.dataset.entryUrl || !category || !body || !history) {
        feedback.textContent = "The secure history form is unavailable.";
        return;
      }
      saving = true;
      button.disabled = true;
      feedback.textContent = "Recording…";
      try {
        const response = await fetch(form.dataset.entryUrl, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify({category, body}),
        });
        if (!response.ok) {
          feedback.textContent = response.status === 400
            ? "Check the category, avoid personal contacts and use Demo: in staging."
            : "Could not record this note. No work has been verified.";
          return;
        }
        const payload = await response.json();
        const saved = payload.entry;
        if (!saved || !saved.id || !saved.category_label) throw new Error("Invalid response");
        // Construct elements with textContent, never HTML from user-entered notes.
        const item = document.createElement("li");
        item.className = "record-entry";
        const head = document.createElement("div");
        head.className = "record-entry-header";
        const label = document.createElement("strong");
        label.textContent = saved.category_label;
        const status = document.createElement("span");
        status.className = "entry-state";
        status.textContent = saved.review_state || "Unverified";
        head.append(label, status);
        const paragraph = document.createElement("p");
        paragraph.textContent = saved.body;
        const date = document.createElement("time");
        date.dateTime = saved.recorded_at;
        date.textContent = saved.recorded_at;
        item.append(head, paragraph, date);
        history.querySelector(".job-history-empty")?.remove();
        history.append(item);
        form.reset();
        feedback.textContent = "Added to history as unverified. Earlier notes were preserved.";
      } catch {
        // A network interruption can happen after a successful commit.
        // Avoid automatic resubmission (which could create a duplicate).
        feedback.textContent = "Could not confirm save. Refresh the history before retrying.";
      } finally {
        saving = false;
        button.disabled = false;
      }
    });
  }
})();
