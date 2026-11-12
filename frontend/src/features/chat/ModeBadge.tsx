import { t } from "../../lib/strings";
import type { AnswerMode } from "../../lib/types";

const ICONS: Record<AnswerMode, string> = {
  grounded: "✓",
  general: "i",
  declined: "–",
  refused: "!",
  out_of_scope: "–",
};

/** How an answer was produced; general knowledge is always visibly labelled. */
export function ModeBadge({ mode, label }: { mode: AnswerMode; label: string | null }) {
  const text = mode === "general" ? (label ?? t.modes.general) : t.modes[mode];
  return (
    <div className={`mode-badge mode-${mode}`} role="note">
      <span className="mode-icon" aria-hidden="true">
        {ICONS[mode]}
      </span>
      <span>{text}</span>
    </div>
  );
}
