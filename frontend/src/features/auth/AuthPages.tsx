import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";

import { useConfig } from "../../app/queries";
import { ApiError, api } from "../../lib/api";
import { t } from "../../lib/strings";
import { useAuth } from "./useAuth";

/** True when passwords would cross the network unencrypted. */
export function isInsecureConnection(location: Location = window.location): boolean {
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  return location.protocol === "http:" && !local;
}

export function HttpWarning() {
  if (!isInsecureConnection()) return null;
  return (
    <div className="banner banner-warning" role="note">
      {t.auth.httpWarning}
    </div>
  );
}

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const config = useConfig().data;
  useEffect(() => {
    document.title = `${title} · ${t.appName}`;
  }, [title]);
  return (
    <div className="auth-page">
      <div className="auth-card">
        <Link to="/" className="brand auth-brand">
          <span className="brand-mark" aria-hidden="true">
            +
          </span>
          {t.appName}
        </Link>
        {config && <p className="muted small">{config.knowledge_base_name}</p>}
        <h1>{title}</h1>
        <HttpWarning />
        {children}
      </div>
    </div>
  );
}

function errorText(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : t.common.somethingWrong;
}

function afterSignIn(mustChange: boolean, from: string | undefined): string {
  if (mustChange) return "/change-password";
  return from && from.startsWith("/") && !from.startsWith("//") ? from : "/";
}

export function LoginPage() {
  const { signIn, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user && !busy) return <Navigate to={afterSignIn(user.must_change_password, from)} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const signedIn = await signIn(email, password);
      navigate(afterSignIn(signedIn.must_change_password, from), { replace: true });
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title={t.auth.welcomeBack}>
      <form className="form" onSubmit={submit} noValidate>
        {error && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        <label htmlFor="login-email">{t.common.email}</label>
        <input
          id="login-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <label htmlFor="login-password">{t.common.password}</label>
        <input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <button type="submit" className="button button-wide" disabled={busy}>
          {busy ? t.common.loading : t.common.signIn}
        </button>
        <p className="muted small">{t.auth.forgot}</p>
      </form>
      <p className="auth-switch">
        {t.auth.noAccount} <Link to="/signup">{t.common.signUp}</Link>
      </p>
      <p className="auth-switch">
        <Link to="/">{t.auth.continueAsGuest}</Link>
      </p>
    </AuthCard>
  );
}

export function SignupPage() {
  const { signUp, user } = useAuth();
  const config = useConfig().data;
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const minLength = config?.password_min_length ?? 10;

  if (user && !busy) return <Navigate to="/" replace />;
  if (config && !config.signup_enabled) {
    return (
      <AuthCard title={t.auth.createTitle}>
        <p>New accounts are not being accepted at the moment.</p>
        <Link to="/login">{t.common.signIn}</Link>
      </AuthCard>
    );
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!accepted) {
      setError("Please read and accept the privacy notice to create an account.");
      return;
    }
    setBusy(true);
    try {
      await signUp({
        email,
        password,
        display_name: name.trim() || undefined,
        accept_privacy: accepted,
      });
      navigate("/", { replace: true });
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard title={t.auth.createTitle}>
      <form className="form" onSubmit={submit} noValidate>
        {error && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        <label htmlFor="signup-email">{t.common.email}</label>
        <input
          id="signup-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <label htmlFor="signup-name">{t.common.displayName}</label>
        <input
          id="signup-name"
          type="text"
          autoComplete="name"
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <label htmlFor="signup-password">{t.common.password}</label>
        <input
          id="signup-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={minLength}
          maxLength={128}
          aria-describedby="signup-password-help"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p id="signup-password-help" className="muted small">
          {t.auth.passwordHint(minLength)}
        </p>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(event) => setAccepted(event.target.checked)}
          />
          <span>
            {t.auth.acceptPrivacy}{" "}
            <Link to="/privacy" target="_blank">
              {t.common.privacy.toLowerCase()}
            </Link>
            .
          </span>
        </label>
        <button type="submit" className="button button-wide" disabled={busy}>
          {busy ? t.common.loading : t.common.signUp}
        </button>
      </form>
      <p className="auth-switch">
        {t.auth.haveAccount} <Link to="/login">{t.common.signIn}</Link>
      </p>
    </AuthCard>
  );
}

export function ChangePasswordForm({ onDone }: { onDone(): void }) {
  const config = useConfig().data;
  const { user, setUser } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const minLength = config?.password_min_length ?? 10;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (next !== repeat) {
      setError(t.auth.mismatch);
      return;
    }
    setBusy(true);
    try {
      await api.changePassword({ current_password: current, new_password: next });
      if (user) setUser({ ...user, must_change_password: false });
      setCurrent("");
      setNext("");
      setRepeat("");
      onDone();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate>
      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}
      <label htmlFor="pw-current">{t.auth.currentPassword}</label>
      <input
        id="pw-current"
        type="password"
        autoComplete="current-password"
        required
        value={current}
        onChange={(event) => setCurrent(event.target.value)}
      />
      <label htmlFor="pw-new">{t.auth.newPassword}</label>
      <input
        id="pw-new"
        type="password"
        autoComplete="new-password"
        required
        minLength={minLength}
        aria-describedby="pw-new-help"
        value={next}
        onChange={(event) => setNext(event.target.value)}
      />
      <p id="pw-new-help" className="muted small">
        {t.auth.passwordHint(minLength)}
      </p>
      <label htmlFor="pw-repeat">{t.auth.confirmPassword}</label>
      <input
        id="pw-repeat"
        type="password"
        autoComplete="new-password"
        required
        value={repeat}
        onChange={(event) => setRepeat(event.target.value)}
      />
      <button type="submit" className="button" disabled={busy}>
        {busy ? t.common.loading : t.settings.changePassword}
      </button>
    </form>
  );
}

export function ChangePasswordPage() {
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();
  if (isLoading) return <div className="page-loading">{t.common.loading}</div>;
  if (!user) return <Navigate to="/login" replace />;
  return (
    <AuthCard title={t.auth.changeTitle}>
      {user.must_change_password && <p>{t.auth.changeForced}</p>}
      <ChangePasswordForm onDone={() => navigate("/", { replace: true })} />
    </AuthCard>
  );
}
