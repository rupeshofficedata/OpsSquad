// Small, targeted formatter for the patterns model-generated summaries
// actually produce (bullet lists, **bold**, `code`, markdown tables) — not
// a full markdown parser, no new dependency for what's a narrow, observed
// need. Shared by Chat.jsx (agent's own final answer) and RunDetail.jsx
// (LLM-generated run narration, see executor.py's summarize_run).
const TABLE_SEPARATOR_RE = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/;

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

function inline(s, key) {
  const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <p key={key} className="text-slate-300">
      {parts.map((p, j) =>
        p.startsWith("**") ? <strong key={j} className="text-slate-100">{p.slice(2, -2)}</strong>
        : p.startsWith("`") ? <code key={j} className="rounded bg-slate-950 px-1 text-indigo-300">{p.slice(1, -1)}</code>
        : p
      )}
    </p>
  );
}

function Table({ header, rows, keyPrefix }) {
  return (
    <div key={keyPrefix} className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-700">
            {header.map((h, i) => <th key={i} className="px-2 py-1 font-medium text-slate-400">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-slate-800/60">
              {row.map((cell, j) => <td key={j} className="px-2 py-1 text-slate-300">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function FormattedText({ text }) {
  const lines = text.split("\n");
  const blocks = [];
  let list = [];

  const flushList = (key) => {
    if (list.length) {
      blocks.push(<ul key={key} className="ml-4 list-disc space-y-0.5">{list.map((li, j) => <li key={j} className="text-slate-300">{li}</li>)}</ul>);
      list = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // A table: a "| ... |" row immediately followed by a "|---|---|"
    // separator row — the rest of the block is its body rows.
    if (line.includes("|") && TABLE_SEPARATOR_RE.test(lines[i + 1] || "")) {
      flushList(`ul-${i}`);
      const header = splitTableRow(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push(<Table key={`table-${i}`} keyPrefix={`table-${i}`} header={header} rows={rows} />);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)/);
    if (bullet) {
      list.push(bullet[1]);
    } else {
      flushList(`ul-${i}`);
      if (line.trim()) blocks.push(inline(line, i));
    }
    i += 1;
  }
  flushList("ul-end");
  return <div className="space-y-1.5">{blocks}</div>;
}
