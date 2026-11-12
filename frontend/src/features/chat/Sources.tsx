import { useEffect, useRef } from "react";

import { t } from "../../lib/strings";
import type { Citation } from "../../lib/types";

interface SourcesProps {
  citations: Citation[];
  highlighted: number | null;
  open: boolean;
  onToggle(): void;
}

/** The official documents behind a grounded answer, with page and excerpt. */
export function Sources({ citations, highlighted, open, onToggle }: SourcesProps) {
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (highlighted === null || !open) return;
    const item = listRef.current?.querySelector<HTMLElement>(`[data-source="${highlighted}"]`);
    item?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    item?.focus({ preventScroll: true });
  }, [highlighted, open]);

  if (!citations.length) return null;
  return (
    <div className="sources">
      <button type="button" className="sources-toggle" aria-expanded={open} onClick={onToggle}>
        {t.chat.sources} ({citations.length}) <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <ol className="sources-list" ref={listRef}>
          {citations.map((c) => (
            <li
              key={`${c.source_index}-${c.chunk_id}`}
              data-source={c.source_index}
              tabIndex={-1}
              className={highlighted === c.source_index ? "is-highlighted" : undefined}
            >
              <div className="source-title">
                <span className="source-number">{c.source_index}</span>
                <span>{c.title || "Document"}</span>
                {c.page_number !== null && (
                  <span className="source-page">
                    {t.chat.page} {c.page_number}
                  </span>
                )}
              </div>
              {c.snippet && <blockquote className="source-snippet">{c.snippet}</blockquote>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
