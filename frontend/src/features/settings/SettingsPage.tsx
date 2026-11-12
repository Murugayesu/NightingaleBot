import { type FormEvent, useEffect, useState } from "react";
import { Navigate } from "react-router";

import { useConfig } from "../../app/queries";
import { ApiError, api } from "../../lib/api";
import { t } from "../../lib/strings";
import { type ThemeChoice, saveTheme, storedTheme } from "../../lib/theme";
import type { User } from "../../lib/types";
import { ChangePasswordForm } from "../auth/AuthPages";
import { useAuth } from "../auth/useAuth";

export function SettingsPage() {
  const { user, isLoading } = useAuth();
  useEffect(() => {
    document.title = `${t.settings.title} · ${t.appName}`;
  }, []);
  if (isLoading) return <div className="page-loading">{t.common.loading}</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: "/settings" }} />;
  return <Settings user={user} />;
}

function Settings({ user }: { user: User }) {
  const { setUser } = useAuth();
  const config = useConfig().data;
  const [name, setName] = useState(user.display_name ?? "");
  const [theme, setTheme] = useState<ThemeChoice>(storedTheme);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletePassword, setDeletePassword] = useState("");

  const report = (caught: unknown) =>
    setError(caught instanceof ApiError ? caught.message : t.common.somethingWrong);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      const updated = await api.updateProfile({ display_name: name.trim() || null });
      setUser(updated);
      setNotice(t.settings.saved);
    } catch (caught) {
      report(caught);
    }
  };

  const download = async () => {
    setError(null);
    try {
      const blob = await api.exportData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "nursingbot-my-data.json";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      report(caught);
    }
  };

  const logoutAll = async () => {
    try {
      await api.logoutAll();
    } finally {
      window.location.assign("/login");
    }
  };

  const deleteAccount = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!window.confirm(t.settings.deleteHelp)) return;
    try {
      await api.deleteAccount(deletePassword);
      window.location.assign("/"); // a fresh start as a guest
    } catch (caught) {
      report(caught);
    }
  };

  return (
    <div className="page">
      <h1>{t.settings.title}</h1>
      {notice && (
        <div className="banner banner-success" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <section className="card">
        <h2>{t.settings.profile}</h2>
        <form className="form" onSubmit={saveProfile}>
          <label htmlFor="settings-email">{t.common.email}</label>
          <input id="settings-email" value={user.email} readOnly />
          <label htmlFor="settings-name">{t.common.displayName}</label>
          <input
            id="settings-name"
            maxLength={80}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit" className="button">
            {t.common.save}
          </button>
        </form>
        {config && <p className="muted small">{t.settings.usage(config.user_daily_token_quota)}</p>}
      </section>

      <section className="card">
        <h2>{t.settings.appearance}</h2>
        <fieldset className="radio-group">
          <legend>{t.settings.theme}</legend>
          {(["system", "light", "dark"] as const).map((choice) => (
            <label key={choice} className="radio">
              <input
                type="radio"
                name="theme"
                value={choice}
                checked={theme === choice}
                onChange={() => {
                  setTheme(choice);
                  saveTheme(choice);
                }}
              />
              {t.settings.themes[choice]}
            </label>
          ))}
        </fieldset>
      </section>

      <section className="card">
        <h2>{t.settings.security}</h2>
        <ChangePasswordForm onDone={() => setNotice(t.auth.changed)} />
        <hr />
        <p className="muted small">{t.settings.logoutAllHelp}</p>
        <button type="button" className="button button-ghost" onClick={() => void logoutAll()}>
          {t.settings.logoutAll}
        </button>
      </section>

      <section className="card">
        <h2>{t.settings.data}</h2>
        <p className="muted small">{t.settings.exportHelp}</p>
        <button type="button" className="button button-ghost" onClick={() => void download()}>
          {t.settings.export}
        </button>
        <hr />
        <h3 className="danger-title">{t.settings.deleteAccount}</h3>
        <p className="muted small">{t.settings.deleteHelp}</p>
        <form className="form" onSubmit={deleteAccount}>
          <label htmlFor="delete-password">{t.common.password}</label>
          <input
            id="delete-password"
            type="password"
            autoComplete="current-password"
            required
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
          />
          <button type="submit" className="button button-danger" disabled={!deletePassword}>
            {t.settings.deleteConfirm}
          </button>
        </form>
      </section>
    </div>
  );
}
