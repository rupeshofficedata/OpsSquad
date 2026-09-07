import { Router } from "express";
import { config } from "../config.js";
import { pool } from "../db.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";
import { requireRole } from "../middleware/requireRole.js";

export const router = Router();

router.get("/", requireAuth, asyncHandler(async (req, res) => {
  const { rows } = await pool.query(
    "SELECT id, slug, name, description, env, created_by, created_at FROM flightplans ORDER BY created_at DESC"
  );
  res.json(rows);
}));

router.post("/", requireAuth, requireRole("dev"), asyncHandler(async (req, res) => {
  const { slug, name, description, definition, env } = req.body;
  if (!slug || !name || !definition) {
    return res.status(400).json({ error: "slug, name, and definition are required" });
  }
  const { rows } = await pool.query(
    `INSERT INTO flightplans (slug, name, description, definition, env, created_by)
     VALUES ($1, $2, $3, $4, $5, $6) RETURNING *`,
    [slug, name, description ?? null, JSON.stringify(definition), env ?? "staging", req.user.sub]
  );
  res.status(201).json(rows[0]);
}));

router.post("/:slug/execute", requireAuth, requireRole("dev"), asyncHandler(async (req, res) => {
  const resp = await fetch(`${config.runtimeUrl}/flightplans/${req.params.slug}/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: req.headers.authorization,
    },
    body: JSON.stringify({ inputs: req.body?.inputs ?? {} }),
  });
  const body = await resp.json();
  res.status(resp.status).json(body);
}));
