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
