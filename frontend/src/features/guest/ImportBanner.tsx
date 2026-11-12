import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { keys } from "../../app/queries";
import { ApiError, api } from "../../lib/api";
import { t } from "../../lib/strings";
import { clearGuestConversations, listGuestConversations, toImportPayload } from "./guestStore";

const DISMISSED = "nb_import_dismissed";
const BATCH = 10;

/**
 * After signing in, offer to copy chats kept in this browser as a guest into
 * the account. Imported answers are flagged and not re-checked.
 */
export function ImportBanner() {
  const client = useQueryClient();
  const [dismissed, setDismissed] = useState(() => sessionStorage.getItem(DISMISSED) === "1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const guest = useQuery({
    queryKey: keys.guestConversations,
    queryFn: listGuestConversations,
    enabled: !dismissed,
  });
  const chats = (guest.data ?? []).filter((c) =>
    c.messages.some((m) => m.status === "complete" && m.content.trim()),
  );

  if (result) {
    return (
      <div className="banner banner-success" role="status">
        <span>{result}</span>
        <button type="button" className="banner-close" onClick={() => setResult(null)}>
          <span aria-hidden="true">×</span>
          <span className="sr-only">{t.common.close}</span>
        </button>
      </div>
    );
  }
  if (dismissed || chats.length === 0) return null;

  const skip = () => {
    sessionStorage.setItem(DISMISSED, "1");
    setDismissed(true);
  };

  const importAll = async () => {
    setBusy(true);
    try {
      let imported = 0;
      for (let i = 0; i < chats.length; i += BATCH) {
        const batch = chats.slice(i, i + BATCH).map(toImportPayload);
        imported += (await api.importConversations(batch)).imported;
      }
      await clearGuestConversations();
      await client.invalidateQueries({ queryKey: keys.guestConversations });
      await client.invalidateQueries({ queryKey: keys.allConversations });
      setResult(t.auth.importDone(imported));
    } catch (caught) {
      setResult(caught instanceof ApiError ? caught.message : t.common.somethingWrong);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="banner banner-info" role="region" aria-label={t.auth.importTitle}>
      <div>
        <strong>{t.auth.importTitle}</strong> {t.auth.importBody(chats.length)}
      </div>
      <div className="form-row">
        <button type="button" className="button button-small" disabled={busy} onClick={importAll}>
          {busy ? t.common.loading : t.auth.importAction}
        </button>
        <button type="button" className="button button-small button-ghost" onClick={skip}>
          {t.auth.importSkip}
        </button>
      </div>
    </div>
  );
}
