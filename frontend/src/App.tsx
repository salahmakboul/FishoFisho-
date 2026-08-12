import { useEffect, useState } from "react";
import { Auth } from "./components/Auth";
import type { AuthMode } from "./components/Auth";
import { Landing } from "./components/Landing";
import { Layout } from "./components/Layout";
import { useWorkspace } from "./hooks/useWorkspace";

const APP_PATH = "/app";

/**
 * The workspace only ever lives at /app — "/" is always the public
 * marketing site, authenticated or not (like most SaaS homepages: signing
 * in doesn't yank you off the page you're on). The only automatic
 * navigation is a guard rail, not a redirect-on-login: visiting /app while
 * signed out bounces to "/", since there's nothing to show there without a
 * session. Every other transition — landing to /app, login/register back to
 * landing, logout back to "/" — happens only from an explicit button click.
 */
// A password-reset email link (see PasswordResetRequestView) points here
// with ?uid=&token= query params — read once on initial load with plain
// URLSearchParams (no router library needed for a single one-off param
// read) so a visitor arriving from that link lands straight on the
// "choose a new password" screen instead of the landing page.
function readResetParams(): { uid: string; token: string } | null {
  const params = new URLSearchParams(window.location.search);
  const uid = params.get("uid");
  const token = params.get("token");
  return uid && token ? { uid, token } : null;
}

export default function App() {
  const workspace = useWorkspace();
  const [resetParams] = useState(readResetParams);
  const [authView, setAuthView] = useState<AuthMode | "landing">(resetParams ? "reset" : "landing");
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function goTo(nextPath: string) {
    if (window.location.pathname !== nextPath) {
      window.history.pushState({}, "", nextPath);
    }
    setPath(nextPath);
  }

  const isAppRoute = path.startsWith(APP_PATH);

  // authStatus starts "checking" while the initial /api/v1/users/me/ call is
  // in flight, so we don't flash the wrong screen before we know whether the
  // visitor is actually logged in.
  if (workspace.authStatus === "checking") return null;

  const authenticated = workspace.authStatus === "authenticated";

  if (isAppRoute && !authenticated) {
    goTo("/");
    return null;
  }

  if (!isAppRoute) {
    if (authView === "landing") {
      return (
        <Landing
          authenticated={authenticated}
          onEnterApp={() => goTo(APP_PATH)}
          onShowLogin={() => setAuthView("login")}
          onShowRegister={() => setAuthView("register")}
        />
      );
    }
    return (
      <Auth
        mode={authView}
        onModeChange={setAuthView}
        onBack={() => setAuthView("landing")}
        // Just refresh auth state and return to the (now-authenticated)
        // landing page — entering the app itself is a separate, explicit
        // click on the "Open workspace" button it now shows.
        onSuccess={() => workspace.checkAuth().then(() => setAuthView("landing"))}
        resetUid={resetParams?.uid}
        resetToken={resetParams?.token}
      />
    );
  }

  return <Layout workspace={workspace} onLoggedOut={() => goTo("/")} />;
}
