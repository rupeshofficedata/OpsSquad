import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, runStreamUrl } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import CommandApprovalBox from "../components/CommandApprovalBox.jsx";
import FormattedText from "../components/FormattedText.jsx";
import KeyValueCards from "../components/KeyValueCards.jsx";
import ReplyBox from "../components/ReplyBox.jsx";
import ToolCallDetail from "../components/ToolCallDetail.jsx";

const TERMINAL_STATUSES = new Set(["success", "failed", "aborted"]);

const STATUS_DOT = { running: "bg-emerald-500", loading: "bg-amber-500", stopped: "bg-red-500" };

const POD_STATUS_DOT = {
  Running: "bg-emerald-500", Succeeded: "bg-slate-500", Pending: "bg-amber-500",
  Failed: "bg-red-500", Unknown: "bg-slate-600",
};

// Every real kubectl.get('pods'/'deployments'/...) call returns this shape
// (see runtime/app/tools/real.py) — render it as cards instead of letting
// it sit buried in a JSON dump.
// Field set varies by k8s kind (see _summarize_k8s_item in real.py) — a
// Pod has ready/status/restarts, a Service has type/cluster_ip/ports,
// neither has the other's fields. Render whatever's actually present
// instead of assuming one fixed shape.
function ResourceCards({ items }) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {items.map(({ name, status, ...rest }) => (
        <div key={name} className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-900/60 px-2 py-1.5 text-xs">
          {status !== undefined && <span className={`h-2 w-2 shrink-0 rounded-full ${POD_STATUS_DOT[status] || "bg-slate-600"}`} />}
          <span className="truncate text-slate-200" title={name}>{name}</span>
          <span className="ml-auto shrink-0 space-x-2 text-slate-500">
            {status && <span>{status}</span>}
            {Object.entries(rest).filter(([, v]) => v != null).map(([k, v]) => <span key={k}>{String(v)}</span>)}
          </span>
        </div>
      ))}
    </div>
  );
}

