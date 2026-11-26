/* Principal-only reassignment UI. Server checks role, tenant, stale assignee,
 * active operator membership and appends the immutable audit record.
 */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".assignment-form")) {
    const select = form.querySelector('select[name="assignee_user_id"]');
    const expected = form.querySelector('input[name="expected_assignee_user_id"]');
    const reason = form.querySelector('textarea[name="reason"]');
    const feedback = form.querySelector(".assignment-feedback");
    const button = form.querySelector('button[type="submit"]');
    const display = form.closest(".management-job")?.querySelector(".assignee-label");
    let saving = false;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving || !form.reportValidity()) return;
      if (!csrf || !form.dataset.assignmentUrl || !expected || !display) {
        feedback.textContent = "The secure assignment form is unavailable.";
        return;
      }
      const assignee_user_id = Number(select.value);
      const expected_assignee_user_id = Number(expected.value);
      if (!Number.isSafeInteger(assignee_user_id) || assignee_user_id < 1 ||
          !Number.isSafeInteger(expected_assignee_user_id) ||
          assignee_user_id === expected_assignee_user_id) {
        feedback.textContent = "Choose a different active operator.";
        return;
      }
      saving = true;
      button.disabled = true;
      feedback.textContent = "Checking and saving assignment…";
      try {
        const response = await fetch(form.dataset.assignmentUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store",
          redirect: "error", headers: {
            "Content-Type": "application/json", "X-CSRF-Token": csrf,
          },
          body: JSON.stringify({
            assignee_user_id,
            expected_assignee_user_id,
            reason: reason.value.trim(),
          }),
        });
        if (response.status === 409) {
          feedback.textContent = "Job changed or is closed. Reload before reassigning.";
          return;
        }
        if (!response.ok) {
          feedback.textContent = response.status === 404
            ? "Operator is not eligible or the job is unavailable."
            : "Could not confirm assignment. Reload before retrying.";
          return;
        }
        const result = await response.json();
        if (result.assigned_user_id !== assignee_user_id ||
            !result.assigned_operator_name) throw new Error("unconfirmed");
        display.textContent = result.assigned_operator_name;
        form.reset();
        expected.value = String(result.assigned_user_id);
        expected.defaultValue = String(result.assigned_user_id);
        for (const option of select.options) {
          option.disabled = option.value === String(result.assigned_user_id);
        }
        select.selectedIndex = 0;
        reason.value = "";
        feedback.textContent = "Assignment recorded. Operator access has changed.";
      } catch {
        feedback.textContent = "Could not confirm. Reload before trying again.";
      } finally {
        saving = false;
        button.disabled = false;
      }
    });
  }
})();


/* Principal progression; refresh authoritative status after confirmation. */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".lifecycle-form")) {
    const button = form.querySelector('button[type="submit"]');
    const feedback = form.querySelector(".lifecycle-feedback");
    const disposition = form.elements.closure_disposition;
    const plan = form.elements.follow_up_plan;
    if (disposition && plan) {
      const updatePlan = () => {
        const needed = disposition.value === "follow_up_required";
        plan.closest(".follow-up-fields").hidden = !needed;
        plan.required = needed;
        if (!needed) plan.value = "";
      };
      disposition.addEventListener("change", updatePlan);
      updatePlan();
    }
    let saving = false;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (saving || !csrf || !form.reportValidity()) return;
      const data = {
        expected_status: form.elements.expected_status.value,
        new_status: form.elements.new_status.value,
        reason: form.elements.reason.value.trim(),
        closure_disposition: form.elements.closure_disposition?.value || null,
        follow_up_plan: plan?.value.trim() || null,
      };
      saving = true;
      button.disabled = true;
      feedback.textContent = "Recording decision…";
      try {
        const response = await fetch(form.dataset.lifecycleUrl, {
          method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify(data),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          feedback.textContent = result.error || "Decision not recorded. Check the required fields or reload the page.";
          return;
        }
        if (result.job_status !== data.new_status || !result.event) throw Error("unconfirmed");
        feedback.textContent = "Decision recorded. Reloading current workshop status.";
        window.location.reload();
      } catch {
        feedback.textContent = "Could not confirm the decision. Reload before trying again.";
      } finally {
        saving = false;
        button.disabled = false;
      }
    });
  }
})();


