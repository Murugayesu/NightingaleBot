import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { keys, useMe } from "../../app/queries";
import { api } from "../../lib/api";
import type { User } from "../../lib/types";

export interface Auth {
  user: User | null;
  isLoading: boolean;
  isAdmin: boolean;
  signIn(email: string, password: string): Promise<User>;
  signUp(body: {
    email: string;
    password: string;
    display_name?: string;
    accept_privacy: boolean;
  }): Promise<User>;
  signOut(): Promise<void>;
  setUser(user: User | null): void;
}

export function useAuth(): Auth {
  const client = useQueryClient();
  const me = useMe();

  const setUser = useCallback(
    (user: User | null) => {
      client.setQueryData(keys.me, user);
      // Everything cached belongs to the previous identity.
      client.removeQueries({ queryKey: keys.allConversations });
      client.removeQueries({ queryKey: ["conversation"] });
      client.removeQueries({ queryKey: keys.admin });
    },
    [client],
  );

  const signIn = useCallback(
    async (email: string, password: string) => {
      const user = await api.login({ email, password });
      setUser(user);
      return user;
    },
    [setUser],
  );

  const signUp = useCallback(
    async (body: Parameters<typeof api.signup>[0]) => {
      const user = await api.signup(body);
      setUser(user);
      return user;
    },
    [setUser],
  );

  // A full page load after signing out: nothing from the previous account
  // stays in memory, and no page that needs a user gets to react first.
  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      window.location.assign("/");
    }
  }, []);

  const user = me.data ?? null;
  return {
    user,
    isLoading: me.isLoading,
    isAdmin: user?.role === "admin",
    signIn,
    signUp,
    signOut,
    setUser,
  };
}
