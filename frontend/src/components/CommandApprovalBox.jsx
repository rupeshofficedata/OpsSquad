// A mutating command (see MUTATING_TOOLS in executor.py) the model wants to
// run — paused for the requesting user's own Approve/Deny before it
// executes (dedicated buttons, not a free-text reply, since this is a
// binary yes/no gate on a specific real command). Shared by Chat.jsx and
// RunDetail.jsx (a chat run reached via Dashboard needs the same ability
// to actually decide, not just Abort).
export default function CommandApprovalBox({ busy, onDecide }) {
  return (
    <div className="mt-2 flex gap-2">
      <button
        onClick={() => onDecide(true)}
        disabled={busy}
        className="rounded-md bg-emerald-600 px-3 py-1 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
      >
        Approve
      </button>
      <button
        onClick={() => onDecide(false)}
        disabled={busy}
        className="rounded-md border border-red-700 px-3 py-1 text-sm text-red-400 hover:bg-red-950 disabled:opacity-50"
      >
        Deny
      </button>
    </div>
  );
}
