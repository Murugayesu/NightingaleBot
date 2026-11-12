import { useEffect } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router";

import { t } from "../../lib/strings";
import { useAuth } from "../auth/useAuth";
import { DocumentsPanel } from "./DocumentsPanel";
import { EvaluationPanel } from "./EvaluationPanel";
import { FeedbackPanel } from "./FeedbackPanel";
import { OverviewPanel } from "./OverviewPanel";
import { UsersPanel } from "./UsersPanel";

const TABS = [
  { to: "/admin", label: "Overview", end: true },
  { to: "/admin/documents", label: "Documents", end: false },
  { to: "/admin/evaluation", label: "Evaluation", end: false },
  { to: "/admin/users", label: "Users", end: false },
  { to: "/admin/feedback", label: "Feedback", end: false },
];

export function AdminPage() {
  const { user, isAdmin, isLoading } = useAuth();
  useEffect(() => {
    document.title = `${t.common.admin} · ${t.appName}`;
  }, []);

  if (isLoading) return <div className="page-loading">{t.common.loading}</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: "/admin" }} />;
  if (!isAdmin) {
    return (
      <div className="empty-state">
        <h1>Admins only</h1>
        <p>Your account does not have access to the admin area.</p>
      </div>
    );
  }

  return (
    <div className="page page-wide">
      <h1>{t.common.admin}</h1>
      <nav className="tabs" aria-label="Admin sections">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => `tab${isActive ? " is-active" : ""}`}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<OverviewPanel />} />
        <Route path="documents" element={<DocumentsPanel />} />
        <Route path="evaluation" element={<EvaluationPanel />} />
        <Route path="users" element={<UsersPanel currentUserId={user.id} />} />
        <Route path="feedback" element={<FeedbackPanel />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : t.common.somethingWrong;
  return (
    <div className="banner banner-error" role="alert">
      {message}
    </div>
  );
}
