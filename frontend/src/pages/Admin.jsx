import { useEffect, useState } from "react";
import { api } from "../api/client.js";

export default function Admin() {
  const [users, setUsers] = useState([]);
  const [audit, setAudit] = useState([]);
  const [error, setError] = useState(null);

  function load() {
    api.listUsers().then(setUsers).catch((e) => setError(e.message));
    api.auditLog().then(setAudit).catch((e) => setError(e.message));
  }

  useEffect(load, []);

  async function handleRoleChange(id, role) {
    try {
      await api.updateUserRole(id, role);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Users</h1>
        {error && <p className="text-red-400">{error}</p>}
        <table className="mt-3 w-full text-sm">
          <thead className="bg-slate-900 text-left text-slate-400">
            <tr>
              <th className="px-4 py-2">Email</th>
              <th className="px-4 py-2">Role</th>
              <th className="px-4 py-2">Last login</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-slate-800">
                <td className="px-4 py-2">{u.email}</td>
                <td className="px-4 py-2">
                  <select
                    value={u.role}
                    onChange={(e) => handleRoleChange(u.id, e.target.value)}
                    className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1"
                  >
                    <option value="viewer">viewer</option>
                    <option value="dev">dev</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td className="px-4 py-2 text-slate-400">
                  {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h2 className="text-lg font-semibold">Audit Log</h2>
        <ul className="mt-3 space-y-1 text-sm text-slate-400">
          {audit.map((a) => (
            <li key={a.id}>
              <span className="text-slate-200">{a.action}</span> on {a.target} —{" "}
              {new Date(a.created_at).toLocaleString()}
            </li>
          ))}
          {audit.length === 0 && <li>No audit entries yet.</li>}
        </ul>
      </div>
    </div>
  );
}
