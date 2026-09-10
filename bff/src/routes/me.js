import { Router } from "express";
import { pool } from "../db.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";

export const router = Router();

router.get("/", requireAuth, asyncHandler(async (req, res) => {
  const { rows } = await pool.query(
    "SELECT id, email, full_name, role, model_provider, model_name, created_at FROM users WHERE id = $1",
    [req.user.sub]
  );
  const user = rows[0];
  if (!user) return res.status(404).json({ error: "User not found" });
  res.json(user);
}));

router.patch("/model", requireAuth, asyncHandler(async (req, res) => {
  const { provider, model } = req.body;
  if (!["anthropic", "local"].includes(provider)) {
    return res.status(400).json({ error: "provider must be 'anthropic' or 'local'" });
  }
  const { rows } = await pool.query(
    "UPDATE users SET model_provider = $2, model_name = $3 WHERE id = $1 RETURNING model_provider, model_name",
    [req.user.sub, provider, model || null]
  );
  res.json(rows[0]);
}));
