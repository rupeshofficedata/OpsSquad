const API_URL = import.meta.env.VITE_API_URL || "http://localhost:4000";

let accessToken = null;

export function setAccessToken(token) {
  accessToken = token;
}

async function request(path, options = {}) {
  const resp = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...options.headers,
    },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.error || `Request failed: ${resp.status}`);
  return body;
}

export const api = {
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  refresh: () => request("/api/auth/refresh", { method: "POST" }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request("/api/me"),
  updateModelPreference: (provider, model, toolCallMode) =>
    request("/api/me/model", { method: "PATCH", body: JSON.stringify({ provider, model, toolCallMode }) }),
  getModelStatus: () => request("/api/model/status"),
  startModel: () => request("/api/model/start", { method: "POST" }),
  listModels: () => request("/api/model/list"),
  loadModel: (model) => request("/api/model/load", { method: "POST", body: JSON.stringify({ model }) }),
  stopModel: () => request("/api/model/stop", { method: "POST" }),
  listAgents: () => request("/api/agents"),
  chat: (prompt, env = "staging", threadId = null) =>
    request("/api/chat", { method: "POST", body: JSON.stringify({ prompt, env, thread_id: threadId }) }),
  getChatThread: (threadId) => request(`/api/chat/threads/${threadId}`),
  replyToChat: (runId, reply) =>
    request(`/api/chat/${runId}/reply`, { method: "POST", body: JSON.stringify({ reply }) }),
  approveCommand: (runId) => request(`/api/chat/${runId}/approve-command`, { method: "POST" }),
  denyCommand: (runId) => request(`/api/chat/${runId}/deny-command`, { method: "POST" }),
  listFlightplans: () => request("/api/flightplans"),
  executeFlightplan: (slug, inputs) =>
    request(`/api/flightplans/${slug}/execute`, { method: "POST", body: JSON.stringify({ inputs }) }),
  listRuns: () => request("/api/runs"),
  getRun: (id) => request(`/api/runs/${id}`),
  approveRun: (id) => request(`/api/runs/${id}/approve`, { method: "POST" }),
  abortRun: (id) => request(`/api/runs/${id}/abort`, { method: "POST" }),
  createRunStreamTicket: (id) => request(`/api/runs/${id}/stream-ticket`, { method: "POST" }),
  listUsers: () => request("/api/admin/users"),
  updateUserRole: (id, role) =>
    request(`/api/admin/users/${id}/role`, { method: "PATCH", body: JSON.stringify({ role }) }),
  auditLog: () => request("/api/admin/audit"),
};

// Ticket-based, not the real access token in the URL (which would leak
// into access/proxy logs) — mint a single-use ticket first, then connect.
export async function runStreamUrl(runId) {
  const { ticket } = await api.createRunStreamTicket(runId);
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/ws/runs/${runId}?ticket=${encodeURIComponent(ticket)}`;
}
