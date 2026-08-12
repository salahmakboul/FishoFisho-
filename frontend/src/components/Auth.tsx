import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";
import { Button } from "./Button";
import styles from "./Auth.module.css";

export type AuthMode = "login" | "register" | "forgot" | "reset";

interface AuthProps {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  /** Returns to the public landing page without signing in. */
  onBack: () => void;
  /** Called after a successful login/register/reset-confirm call — re-runs
   * the same getMe()-based auth check useWorkspace uses on mount, which is
   * what actually flips the app into the authenticated workspace. */
  onSuccess: () => Promise<unknown>;
  /** uid/token pulled from the ?uid=&token= query string on the password
   * reset link (see App.tsx). Only meaningful when mode === "reset". */
  resetUid?: string;
  resetToken?: string;
}

/**
 * Combined login/register/forgot-password/reset-password screen shown in
 * place of <Landing/> once a visitor picks "Log in" or "Get started" (see
 * App.tsx), or lands on a password-reset link — a mode toggle rather than
 * separate pages, matching this project's stated preference for fewer
 * files. Visual language matches Landing.tsx: same tokens, same Button
 * component, no color beyond the monochrome scale.
 */
export function Auth({ mode, onModeChange, onBack, onSuccess, resetUid, resetToken }: AuthProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  // Set once a "forgot password" request succeeds — swaps the request form
  // out for a confirmation message instead of navigating anywhere, since
  // there's nothing else useful to show on this screen.
  const [resetRequested, setResetRequested] = useState(false);

  // Clear transient form state when switching between modes so e.g. a
  // register field error doesn't linger after flipping to login.
  useEffect(() => {
    setPassword("");
    setPassword2("");
    setFormError(null);
    setFieldErrors({});
    setResetRequested(false);
  }, [mode]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setFormError(null);
    setFieldErrors({});
    try {
      if (mode === "login") {
        await api.login(username.trim(), password, remember);
        await onSuccess();
      } else if (mode === "register") {
        await api.register(username.trim(), password, password2);
        await onSuccess();
      } else if (mode === "forgot") {
        await api.requestPasswordReset(username.trim());
        setResetRequested(true);
      } else {
        await api.confirmPasswordReset(resetUid ?? "", resetToken ?? "", password, password2);
        await onSuccess();
      }
    } catch (err) {
      if (err instanceof ApiError && err.fields) {
        setFieldErrors(err.fields);
      } else {
        setFormError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  const usernameError = fieldErrors.username?.[0];
  const password1Error = fieldErrors.password1?.[0];
  const password2Error = fieldErrors.password2?.[0];

  const titles: Record<AuthMode, string> = {
    login: "Welcome back",
    register: "Create your account",
    forgot: "Reset your password",
    reset: "Choose a new password",
  };
  const subtitles: Record<AuthMode, string> = {
    login: "Sign in to get back to your rooms and conversations.",
    register: "Set up your workspace in a few seconds.",
    forgot: "Enter your username and we'll send a reset link to the console log.",
    reset: "Enter a new password for your account.",
  };

  return (
    <div className={styles.page}>
      <header className={styles.nav}>
        <div className={styles.navInner}>
          <button type="button" className={styles.brand} onClick={onBack}>
            <span className={styles.brandMark} aria-hidden="true">F</span>
            <span className={styles.brandName}>FishoFisho</span>
          </button>
        </div>
      </header>

      <main className={styles.main}>
        <div className={styles.card}>
          <h1 className={styles.title}>{titles[mode]}</h1>
          <p className={styles.subtitle}>{subtitles[mode]}</p>

          {mode === "forgot" && resetRequested ? (
            <>
              <p className={styles.formError} role="status">
                If that account exists, a password reset link has been sent.
              </p>
              <p className={styles.switch}>
                <button type="button" className={styles.switchLink} onClick={() => onModeChange("login")}>
                  Back to sign in
                </button>
              </p>
            </>
          ) : (
            <form className={styles.form} onSubmit={handleSubmit} noValidate>
              {(mode === "login" || mode === "register" || mode === "forgot") && (
                <div className={styles.field}>
                  <label className={styles.label} htmlFor="auth-username">
                    Username
                  </label>
                  <input
                    id="auth-username"
                    className={styles.input}
                    type="text"
                    autoComplete="username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    required
                    aria-invalid={usernameError ? true : undefined}
                    aria-describedby={usernameError ? "auth-username-error" : undefined}
                  />
                  {usernameError && (
                    <span id="auth-username-error" className={styles.fieldError} role="alert">
                      {usernameError}
                    </span>
                  )}
                </div>
              )}

              {mode !== "forgot" && (
                <div className={styles.field}>
                  <label className={styles.label} htmlFor="auth-password">
                    {mode === "reset" ? "New password" : "Password"}
                  </label>
                  <input
                    id="auth-password"
                    className={styles.input}
                    type="password"
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    aria-invalid={password1Error ? true : undefined}
                    aria-describedby={password1Error ? "auth-password-error" : undefined}
                  />
                  {password1Error && (
                    <span id="auth-password-error" className={styles.fieldError} role="alert">
                      {password1Error}
                    </span>
                  )}
                </div>
              )}

              {(mode === "register" || mode === "reset") && (
                <div className={styles.field}>
                  <label className={styles.label} htmlFor="auth-password2">
                    Confirm password
                  </label>
                  <input
                    id="auth-password2"
                    className={styles.input}
                    type="password"
                    autoComplete="new-password"
                    value={password2}
                    onChange={(e) => setPassword2(e.target.value)}
                    required
                    aria-invalid={password2Error ? true : undefined}
                    aria-describedby={password2Error ? "auth-password2-error" : undefined}
                  />
                  {password2Error && (
                    <span id="auth-password2-error" className={styles.fieldError} role="alert">
                      {password2Error}
                    </span>
                  )}
                </div>
              )}

              {mode === "login" && (
                <label className={styles.remember}>
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                  />
                  Remember me
                </label>
              )}

              {formError && (
                <p className={styles.formError} role="alert">
                  {formError}
                </p>
              )}

              <Button type="submit" variant="primary" className={styles.submit} loading={loading}>
                {mode === "login" && "Sign in"}
                {mode === "register" && "Create account"}
                {mode === "forgot" && "Send reset link"}
                {mode === "reset" && "Set new password"}
              </Button>
            </form>
          )}

          {mode === "login" && (
            <p className={styles.switch}>
              <button type="button" className={styles.switchLink} onClick={() => onModeChange("forgot")}>
                Forgot password?
              </button>
            </p>
          )}

          {(mode === "login" || mode === "register") && (
            <p className={styles.switch}>
              {mode === "login" ? (
                <>
                  Don&apos;t have an account?{" "}
                  <button type="button" className={styles.switchLink} onClick={() => onModeChange("register")}>
                    Create one
                  </button>
                </>
              ) : (
                <>
                  Already have an account?{" "}
                  <button type="button" className={styles.switchLink} onClick={() => onModeChange("login")}>
                    Sign in
                  </button>
                </>
              )}
            </p>
          )}

          {mode === "forgot" && !resetRequested && (
            <p className={styles.switch}>
              <button type="button" className={styles.switchLink} onClick={() => onModeChange("login")}>
                Back to sign in
              </button>
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
