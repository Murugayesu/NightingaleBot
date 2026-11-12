/** Query keys and the app-wide queries (config and the signed-in user). */
import { QueryClient, useQuery } from "@tanstack/react-query";

import { ApiError, api } from "../lib/api";
import type { AppConfig, User } from "../lib/types";

export const keys = {
  config: ["config"] as const,
  me: ["me"] as const,
  conversations: (search: string, archived: boolean) =>
    ["conversations", { search, archived }] as const,
  allConversations: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  guestConversations: ["guest", "conversations"] as const,
  guestConversation: (id: string) => ["guest", "conversation", id] as const,
  admin: ["admin"] as const,
};

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) =>
          count < 2 && !(error instanceof ApiError && error.status < 500),
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  });
}

export function useConfig() {
  return useQuery<AppConfig>({
    queryKey: keys.config,
    queryFn: api.config,
    staleTime: Infinity,
  });
}

/** The signed-in user, or null for a guest. */
export async function fetchMe(): Promise<User | null> {
  return (await api.session()).user;
}

export function useMe() {
  return useQuery<User | null>({ queryKey: keys.me, queryFn: fetchMe, staleTime: 60_000 });
}
