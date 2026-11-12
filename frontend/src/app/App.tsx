import { Component, type ReactNode, useEffect, useState } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router";

import { AdminPage } from "../features/admin/AdminPage";
import {
  ChangePasswordPage,
  LoginPage,
  SignupPage,
} from "../features/auth/AuthPages";
import { useAuth } from "../features/auth/useAuth";
import { ChatPage } from "../features/chat/ChatPage";
import { ImportBanner } from "../features/guest/ImportBanner";
import { PrivacyPage } from "../features/settings/PrivacyPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { Sidebar } from "../features/sidebar/Sidebar";
import { t } from "../lib/strings";
import { useConfig } from "./queries";

/** Sidebar + main area; on small screens the sidebar is a drawer. */
function Shell() {
  const { user } = useAuth();
  const config = useConfig();
  const navigate = useNavigate();
  const location = useLocation();
  // The drawer belongs to the page it was opened on: navigating closes it.
  const [openedOn, setOpenedOn] = useState<string | null>(null);
  const sidebarOpen = openedOn === location.pathname;
  const setSidebarOpen = (open: boolean) => setOpenedOn(open ? location.pathname : null);

  // Ctrl/Cmd + Shift + O starts a new chat (as in other chat apps).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "o") {
        event.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  if (user?.must_change_password) return <Navigate to="/change-password" replace />;

  return (
    <div className="layout">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      {sidebarOpen && (
        <div className="scrim" aria-hidden="true" onClick={() => setSidebarOpen(false)} />
      )}
      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-button"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
          >
            <span aria-hidden="true">☰</span>
            <span className="sr-only">{t.sidebar.expand}</span>
          </button>
          <span className="topbar-title">{config.data?.knowledge_base_name ?? t.appName}</span>
          <button type="button" className="icon-button" onClick={() => navigate("/")}>
            <span aria-hidden="true">＋</span>
            <span className="sr-only">{t.sidebar.newChat}</span>
          </button>
        </header>
        {config.isError && (
          <div className="banner banner-error" role="alert">
            {t.common.somethingWrong} The service may be starting up; reload in a moment.
          </div>
        )}
        {user && <ImportBanner />}
        <main id="main" className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="empty-state">
          <h1>{t.common.somethingWrong}</h1>
          <button type="button" className="button" onClick={() => window.location.reload()}>
            {t.common.retry}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/change-password" element={<ChangePasswordPage />} />
        <Route element={<Shell />}>
          <Route index element={<ChatPage />} />
          <Route path="c/:id" element={<ChatPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="admin/*" element={<AdminPage />} />
          <Route path="privacy" element={<PrivacyPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
