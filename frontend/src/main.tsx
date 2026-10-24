import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Base styles first so component stylesheets can override them.
import "./styles/base.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
