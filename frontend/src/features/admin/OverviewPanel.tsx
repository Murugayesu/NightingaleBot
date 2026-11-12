import { useQuery } from "@tanstack/react-query";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { t } from "../../lib/strings";
import { ErrorNote } from "./AdminPage";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export function OverviewPanel() {
  const health = useQuery({ queryKey: [...keys.admin, "health"], queryFn: api.admin.health });
  const stats = useQuery({ queryKey: [...keys.admin, "stats"], queryFn: api.admin.stats });

  return (
    <div className="admin-panel">
      <ErrorNote error={health.error ?? stats.error} />
      {health.data && (
        <section className="card">
          <h2>
            System health{" "}
            <span className={`pill pill-${health.data.status === "ok" ? "ok" : "warn"}`}>
              {health.data.status}
            </span>
          </h2>
          <dl className="details">
            <dt>Database</dt>
            <dd>{health.data.database}</dd>
            <dt>Object storage</dt>
            <dd>{health.data.storage}</dd>
            <dt>Job queue</dt>
            <dd>{health.data.queue}</dd>
            <dt>Answer model</dt>
            <dd>{health.data.llm_model}</dd>
            <dt>Embedding model</dt>
            <dd>
              {health.data.embedding_model}
              {health.data.stale_embedding_chunks > 0 &&
                ` (${health.data.stale_embedding_chunks} chunks need re-indexing)`}
            </dd>
            <dt>Environment</dt>
            <dd>{health.data.environment}</dd>
          </dl>
        </section>
      )}
      {stats.data && (
        <>
          <section className="stats-grid">
            <Stat label="Users" value={stats.data.total_users} />
            <Stat label="Conversations" value={stats.data.total_conversations} />
            <Stat label="Guest sessions" value={stats.data.total_sessions} />
            <Stat label="Documents" value={stats.data.total_documents} />
            <Stat label="Indexed chunks" value={stats.data.total_chunks} />
            <Stat label="Tokens today" value={stats.data.tokens_today.toLocaleString()} />
          </section>
          <section className="card">
            <h2>Answers in the last 7 days (signed-in users)</h2>
            {Object.keys(stats.data.answers_last_7_days).length === 0 ? (
              <p className="muted">No answers yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">How it was answered</th>
                    <th scope="col">Answers</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats.data.answers_last_7_days).map(([mode, count]) => (
                    <tr key={mode}>
                      <td>{t.modes[mode as keyof typeof t.modes] ?? mode}</td>
                      <td>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