/* Exact NGN input conversion; server independently verifies every total. */
(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  const max = 10000000000;
  function kobo(value) {
    const match = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(value.trim());
    if (!match) throw Error("Enter a valid naira amount with at most two decimal places.");
    const amount = Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
    if (amount > max) throw Error("Amount exceeds the pilot limit.");
    return amount;
  }
  async function submit(form, payload) {
    const feedback = form.querySelector(".estimate-feedback");
    const button = form.querySelector('button[type="submit"]');
    if (!csrf || button.disabled || !form.reportValidity()) return;
    button.disabled = true;
    feedback.textContent = "Saving…";
    try {
      const res = await fetch(form.dataset.url, {
        method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.estimate) {
        feedback.textContent = data.error || "Not saved. Check the inputs or reload.";
        return;
      }
      feedback.textContent = "Saved. Refreshing estimate.";
      window.location.reload();
    } catch {
      feedback.textContent = "Could not confirm the save. Reload before retrying.";
    } finally {
      button.disabled = false;
    }
  }
  const form = document.querySelector(".estimate-draft-form");
  if (form) {
    const container = form.querySelector(".estimate-lines");
    const preview = form.querySelector(".estimate-preview");
    const feedback = form.querySelector(".estimate-feedback");
    const template = container.firstElementChild.cloneNode(true);
    function read() {
      const items = [...container.querySelectorAll(".estimate-line")].map(row => {
        const quantityText = row.querySelector('[name="quantity"]').value.trim();
        const quantity = Number(quantityText);
        if (!/^\d+$/.test(quantityText) || !Number.isInteger(quantity) || quantity < 1 || quantity > 1000)
          throw Error("Quantity must be a whole number from 1 to 1000.");
        return {
          category: row.querySelector('[name="category"]').value,
          description: row.querySelector('[name="description"]').value.trim(),
          quantity, unit_kobo: kobo(row.querySelector('[name="unit_price"]').value),
        };
      });
      const discount_kobo = kobo(form.elements.discount.value);
      const subtotal = items.reduce((sum, item) => sum + item.quantity * item.unit_kobo, 0);
      if (subtotal > max || discount_kobo > subtotal) throw Error("Check subtotal and discount.");
      return {items, discount_kobo, subtotal};
    }
    function refresh() {
      try {
        const result = read();
        preview.textContent = new Intl.NumberFormat("en-NG", {
          style: "currency", currency: "NGN",
        }).format((result.subtotal - result.discount_kobo) / 100);
      } catch {
        preview.textContent = "Check amounts";
      }
    }
    form.addEventListener("input", refresh);
    form.querySelector(".add-estimate-line").addEventListener("click", () => {
      if (container.children.length >= 30) return;
      const row = template.cloneNode(true);
      row.querySelector('[name="category"]').selectedIndex = 0;
      row.querySelector('[name="description"]').value = "";
      row.querySelector('[name="quantity"]').value = "1";
      row.querySelector('[name="unit_price"]').value = "";
      container.append(row);
      refresh();
    });
    container.addEventListener("click", event => {
      const button = event.target.closest(".remove-estimate-line");
      if (button && container.children.length > 1) {
        button.closest(".estimate-line").remove();
        refresh();
      }
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      try {
        const result = read();
        submit(form, {expected_version: Number(form.dataset.version),
          items: result.items, discount_kobo: result.discount_kobo});
      } catch (error) { feedback.textContent = error.message; }
    });
    refresh();
  }
  for (const approval of document.querySelectorAll(".estimate-approve-form")) {
    approval.addEventListener("submit", event => {
      event.preventDefault();
      submit(approval, {approval_note: approval.elements.approval_note.value.trim()});
    });
  }
})();


(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".authorization-form")) {
    const button = form.querySelector('button[type="submit"]');
    const feedback = form.querySelector(".authorization-feedback");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!csrf || button.disabled || !form.reportValidity()) return;
      button.disabled = true;
      feedback.textContent = "Recording customer decision…";
      try {
        const response = await fetch(form.dataset.url, {
          method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify({
            expected_sequence: Number(form.dataset.sequence),
            decision: form.elements.decision.value,
            channel: form.elements.channel.value,
            evidence_reference: form.elements.evidence_reference.value.trim(),
            customer_decision_attested: form.elements.customer_decision_attested.checked,
          }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.event) {
          feedback.textContent = result.error || "Decision not saved. Reload and review the current record.";
          return;
        }
        window.location.reload();
      } catch {
        feedback.textContent = "Could not confirm the save. Reload before retrying.";
      } finally {
        button.disabled = false;
      }
    });
  }
})();


(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  for (const form of document.querySelectorAll(".invoice-form")) {
    const button = form.querySelector('button[type="submit"]');
    const feedback = form.querySelector(".invoice-feedback");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!csrf || button.disabled || !form.reportValidity()) return;
      button.disabled = true;
      feedback.textContent = "Recording training invoice…";
      try {
        const response = await fetch(form.dataset.url, {
          method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify({
            expected_version: Number(form.dataset.version),
            expected_authorization_sequence: Number(form.dataset.sequence),
            issue_reason: form.elements.issue_reason.value.trim(),
            issue_attested: form.elements.issue_attested.checked,
          }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.invoice) {
          feedback.textContent = result.error || "Invoice not recorded. Reload and review the job.";
          return;
        }
        window.location.reload();
      } catch {
        feedback.textContent = "Could not confirm invoice creation. Reload before retrying.";
      } finally {
        button.disabled = false;
      }
    });
  }
})();


(() => {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  function kobo(text) {
    const match = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(text.trim());
    if (!match) throw Error("Enter a valid positive naira amount with up to two decimal places.");
    const result = Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
    if (result <= 0 || result > 10000000000) throw Error("Amount outside the pilot range.");
    return result;
  }
  for (const form of document.querySelectorAll(".payment-report-form, .payment-review-form")) {
    const button = form.querySelector('button[type="submit"]');
    const feedback = form.querySelector(".payment-feedback");
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!csrf || button.disabled || !form.reportValidity()) return;
      try {
        const payload = form.classList.contains("payment-report-form") ? {
          reference: form.elements.reference.value.trim(),
          method: form.elements.method.value,
          amount_kobo: kobo(form.elements.amount.value),
        } : {
          decision: form.elements.decision.value,
          review_note: form.elements.review_note.value.trim(),
          review_attested: form.elements.review_attested.checked,
        };
        button.disabled = true;
        feedback.textContent = "Saving fictional payment record…";
        const response = await fetch(form.dataset.url, {
          method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !Array.isArray(result.records)) {
          feedback.textContent = result.error || "Not recorded. Reload before retrying.";
          return;
        }
        window.location.reload();
      } catch (error) {
        feedback.textContent = error instanceof Error && /amount|range/.test(error.message)
          ? error.message : "Could not confirm save. Reload before retrying.";
      } finally {
        button.disabled = false;
      }
    });
  }
})();
