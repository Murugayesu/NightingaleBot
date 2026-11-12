import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import type { AdminUser, TemporaryPassword } from "../../lib/types";
import { ErrorNote } from "./AdminPage";

const USERS_KEY = [...keys.admin, "users"];

export function UsersPanel({ currentUserId }: { currentUserId: string }) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const deferred = useDeferredValue(search.trim());
  const [temporary, setTemporary] = useState<TemporaryPassword | null>(null);

  const users = useQuery({
    queryKey: [...USERS_KEY, deferred],
    queryFn: () => api.admin.users({ query: deferred }),
  });
  const refresh = () => client.invalidateQueries({ queryKey: USERS_KEY });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: "user" | "admin"; disabled?: boolean } }) =>
      api.admin.updateUser(id, body),
    onSuccess: () => void refresh(),
  });
  const reset = useMutation({
    mutationFn: (id: string) => api.admin.resetPassword(id),
    onSuccess: (result) => {
      setTemporary(result);
      void refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.admin.deleteUser(id),
    onSuccess: () => void refresh(),
  });

  const confirmReset = (user: AdminUser) => {
    if (
      window.confirm(
        `Reset the password of ${user.email}? They will be signed out everywhere and must choose a new password at their next sign-in.`,
      )
    ) {
      setTemporary(null);
      reset.mutate(user.id);
    }
  };

  return (
    <div className="admin-panel">
      <ErrorNote error={update.error ?? reset.error ?? remove.error ?? users.error} />
      {temporary && (
        <section className="card card-highlight" role="status">
          <h2>Temporary password for {temporary.email}</h2>
          <p className="temp-password">
            <code>{temporary.temporary_password}</code>
          </p>
          <p className="muted small">{temporary.note}</p>
          <button type="button" className="button button-small" onClick={() => setTemporary(null)}>
            I have given it to them
          </button>
        </section>
      )}
      <section className="card">
        <div className="card-header">
          <h2>Accounts ({users.data?.total ?? 0})</h2>
          <div>
            <label htmlFor="user-search" className="sr-only">
              Search by e-mail
            </label>
            <input
              id="user-search"
              type="search"
              placeholder="Search by e-mail"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>
        <p className="muted small">
          There is no e-mail server, so forgotten passwords are reset here: the user gets a
          one-time temporary password from you, in person or by phone.
        </p>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">E-mail</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Chats</th>
                <th scope="col">Joined</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.data?.users.map((user) => {
                const self = user.id === currentUserId;
                return (
                  <tr key={user.id}>
                    <td className="wrap">
                      {user.email}
                      {user.display_name && <span className="muted small block">{user.display_name}</span>}
                    </td>
                    <td>{user.role}</td>
                    <td>
                      {user.disabled ? (
                        <span className="pill pill-failed">disabled</span>
                      ) : user.must_change_password ? (
                        <span className="pill pill-pending">reset pending</span>
                      ) : (
                        <span className="pill pill-completed">active</span>
                      )}
                    </td>
                    <td>{user.conversations}</td>
                    <td>{formatDateTime(user.created_at)}</td>
                    <td>{formatDateTime(user.last_login_at)}</td>
                    <td className="actions">
                      {self ? (
                        <span className="muted small">you</span>
                      ) : (
                        <>
                          <button type="button" className="link-button" onClick={() => confirmReset(user)}>
                            Reset password
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={() =>
                              update.mutate({
                                id: user.id,
                                body: { role: user.role === "admin" ? "user" : "admin" },
                              })
                            }
                          >
                            {user.role === "admin" ? "Make user" : "Make admin"}
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => update.mutate({ id: user.id, body: { disabled: !user.disabled } })}
                          >
                            {user.disabled ? "Enable" : "Disable"}
                          </button>
                          <button
                            type="button"
                            className="link-button danger"
                            onClick={() => {
                              if (window.confirm(`Delete ${user.email} and all their chats? This cannot be undone.`)) {
                                remove.mutate(user.id);
                              }
                            }}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
