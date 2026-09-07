import { Router } from "express";
import { config } from "../config.js";
import { pool } from "../db.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";
import { requireRole } from "../middleware/requireRole.js";

export const router = Router();

router.get("/", requireAuth, asyncHandler(async (req, res) => {
  const { rows } = await pool.query(
    `SELECT r.id, r.kind, r.status, r.prompt, r.started_at, r.finished_at,
            a.slug AS agent_slug, f.slug AS flightplan_slug
     FROM runs r
     LEFT JOIN agents a ON a.id = r.agent_id
     LEFT JOIN flightplans f ON f.id = r.flightplan_id
     ORDER BY r.started_at DESC
     LIMIT 100`
  );
  res.json(rows);
}));

router.get("/:id", requireAuth, asyncHandler(async (req, res) => {
  const { rows: runRows } = await pool.query("SELECT * FROM runs WHERE id = $1", [req.params.id]);
  const run = runRows[0];
  if (!run) return res.status(404).json({ error: "Run not found" });

  const { rows: steps } = await pool.query(
    "SELECT * FROM run_steps WHERE run_id = $1 ORDER BY step_order", [req.params.id]
  );
  res.json({ ...run, steps });
}));

router.post("/:id/approve", requireAuth, requireRole("admin"), asyncHandler(async (req, res) => {
  const resp = await fetch(`${config.runtimeUrl}/flightplans/runs/${req.params.id}/approve`, {
    method: "POST",
    headers: { Authorization: req.headers.authorization },
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}));

router.post("/:id/abort", requireAuth, requireRole("dev"), asyncHandler(async (req, res) => {
  const resp = await fetch(`${config.runtimeUrl}/runs/${req.params.id}/abort`, {
    method: "POST",
    headers: { Authorization: req.headers.authorization },
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}));
