/**
 * Standalone entry for the avatar playground (/avatar-dev.html on the Vite dev
 * server), independent of the application router.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/globals.css";
import AvatarPlayground from "./AvatarPlayground";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <AvatarPlayground />
    </StrictMode>,
  );
}
