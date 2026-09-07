import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext.jsx";

const ROLE_ORDER = ["viewer", "dev", "admin"];

export function ProtectedRoute({ children, minRole = "viewer" }) {
  const { user, loading } = useAuth();

  if (loading) return <div className="p-8 text-slate-400">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (ROLE_ORDER.indexOf(user.role) < ROLE_ORDER.indexOf(minRole)) {
    return <div className="p-8 text-red-400">You don't have access to this page.</div>;
  }
  return children;
}
