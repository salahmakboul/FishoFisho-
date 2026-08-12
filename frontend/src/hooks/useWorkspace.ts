import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import type {
  ConnectionStatus,
  Conversation,
  Message,
  Notification,
  NotificationPreference,
  Reaction,
  Room,
  User,
} from "../types";

type AsyncStatus = "idle" | "loading" | "ready" | "error";

/**
 * Owns the single WebSocket connection plus the workspace-wide state that
 * depends on it (rooms, per-room messages, notifications, current user).
 * Every component reads this hook's return value (passed down as props from
 * App.tsx) instead of opening its own connection or duplicating fetches.
 */
export type AuthStatus = "checking" | "authenticated" | "anonymous";

export function useWorkspace() {
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [users, setUsers] = useState<User[]>([]);

  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomsStatus, setRoomsStatus] = useState<AsyncStatus>("idle");
  const [roomsError, setRoomsError] = useState<string | null>(null);

  const [activeRoomId, setActiveRoomId] = useState<number | null>(null);
  const [messagesByRoom, setMessagesByRoom] = useState<Record<number, Message[]>>({});
  const [messagesStatus, setMessagesStatus] = useState<Record<number, AsyncStatus>>({});
  const [messagesError, setMessagesError] = useState<Record<number, string>>({});
  const [sending, setSending] = useState(false);
  // Cursor-pagination "load older messages" state (see lib/api.ts's
  // listMessages). null = no older page known yet loaded; undefined key =
  // not fetched yet.
  const [messagesNextCursor, setMessagesNextCursor] = useState<Record<number, string | null>>({});
  const [loadingOlderMessages, setLoadingOlderMessages] = useState<Record<number, boolean>>({});

  const [notifications, setNotifications] = useState<Notification[]>([]);
  // Global per-category notification toggles (mentions/channel_wide/
  // thread_replies) — null until the initial fetch resolves; ProfilePanel's
  // Notifications section treats null as "loading". Room-level overrides
  // (RoomNotificationSetting) are deliberately NOT hoisted into workspace
  // state the way this global one is — RoomSettingsPanel fetches/updates its
  // own room's setting directly via api.ts, the same pattern it already
  // uses for that room's member list.
  const [notificationPreference, setNotificationPreference] = useState<NotificationPreference | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");

  // Direct messages (1:1 conversations) + the "focus" flag that tells Layout
  // whether the main pane should show a room or a conversation right now.
  const [focusMode, setFocusMode] = useState<"room" | "conversation">("room");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationsStatus, setConversationsStatus] = useState<AsyncStatus>("idle");
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [activeConversationId, setActiveConversationIdState] = useState<string | null>(null);
  const [messagesByConversation, setMessagesByConversation] = useState<Record<string, Message[]>>({});
  const [conversationMessagesStatus, setConversationMessagesStatus] = useState<Record<string, AsyncStatus>>({});
  const [conversationMessagesError, setConversationMessagesError] = useState<Record<string, string>>({});
  const [sendingDm, setSendingDm] = useState(false);

  const streamRef = useRef<api.StreamHandle | null>(null);
  const loadedRooms = useRef<Set<number>>(new Set());
  const loadedConversations = useRef<Set<string>>(new Set());
  // Set once the socket has opened successfully the first time — lets the
  // status handler below tell "first connect" apart from "reconnected after
  // a drop", which is when it needs to re-join the active room and
  // reconcile with the server (see the WS effect).
  const hasConnectedOnceRef = useRef(false);

  const loadRooms = useCallback(() => {
    setRoomsStatus("loading");
    setRoomsError(null);
    api
      .listRooms()
      .then((data) => {
        setRooms(data);
        setRoomsStatus("ready");
        if (data.length > 0) setActiveRoomId((prev) => prev ?? data[0].id);
      })
      .catch((err) => {
        setRoomsError(err instanceof Error ? err.message : "Failed to load rooms.");
        setRoomsStatus("error");
      });
  }, []);

  const loadConversations = useCallback(() => {
    setConversationsStatus("loading");
    setConversationsError(null);
    api
      .listConversations()
      .then((data) => {
        setConversations(data);
        setConversationsStatus("ready");
      })
      .catch((err) => {
        setConversationsError(err instanceof Error ? err.message : "Failed to load conversations.");
        setConversationsStatus("error");
      });
  }, []);

  // checkAuth() is the app's single auth-status signal: getMe() succeeding
  // means there's a valid session (sets "authenticated" + loads the current
  // user's conversations), failing (401) means there isn't ("anonymous").
  // Called on mount below, and again after Auth.tsx's login/register calls
  // succeed — that's the one auth-detection mechanism the whole app shares,
  // rather than login/register separately guessing the resulting state.
  const checkAuth = useCallback(() => {
    return api
      .getMe()
      .then((user) => {
        setCurrentUser(user);
        setAuthStatus("authenticated");
        loadConversations();
        return user;
      })
      .catch(() => {
        setCurrentUser(null);
        setAuthStatus("anonymous");
        return null;
      });
  }, [loadConversations]);

  // Initial load: current user (+ auth status), directory, room list,
  // notifications.
  useEffect(() => {
    checkAuth();
    api.listUsers().then(setUsers).catch(() => setUsers([]));
    api.listNotifications().then(setNotifications).catch(() => setNotifications([]));
    api.getNotificationPreference().then(setNotificationPreference).catch(() => {});
    loadRooms();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Logs the session out server-side, then re-runs the exact same
  // getMe()-based auth check used on mount — getMe() will now fail and flip
  // authStatus back to "anonymous", landing the user back on <Landing/>.
  const logout = useCallback(async () => {
    await api.logout();
    await checkAuth();
  }, [checkAuth]);

  // Single WebSocket connection for the app's lifetime.
  useEffect(() => {
    const handle = api.connectStream(
      (event) => {
        if (event.type === "message.new") {
          setMessagesByRoom((prev) => {
            const existing = prev[event.room_id] ?? [];
            if (existing.some((m) => m.id === event.message.id)) return prev;
            return { ...prev, [event.room_id]: [...existing, event.message] };
          });
          setRooms((prev) =>
            prev.map((r) =>
              r.id === event.room_id && r.id !== activeRoomIdRef.current
                ? { ...r, unread_count: r.unread_count + 1 }
                : r
            )
          );
          // Threading: a reply's own row (just appended above) already
          // carries its own accurate reply_count (always 0), but the ROOT
          // message's row already in state is now stale by one — bump it
          // here so the "N replies" summary in the main list updates live
          // without needing a refetch. Keyed off `message.room` (always
          // present on the payload) rather than the event's own room_id,
          // since a thread panel open for a room the user isn't currently
          // viewing should still see this reflected if/when they open it.
          const replyToId = event.message.reply_to;
          if (replyToId != null) {
            const roomKey = event.message.room ?? event.room_id;
            setMessagesByRoom((prev) => {
              const existing = prev[roomKey];
              if (!existing) return prev;
              return {
                ...prev,
                [roomKey]: existing.map((m) =>
                  m.id === replyToId ? { ...m, reply_count: (m.reply_count ?? 0) + 1 } : m
                ),
              };
            });
          }
        } else if (event.type === "message.updated" || event.type === "message.deleted") {
          const roomId = event.message.room;
          if (roomId == null) return;
          setMessagesByRoom((prev) => {
            const existing = prev[roomId];
            if (!existing) return prev;
            return {
              ...prev,
              [roomId]: existing.map((m) => (m.id === event.message.id ? event.message : m)),
            };
          });
        } else if (event.type === "message.reactions_changed") {
          setMessagesByRoom((prev) => {
            let touchedRoomId: number | null = null;
            for (const [roomIdStr, msgs] of Object.entries(prev)) {
              if (msgs.some((m) => m.id === event.message_id)) {
                touchedRoomId = Number(roomIdStr);
                break;
              }
            }
            if (touchedRoomId == null) return prev;
            return {
              ...prev,
              [touchedRoomId]: prev[touchedRoomId].map((m) =>
                m.id === event.message_id ? { ...m, reactions: event.reactions } : m
              ),
            };
          });
        } else if (event.type === "notification.new") {
          setNotifications((prev) => [event.notification, ...prev]);
        } else if (event.type === "private_message.new") {
          const { conversation_id: id, message } = event;
          setMessagesByConversation((prev) => {
            const existing = prev[id] ?? [];
            if (existing.some((m) => m.id === message.id)) return prev;
            return { ...prev, [id]: [...existing, message] };
          });
          setConversations((prev) => {
            const idx = prev.findIndex((c) => c.id === id);
            if (idx === -1) return prev;
            const isActive = id === activeConversationIdRef.current;
            const updated = {
              ...prev[idx],
              last_message: message,
              unread_count: isActive ? 0 : prev[idx].unread_count + 1,
              updated_at: message.created,
            };
            return [updated, ...prev.filter((c) => c.id !== id)];
          });
        }
      },
      (status) => {
        setConnectionStatus(status);
        if (status !== "open") return;
        if (!hasConnectedOnceRef.current) {
          hasConnectedOnceRef.current = true;
          return;
        }
        // Reconnected after a drop: the "room.join" the server had for us
        // is gone (it lived on the old socket), so re-send it for whatever
        // room is currently open, and refetch that room's latest messages
        // — the client may have missed "message.new" events entirely while
        // disconnected, so this is server-authoritative reconciliation, not
        // just "resume the live feed". Dedup-by-id merge, same pattern the
        // WS message.new handler above already uses.
        const roomId = activeRoomIdRef.current;
        if (roomId == null) return;
        streamRef.current?.send({ type: "room.join", room_id: roomId });
        api
          .listMessages(roomId)
          .then((page) => {
            setMessagesByRoom((prev) => {
              const existing = prev[roomId] ?? [];
              const existingIds = new Set(existing.map((m) => m.id));
              const merged = [...existing, ...page.messages.filter((m) => !existingIds.has(m.id))];
              return { ...prev, [roomId]: merged };
            });
          })
          .catch(() => {
            // Best-effort reconciliation — a failed refetch here just means
            // the client stays slightly behind until the next successful
            // one; it doesn't need its own error UI.
          });
      }
    );
    streamRef.current = handle;
    return () => handle.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Track the active room in a ref so the WS handler above (registered once)
  // always sees the current value without resubscribing.
  const activeRoomIdRef = useRef<number | null>(null);
  useEffect(() => {
    activeRoomIdRef.current = activeRoomId;
  }, [activeRoomId]);

  // Same pattern for the active conversation, read by the WS handler above.
  const activeConversationIdRef = useRef<string | null>(null);
  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  // Join/leave rooms over the socket and lazily load message history.
  useEffect(() => {
    if (activeRoomId == null) return;
    streamRef.current?.send({ type: "room.join", room_id: activeRoomId });
    setRooms((prev) => prev.map((r) => (r.id === activeRoomId ? { ...r, unread_count: 0 } : r)));

    if (!loadedRooms.current.has(activeRoomId)) {
      loadedRooms.current.add(activeRoomId);
      setMessagesStatus((prev) => ({ ...prev, [activeRoomId]: "loading" }));
      api
        .listMessages(activeRoomId)
        .then((page) => {
          setMessagesByRoom((prev) => ({ ...prev, [activeRoomId]: page.messages }));
          setMessagesNextCursor((prev) => ({ ...prev, [activeRoomId]: page.nextCursor }));
          setMessagesStatus((prev) => ({ ...prev, [activeRoomId]: "ready" }));
        })
        .catch((err) => {
          loadedRooms.current.delete(activeRoomId);
          setMessagesError((prev) => ({
            ...prev,
            [activeRoomId]: err instanceof Error ? err.message : "Failed to load messages.",
          }));
          setMessagesStatus((prev) => ({ ...prev, [activeRoomId]: "error" }));
        });
    }

    return () => {
      streamRef.current?.send({ type: "room.leave", room_id: activeRoomId });
    };
  }, [activeRoomId]);

  // Replaces the local pending/failed placeholder (matched by client_id)
  // with the server's real response, deduping against anything already in
  // the list (e.g. a WS "message.new" echo that arrived first).
  const settleRoomMessage = useCallback((roomId: number, clientId: string, message: Message) => {
    setMessagesByRoom((prev) => {
      const existing = prev[roomId] ?? [];
      const withoutPlaceholder = existing.filter((m) => m.client_id !== clientId || m.id === message.id);
      if (withoutPlaceholder.some((m) => m.id === message.id)) {
        return { ...prev, [roomId]: withoutPlaceholder };
      }
      return { ...prev, [roomId]: [...withoutPlaceholder, message] };
    });
  }, []);

  const failRoomMessage = useCallback((roomId: number, clientId: string) => {
    setMessagesByRoom((prev) => ({
      ...prev,
      [roomId]: (prev[roomId] ?? []).map((m) => (m.client_id === clientId ? { ...m, status: "failed" } : m)),
    }));
  }, []);

  const sendMessage = useCallback(
    async (body: string, opts?: { replyTo?: number | string; attachment?: File }) => {
      if (activeRoomId == null || currentUser == null) return;
      const roomId = activeRoomId;
      const clientId = crypto.randomUUID();
      // Optimistic local echo: show the message immediately as "sending"
      // rather than waiting on the REST round-trip, using client_id as its
      // temporary id (Message.id is otherwise a server-assigned number).
      const pending: Message = {
        id: clientId,
        user: currentUser,
        body,
        mentions: [],
        created: new Date().toISOString(),
        room: roomId,
        reply_to: typeof opts?.replyTo === "number" ? opts.replyTo : undefined,
        client_id: clientId,
        status: "sending",
      };
      setMessagesByRoom((prev) => ({ ...prev, [roomId]: [...(prev[roomId] ?? []), pending] }));
      setSending(true);
      try {
        const message = await api.createMessage(roomId, body, { ...opts, clientId });
        settleRoomMessage(roomId, clientId, message);
      } catch (err) {
        failRoomMessage(roomId, clientId);
        throw err;
      } finally {
        setSending(false);
      }
    },
    [activeRoomId, currentUser, settleRoomMessage, failRoomMessage]
  );

  // Re-POSTs a previously-failed message with the SAME client_id — safe to
  // repeat thanks to the backend's (user, client_id) idempotency check
  // (api_views.py), so a flaky-network retry can never create a duplicate.
  const retrySendMessage = useCallback(
    async (roomId: number, clientId: string) => {
      const message = messagesByRoom[roomId]?.find((m) => m.client_id === clientId);
      if (!message) return;
      setMessagesByRoom((prev) => ({
        ...prev,
        [roomId]: (prev[roomId] ?? []).map((m) => (m.client_id === clientId ? { ...m, status: "sending" } : m)),
      }));
      try {
        const sent = await api.createMessage(roomId, message.body, {
          replyTo: typeof message.reply_to === "number" ? message.reply_to : undefined,
          clientId,
        });
        settleRoomMessage(roomId, clientId, sent);
      } catch {
        failRoomMessage(roomId, clientId);
      }
    },
    [messagesByRoom, settleRoomMessage, failRoomMessage]
  );

  const editMessage = useCallback(
    async (messageId: number, body: string) => {
      if (activeRoomId == null) return;
      const roomId = activeRoomId;
      const updated = await api.editMessage(roomId, messageId, body);
      setMessagesByRoom((prev) => ({
        ...prev,
        [roomId]: (prev[roomId] ?? []).map((m) => (m.id === messageId ? updated : m)),
      }));
    },
    [activeRoomId]
  );

  const deleteMessage = useCallback(
    async (messageId: number) => {
      if (activeRoomId == null) return;
      const roomId = activeRoomId;
      const updated = await api.deleteMessage(roomId, messageId);
      setMessagesByRoom((prev) => ({
        ...prev,
        [roomId]: (prev[roomId] ?? []).map((m) => (m.id === messageId ? updated : m)),
      }));
    },
    [activeRoomId]
  );

  const toggleReaction = useCallback(
    async (messageId: number, emoji: string) => {
      if (activeRoomId == null) return;
      const roomId = activeRoomId;
      const reactions: Reaction[] = await api.toggleReaction(roomId, messageId, emoji);
      setMessagesByRoom((prev) => ({
        ...prev,
        [roomId]: (prev[roomId] ?? []).map((m) => (m.id === messageId ? { ...m, reactions } : m)),
      }));
    },
    [activeRoomId]
  );

  const loadOlderMessages = useCallback(
    async (roomId: number) => {
      const cursor = messagesNextCursor[roomId];
      if (!cursor || loadingOlderMessages[roomId]) return;
      setLoadingOlderMessages((prev) => ({ ...prev, [roomId]: true }));
      try {
        const page = await api.listMessages(roomId, cursor);
        setMessagesByRoom((prev) => {
          const existing = prev[roomId] ?? [];
          const existingIds = new Set(existing.map((m) => m.id));
          const older = page.messages.filter((m) => !existingIds.has(m.id));
          return { ...prev, [roomId]: [...older, ...existing] };
        });
        setMessagesNextCursor((prev) => ({ ...prev, [roomId]: page.nextCursor }));
      } finally {
        setLoadingOlderMessages((prev) => ({ ...prev, [roomId]: false }));
      }
    },
    [messagesNextCursor, loadingOlderMessages]
  );

  const retryLoadMessages = useCallback((roomId: number) => {
    setMessagesStatus((prev) => ({ ...prev, [roomId]: "loading" }));
    api
      .listMessages(roomId)
      .then((page) => {
        loadedRooms.current.add(roomId);
        setMessagesByRoom((prev) => ({ ...prev, [roomId]: page.messages }));
        setMessagesNextCursor((prev) => ({ ...prev, [roomId]: page.nextCursor }));
        setMessagesStatus((prev) => ({ ...prev, [roomId]: "ready" }));
      })
      .catch((err) => {
        setMessagesError((prev) => ({
          ...prev,
          [roomId]: err instanceof Error ? err.message : "Failed to load messages.",
        }));
        setMessagesStatus((prev) => ({ ...prev, [roomId]: "error" }));
      });
  }, []);

  // Load a conversation's message history once on open (marks unread ones
  // read server-side, same as the old private_chat view did). New replies
  // after that arrive live via the "private_message.new" WS event handled
  // above — PrivateMessage now has a post_save broadcast (signals.py) same
  // as Message does, so no polling fallback is needed here anymore.
  useEffect(() => {
    if (activeConversationId == null) return;
    const id = activeConversationId;
    if (loadedConversations.current.has(id)) {
      setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, unread_count: 0 } : c)));
      return;
    }

    setConversationMessagesStatus((prev) => ({ ...prev, [id]: "loading" }));
    api
      .listConversationMessages(id)
      .then((msgs) => {
        loadedConversations.current.add(id);
        setMessagesByConversation((prev) => ({ ...prev, [id]: msgs }));
        setConversationMessagesStatus((prev) => ({ ...prev, [id]: "ready" }));
        setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, unread_count: 0 } : c)));
      })
      .catch((err) => {
        setConversationMessagesError((prev) => ({
          ...prev,
          [id]: err instanceof Error ? err.message : "Failed to load messages.",
        }));
        setConversationMessagesStatus((prev) => ({ ...prev, [id]: "error" }));
      });
  }, [activeConversationId]);

  const settleConversationMessage = useCallback((conversationId: string, clientId: string, message: Message) => {
    setMessagesByConversation((prev) => {
      const existing = prev[conversationId] ?? [];
      const withoutPlaceholder = existing.filter((m) => m.client_id !== clientId || m.id === message.id);
      if (withoutPlaceholder.some((m) => m.id === message.id)) {
        return { ...prev, [conversationId]: withoutPlaceholder };
      }
      return { ...prev, [conversationId]: [...withoutPlaceholder, message] };
    });
    setConversations((prev) => {
      const idx = prev.findIndex((c) => c.id === conversationId);
      if (idx === -1) return prev;
      const updated = { ...prev[idx], last_message: message, unread_count: 0, updated_at: message.created };
      return [updated, ...prev.filter((c) => c.id !== conversationId)];
    });
  }, []);

  const failConversationMessage = useCallback((conversationId: string, clientId: string) => {
    setMessagesByConversation((prev) => ({
      ...prev,
      [conversationId]: (prev[conversationId] ?? []).map((m) =>
        m.client_id === clientId ? { ...m, status: "failed" } : m
      ),
    }));
  }, []);

  const sendConversationMessage = useCallback(
    async (body: string) => {
      if (activeConversationId == null || currentUser == null) return;
      const id = activeConversationId;
      const clientId = crypto.randomUUID();
      const pending: Message = {
        id: clientId,
        user: currentUser,
        body,
        mentions: [],
        created: new Date().toISOString(),
        client_id: clientId,
        status: "sending",
      };
      setMessagesByConversation((prev) => ({ ...prev, [id]: [...(prev[id] ?? []), pending] }));
      setSendingDm(true);
      try {
        const message = await api.createConversationMessage(id, body, { clientId });
        settleConversationMessage(id, clientId, message);
      } catch (err) {
        failConversationMessage(id, clientId);
        throw err;
      } finally {
        setSendingDm(false);
      }
    },
    [activeConversationId, currentUser, settleConversationMessage, failConversationMessage]
  );

  // Same idempotent-retry pattern as retrySendMessage, for DM threads.
  const retrySendConversationMessage = useCallback(
    async (conversationId: string, clientId: string) => {
      const message = messagesByConversation[conversationId]?.find((m) => m.client_id === clientId);
      if (!message) return;
      setMessagesByConversation((prev) => ({
        ...prev,
        [conversationId]: (prev[conversationId] ?? []).map((m) =>
          m.client_id === clientId ? { ...m, status: "sending" } : m
        ),
      }));
      try {
        const sent = await api.createConversationMessage(conversationId, message.body, { clientId });
        settleConversationMessage(conversationId, clientId, sent);
      } catch {
        failConversationMessage(conversationId, clientId);
      }
    },
    [messagesByConversation, settleConversationMessage, failConversationMessage]
  );

  const retryLoadConversationMessages = useCallback((conversationId: string) => {
    setConversationMessagesStatus((prev) => ({ ...prev, [conversationId]: "loading" }));
    api
      .listConversationMessages(conversationId)
      .then((msgs) => {
        loadedConversations.current.add(conversationId);
        setMessagesByConversation((prev) => ({ ...prev, [conversationId]: msgs }));
        setConversationMessagesStatus((prev) => ({ ...prev, [conversationId]: "ready" }));
      })
      .catch((err) => {
        setConversationMessagesError((prev) => ({
          ...prev,
          [conversationId]: err instanceof Error ? err.message : "Failed to load messages.",
        }));
        setConversationMessagesStatus((prev) => ({ ...prev, [conversationId]: "error" }));
      });
  }, []);

  const selectConversation = useCallback((id: string) => {
    setActiveConversationIdState(id);
    setFocusMode("conversation");
  }, []);

  const selectRoom = useCallback((id: number) => {
    setActiveRoomId(id);
    setFocusMode("room");
  }, []);

  const startConversation = useCallback(
    async (userIds: number | number[]) => {
      if (!currentUser) throw new Error("You need to be signed in to start a conversation.");
      const conversation = await api.createConversation(userIds);
      setConversations((prev) => {
        const exists = prev.some((c) => c.id === conversation.id);
        return exists ? prev.map((c) => (c.id === conversation.id ? conversation : c)) : [conversation, ...prev];
      });
      setActiveConversationIdState(conversation.id);
      setFocusMode("conversation");
      return conversation;
    },
    [currentUser]
  );

  const removeConversation = useCallback(
    async (id: string) => {
      await api.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setMessagesByConversation((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      loadedConversations.current.delete(id);
      if (activeConversationId === id) {
        setActiveConversationIdState(null);
        setFocusMode("room");
      }
    },
    [activeConversationId]
  );

  const markNotificationRead = useCallback(async (id: number) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, is_read: true } : n)));
    await api.markNotificationRead(id).catch(() => {});
  }, []);

  const markAllNotificationsRead = useCallback(async () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    await api.markAllNotificationsRead().catch(() => {});
  }, []);

  const updateNotificationPreference = useCallback(async (patch: Partial<NotificationPreference>) => {
    const updated = await api.updateNotificationPreference(patch);
    setNotificationPreference(updated);
    return updated;
  }, []);

  const updateProfile = useCallback(async (patch: { bio?: string; avatar?: File }) => {
    const updated = await api.updateMe(patch);
    setCurrentUser(updated);
    return updated;
  }, []);

  const createRoom = useCallback(
    async (data: { name: string; topic_id?: number; description?: string; is_private?: boolean }) => {
    const room = await api.createRoom(data);
    setRooms((prev) => [...prev, room]);
    setActiveRoomId(room.id);
    setFocusMode("room");
    return room;
  }, []);

  const updateRoomSettings = useCallback(
    async (roomId: number, patch: Partial<Pick<Room, "name" | "description" | "is_private">>) => {
      const updated = await api.updateRoom(roomId, patch);
      setRooms((prev) => prev.map((r) => (r.id === roomId ? { ...r, ...updated } : r)));
      return updated;
    },
    []
  );

  const archiveRoom = useCallback(async (roomId: number) => {
    const updated = await api.archiveRoom(roomId);
    setRooms((prev) => prev.map((r) => (r.id === roomId ? { ...r, ...updated } : r)));
    return updated;
  }, []);

  const unarchiveRoom = useCallback(async (roomId: number) => {
    const updated = await api.unarchiveRoom(roomId);
    setRooms((prev) => prev.map((r) => (r.id === roomId ? { ...r, ...updated } : r)));
    return updated;
  }, []);

  const unreadNotificationCount = notifications.filter((n) => !n.is_read).length;
  const unreadConversationCount = conversations.reduce((sum, c) => sum + c.unread_count, 0);

  const retryLoadConversations = useCallback(() => {
    if (currentUser) loadConversations();
  }, [currentUser, loadConversations]);

  return {
    currentUser,
    authStatus,
    checkAuth,
    logout,
    users,
    rooms,
    roomsStatus,
    roomsError,
    retryLoadRooms: loadRooms,
    activeRoomId,
    setActiveRoomId: selectRoom,
    messages: activeRoomId != null ? messagesByRoom[activeRoomId] ?? [] : [],
    messagesStatus: activeRoomId != null ? messagesStatus[activeRoomId] ?? "idle" : "idle",
    messagesError: activeRoomId != null ? messagesError[activeRoomId] : undefined,
    retryLoadMessages,
    sendMessage,
    retrySendMessage,
    sending,
    editMessage,
    deleteMessage,
    toggleReaction,
    loadOlderMessages,
    hasOlderMessages: activeRoomId != null ? !!messagesNextCursor[activeRoomId] : false,
    loadingOlderMessages: activeRoomId != null ? !!loadingOlderMessages[activeRoomId] : false,
    notifications,
    unreadNotificationCount,
    markNotificationRead,
    markAllNotificationsRead,
    notificationPreference,
    updateNotificationPreference,
    updateProfile,
    createRoom,
    updateRoomSettings,
    archiveRoom,
    unarchiveRoom,
    connectionStatus,

    // Direct messages
    focusMode,
    conversations,
    conversationsStatus,
    conversationsError,
    retryLoadConversations,
    unreadConversationCount,
    activeConversationId,
    setActiveConversationId: selectConversation,
    conversationMessages: activeConversationId != null ? messagesByConversation[activeConversationId] ?? [] : [],
    conversationMessagesStatus:
      activeConversationId != null ? conversationMessagesStatus[activeConversationId] ?? "idle" : "idle",
    conversationMessagesError:
      activeConversationId != null ? conversationMessagesError[activeConversationId] : undefined,
    retryLoadConversationMessages,
    sendConversationMessage,
    retrySendConversationMessage,
    sendingDm,
    startConversation,
    removeConversation,
  };
}

export type Workspace = ReturnType<typeof useWorkspace>;
