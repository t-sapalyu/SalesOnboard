// Every call to the backend goes through this file.
// The browser sends the login cookie automatically, because the page and the
// API are on the same site (see the proxy in vite.config.js).

async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // FastAPI puts the error message in "detail". For a bad request body it is
    // a list instead of a sentence, so we show a general message then.
    const message = typeof body.detail === "string" ? body.detail : "Something went wrong.";
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

function postJson(path, data) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function login(username, password) {
  return postJson("/login", { username, password });
}

export function logout() {
  return postJson("/logout", {});
}

// Returns the logged-in customer, or null when nobody is logged in.
export async function getMe() {
  try {
    return await request("/me");
  } catch (error) {
    if (error.status === 401) {
      return null;
    }
    throw error;
  }
}

export function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/upload", { method: "POST", body: form });
}
