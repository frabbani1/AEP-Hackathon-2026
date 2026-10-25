import { useCallback, useEffect, useState, type MouseEvent } from "react";

/**
 * Minimal client-side routing for a handful of pages, so no router dependency is needed.
 * Uses the History API. The host must serve index.html for every route below (a dev server
 * does this by default; Amplify needs a 200 rewrite rule).
 */

export type RoutePath = "/" | "/map" | "/safety" | "/about";

const ROUTES: readonly RoutePath[] = ["/", "/map", "/safety", "/about"];

function toRoute(pathname: string): RoutePath {
  const clean = pathname.replace(/\/+$/, "") || "/";
  return ROUTES.find((route) => route === clean) ?? "/";
}

/**
 * Click handler for an in-app link: navigates without a page load. Modified clicks (new tab, new window) are left to
 * the browser, so the link still works like a link.
 */
export function internalLink(navigate: (to: RoutePath) => void, to: RoutePath) {
  return (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
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
