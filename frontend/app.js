// In-memory token store. Held only in this module's closure for the page
// session; never written to localStorage, sessionStorage, or cookies. A full
// reload drops it and re-prompts the operator.
let authToken = null;

export function getToken() {
  return authToken;
}

export function setToken(value) {
  authToken = value;
}

export function clearToken() {
  authToken = null;
}

function showTokenModal() {
  return new Promise((resolve) => {
    const modal = document.getElementById("token-modal");
    const form = document.getElementById("token-form");
    const input = document.getElementById("token-input");
    modal.hidden = false;
    input.focus();

    form.addEventListener(
      "submit",
      (e) => {
        e.preventDefault();
        const value = input.value.trim();
        if (!value) return;
        setToken(value);
        input.value = "";
        modal.hidden = true;
        resolve(value);
      },
      { once: true },
    );
  });
}

async function ensureToken() {
  if (authToken) return authToken;
  return showTokenModal();
}

async function init() {
  await ensureToken();
  // Session list, history rendering, and SSE consumer are wired in task 6.2.
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
