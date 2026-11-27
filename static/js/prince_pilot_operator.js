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
    for (const field of ["vehicle_brand", "vehicle_model", "reported_concern"]) {
      const value = String(data.get(field) || "").trim();
      if (value) payload[field] = value;
    }
    for (const field of ["vin", "registration_number", "colour", "engine_number", "trim", "engine_type", "fuel_type", "transmission", "body_type"]) {
      const value = String(data.get(field) || "").trim();
      if (value) payload[field] = value;
    }
    const odometer = String(data.get("odometer_km") || "").trim();
    if (odometer) payload.odometer_km = Number(odometer);
    if (data.get("vehicle_details_confirmed") === "on") payload.vehicle_details_confirmed = true;
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
      if (response.status === 409) {
        feedback.textContent = "Vehicle may already be registered. Ask a principal to review the existing record before trying again.";
        submit.disabled = false;
        submitting = false;
        return;
      }
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


/* Private contacts are fetched by a principal only, never included in operator HTML. */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".principal-contact-form")) {
    const details = form.closest("details");
    const fields = form.querySelector(".private-contact-fields");
    const feedback = form.querySelector(".private-contact-message");
    const load = form.querySelector(".load-private-btn");
    const hide = form.querySelector(".hide-private-btn");
    const save = form.querySelector(".save-private-btn");
    const sensitive = [...form.querySelectorAll('[name="phone_number"], [name="email"], [name="home_address"]')];
    let busy = false;
    const lock = () => {
      sensitive.forEach(field => { field.value = ""; });
      fields.hidden = true;
      load.hidden = false;
      feedback.textContent = "";
    };
    details?.addEventListener("toggle", () => { if (!details.open) lock(); });
    hide.addEventListener("click", () => { lock(); details.open = false; });
    load.addEventListener("click", async () => {
      if (busy || !form.dataset.privateUrl) return;
      busy = true;
      load.disabled = true;
      feedback.textContent = "Loading protected details…";
      try {
        const response = await fetch(form.dataset.privateUrl, {
          credentials: "same-origin", cache: "no-store", redirect: "error",
        });
        if (!response.ok) throw new Error("load");
        const data = await response.json();
        for (const field of sensitive) field.value = data[field.name] ?? "";
        fields.hidden = false;
        load.hidden = true;
        feedback.textContent = "Protected contact details unlocked on this screen only.";
      } catch {
        feedback.textContent = "Cannot access private details with this account.";
      } finally {
        busy = false;
        load.disabled = false;
      }
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy || fields.hidden || !csrf || !form.dataset.updateUrl || !form.reportValidity()) return;
      busy = true;
      save.disabled = true;
      feedback.textContent = "Saving encrypted details…";
      try {
        const body = Object.fromEntries(sensitive.map(field => [field.name, field.value.trim() || null]));
        const response = await fetch(form.dataset.updateUrl, {
          method: "PATCH", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error("save");
        lock();
        feedback.textContent = "Private details updated and locked.";
      } catch {
        feedback.textContent = "Could not confirm update. Avoid resubmitting until you reload.";
      } finally {
        busy = false;
        save.disabled = false;
      }
    });
  }
})();

/* Only principals can attest work or request evidence. The server enforces role. */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".principal-review-form")) {
    const feedback = form.querySelector(".principal-review-message");
    const save = form.querySelector('button[type="submit"]');
    let busy = false;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy || !csrf || !form.reportValidity()) return;
      const decision = form.querySelector('[name="decision"]').value;
      const basis = form.querySelector('[name="basis"]').value;
      const review_note = form.querySelector('[name="review_note"]').value.trim();
      if ((decision === "work_confirmed" && basis === "not_applicable") ||
          (decision !== "work_confirmed" && basis !== "not_applicable")) {
        feedback.textContent = "Select an evidence basis only when confirming work.";
        return;
      }
      busy = true;
      save.disabled = true;
      feedback.textContent = "Recording your decision…";
      try {
        const response = await fetch(form.dataset.reviewUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify({decision, basis, review_note}),
        });
        if (!response.ok) throw new Error("review");
        const payload = await response.json();
        if (!payload.principal_review) throw new Error("bad-response");
        const notice = document.createElement("div");
        notice.className = "principal-review-result";
        const strong = document.createElement("strong");
        strong.textContent = "Principal decision: " + payload.principal_review.decision.replaceAll("_", " ");
        const p = document.createElement("p");
        p.textContent = payload.principal_review.review_note;
        notice.append(strong, p);
        form.replaceWith(notice);
      } catch {
        feedback.textContent = "Review not confirmed. Reload before trying again.";
        save.disabled = false;
        busy = false;
      }
    });
  }
})();


