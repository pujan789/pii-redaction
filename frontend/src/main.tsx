import "@fontsource-variable/plus-jakarta-sans";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { recordPageView } from "./pageViews";
import "./styles.css";

recordPageView("app");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
