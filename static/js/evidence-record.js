(function () {
    "use strict";

    const root = document.querySelector("[data-evidence-record-root]");
    if (!root) {
        return;
    }

    const maxImageBytes = 2 * 1024 * 1024;
    const acceptedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
    const acceptedReasons = [
        ["advisor_verified", "Advisor verified"],
        ["sufficient_for_record", "Sufficient for care record"],
    ];
    const rejectedReasons = [
        ["insufficient_quality", "Insufficient image quality"],
        ["not_relevant", "Not relevant to this care record"],
        ["wrong_vehicle", "Wrong vehicle"],
        ["duplicate", "Duplicate evidence"],
        ["privacy_restriction", "Privacy restriction"],
    ];

    function safeMessage(payload, fallback) {
        if (payload && typeof payload.message === "string" && payload.message.trim()) {
            return payload.message;
        }
        return fallback;
    }

    async function jsonOrEmpty(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    }

    const uploadForm = root.querySelector("#evidence-image-upload-form");
    if (uploadForm) {
        uploadForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            const status = uploadForm.querySelector("[data-evidence-upload-status]");
            const submit = uploadForm.querySelector("[data-evidence-upload-submit]");
            const input = uploadForm.querySelector("[data-evidence-image-input]");
            const file = input && input.files ? input.files[0] : null;

            if (!file) {
                status.textContent = "Select an image first.";
                return;
            }
            if (!acceptedTypes.has(file.type)) {
                status.textContent = "Use a JPEG, PNG or WebP image.";
                return;
            }
            if (file.size > maxImageBytes) {
                status.textContent = "The image is larger than 2 MB.";
                return;
            }

            submit.disabled = true;
            status.textContent = "Uploading securely…";

            try {
                const response = await fetch(uploadForm.dataset.uploadUrl, {
                    method: "POST",
                    body: new FormData(uploadForm),
                    headers: { Accept: "application/json" },
                });
                const payload = await jsonOrEmpty(response);
                if (!response.ok) {
                    throw new Error(
                        safeMessage(payload, "Aura could not accept this image.")
                    );
                }
                status.textContent = "Submitted privately. Pending advisor review.";
                window.setTimeout(function () {
                    window.location.reload();
                }, 700);
            } catch (error) {
                status.textContent = error.message || "Aura could not accept this image.";
                submit.disabled = false;
            }
        });
    }

    let activeObjectUrl = null;
    const modal = root.querySelector("[data-evidence-preview-modal]");
    const previewImage = root.querySelector("[data-evidence-preview-image]");

    function closePreview() {
        if (modal) {
            modal.hidden = true;
        }
        if (previewImage) {
            previewImage.removeAttribute("src");
        }
        if (activeObjectUrl) {
            URL.revokeObjectURL(activeObjectUrl);
            activeObjectUrl = null;
        }
    }

    root.querySelectorAll("[data-evidence-preview-close]").forEach(function (button) {
        button.addEventListener("click", closePreview);
    });

    root.querySelectorAll("[data-evidence-preview-button]").forEach(function (button) {
        button.addEventListener("click", async function () {
            const item = button.closest("[data-pending-evidence]");
            const status = item ? item.querySelector("[data-evidence-item-status]") : null;
            button.disabled = true;
            if (status) {
                status.textContent = "Opening private image…";
            }

            try {
                const grantResponse = await fetch(button.dataset.grantUrl, {
                    method: "POST",
                    headers: { Accept: "application/json" },
                });
                const grant = await jsonOrEmpty(grantResponse);
                if (!grantResponse.ok || !grant.grant_token || !grant.content_endpoint) {
                    throw new Error(
                        safeMessage(grant, "Aura could not authorize this preview.")
                    );
                }

                const contentResponse = await fetch(grant.content_endpoint, {
                    method: "POST",
                    headers: {
                        Accept: "image/*",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ grant_token: grant.grant_token }),
                });
                if (!contentResponse.ok) {
                    const failure = await jsonOrEmpty(contentResponse);
                    throw new Error(
                        safeMessage(
                            failure,
                            "Aura could not retrieve this private image."
                        )
                    );
                }

                const blob = await contentResponse.blob();
                if (!blob.type.startsWith("image/")) {
                    throw new Error("Aura returned an unsupported private media type.");
                }

                closePreview();
                activeObjectUrl = URL.createObjectURL(blob);
                previewImage.src = activeObjectUrl;
                modal.hidden = false;

                const reviewControls = root.querySelector(
                    `[data-evidence-review-controls="${button.dataset.evidenceId}"]`
                );
                if (reviewControls) {
                    reviewControls.hidden = false;
                }
                if (status) {
                    status.textContent = "Private image opened for review.";
                }
            } catch (error) {
                if (status) {
                    status.textContent = error.message || "Aura could not open this image.";
                }
            } finally {
                button.disabled = false;
            }
        });
    });

    function populateReasons(form) {
        const decision = form.querySelector("[data-evidence-review-decision]");
        const reason = form.querySelector("[data-evidence-review-reason]");
        if (!decision || !reason) {
            return;
        }

        const reasons = decision.value === "rejected" ? rejectedReasons : acceptedReasons;
        reason.innerHTML = "";
        reasons.forEach(function (entry) {
            const option = document.createElement("option");
            option.value = entry[0];
            option.textContent = entry[1];
            reason.appendChild(option);
        });
    }

    root.querySelectorAll("[data-evidence-review-controls]").forEach(function (form) {
        populateReasons(form);
        const decision = form.querySelector("[data-evidence-review-decision]");
        if (decision) {
            decision.addEventListener("change", function () {
                populateReasons(form);
            });
        }

        form.addEventListener("submit", async function (event) {
            event.preventDefault();
            const status = form.querySelector("[data-evidence-review-status]");
            const submit = form.querySelector("[data-evidence-review-submit]");
            submit.disabled = true;
            status.textContent = "Saving review…";

            try {
                const response = await fetch(form.dataset.reviewUrl, {
                    method: "POST",
                    body: new FormData(form),
                    headers: { Accept: "application/json" },
                });
                const payload = await jsonOrEmpty(response);
                if (!response.ok) {
                    throw new Error(
                        safeMessage(payload, "Aura could not save this review.")
                    );
                }
                status.textContent = "Review saved.";
                closePreview();
                window.setTimeout(function () {
                    window.location.reload();
                }, 500);
            } catch (error) {
                status.textContent = error.message || "Aura could not save this review.";
                submit.disabled = false;
            }
        });
    });
})();
