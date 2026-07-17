import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

import.meta.glob("./theme/*.css", { eager: true });

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
