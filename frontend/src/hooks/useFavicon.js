import { useEffect } from "react";

const FAVICON_LIGHT = "/favicon.svg";
const FAVICON_DARK = "/favicon-dark.svg";

function getFaviconHref() {
  return document.documentElement.classList.contains("dark")
    ? FAVICON_DARK
    : FAVICON_LIGHT;
}

function applyFavicon() {
  let link = document.querySelector("link[rel='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/svg+xml";
    document.head.appendChild(link);
  }
  link.href = getFaviconHref();
}

export default function useFavicon() {
  useEffect(() => {
    applyFavicon();

    const observer = new MutationObserver(applyFavicon);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);
}
