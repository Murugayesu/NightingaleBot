import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { formatDateTime, percent } from "../../lib/format";
import { ErrorNote } from "./AdminPage";

const EVAL_KEY = [...keys.admin, "eval"];

// Rates shown as percentages; everything else as a plain value.
const RATE_METRICS = new Set([
  "hit_rate",
  "mrr",
  "answerable_gate_pass_rate",
  "unanswerable_gate_block_rate",
  "pass_rate",
  "answer_pass_rate",
  "abstain_pass_rate",
  "refuse_pass_rate",
  "general_pass_rate",
  "out_of_scope_pass_rate",
  "routing_accuracy",
  "general_label_rate",
  "mean_fact_recall",
  "faithfulness",
  "answer_relevancy",
]);

function MetricValue({ name, value }: { name: string; value: unknown }) {
  if (RATE_METRICS.has(name)) return <>{percent(value)}</>;
  if (value && typeof value === "object") return <code>{JSON.stringify(value)}</code>;
  return <>{value === null || value === undefined ? "—" : String(value)}</>;
}

export function EvaluationPanel() {
  const client = useQueryClient();
  const [judge, setJudge] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: EVAL_KEY,
    queryFn: api.admin.evalRuns,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "queued" || r.status === "running")
        ? 4000
        : false,
  });
  const detail = useQuery({
    queryKey: [...EVAL_KEY, selected],
    queryFn: () => api.admin.evalRun(selected!),
    enabled: selected !== null,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "queued") ? 2000 : false,
  });
  const start = useMutation({
    mutationFn: (mode: "retrieval" | "answer") => api.admin.startEval(mode, judge),
    onSuccess: (run) => {
      setSelected(run.id);
      void client.invalidateQueries({ queryKey: EVAL_KEY });
    },
  });

  const failures = (detail.data?.results ?? []).filter((r) => !r.passed);

  return (
    <div className="admin-panel">
      <ErrorNote error={start.error ?? runs.error ?? detail.error} />
      <section className="card">
        <h2>Run the evaluation set</h2>
        <p className="muted small">
          Retrieval is cheap (no model calls). The answer run asks every question through the
          full pipeline and checks grounding, routing, labels and official-fact leaks.
        </p>
        <div className="form-row">
          <button type="button" className="button" disabled={start.isPending} onClick={() => start.mutate("retrieval")}>
            Run retrieval eval
          </button>
          <button type="button" className="button button-ghost" disabled={start.isPending} onClick={() => start.mutate("answer")}>
            Run answer eval
          </button>
          <label className="checkbox">
            <input type="checkbox" checked={judge} onChange={(event) => setJudge(event.target.checked)} />
            <span>Add LLM-judge scores (answer runs; more model calls)</span>
          </label>
        </div>
      </section>

      <section className="card">
        <h2>Recent runs</h2>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Started</th>
                <th scope="col">Mode</th>
                <th scope="col">Status</th>
                <th scope="col">Headline</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.data?.map((run) => (
                <tr key={run.id} className={run.id === selected ? "is-selected" : undefined}>
                  <td>{formatDateTime(run.created_at)}</td>
                  <td>{run.mode}</td>
                  <td>
                    <span className={`pill pill-${run.status}`}>{run.status}</span>
                  </td>
                  <td>
                    {run.mode === "retrieval"
                      ? `hit rate ${percent(run.metrics.hit_rate)}`
                      : `pass rate ${percent(run.metrics.pass_rate)}`}
                  </td>
                  <td>
                    <button type="button" className="link-button" onClick={() => setSelected(run.id)}>
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {detail.data && (
        <section className="card">
          <div className="card-header">
            <h2>
              {detail.data.mode} run · {formatDateTime(detail.data.created_at)}
            </h2>
            <button type="button" className="button button-ghost button-small" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          {detail.data.error && <p className="banner banner-error">{detail.data.error}</p>}
          <dl className="details">
            {Object.entries(detail.data.metrics).map(([name, value]) => (
              <div key={name} className="detail-row">
                <dt>{name.replace(/_/g, " ")}</dt>
                <dd>
                  <MetricValue name={name} value={value} />
                </dd>
              </div>
            ))}
          </dl>
          {failures.length > 0 && (
            <>
              <h3>Failures ({failures.length})</h3>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Case</th>
                      <th scope="col">Question</th>
                      <th scope="col">Expected</th>
                      <th scope="col">What happened</th>
                    </tr>
                  </thead>
                  <tbody>
                    {failures.map((row) => (
                      <tr key={String(row.case_id)}>
                        <td>{String(row.case_id)}</td>
                        <td className="wrap">{String(row.question ?? "")}</td>
                        <td>{String(row.expected_behavior ?? "")}</td>
                        <td className="wrap">{String(row.detail ?? "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
