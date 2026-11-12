import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import { t } from "../../lib/strings";
import { ErrorNote } from "./AdminPage";

/**
 * Rated answers, without any user identity. "Not helpful" answers are the
 * best source of new evaluation cases (add them to eval/evalset.csv after a
 * human has checked the right answer).
 */
export function FeedbackPanel() {
  const [rating, setRating] = useState<number | null>(-1);
  const feedback = useQuery({
    queryKey: [...keys.admin, "feedback", rating],
    queryFn: () => api.admin.feedback({ rating }),
  });

  return (
    <div className="admin-panel">
      <ErrorNote error={feedback.error} />
      <section className="card">
        <div className="card-header">
          <h2>Answer feedback ({feedback.data?.total ?? 0})</h2>
          <div>
            <label htmlFor="rating-filter" className="sr-only">
              Rating
            </label>
            <select
              id="rating-filter"
              value={rating === null ? "" : String(rating)}
              onChange={(event) => setRating(event.target.value === "" ? null : Number(event.target.value))}
            >
              <option value="-1">Not helpful</option>
              <option value="1">Helpful</option>
              <option value="">All</option>
            </select>
          </div>
        </div>
        <p className="muted small">
          Users are not identified. Turn confirmed problems into evaluation cases.
        </p>
        {feedback.data?.items.length === 0 && <p className="muted">No feedback yet.</p>}
        <ul className="feedback-list">
          {feedback.data?.items.map((item) => (
            <li key={item.message_id} className="feedback-item">
              <p className="muted small">
                {item.rating === 1 ? "👍 Helpful" : "👎 Not helpful"} ·{" "}
                {item.answer_mode ? t.modes[item.answer_mode] : "—"} · {formatDateTime(item.created_at)}
              </p>
              <p>
                <strong>Question:</strong> {item.question}
              </p>
              <blockquote>{item.answer}</blockquote>
              {item.comment && (
                <p>
                  <strong>Comment:</strong> {item.comment}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
