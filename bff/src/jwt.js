// Single source of truth for every JWT operation on the BFF — mirrors how
// runtime/app/security.py centralizes this on the Python side (which
// already pins algorithms=[...]). Previously 3 separate call sites each
// called jwt.verify() ad hoc, none pinning `algorithms`.
import jwt from "jsonwebtoken";
import { config } from "./config.js";

export function signAccessToken(user) {
  return jwt.sign(
    { sub: user.id, email: user.email, role: user.role },
    config.jwtSecret,
    { algorithm: "HS256", expiresIn: config.jwtAccessTtl }
  );
}

export function signRefreshToken(user) {
  return jwt.sign(
    { sub: user.id, type: "refresh" },
    config.jwtSecret,
    { algorithm: "HS256", expiresIn: config.jwtRefreshTtl }
  );
}

export function verifyToken(token) {
  return jwt.verify(token, config.jwtSecret, { algorithms: ["HS256"] });
}
