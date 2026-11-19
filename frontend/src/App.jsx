// The top of the app: shows the login page, or the onboarding page once logged in.
import { useEffect, useState } from "react";

import { getMe, logout } from "./api.js";
import LoginPage from "./LoginPage.jsx";
import UploadPage from "./UploadPage.jsx";

export default function App() {
  const [tenant, setTenant] = useState(null); // {tenant_id, name} when logged in
  const [checking, setChecking] = useState(true); // true until we know who is logged in

  // On page load, ask the backend if the login cookie is still valid.
  useEffect(() => {
    getMe()
      .then(setTenant)
      .finally(() => setChecking(false));
  }, []);

  async function handleLogout() {
    await logout();
    setTenant(null);
  }

  if (checking) {
    return <p className="page">Loading...</p>;
  }

  if (tenant === null) {
    return <LoginPage onLogin={setTenant} />;
  }

  return (
    <div className="page">
      <header className="header">
        <h1>SalesOnboard</h1>
        <span>
          {tenant.name} <button onClick={handleLogout}>Log out</button>
        </span>
      </header>
      <UploadPage />
    </div>
  );
}
