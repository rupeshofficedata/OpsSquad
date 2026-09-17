import { useState } from "react";

// Inline reply box for a paused (awaiting_user_input) run — the model
// called ask_user and is waiting; submitting resumes that same run_id with
// the full prior tool-use history (see POST /chat/{run_id}/reply). Shared
// by Chat.jsx and RunDetail.jsx (a chat run reached via Dashboard needs the
// same ability to actually answer, not just Abort).
export default function ReplyBox({ busy, onReply }) {
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (!text.trim()) return; onReply(text); setText(""); }}
      className="mt-2 flex gap-2"
    >
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Your answer…"
        className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-sm outline-none focus:border-indigo-500"
      />
      <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-3 py-1 text-sm font-medium hover:bg-indigo-500 disabled:opacity-50">
        Reply
      </button>
    </form>
  );
}