/* Returning vehicle recognition: exact lookup, never put identifiers in a URL or browser storage. */
(() => {
  "use strict";
  const lookup = document.getElementById("returning-lookup");
  if (!lookup) return;
  const jobForm = document.getElementById("return-job");
  const result = document.getElementById("return-result");
  const feedback = document.getElementById("return-feedback");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  let selection = null;
  let busy = false;
  const clear = () => {
    selection = null;
    result.hidden = true;
    jobForm.hidden = true;
    jobForm.reset();
    document.getElementById("return-history").replaceChildren();
    document.getElementById("return-customer").textContent = "";
    document.getElementById("return-vehicle-heading").textContent = "";
  };
  lookup.addEventListener("input", () => { clear(); feedback.textContent = ""; });
  const post = (url, payload) => fetch(url, {
    method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
    body: JSON.stringify(payload),
  });
  lookup.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !csrf || !lookup.reportValidity()) return;
    clear();
    const data = new FormData(lookup);
    const query = {identifier_type: data.get("identifier_type"), identifier: String(data.get("identifier") || "").trim()};
    busy = true;
    const button = lookup.querySelector('button[type="submit"]');
    button.disabled = true;
    feedback.textContent = "Finding vehicle…";
    try {
      const response = await post(lookup.dataset.url, query);
      if (!response.ok) throw new Error("lookup");
      const record = await response.json();
      if (lookup.elements.identifier.value.trim() !== query.identifier ||
          lookup.elements.identifier_type.value !== query.identifier_type) return;
      selection = {...query, vehicle_id: record.vehicle_id};
      document.getElementById("return-vehicle-heading").textContent =
        [record.brand, record.model, record.year].filter(Boolean).join(" ") + " · Vehicle #" + record.vehicle_id;
      document.getElementById("return-customer").textContent = "Registered customer: " + record.customer_name;
      const history = document.getElementById("return-history");
      for (const visit of record.history) {
        const li = document.createElement("li");
        li.textContent = "Job #" + visit.job_id + " · " + visit.date + " · " +
          visit.status.replaceAll("_", " ") + (visit.odometer_km == null ? "" : " · " + visit.odometer_km + " km") +
          (visit.reported_concern ? " · " + visit.reported_concern : "");
        history.append(li);
      }
      result.hidden = false;
      jobForm.hidden = record.active_job_exists;
      feedback.textContent = record.active_job_exists
        ? "An active job already exists. Continue the existing job or ask a principal to review."
        : "Vehicle found. Confirm the current customer before opening a new job.";
    } catch {
      clear();
      feedback.textContent = "No unique accessible vehicle found. Check the identifier or ask a principal. Do not register a duplicate.";
    } finally { busy = false; button.disabled = false; }
  });
  jobForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !selection || !jobForm.reportValidity()) return;
    const data = new FormData(jobForm);
    const payload = {...selection, reported_concern: String(data.get("reported_concern") || "").trim(),
      privacy_notice_version: data.get("privacy_notice_version"),
      ownership_confirmed: data.get("ownership_confirmed") === "on",
      participation_confirmed: data.get("participation_confirmed") === "on"};
    const km = String(data.get("odometer_km") || "").trim();
    if (km) payload.odometer_km = Number(km);
    busy = true;
    const button = jobForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await post(jobForm.dataset.url, payload);
      if (!response.ok) throw new Error("save");
      const saved = await response.json();
      clear();
      lookup.reset();
      const url = new URL(window.location.href);
      url.search = "";
      url.hash = "job-record-" + saved.job_id;
      window.history.replaceState(null, "", url.toString());
      window.location.reload();
    } catch {
      feedback.textContent = "New job not confirmed. Search again before retrying; an active job may already exist.";
      clear();
    } finally { busy = false; button.disabled = false; }
  });
})();