// Live, always-visible trace of the whole tool-use loop, one card per
// round exactly as it was persisted (see executor.py's on_step / routes/
// chat.py's _execute_chat): the model's reasoning for that round, then
// whatever tool call(s) it issued and their real result — not just a
// flattened final summary, so you can watch input -> model output -> tool
// execution -> result for every round, live, as run_steps rows arrive over
// the WS stream.
function LiveSteps({ steps }) {
  // The final row (see _execute_chat in routes/chat.py) carries only the
  // finished output/status, no reasoning/tool_calls — every round was
  // already shown live above it. Skip it here so it isn't a blank card.
  const rounds = steps.filter((s) => s.reasoning || s.tool_calls?.length > 0);
  return (
    <div className="space-y-1.5">
      {rounds.map((s, idx) => (
        <div key={s.id || idx} className="rounded-md border border-slate-700 bg-slate-900/40 p-2 text-xs">
          <div className="mb-1 flex items-center gap-1.5 text-slate-500">
            <span>Round {idx + 1}</span>
            {s.status === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />}
          </div>
          {s.reasoning && <p className="mb-1 whitespace-pre-wrap text-slate-300">{s.reasoning}</p>}
          {s.tool_calls?.length > 0 && (
            <div className="space-y-1">
              {s.tool_calls.map((tc, j) => <ToolCallDetail key={j} tc={tc} />)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// The reasoning/tool-call trace (LiveSteps) is collapsed behind this by
// default — the point of the redesign is that the polished final answer is
// what's visible, not the round-by-round trace. Closed by default even
// while a run is still going: `running` here only swaps the label/dot so
// there's still a liveness cue without dumping the trace open on every
// message.
function StepsDisclosure({ steps, running }) {
  const [open, setOpen] = useState(false);
  const rounds = steps.filter((s) => s.reasoning || s.tool_calls?.length > 0);
  if (rounds.length === 0) return null;
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300"
      >
        <span className="w-3 text-center">{open ? "▾" : "▸"}</span>
        <span>{open ? "Hide" : "Show"} steps ({rounds.length})</span>
        {running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />}
      </button>
      {open && <div className="mt-1.5"><LiveSteps steps={steps} /></div>}
    </div>
  );
}

export default function Chat() {
  const { user, updateUser } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [prompt, setPrompt] = useState("");
  const [env, setEnv] = useState("staging");
  const [history, setHistory] = useState([]);
  const [runsById, setRunsById] = useState({});
  const [pendingCount, setPendingCount] = useState(0);
  const busy = pendingCount > 0;
  const [modelSaving, setModelSaving] = useState(false);
  const [modelNameDraft, setModelNameDraft] = useState(user.model_name || "");
  const [modelStatus, setModelStatus] = useState(null);
  const [starting, setStarting] = useState(false);
  const [answeredIndices, setAnsweredIndices] = useState(new Set());
  // The root run's id of the thread this page is continuing, or null for
  // a not-yet-started one — see routes/chat.py's build_thread_history.
  // Kept in the URL (?thread=) so a reload resumes the same thread instead
  // of losing it (see Chat.jsx's ?thread hydration effect below).
  const [threadId, setThreadId] = useState(searchParams.get("thread"));
  const [threadLoading, setThreadLoading] = useState(!!searchParams.get("thread"));
  // handleSubmit sets ?thread= itself right after a send, once it already
  // has the live run's state in `history`/`runsById` — that URL change
  // would otherwise re-trigger the hydration effect below and stomp that
  // live state with a DB fetch that doesn't have the in-flight run's
  // answer yet (chat_messages only gets the agent's turn once it finishes
  // — see _execute_chat). This flag tells that one self-inflicted URL
  // change to skip hydrating, without blocking a genuine reload or a
  // Dashboard "Continue" navigation (both come from outside handleSubmit).
  const skipNextHydration = useRef(false);

  // Hydrates from an existing thread — either a reload with ?thread= still
  // in the URL, or Dashboard's "Continue" link. Only runs once per
  // thread_id: every message here is real (see get_thread's docstring —
  // add_chat_message is never called with a simulated run's output), so
  // it's rendered as plain read-only turns, no LiveSteps/tool-call replay
  // (that raw state was never persisted across runs in the first place).
  useEffect(() => {
    const id = searchParams.get("thread");
    if (!id) return;
    if (skipNextHydration.current) { skipNextHydration.current = false; return; }
    let cancelled = false;
    setThreadLoading(true);
    api.getChatThread(id)
      .then((res) => {
        if (cancelled) return;
        setThreadId(res.thread_id);
        setHistory(res.messages.map((m) => ({
          role: m.role === "agent" ? "agent-historical" : "user",
          text: m.content,
        })));
      })
      .catch((err) => {
        if (cancelled) return;
        setHistory([{ role: "error", text: `Couldn't load thread: ${err.message}` }]);
      })
      .finally(() => { if (!cancelled) setThreadLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get("thread")]);

  function handleNewChat() {
    setThreadId(null);
    setHistory([]);
    setRunsById({});
    setAnsweredIndices(new Set());
    setSearchParams((p) => { p.delete("thread"); return p; });
  }

  useEffect(() => {
    if (user.model_provider !== "local") return;
    let cancelled = false;
    const poll = () => api.getModelStatus().then((r) => { if (!cancelled) setModelStatus(r.state); }).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [user.model_provider]);

  async function handleStartModel() {
    setStarting(true);
    try {
      const r = await api.startModel();
      setModelStatus(r.state);
    } finally {
      setStarting(false);
    }
  }

  async function handleProviderChange(provider) {
    setModelSaving(true);
    try {
      updateUser(await api.updateModelPreference(provider, provider === "local" ? modelNameDraft : null));
    } finally {
      setModelSaving(false);
    }
  }

  async function handleModelNameBlur() {
    if (user.model_provider !== "local") return;
    setModelSaving(true);
    try {
      updateUser(await api.updateModelPreference("local", modelNameDraft || null));
    } finally {
      setModelSaving(false);
    }
  }

  async function handleToolCallModeChange(toolCallMode) {
    setModelSaving(true);
    try {
      updateUser(await api.updateModelPreference("local", modelNameDraft || null, toolCallMode));
    } finally {
      setModelSaving(false);
    }
  }

  // POST /chat and /reply now background the whole tool-use loop and
  // return {status:"queued", run_id} right away — this opens the same WS
  // run-stream RunDetail.jsx already uses to watch it live, round by round,
  // instead of blocking on one big response at the end.
  async function subscribeToRun(runId) {
    setPendingCount((c) => c + 1);
    const url = await runStreamUrl(runId);
    const ws = new WebSocket(url);
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.error) {
        setPendingCount((c) => Math.max(0, c - 1));
        return;
      }
      setRunsById((r) => ({ ...r, [runId]: data }));
      if (TERMINAL_STATUSES.has(data.run.status) || data.run.status === "awaiting_user_input" || data.run.status === "awaiting_command_approval") {
        setPendingCount((c) => Math.max(0, c - 1));
      }
    };
    ws.onerror = () => setPendingCount((c) => Math.max(0, c - 1));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!prompt.trim()) return;
    const mine = { role: "user", text: prompt };
    setHistory((h) => [...h, mine]);
    setPrompt("");
    try {
      const res = await api.chat(mine.text, env, threadId);
      if (res.thread_id && res.thread_id !== threadId) {
        skipNextHydration.current = true;
        setThreadId(res.thread_id);
        setSearchParams((p) => { p.set("thread", res.thread_id); return p; });
      }
      setHistory((h) => [...h, { role: "agent", runId: res.run_id, agent: res.agent }]);
      if (res.status === "queued") {
        subscribeToRun(res.run_id);
      } else {
        // awaiting_approval — nothing running, no stream to open.
        setRunsById((r) => ({ ...r, [res.run_id]: { run: { status: res.status }, steps: [] } }));
      }
    } catch (err) {
      setHistory((h) => [...h, { role: "error", text: err.message }]);
    }
  }

  async function handleReply(runId, index, replyText) {
    setAnsweredIndices((s) => new Set(s).add(index));
    setHistory((h) => [...h, { role: "user", text: replyText }]);
    setPendingCount((c) => c + 1);
    try {
      await api.replyToChat(runId, replyText);
      // The WS for this runId is still open (the stream only closes on a
      // terminal status, and awaiting_user_input isn't one) — it'll pick
      // up the resumed rounds automatically, no need to resubscribe.
      // ponytail: a poll tick between this call and the backend actually
      // flipping status to 'running' can still see the old
      // 'awaiting_user_input' and decrement pendingCount early — Send
      // re-enables a couple seconds before the reply is truly done. Fix if
      // that's ever more than cosmetic: track per-runId pending state keyed
      // off a status transition, not a raw count.
    } catch (err) {
      setPendingCount((c) => Math.max(0, c - 1));
      setHistory((h) => [...h, { role: "error", text: err.message }]);
    }
  }

  async function handleCommandDecision(runId, index, approved) {
    setAnsweredIndices((s) => new Set(s).add(index));
    setPendingCount((c) => c + 1);
    try {
      await (approved ? api.approveCommand(runId) : api.denyCommand(runId));
      // Same WS-still-open reasoning as handleReply — no need to resubscribe.
    } catch (err) {
      setPendingCount((c) => Math.max(0, c - 1));
      setHistory((h) => [...h, { role: "error", text: err.message }]);
    }
  }

  return (
    <div className="flex h-full flex-col space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-semibold">Chat</h1>
          {threadId && (
            <button
              onClick={handleNewChat}
              title="Start a new thread — this one stays reachable from Run History"
              className="rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:bg-slate-800"
            >
              New chat
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={user.model_provider}
            onChange={(e) => handleProviderChange(e.target.value)}
            disabled={modelSaving}
            title="Which model your agent runs execute against"
            className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
          >
            <option value="anthropic">Claude (Anthropic API)</option>
            <option value="local">Local (llama.cpp)</option>
          </select>
          {user.model_provider === "local" && (
            <>
              <input
                value={modelNameDraft}
                onChange={(e) => setModelNameDraft(e.target.value)}
                onBlur={handleModelNameBlur}
                placeholder="model label, e.g. qwen2.5-coder:7b"
                className="w-44 rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
              />
              <select
                value={user.tool_call_mode || "lenient"}
                onChange={(e) => handleToolCallModeChange(e.target.value)}
                disabled={modelSaving}
                title="strict: only trust the model's real structured tool_calls field. lenient: also parse tool calls the model wrote as plain text."
                className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
              >
                <option value="lenient">Lenient (fallback parsing)</option>
                <option value="strict">Strict (real tool calls only)</option>
              </select>
              <span
                className={`h-2.5 w-2.5 rounded-full ${STATUS_DOT[modelStatus] || "bg-slate-600"}`}
                title={`model: ${modelStatus || "unknown"}`}
              />
              <span className="text-xs text-slate-400">{modelStatus || "unknown"}</span>
              <button
                onClick={handleStartModel}
                disabled={modelStatus !== "stopped" || starting}
                className="rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800 disabled:opacity-40"
              >
                {starting ? "Starting…" : "Start model"}
              </button>
            </>
          )}
          <select
            value={env}
            onChange={(e) => setEnv(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm"
          >
            <option value="staging">staging</option>
            <option value="prod">prod</option>
          </select>
        </div>
      </div>

      <p className="text-xs text-slate-600">
        Every action targets this OpsSquad deployment itself (its own namespace, its own source, its own demo config) — there's no other cluster or repo to point it at.
      </p>

      <div className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        {threadLoading && <p className="text-slate-500">Loading thread…</p>}
        {!threadLoading && history.length === 0 && (
          <p className="text-slate-500">
            Try: "scan the payments-api image for critical CVEs" or "what's the current cluster cost?"
          </p>
        )}
        {history.map((m, i) => (
          <div key={i} className="space-y-1">
            {m.role === "user" && <p className="font-medium text-indigo-300">You: {m.text}</p>}
            {m.role === "error" && <p className="text-red-400">Error: {m.text}</p>}
            {/* A prior turn hydrated from GET /chat/threads/{id} — plain text
                only, no run/steps to replay (see the hydration effect above). */}
            {m.role === "agent-historical" && (
              <div className="rounded-md bg-slate-800 p-3 text-sm">
                <FormattedText text={m.text} />
              </div>
            )}
            {m.role === "agent" && (() => {
              const data = runsById[m.runId];
              const status = data?.run?.status;
              const steps = data?.steps || [];
              const lastStep = steps[steps.length - 1];
              const allToolCalls = steps.flatMap((s) => s.tool_calls || []);
              const items = allToolCalls.flatMap((tc) => tc.result?.data?.items || []);
              const summary = lastStep?.output?.summary;
              const question = lastStep?.output?.question;
              const pendingCommand = lastStep?.output?.pending_command;
              // No model actually ran this turn (no Anthropic key, no
              // reachable local server — see run_agent's `simulated` flag
              // in executor.py). summary below is a canned placeholder,
              // not a real answer, and was never saved into thread memory.
              const isSimulated = lastStep?.output?.simulated === true;
              // summary only exists on the final marker run_step (added
              // once _execute_chat's round loop finishes — see
              // routes/chat.py), so it's naturally absent while `status`
              // is still 'running'. The per-round trace itself lives only
              // inside the collapsed StepsDisclosure below, never inline,
              // so there's nothing to de-duplicate here anymore.
              const roundCount = steps.filter((s) => s.reasoning || s.tool_calls?.length > 0).length;

              return (
                <div className="rounded-md bg-slate-800 p-3 text-sm">
                  <p className="mb-1 text-slate-400">
                    🧭 Matched agent → <span className="text-slate-200">{m.agent}</span>
                    {" · "}
                    <Link to={`/runs/${m.runId}`} className="text-indigo-400 hover:underline">view run</Link>
                    {status && <span className="ml-2 text-xs uppercase text-slate-500">{status}</span>}
                  </p>
                  {!data ? (
                    <p className="text-slate-500">Starting…</p>
                  ) : status === "awaiting_approval" ? (
                    <p className="text-amber-400">This is a mutating action in prod — awaiting admin approval.</p>
                  ) : (
                    <>
                      {isSimulated && (
                        <p className="mb-2 text-xs text-amber-500">
                          ⚠ No model configured (no Anthropic key, no reachable local server) — this is a canned placeholder, not a real answer, and won't be remembered in this thread.
                        </p>
                      )}
                      <div className="space-y-2">
                        {items.length > 0 && <ResourceCards items={items} />}
                        {typeof summary === "string" && summary.trim() ? (
                          <FormattedText text={summary} />
                        ) : status === "running" ? (
                          <p className="text-slate-500">
                            <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                            Working… ({roundCount} round{roundCount === 1 ? "" : "s"} so far)
                          </p>
                        ) : (
                          !items.length && lastStep?.output && <KeyValueCards data={lastStep.output} />
                        )}
                      </div>
                      <StepsDisclosure steps={steps} running={status === "running"} />
                      {status === "awaiting_user_input" && !answeredIndices.has(i) && (
                        <div className="mt-2 rounded-md border border-amber-700/50 bg-amber-950/20 p-2">
                          <p className="text-sm text-amber-400">❓ {question}</p>
                          <ReplyBox busy={busy} onReply={(text) => handleReply(m.runId, i, text)} />
                        </div>
                      )}
                      {status === "awaiting_command_approval" && !answeredIndices.has(i) && pendingCommand && (
                        <div className="mt-2 rounded-md border border-amber-700/50 bg-amber-950/20 p-2">
                          <p className="text-sm text-amber-400">
                            ⚠️ About to run <span className="font-mono">{pendingCommand.tool}</span>
                            {" "}({Object.entries(pendingCommand.input || {}).map(([k, v]) => `${k}=${v}`).join(", ")})
                          </p>
                          <CommandApprovalBox busy={busy} onDecide={(approved) => handleCommandDecision(m.runId, i, approved)} />
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })()}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask an agent to do something…"
          className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 outline-none focus:border-indigo-500"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy ? "Running…" : "Send"}
        </button>
      </form>
    </div>
  );
}
