window.APP_CONFIG = {
  // Local dev serves the frontend and backend on separate ports (frontend on
  // :8000 via `python -m http.server`, backend on :8001 via uvicorn) --
  // detect that one specific case and point at the known dev backend.
  // Anywhere else (a real deployment), app/main.py serves this file itself,
  // so same-origin (empty string, relative requests) is correct with no
  // per-deployment config -- works unchanged whether that origin is a bare
  // domain, a subdomain, or a path prefix behind a reverse proxy.
  API_BASE_URL: location.port === "8000" ? "http://127.0.0.1:8001" : "",
  USE_MOCKS: false
};
