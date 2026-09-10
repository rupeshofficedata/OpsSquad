const NODE_W = 108;
const NODE_H = 34;
const GAP_X = 14;
const GAP_Y = 26;
const PAD = 8;

// Dependency depth (longest path from a root) → vertical level. Steps that
// share a level (no `needs` relationship between them) sit side by side —
// covers real parallel branches, though the 3 seeded Flightplans are all
// single chains so this mostly just draws a straight line top to bottom.
function layout(steps) {
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const level = {};
  function levelOf(id) {
    if (level[id] != null) return level[id];
    const needs = byId[id]?.needs || [];
    level[id] = needs.length ? Math.max(...needs.map(levelOf)) + 1 : 0;
    return level[id];
  }
  steps.forEach((s) => levelOf(s.id));

  const byLevel = {};
  steps.forEach((s) => {
    (byLevel[level[s.id]] ??= []).push(s.id);
  });

  const pos = {};
  Object.values(byLevel).forEach((ids) => {
    ids.forEach((id, slot) => { pos[id] = { level: level[id], slot }; });
  });

  const maxSlots = Math.max(1, ...Object.values(byLevel).map((ids) => ids.length));
  const maxLevel = Math.max(0, ...Object.values(level));
  const width = PAD * 2 + maxSlots * NODE_W + (maxSlots - 1) * GAP_X;
  const height = PAD * 2 + (maxLevel + 1) * NODE_H + maxLevel * GAP_Y;

  const center = (id) => {
    const { level: lvl, slot } = pos[id];
    const slotsInLevel = byLevel[lvl].length;
    const rowWidth = slotsInLevel * NODE_W + (slotsInLevel - 1) * GAP_X;
    const rowStart = PAD + (width - PAD * 2 - rowWidth) / 2;
    const x = rowStart + slot * (NODE_W + GAP_X);
    const y = PAD + lvl * (NODE_H + GAP_Y);
    return { x, y, cx: x + NODE_W / 2 };
  };

  return { width, height, center };
}

export default function FlightplanGraph({ steps }) {
  if (!steps?.length) return null;
  const { width, height, center } = layout(steps);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ maxHeight: 320 }}>
      {steps.map((s) =>
        (s.needs || []).map((depId) => {
          const from = center(depId);
          const to = center(s.id);
          if (!from || !to) return null;
          return (
            <line
              key={`${depId}->${s.id}`}
              x1={from.cx} y1={from.y + NODE_H}
              x2={to.cx} y2={to.y}
              stroke="currentColor" className="text-slate-600"
              strokeWidth="1.5"
              strokeDasharray={s.when ? "4 3" : undefined}
            />
          );
        })
      )}
      {steps.map((s) => {
        const { x, y } = center(s.id);
        const isApproval = s.type === "approval";
        return (
          <g key={s.id}>
            <rect
              x={x} y={y} width={NODE_W} height={NODE_H} rx={isApproval ? 17 : 6}
              className={isApproval ? "fill-amber-950 stroke-amber-600" : "fill-slate-800 stroke-slate-600"}
              strokeWidth="1.5"
              strokeDasharray={s.when ? "3 2" : undefined}
            />
            <text x={x + NODE_W / 2} y={y + 14} textAnchor="middle" className="fill-slate-200" fontSize="9" fontWeight="600">
              {s.id}
            </text>
            <text x={x + NODE_W / 2} y={y + 26} textAnchor="middle" className="fill-slate-400" fontSize="7.5">
              {isApproval ? `approval · ${s.approvers?.join(",") || "admin"}` : s.agent}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
