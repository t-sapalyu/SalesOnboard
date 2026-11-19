import { useState } from "react";

import { login } from "./api.js";

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault(); // stop the browser from reloading the page
    setError("");
    try {
      const tenant = await login(username, password);
      onLogin(tenant);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="page login">
      <h1>SalesOnboard</h1>
      <form onSubmit={handleSubmit} className="card">
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={!username || !password}>
          Log in
        </button>
      </form>
    </div>
  );
}
