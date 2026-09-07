import { NavLink, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./auth/ProtectedRoute.jsx";
import { useAuth } from "./auth/AuthContext.jsx";
import Admin from "./pages/Admin.jsx";
import Chat from "./pages/Chat.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Flightplans from "./pages/Flightplans.jsx";
import Login from "./pages/Login.jsx";
import RunDetail from "./pages/RunDetail.jsx";

function navClass({ isActive }) {
  return `rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"}`;
}

function Layout({ children }) {
  const { user, logout } = useAuth();
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="font-semibold">🛠️ OpsSquad</span>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navClass}>Dashboard</NavLink>
            <NavLink to="/chat" className={navClass}>Chat</NavLink>
            <NavLink to="/flightplans" className={navClass}>Flightplans</NavLink>
            {user?.role === "admin" && <NavLink to="/admin" className={navClass}>Admin</NavLink>}
          </nav>
        </div>
        {user && (
          <div className="flex items-center gap-3 text-sm text-slate-400">
            <span>{user.email} · {user.role}</span>
            <button onClick={logout} className="rounded-md border border-slate-700 px-3 py-1 hover:bg-slate-800">
              Log out
            </button>
          </div>
        )}
      </header>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/flightplans" element={<ProtectedRoute minRole="dev"><Flightplans /></ProtectedRoute>} />
                <Route path="/runs/:id" element={<RunDetail />} />
                <Route path="/admin" element={<ProtectedRoute minRole="admin"><Admin /></ProtectedRoute>} />
              </Routes>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
