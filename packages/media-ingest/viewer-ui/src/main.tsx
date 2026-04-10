import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
// Catalyst-UI theme CSS — provides --background, --primary, --card, etc.
// HACK: Vite can't resolve catalyst-ui's ./styles/* export, so alias in vite.config.ts
import "catalyst-theme";

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
