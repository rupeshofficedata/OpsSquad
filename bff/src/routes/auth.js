import bcrypt from "bcryptjs";
import rateLimit from "express-rate-limit";
import { Router } from "express";
import { pool } from "../db.js";
import { redis } from "../redis.js";
import { asyncHandler } from "../middleware/asyncHandler.js";
import { requireAuth } from "../middleware/requireAuth.js";
import { signAccessToken, signRefreshToken, verifyToken } from "../jwt.js";

export const router = Router();

// Stricter than the app-wide 120/min — brute-forcing logins specifically
// is the actual risk the global limit doesn't address.
const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 10,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many login attempts, try again later" },
});

router.post("/login", loginLimiter, asyncHandler(async (req, res) => {
  const { email, password } = req.body;
  if (!email || !password) {
    return res.status(400).json({ error: "email and password are required" });
  }

  const { rows } = await pool.query(
    "SELECT id, email, password_hash, role, is_active FROM users WHERE email = $1",
    [email]
  );
  const user = rows[0];
  if (!user || !user.is_active) {
    return res.status(401).json({ error: "Invalid credentials" });
  }

  const valid = await bcrypt.compare(password, user.password_hash);
  if (!valid) {
    return res.status(401).json({ error: "Invalid credentials" });
  }

  await pool.query("UPDATE users SET last_login_at = NOW() WHERE id = $1", [user.id]);

  const accessToken = signAccessToken(user);
  const refreshToken = signRefreshToken(user);
  await redis.set(`refresh:${user.id}`, refreshToken, { EX: 7 * 24 * 3600 });

  res.cookie("refreshToken", refreshToken, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    maxAge: 7 * 24 * 3600 * 1000,
  });

  res.json({
    accessToken,
    user: { id: user.id, email: user.email, role: user.role },
  });
}));

router.post("/refresh", asyncHandler(async (req, res) => {
  const token = req.cookies?.refreshToken;
  if (!token) return res.status(401).json({ error: "No refresh token" });

  let payload;
  try {
    payload = verifyToken(token);
  } catch {
    return res.status(401).json({ error: "Invalid refresh token" });
  }

  const stored = await redis.get(`refresh:${payload.sub}`);
  if (stored !== token) {
    return res.status(401).json({ error: "Refresh token revoked" });
  }

  const { rows } = await pool.query(
    "SELECT id, email, role FROM users WHERE id = $1",
    [payload.sub]
  );
  const user = rows[0];
  if (!user) return res.status(401).json({ error: "User not found" });

  res.json({ accessToken: signAccessToken(user) });
}));

router.post("/logout", requireAuth, asyncHandler(async (req, res) => {
  await redis.del(`refresh:${req.user.sub}`);
  res.clearCookie("refreshToken");
  res.json({ ok: true });
}));
