import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App, { LOGIN_ERRORS } from "./App";
import "./style.css";

// Read once, before anything renders: the address is cleaned up right away, so
// a reload does not show the same error again.
const params = new URLSearchParams(window.location.search);
const loginError = LOGIN_ERRORS[params.get("error") ?? ""];
if (params.has("error")) window.history.replaceState(null, "", "/");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App loginError={loginError} />
  </StrictMode>,
);
