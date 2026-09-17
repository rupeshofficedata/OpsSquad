import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, runStreamUrl } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import CommandApprovalBox from "../components/CommandApprovalBox.jsx";
import FormattedText from "../components/FormattedText.jsx";
import KeyValueCards from "../components/KeyValueCards.jsx";
import ReplyBox from "../components/ReplyBox.jsx";
import ToolCallDetail from "../components/ToolCallDetail.jsx";

export default function RunDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const [run, setRun] = useState(null);
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let ws;
    let cancelled = false;

    (async () => {
      try {
        const url = await runStreamUrl(id); // mints a fresh ticket, then opens the socket
        if (cancelled) return;
        ws = new WebSocket(url);
        ws.onmessage = (event) => {
          const data = JSON.parse(event.data);
          if (data.error) return setError(data.error);
          setRun(data.run);
          setSteps(data.steps);
        };
        ws.onerror = () => setError("Live stream disconnected — falling back to a one-time fetch.");
      } catch (e) {
        setError(e.message);
      }
    })();

    api.getRun(id).then((r) => {
      setRun(r);
      setSteps(r.steps);
    }).catch((e) => setError(e.message));

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [id]);

  async function handleApprove() {
    try {
      await api.approveRun(id);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleAbort() {
    try {
      await api.abortRun(id);
    } catch (err) {
      setError(err.message);
    }
  }

  // Only chat runs ever pause at awaiting_user_input/awaiting_command_approval
  // (Flightplan steps are pre_approved — see graph.py) — a chat run reached
  // via Dashboard used to dead-end here with only Abort, no way to actually
  // answer/decide (Chat.jsx had ReplyBox/CommandApprovalBox, this page never did).
  async function handleReply(replyText) {
    setBusy(true);
    try {
      await api.replyToChat(id, replyText);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCommandDecision(approved) {
    setBusy(true);
    try {
      await (approved ? api.approveCommand(id) : api.denyCommand(id));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!run) return <div className="text-slate-400">{error || "Loading…"}</div>;

  const lastStep = steps[steps.length - 1];
  const question = lastStep?.output?.question;
  const pendingCommand = lastStep?.output?.pending_command;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Run {run.id.slice(0, 8)}</h1>
          <p className="text-sm text-slate-400">
            {run.kind} · status: <span className="font-medium">{run.status}</span>
          </p>
        </div>
        <div className="flex gap-2">
          {run.status === "awaiting_approval" && user?.role === "admin" && (
            <button onClick={handleApprove} className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium hover:bg-emerald-500">
              Approve
            </button>
          )}
          {["queued", "running", "awaiting_approval", "awaiting_user_input", "awaiting_command_approval"].includes(run.status) && (
            <button onClick={handleAbort} className="rounded-md border border-red-700 px-3 py-1.5 text-sm text-red-400 hover:bg-red-950">
              Abort
            </button>
          )}
        </div>
      </div>

      {run.prompt && (
        <p className="rounded-md border border-slate-800 bg-slate-900/40 p-3 text-sm text-slate-300">
          <span className="text-slate-500">Question: </span>{run.prompt}
        </p>
      )}

      {run.status === "awaiting_user_input" && (
        <div className="rounded-md border border-amber-700/50 bg-amber-950/20 p-3">
          <p className="text-sm text-amber-400">❓ {question}</p>
          <ReplyBox busy={busy} onReply={handleReply} />
        </div>
      )}

      {run.status === "awaiting_command_approval" && pendingCommand && (
        <div className="rounded-md border border-amber-700/50 bg-amber-950/20 p-3">
          <p className="text-sm text-amber-400">
            ⚠️ About to run <span className="font-mono">{pendingCommand.tool}</span>
            {" "}({Object.entries(pendingCommand.input || {}).map(([k, v]) => `${k}=${v}`).join(", ")})
          </p>
          <CommandApprovalBox busy={busy} onDecide={handleCommandDecision} />
        </div>
      )}

      {run.result?.narration && (
        <div className="rounded-md border border-indigo-800/50 bg-indigo-950/20 p-3">
          <p className="mb-1 text-xs uppercase text-indigo-400">Summary</p>
          <FormattedText text={run.result.narration} />
        </div>
      )}

      {error && <p className="text-amber-400 text-sm">{error}</p>}

      <ol className="space-y-3">
        {steps.map((s) => (
          <li key={s.id} className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium">{s.step_id || s.agent_slug} · {s.agent_slug}</span>
              <span className="text-xs uppercase text-slate-400">{s.status}</span>
            </div>
            {s.reasoning && <p className="mt-2 text-sm text-slate-400">{s.reasoning}</p>}
            {s.tool_calls?.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {s.tool_calls.map((tc, i) => <ToolCallDetail key={i} tc={tc} />)}
              </div>
            )}
            {s.output && (typeof s.output.summary === "string" && s.output.summary.trim() ? (
              <div className="mt-2"><FormattedText text={s.output.summary} /></div>
            ) : (
              <div className="mt-2"><KeyValueCards data={s.output} /></div>
            ))}
          </li>
        ))}
        {steps.length === 0 && <p className="text-slate-500">No steps recorded yet.</p>}
      </ol>
    </div>
  );
}
