import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./app/App";
import { createQueryClient } from "./app/queries";
import { applyTheme, storedTheme } from "./lib/theme";
import "./styles/app.css";

applyTheme(storedTheme());
window
  .matchMedia?.("(prefers-color-scheme: dark)")
  .addEventListener?.("change", () => applyTheme(storedTheme()));

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
