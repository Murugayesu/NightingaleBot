import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import type { DocumentItem } from "../../lib/types";
import { ErrorNote } from "./AdminPage";

const DOC_KEY = [...keys.admin, "documents"];
const ACCEPT = ".pdf,.docx,.txt,.text,.md,.markdown,.csv,.tsv,.xlsx,.json";

export function DocumentsPanel() {
  const client = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [docType, setDocType] = useState("general");
  const [chunksOf, setChunksOf] = useState<DocumentItem | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const documents = useQuery({
    queryKey: [...DOC_KEY, statusFilter],
    queryFn: () => api.admin.documents({ status: statusFilter || undefined, limit: 100 }),
    // Keep polling while something is still being processed.
    refetchInterval: (query) =>
      query.state.data?.documents.some((d) => d.status === "pending" || d.status === "processing")
        ? 3000
        : false,
  });
  const chunks = useQuery({
    queryKey: [...DOC_KEY, "chunks", chunksOf?.id],
    queryFn: () => api.admin.chunks(chunksOf!.id),
    enabled: chunksOf !== null,
  });
  const refresh = () => client.invalidateQueries({ queryKey: DOC_KEY });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const results = [];
      for (const file of files) results.push(await api.admin.uploadDocument(file, docType));
      return results;
    },
    onSuccess: (results) => {
      setMessage(`Uploaded ${results.length} file(s). Indexing runs in the background.`);
      if (fileRef.current) fileRef.current.value = "";
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteDocument(id),
    onSuccess: () => void refresh(),
  });
  const reingest = useMutation({
    mutationFn: (id: string) => api.admin.reingestDocument(id),
    onSuccess: () => void refresh(),
  });
  const reindex = useMutation({
    mutationFn: api.admin.reindexAll,
    onSuccess: (result) => {
      setMessage(`Re-indexing ${result.queued} document(s).`);
      void refresh();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const files = Array.from(fileRef.current?.files ?? []);
    if (files.length) upload.mutate(files);
  };

  return (
    <div className="admin-panel">
      <ErrorNote
        error={upload.error ?? remove.error ?? reingest.error ?? reindex.error ?? documents.error}
      />
      {message && (
        <div className="banner banner-success" role="status">
          {message}
        </div>
      )}
      <section className="card">
        <h2>Upload official documents</h2>
        <form className="form form-inline" onSubmit={submit}>
          <label htmlFor="doc-file">Files (PDF, Word, text, CSV, Excel; up to 50 MB each)</label>
          <input id="doc-file" ref={fileRef} type="file" accept={ACCEPT} multiple required />
          <label htmlFor="doc-type">Document type</label>
          <input
            id="doc-type"
            value={docType}
            maxLength={64}
            onChange={(event) => setDocType(event.target.value)}
          />
          <button type="submit" className="button" disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Upload"}
          </button>
        </form>
      </section>

      <section className="card">
        <div className="card-header">
          <h2>Documents ({documents.data?.total ?? 0})</h2>
          <div className="form-row">
            <label htmlFor="doc-status" className="sr-only">
              Filter by status
            </label>
            <select
              id="doc-status"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="">All statuses</option>
              <option value="completed">Completed</option>
              <option value="pending">Pending</option>
              <option value="processing">Processing</option>
              <option value="failed">Failed</option>
            </select>
            <button
              type="button"
              className="button button-ghost button-small"
              disabled={reindex.isPending}
              onClick={() => {
                if (window.confirm("Re-index every document? This re-embeds everything.")) {
                  reindex.mutate();
                }
              }}
            >
              Re-index all
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Type</th>
                <th scope="col">Status</th>
                <th scope="col">Chunks</th>
                <th scope="col">Added</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {documents.data?.documents.map((doc) => (
                <tr key={doc.id}>
                  <td className="wrap">{doc.filename}</td>
                  <td>{doc.doc_type}</td>
                  <td>
                    <span className={`pill pill-${doc.status}`}>{doc.status}</span>
                    {doc.error_message && (
                      <span className="muted small block">{doc.error_message}</span>
                    )}
                  </td>
                  <td>{doc.chunk_count}</td>
                  <td>{formatDateTime(doc.created_at)}</td>
                  <td className="actions">
                    <button type="button" className="link-button" onClick={() => setChunksOf(doc)}>
                      Chunks
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => reingest.mutate(doc.id)}
                    >
                      Re-ingest
                    </button>
                    <button
                      type="button"
                      className="link-button danger"
                      onClick={() => {
                        if (window.confirm(`Delete ${doc.filename}?`)) remove.mutate(doc.id);
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {chunksOf && (
        <section className="card">
          <div className="card-header">
            <h2>Chunks of {chunksOf.filename}</h2>
            <button type="button" className="button button-ghost button-small" onClick={() => setChunksOf(null)}>
              Close
            </button>
          </div>
          <ErrorNote error={chunks.error} />
          <ol className="chunk-list">
            {chunks.data?.map((chunk) => (
              <li key={chunk.id}>
                <p className="muted small">
                  #{chunk.chunk_index}
                  {chunk.page_number !== null && ` · page ${chunk.page_number}`} ·{" "}
                  {chunk.token_count} tokens
                  {chunk.heading_hierarchy.length > 0 && ` · ${chunk.heading_hierarchy.join(" › ")}`}
                </p>
                <pre className="chunk-text">{chunk.content}</pre>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
