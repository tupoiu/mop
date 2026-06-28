// In-memory token store. Held only in this module's closure for the page
// session; never written to localStorage, sessionStorage, or cookies. A full
// reload drops it and re-prompts the operator.
let authToken = null;
let activeSessionId = null;

export function getToken() { return authToken; }
export function setToken(value) { authToken = value; }
export function clearToken() { authToken = null; }

// --- Token modal ---

function showTokenModal() {
  return new Promise((resolve) => {
    const modal = document.getElementById("token-modal");
    const form = document.getElementById("token-form");
    const input = document.getElementById("token-input");
    modal.hidden = false;
    input.focus();
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      setToken(value);
      input.value = "";
      modal.hidden = true;
      resolve(value);
    }, { once: true });
  });
}

async function ensureToken() {
  if (authToken) return authToken;
  return showTokenModal();
}

// --- API helpers ---

async function api(method, path, body) {
  const headers = { "Authorization": `Bearer ${authToken}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// --- Session list ---

async function loadSessions() {
  const { sessions } = await api("GET", "/api/sessions");
  const list = document.getElementById("session-list");
  list.innerHTML = "";
  for (const s of sessions) {
    const li = document.createElement("li");
    li.textContent = s.title || s.id.slice(0, 8);
    li.dataset.sessionId = s.id;
    if (s.id === activeSessionId) li.classList.add("active");
    li.addEventListener("click", () => selectSession(s.id));
    list.appendChild(li);
  }
}

async function selectSession(sessionId) {
  activeSessionId = sessionId;
  document.querySelectorAll("#session-list li").forEach((li) => {
    li.classList.toggle("active", li.dataset.sessionId === sessionId);
  });
  resetAllyPanel();
  const { messages } = await api("GET", `/api/sessions/${sessionId}/messages`);
  renderHistory(messages);
}

// --- Conversation rendering ---

function scrollToBottom() {
  const conv = document.getElementById("conversation");
  conv.scrollTop = conv.scrollHeight;
}

function appendUserBubble(text) {
  const conv = document.getElementById("conversation");
  const div = document.createElement("div");
  div.className = "bubble user";
  div.textContent = text;
  conv.appendChild(div);
  scrollToBottom();
}

function makeAssistantBubble() {
  const conv = document.getElementById("conversation");
  const div = document.createElement("div");
  div.className = "bubble assistant";
  conv.appendChild(div);
  return div;
}

function appendToolBlock(summaryText, bodyText) {
  const conv = document.getElementById("conversation");
  const details = document.createElement("details");
  details.className = "tool-block";
  const summary = document.createElement("summary");
  summary.textContent = summaryText;
  details.appendChild(summary);
  const pre = document.createElement("pre");
  pre.textContent = bodyText;
  details.appendChild(pre);
  conv.appendChild(details);
  scrollToBottom();
}

function renderHistory(messages) {
  const conv = document.getElementById("conversation");
  conv.innerHTML = "";
  for (const msg of messages) {
    const content = JSON.parse(msg.content_json);
    if (msg.role === "user") {
      appendUserBubble(content.text || "");
    } else if (msg.kind === "text") {
      const div = document.createElement("div");
      div.className = "bubble assistant";
      div.textContent = content.text || "";
      conv.appendChild(div);
    } else if (msg.kind === "tool_call") {
      appendToolBlock(`Tool: ${content.name}`, JSON.stringify(content.input, null, 2));
    } else if (msg.kind === "tool_result") {
      appendToolBlock(
        `Result${content.is_error ? " (error)" : ""}`,
        content.output,
      );
    }
  }
}

// --- Ally summary panel (ephemeral; never persisted) ---

function allyLengthString(data) {
  const agent = data.agent_words ?? "";
  const user = data.user_words ?? "";
  const messages = data.message_count ?? "";
  return `${agent}/${user}W (A/U), ${messages}M`;
}

function resetAllyPanel() {
  const topic = document.getElementById("ally-topic");
  const cls = document.getElementById("ally-class");
  const time = document.getElementById("ally-time");
  const length = document.getElementById("ally-length");
  if (topic) topic.textContent = "";
  if (cls) cls.textContent = "";
  if (time) time.textContent = "";
  if (length) length.textContent = "";
  const panel = document.getElementById("ally-panel");
  if (panel) panel.classList.remove("warning");
  const warning = document.getElementById("ally-warning");
  if (warning) warning.hidden = true;
}

// --- SSE consumer (fetch + ReadableStream; native EventSource can't send headers) ---

function parseSSEBlock(block) {
  let type = null;
  let dataStr = null;
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) type = line.slice(7);
    else if (line.startsWith("data: ")) dataStr = line.slice(6);
  }
  if (!type || !dataStr) return null;
  try {
    return { type, data: JSON.parse(dataStr) };
  } catch {
    return null;
  }
}

async function streamTurn(sessionId, userContent) {
  const resp = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${authToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content: userContent }),
  });

  const assistantBubble = makeAssistantBubble();

  if (!resp.ok) {
    assistantBubble.textContent = `Error: HTTP ${resp.status}`;
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    outer: while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop(); // retain incomplete trailing chunk
      for (const block of parts) {
        if (!block.trim()) continue;
        const event = parseSSEBlock(block);
        if (!event) continue;
        const { type, data } = event;
        if (type === "text") {
          assistantBubble.textContent += data.text;
          scrollToBottom();
        } else if (type === "tool_call") {
          appendToolBlock(`Tool: ${data.name}`, JSON.stringify(data.input, null, 2));
        } else if (type === "tool_result") {
          appendToolBlock(
            `Result${data.is_error ? " (error)" : ""}`,
            data.output,
          );
        } else if (type === "ally_metrics") {
          const length = document.getElementById("ally-length");
          if (length) length.textContent = allyLengthString(data);
          const time = document.getElementById("ally-time");
          if (time) time.textContent = data.uk_time ?? "";
        } else if (type === "ally_summary") {
          const topic = document.getElementById("ally-topic");
          if (topic) topic.textContent = data.topic ?? "";
          const cls = document.getElementById("ally-class");
          if (cls) cls.textContent = data.classification ?? "";
          const time = document.getElementById("ally-time");
          if (time) time.textContent = data.uk_time ?? "";
          const length = document.getElementById("ally-length");
          if (length) length.textContent = allyLengthString(data);
          const panel = document.getElementById("ally-panel");
          const warning = document.getElementById("ally-warning");
          if (data.warning) {
            if (panel) panel.classList.add("warning");
            if (warning) warning.hidden = false;
          } else {
            if (panel) panel.classList.remove("warning");
            if (warning) warning.hidden = true;
          }
        } else if (type === "done") {
          if (!assistantBubble.textContent) assistantBubble.remove();
          await loadSessions();
          break outer;
        } else if (type === "error") {
          assistantBubble.textContent = `Error: ${data.message}`;
          break outer;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// --- Init ---

async function init() {
  await ensureToken();
  await loadSessions();

  document.getElementById("new-chat").addEventListener("click", async () => {
    const session = await api("POST", "/api/sessions", {});
    activeSessionId = session.id;
    await loadSessions();
    document.getElementById("conversation").innerHTML = "";
    resetAllyPanel();
  });

  document.getElementById("composer-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("composer").requestSubmit();
    }
  });

  document.getElementById("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("composer-input");
    const content = input.value.trim();
    if (!content) return;
    if (!activeSessionId) {
      const session = await api("POST", "/api/sessions", {});
      activeSessionId = session.id;
      await loadSessions();
    }
    input.value = "";
    appendUserBubble(content);
    await streamTurn(activeSessionId, content);
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
