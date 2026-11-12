import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router";

import { keys } from "../../app/queries";
import { api } from "../../lib/api";
import { type DateGroup, dateGroup, groupLabel } from "../../lib/format";
import { t } from "../../lib/strings";
import { useAuth } from "../auth/useAuth";
import {
  deleteGuestConversation,
  getGuestConversation,
  listGuestConversations,
  saveGuestConversation,
} from "../guest/guestStore";

interface Item {
  id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  updatedAt: string;
}

interface SidebarProps {
  open: boolean;
  onClose(): void;
}

const GROUP_ORDER: DateGroup[] = ["today", "week", "month", "older"];

export function Sidebar({ open, onClose }: SidebarProps) {
  const { user, isAdmin, signOut } = useAuth();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const deferredSearch = useDeferredValue(search.trim());
  const isGuest = !user;

  const server = useInfiniteQuery({
    queryKey: keys.conversations(deferredSearch, archived),
    queryFn: ({ pageParam }) =>
      api.listConversations({ query: deferredSearch, archived, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !isGuest,
  });
  const guest = useQuery({
    queryKey: keys.guestConversations,
    queryFn: listGuestConversations,
    enabled: isGuest,
  });

  const items: Item[] = useMemo(() => {
    if (isGuest) {
      const needle = deferredSearch.toLowerCase();
      return (guest.data ?? [])
        .filter((c) => c.messages.length > 0)
        .filter(
          (c) =>
            !needle ||
            c.title.toLowerCase().includes(needle) ||
            c.messages.some((m) => m.content.toLowerCase().includes(needle)),
        )
        .map((c) => ({
          id: c.id,
          title: c.title,
          pinned: false,
          archived: false,
          updatedAt: c.updatedAt,
        }));
    }
    return (server.data?.pages ?? []).flatMap((page) =>
      page.conversations.map((c) => ({
        id: c.id,
        title: c.title,
        pinned: c.pinned,
        archived: c.archived,
        updatedAt: c.updated_at,
      })),
    );
  }, [deferredSearch, guest.data, isGuest, server.data]);

  const groups = useMemo(() => {
    const pinned = items.filter((i) => i.pinned);
    const byDate = new Map<DateGroup, Item[]>();
    for (const item of items.filter((i) => !i.pinned)) {
      const group = dateGroup(item.updatedAt);
      byDate.set(group, [...(byDate.get(group) ?? []), item]);
    }
    const result: Array<{ label: string; items: Item[] }> = [];
    if (pinned.length) result.push({ label: t.sidebar.pinned, items: pinned });
    for (const group of GROUP_ORDER) {
      const list = byDate.get(group);
      if (list?.length) result.push({ label: groupLabel[group], items: list });
    }
    return result;
  }, [items]);

  const loading = isGuest ? guest.isLoading : server.isLoading;

  return (
    <aside className={`sidebar${open ? " is-open" : ""}`} aria-label="Chats">
      <div className="sidebar-top">
        <Link to="/" className="brand" onClick={onClose}>
          <span className="brand-mark" aria-hidden="true">
            +
          </span>
          {t.appName}
        </Link>
        <button type="button" className="icon-button sidebar-close" onClick={onClose}>
          <span aria-hidden="true">×</span>
          <span className="sr-only">{t.sidebar.collapse}</span>
        </button>
      </div>
      <button
        type="button"
        className="new-chat"
        onClick={() => {
          setArchived(false);
          navigate("/");
          onClose();
        }}
      >
        <span aria-hidden="true">＋</span> {t.sidebar.newChat}
      </button>
      <div className="sidebar-search">
        <label htmlFor="chat-search" className="sr-only">
          {t.sidebar.search}
        </label>
        <input
          id="chat-search"
          type="search"
          placeholder={t.sidebar.search}
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <nav className="conversation-list">
        {archived && (
          <button type="button" className="link-button" onClick={() => setArchived(false)}>
            ← {t.sidebar.backToChats}
          </button>
        )}
        {loading && <p className="muted small">{t.common.loading}</p>}
        {!loading && items.length === 0 && (
          <p className="muted small">{deferredSearch ? t.sidebar.noMatches : t.sidebar.empty}</p>
        )}
        {groups.map((group) => (
          <section key={group.label}>
            <h2 className="group-label">{group.label}</h2>
            <ul>
              {group.items.map((item) => (
                <ConversationItem key={item.id} item={item} isGuest={isGuest} onOpen={onClose} />
              ))}
            </ul>
          </section>
        ))}
        {!isGuest && server.hasNextPage && (
          <button
            type="button"
            className="link-button"
            disabled={server.isFetchingNextPage}
            onClick={() => void server.fetchNextPage()}
          >
            {t.sidebar.loadMore}
          </button>
        )}
      </nav>

      <div className="sidebar-footer">
        {!isGuest && !archived && (
          <button type="button" className="link-button small" onClick={() => setArchived(true)}>
            {t.sidebar.archived}
          </button>
        )}
        {user ? (
          <div className="account">
            <div className="avatar" aria-hidden="true">
              {(user.display_name || user.email).charAt(0).toUpperCase()}
            </div>
            <div className="account-name">
              <span>{user.display_name || user.email}</span>
              {user.display_name && <span className="muted small">{user.email}</span>}
            </div>
            <div className="account-links">
              <Link to="/settings" onClick={onClose}>
                {t.common.settings}
              </Link>
              {isAdmin && (
                <Link to="/admin" onClick={onClose}>
                  {t.common.admin}
                </Link>
              )}
              <button
                type="button"
                className="link-button"
                onClick={() => void signOut()}
              >
                {t.common.signOut}
              </button>
            </div>
          </div>
        ) : (
          <div className="guest-box">
            <p className="small">{t.sidebar.guestNote}</p>
            <div className="form-row">
              <Link className="button button-small" to="/login" onClick={onClose}>
                {t.common.signIn}
              </Link>
              <Link className="button button-small button-ghost" to="/signup" onClick={onClose}>
                {t.common.signUp}
              </Link>
            </div>
          </div>
        )}
        <Link to="/privacy" className="muted small" onClick={onClose}>
          {t.common.privacy}
        </Link>
      </div>
    </aside>
  );
}

function ConversationItem({
  item,
  isGuest,
  onOpen,
}: {
  item: Item;
  isGuest: boolean;
  onOpen(): void;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const { id: currentId } = useParams();
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(item.title);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event instanceof MouseEvent && menuRef.current?.contains(event.target as Node)) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menuOpen]);

  const refresh = async () => {
    await client.invalidateQueries({
      queryKey: isGuest ? keys.guestConversations : keys.allConversations,
    });
    await client.invalidateQueries({
      queryKey: isGuest ? keys.guestConversation(item.id) : keys.conversation(item.id),
    });
  };

  const change = async (body: { title?: string; pinned?: boolean; archived?: boolean }) => {
    setMenuOpen(false);
    if (isGuest) {
      const conversation = await getGuestConversation(item.id);
      if (conversation && body.title) {
        await saveGuestConversation({ ...conversation, title: body.title });
      }
    } else {
      await api.updateConversation(item.id, body);
    }
    await refresh();
    if (body.archived && currentId === item.id) navigate("/");
  };

  const remove = async () => {
    setMenuOpen(false);
    if (!window.confirm(t.sidebar.deleteConfirm)) return;
    if (isGuest) await deleteGuestConversation(item.id);
    else await api.deleteConversation(item.id);
    await refresh();
    if (currentId === item.id) navigate("/");
  };

  const saveTitle = async () => {
    const value = title.trim();
    setRenaming(false);
    if (value && value !== item.title) await change({ title: value });
    else setTitle(item.title);
  };

  return (
    <li className="conversation-item">
      {renaming ? (
        <input
          className="rename-input"
          aria-label={t.sidebar.rename}
          value={title}
          maxLength={200}
          autoFocus
          onChange={(event) => setTitle(event.target.value)}
          onBlur={() => void saveTitle()}
          onKeyDown={(event) => {
            if (event.key === "Enter") void saveTitle();
            if (event.key === "Escape") {
              setTitle(item.title);
              setRenaming(false);
            }
          }}
        />
      ) : (
        <NavLink
          to={`/c/${item.id}`}
          className={({ isActive }) => `conversation-link${isActive ? " is-active" : ""}`}
          onClick={onOpen}
          title={item.title}
        >
          {item.pinned && <span aria-hidden="true">📌 </span>}
          {item.title}
        </NavLink>
      )}
      <div className="menu" ref={menuRef}>
        <button
          type="button"
          className="icon-button menu-button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span aria-hidden="true">⋯</span>
          <span className="sr-only">
            {t.sidebar.options}: {item.title}
          </span>
        </button>
        {menuOpen && (
          <div className="menu-popover" role="menu">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setRenaming(true);
              }}
            >
              {t.sidebar.rename}
            </button>
            {!isGuest && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => void change({ pinned: !item.pinned })}
                >
                  {item.pinned ? t.sidebar.unpin : t.sidebar.pin}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => void change({ archived: !item.archived })}
                >
                  {item.archived ? t.sidebar.unarchive : t.sidebar.archive}
                </button>
              </>
            )}
            <button type="button" role="menuitem" className="danger" onClick={() => void remove()}>
              {t.common.delete}
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
