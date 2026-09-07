import { Router } from "express";
import { pool } from "../db.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";

export const router = Router();

router.get("/", requireAuth, asyncHandler(async (req, res) => {
  const { rows } = await pool.query(
    "SELECT id, email, full_name, role, created_at FROM users WHERE id = $1",
    [req.user.sub]
  );
  const user = rows[0];
  if (!user) return res.status(404).json({ error: "User not found" });
  res.json(user);
}));
