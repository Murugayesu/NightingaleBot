import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { keys, useConfig } from "../../app/queries";
import { ApiError, api } from "../../lib/api";
import { t } from "../../lib/strings";
import type { AppConfig } from "../../lib/types";
import { useAuth } from "../auth/useAuth";
import { newId, saveGuestConversation } from "../guest/guestStore";
import { Composer, type ComposerHandle } from "./Composer";
import { MessageView } from "./MessageView";
import { useChat } from "./useChat";

const SUGGESTIONS = [
  "How do I register with TNNMC online?",
  "What is needed to renew my nurse registration?",
  "Explain the five rights of medication administration.",
  "How can pressure ulcers be prevented?",
];

interface AskState {
  ask?: string;
}

export function ChatPage() {
  const { id } = useParams();
  const location = useLocation();
  const { user, isLoading } = useAuth();
  const config = useConfig().data;

  if (isLoading || !config) {
    return <div className="page-loading">{t.common.loading}</div>;
  }
  if (!user && !config.guest_chat_enabled) {
    return (
      <div className="empty-state">
        <h1>{config.knowledge_base_name}</h1>
        <p>{t.chat.signInToSave}</p>
        <Link className="button" to="/login">
          {t.common.signIn}
        </Link>
      </div>
    );
  }
  if (!id) return <NewChat config={config} isGuest={!user} />;
  return (
    <Conversation
      key={`${user ? "u" : "g"}-${id}`}
      id={id}
      isGuest={!user}
      config={config}
      initialQuestion={(location.state as AskState | null)?.ask}
    />
  );
}

function NewChat({ config, isGuest }: { config: AppConfig; isGuest: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const composer = useRef<ComposerHandle>(null);

  useEffect(() => {
    document.title = t.appName;
    composer.current?.focus();
  }, []);

  const start = async (question: string): Promise<boolean> => {
    setError(null);
    try {
      let id: string;
      if (isGuest) {
        id = newId();
        const now = new Date().toISOString();
        await saveGuestConversation({
          id,
          title: "New chat",
          createdAt: now,
          updatedAt: now,
          messages: [],
        });
        void client.invalidateQueries({ queryKey: keys.guestConversations });
      } else {
        id = (await api.createConversation()).id;
        void client.invalidateQueries({ queryKey: keys.allConversations });
      }
      navigate(`/c/${id}`, { state: { ask: question } satisfies AskState });
      return true;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t.common.somethingWrong);
      return false;
    }
  };

  return (
    <div className="chat">
      <div className="chat-scroll">
        <div className="welcome">
          <div className="welcome-mark" aria-hidden="true">
            +
          </div>
          <h1>{config.knowledge_base_name}</h1>
          <p>{config.welcome_message}</p>
          <ul className="suggestions">
            {SUGGESTIONS.map((suggestion) => (
              <li key={suggestion}>
                <button type="button" onClick={() => void start(suggestion)}>
                  {suggestion}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="chat-footer">
        {error && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        <Composer
          ref={composer}
          busy={false}
          maxChars={config.max_query_chars}
          onSend={start}
          onStop={() => undefined}
        />
        <p className="disclaimer">{t.chat.disclaimer}</p>
      </div>
    </div>
  );
}

interface ConversationProps {
  id: string;
  isGuest: boolean;
  config: AppConfig;
  initialQuestion?: string;
}

function Conversation({ id, isGuest, config, initialQuestion }: ConversationProps) {
  const chat = useChat(id, isGuest, config);
  const navigate = useNavigate();
  const location = useLocation();
  const asked = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const composer = useRef<ComposerHandle>(null);
  const { send } = chat;

  // The first question of a new chat arrives with the navigation.
  useEffect(() => {
    if (!initialQuestion || asked.current) return;
    asked.current = true;
    navigate(location.pathname, { replace: true, state: null });
    void send(initialQuestion);
  }, [initialQuestion, location.pathname, navigate, send]);

  useEffect(() => {
    document.title = chat.title ? `${chat.title} · ${t.appName}` : t.appName;
  }, [chat.title]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (element && stickToBottom.current) element.scrollTop = element.scrollHeight;
  }, [chat.messages]);

  if (chat.notFound) {
    return (
      <div className="empty-state">
        <h1>Chat not found</h1>
        <p>This chat does not exist, or it was deleted.</p>
        <Link className="button" to="/">
          {t.sidebar.newChat}
        </Link>
      </div>
    );
  }

  const last = chat.messages[chat.messages.length - 1];
  return (
    <div className="chat">
      <div
        className="chat-scroll"
        ref={scroller}
        onScroll={(event) => {
          const el = event.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        }}
      >
        <div className="messages" aria-busy={chat.busy}>
          {chat.loading && <p className="page-loading">{t.common.loading}</p>}
          {chat.messages.map((message) => (
            <MessageView
              key={message.key}
              message={message}
              isLast={message === last}
              busy={chat.busy}
              contactUrl={config.contact_url}
              canRate={!isGuest}
              onRegenerate={() => void chat.regenerate()}
              onRate={(rating, comment) => chat.rate(message, rating, comment)}
            />
          ))}
        </div>
      </div>
      <div className="sr-only" aria-live="polite">
        {chat.phase === "searching" ? t.chat.thinking : chat.phase === "writing" ? t.chat.writing : ""}
      </div>
      <div className="chat-footer">
        {chat.phase === "searching" && <p className="status-line">{t.chat.thinking}</p>}
        {chat.error && (
          <div className="banner banner-error" role="alert">
            <span>{chat.error}</span>
            <button type="button" className="banner-close" onClick={chat.clearError}>
              <span aria-hidden="true">×</span>
              <span className="sr-only">{t.common.close}</span>
            </button>
          </div>
        )}
        {isGuest && chat.messages.length > 0 && (
          <p className="guest-hint">
            <Link to="/signup">{t.common.signUp}</Link> · {t.chat.signInToSave}
          </p>
        )}
        <Composer
          ref={composer}
          busy={chat.busy}
          maxChars={config.max_query_chars}
          onSend={chat.send}
          onStop={chat.stop}
        />
        <p className="disclaimer">{t.chat.disclaimer}</p>
      </div>
    </div>
  );
}
