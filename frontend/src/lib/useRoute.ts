import { useCallback, useEffect, useState } from "react";

/**
 * Minimal client-side routing for two pages, so no router dependency is needed.
 * Uses the History API. The host must serve index.html for /map (a dev server
 * does this by default; Amplify needs a 200 rewrite rule).
 */

export type RoutePath = "/" | "/map";

function toRoute(pathname: string): RoutePath {
  return pathname.replace(/\/+$/, "") === "/map" ? "/map" : "/";
}

export function useRoute() {
  const [path, setPath] = useState<RoutePath>(() => toRoute(window.location.pathname));

  // Back and forward buttons.
  useEffect(() => {
    const onPopState = () => setPath(toRoute(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((to: RoutePath) => {
    if (window.location.pathname !== to) window.history.pushState(null, "", to);
    setPath(to);
  }, []);

  return { path, navigate };
}
