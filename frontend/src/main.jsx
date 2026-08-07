import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import "./fonts/wenkai";
import "./fonts/wenkai-mono";
import "./fonts/noto-sans-sc";
import "./fonts/jetbrains-mono";
import "./fonts/stix-two-text";
import "./i18n";

import.meta.glob("./theme/*.css", { eager: true });

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
