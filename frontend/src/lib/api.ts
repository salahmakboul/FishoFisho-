import type {
  AIInteraction,
  AuditEvent,
  BlockedKeyword,
  ClientEvent,
  Conversation,
  ConnectionStatus,
  Invitation,
  Message,
  ModerationAction,
  ModerationActionType,
  Notification,
  NotificationPreference,
  Reaction,
  Room,
  RoomMember,
  RoomNotificationSetting,
  SearchMessageResult,
  SearchResults,
  ServerEvent,
  Topic,
  User,
  Webhook,
  WorkspaceRole,
} from "../types";

// ---------------------------------------------------------------------------
// Mock switch
//
// The live backend (REST + WS per the contract) is being built in parallel.
// Until it's reachable, USE_MOCK keeps the whole app interactive against an
// in-memory fixture that lives in this file. Flip it with an env var:
//   VITE_USE_MOCK=false npm run dev
// or just edit the default below once the backend is up. Every exported
// function has a real `fetch`/WebSocket implementation behind the flag, so
// switching is a one-line change, not a rewrite.
// ---------------------------------------------------------------------------
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

const API_BASE = "/api/v1";

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)")
  );
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  status: number;
  /** Per-field validation errors, when the backend responded with a DRF/
   * Django-forms-style error body ({"username": ["..."], ...}) instead of a
   * single {"detail": "..."} message — e.g. register()'s UserCreationForm
   * errors. Undefined for single-message errors. */
  fields?: Record<string, string[]>;
  constructor(status: number, message: string, fields?: Record<string, string[]>) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  const isFormData = init.body instanceof FormData;
  if (!isFormData && init.body) headers.set("Content-Type", "application/json");
  if (method !== "GET" && method !== "HEAD") {
    const csrf = getCookie("csrftoken");
    if (csrf) headers.set("X-CSRFToken", csrf);
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });
  if (!res.ok) {
    let message = res.statusText;
    let fields: Record<string, string[]> | undefined;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      } else if (body && typeof body === "object" && !Array.isArray(body)) {
        // Field-error shape, e.g. register()'s UserCreationForm errors:
        // {"username": ["A user with that username already exists."]}
        fields = body as Record<string, string[]>;
        message = Object.values(fields).flat().join(" ") || JSON.stringify(body);
      } else {
        message = JSON.stringify(body);
      }
    } catch {
      // ignore
    }
    throw new ApiError(res.status, message || `Request failed (${res.status})`, fields);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// =============================================================================
// Fixture data (mock mode only)
// =============================================================================

const mockUsers: User[] = [
  { id: 1, username: "you", avatar: null, bio: "Building things, one room at a time." },
  { id: 2, username: "maria", avatar: null, bio: "Design & frontend." },
  { id: 3, username: "devon", avatar: null, bio: "Backend + infra." },
  { id: 4, username: "priya", avatar: null, bio: "Product." },
  { id: 999, username: "FishoAI", avatar: null, bio: "Ask me anything about the workspace.", is_bot: true },
];

const mockTopics: Topic[] = [
  { id: 1, name: "general" },
  { id: 2, name: "engineering" },
  { id: 3, name: "random" },
  { id: 4, name: "design" },
];

const mockRooms: Room[] = [
  { id: 1, name: "general", topic: mockTopics[0], description: "Company-wide announcements and chat.", member_count: 24, unread_count: 0 },
  { id: 2, name: "engineering", topic: mockTopics[1], description: "Build stuff, break stuff, fix stuff.", member_count: 11, unread_count: 2 },
  { id: 3, name: "random", topic: mockTopics[2], description: "Off-topic chatter, memes, links.", member_count: 18, unread_count: 0 },
  { id: 4, name: "design", topic: mockTopics[3], description: "Design crits and inspiration.", member_count: 7, unread_count: 5 },
];

function minutesAgo(n: number): string {
  return new Date(Date.now() - n * 60_000).toISOString();
}

let nextMessageId = 1000;
let nextNotificationId = 100;

// --- threading (mock mode only) ---
// The mock current user's set of followed thread-root ids (mock mode only
// ever acts as a single user, so this doesn't need to be keyed by user).
const mockFollowedThreadIds = new Set<number>();

/** Resolve a reply target to its thread ROOT, mirroring the server's
 * MessageListCreateView._resolve_reply_to: replying to an already-threaded
 * message flattens onto that message's root instead of chaining. */
function resolveMockReplyRoot(roomId: number, replyToId: number | string | undefined): number | undefined {
  if (typeof replyToId !== "number") return undefined;
  const target = (mockMessages[roomId] ?? []).find((m) => m.id === replyToId);
  if (!target) return undefined;
  return typeof target.reply_to === "number" ? target.reply_to : (target.id as number);
}

/** Recompute reply_count/is_thread_root/is_following_thread for every
 * message in a mock room, mirroring what MessageSerializer computes
 * server-side. Called after anything that could change thread shape. */
function annotateMockThreadFields(roomId: number) {
  const msgs = mockMessages[roomId] ?? [];
  const countByRoot = new Map<number, number>();
  msgs.forEach((m) => {
    if (typeof m.reply_to === "number") {
      countByRoot.set(m.reply_to, (countByRoot.get(m.reply_to) ?? 0) + 1);
    }
  });
  msgs.forEach((m) => {
    const isRoot = m.reply_to == null;
    m.is_thread_root = isRoot;
    m.reply_count = isRoot ? countByRoot.get(m.id as number) ?? 0 : 0;
    m.is_following_thread = isRoot && typeof m.id === "number" && mockFollowedThreadIds.has(m.id);
  });
}

const mockMessages: Record<number, Message[]> = {
  1: [
    { id: nextMessageId++, user: mockUsers[3], body: "Reminder: all-hands is at 3pm today.", mentions: [], created: minutesAgo(180) },
    { id: nextMessageId++, user: mockUsers[1], body: "Thanks for the heads up, priya.", mentions: [4], created: minutesAgo(175) },
    { id: nextMessageId++, user: mockUsers[0], body: "Will there be a recording for people who can't make it?", mentions: [], created: minutesAgo(40) },
  ],
  2: [
    { id: nextMessageId++, user: mockUsers[2], body: "Deploy pipeline is green again, the flaky test was a timing issue.", mentions: [], created: minutesAgo(220) },
    { id: nextMessageId++, user: mockUsers[1], body: "@devon nice catch. Was it the websocket reconnect test?", mentions: [3], created: minutesAgo(200) },
    { id: nextMessageId++, user: mockUsers[2], body: "Yep, exactly that one.", mentions: [], created: minutesAgo(190) },
    { id: nextMessageId++, user: mockUsers[0], body: "Following up here later today.", mentions: [], created: minutesAgo(12) },
  ],
  3: [
    { id: nextMessageId++, user: mockUsers[1], body: "found a stray semicolon that's been in prod for 3 years", mentions: [], created: minutesAgo(300) },
    { id: nextMessageId++, user: mockUsers[3], body: "iconic", mentions: [], created: minutesAgo(298) },
  ],
  4: [
    { id: nextMessageId++, user: mockUsers[1], body: "Pushed new room-list spacing to the crit doc, would love eyes before Friday.", mentions: [], created: minutesAgo(90) },
    { id: nextMessageId++, user: mockUsers[3], body: "@maria looking now.", mentions: [2], created: minutesAgo(85) },
  ],
};

const mockNotifications: Notification[] = [
  { id: nextNotificationId++, notification_type: "mention", message: "maria mentioned you in #general", room_id: 1, created_at: minutesAgo(175), is_read: true },
  { id: nextNotificationId++, notification_type: "mention", message: "devon mentioned you in #design", room_id: 4, created_at: minutesAgo(85), is_read: false },
  { id: nextNotificationId++, notification_type: "system", message: "You were added to #engineering", room_id: 2, created_at: minutesAgo(600), is_read: true },
];

const mockConversations: Conversation[] = [
  {
    id: "conv-1",
    participants: [mockUsers[0], mockUsers[1]],
    last_message: null,
    display_name: mockUsers[1].username,
    unread_count: 0,
    created_at: minutesAgo(1440),
    updated_at: minutesAgo(60),
  },
  {
    id: "conv-2",
    participants: [mockUsers[0], mockUsers[3]],
    last_message: null,
    display_name: mockUsers[3].username,
    unread_count: 1,
    created_at: minutesAgo(2000),
    updated_at: minutesAgo(20),
  },
];

const mockConversationMessages: Record<string, Message[]> = {
  "conv-1": [
    { id: nextMessageId++, user: mockUsers[1], body: "Hey, did you see the design crit notes?", mentions: [], created: minutesAgo(60) },
  ],
  "conv-2": [
    { id: nextMessageId++, user: mockUsers[3], body: "Quick one — can we push the sync to 4pm?", mentions: [], created: minutesAgo(180) },
    { id: nextMessageId++, user: mockUsers[3], body: "No worries if not, just let me know.", mentions: [], created: minutesAgo(20) },
  ],
};

// Seed each fixture conversation's last_message from its message list so the
// two stay consistent without hand-duplicating the last line above.
mockConversations.forEach((c) => {
  const msgs = mockConversationMessages[c.id] ?? [];
  c.last_message = msgs.length > 0 ? msgs[msgs.length - 1] : null;
});

export const mockCurrentUser: User = mockUsers[0];

// =============================================================================
// Mock event bus — lets mock REST writes (e.g. sending a message) feed the
// mock WebSocket stream, and lets simulated peer activity + the AI bot
// respond, the same way the real server would broadcast to the socket.
// =============================================================================

type Listener = (event: ServerEvent) => void;
const busListeners = new Set<Listener>();
function publish(event: ServerEvent) {
  busListeners.forEach((listener) => listener(event));
}

const AI_BOT = mockUsers.find((u) => u.username === "FishoAI")!;
const AI_REPLIES = [
  "Here's a quick summary of the last few messages in this room.",
  "I can help with that — want me to draft a reply?",
  "Noted. I'll keep an eye on this thread.",
  "Good question. Based on recent activity here, I'd say it's on track.",
];

function maybeTriggerAi(roomId: number, body: string) {
  if (!/@fishoai/i.test(body)) return;
  const reply = AI_REPLIES[Math.floor(Math.random() * AI_REPLIES.length)];
  window.setTimeout(() => {
    const message: Message = {
      id: nextMessageId++,
      user: AI_BOT,
      body: reply,
      mentions: [],
      created: new Date().toISOString(),
    };
    (mockMessages[roomId] ??= []).push(message);
    publish({ type: "message.new", room_id: roomId, message });
  }, 900);
}

// Light simulated peer presence so the workspace feels alive without a live
// backend: every so often, a mock teammate posts in a room and — if they
// @-mention "you" — a matching notification is emitted too.
let peerActivityStarted = false;
function startPeerActivity() {
  if (peerActivityStarted || !USE_MOCK) return;
  peerActivityStarted = true;
  const lines = [
    { body: "quick sanity check — anyone else seeing slow page loads?", mention: false },
    { body: "@you got a sec to take a look at this when you're free?", mention: true },
    { body: "shipped the fix, should be live now.", mention: false },
  ];
  window.setInterval(() => {
    const roomIds = mockRooms.map((r) => r.id);
    const roomId = roomIds[Math.floor(Math.random() * roomIds.length)];
    const peer = mockUsers[1 + Math.floor(Math.random() * 3)];
    const line = lines[Math.floor(Math.random() * lines.length)];
    const message: Message = {
      id: nextMessageId++,
      user: peer,
      body: line.body,
      mentions: line.mention ? [mockCurrentUser.id] : [],
      created: new Date().toISOString(),
    };
    (mockMessages[roomId] ??= []).push(message);
    publish({ type: "message.new", room_id: roomId, message });
    if (line.mention) {
      const room = mockRooms.find((r) => r.id === roomId)!;
      const notification: Notification = {
        id: nextNotificationId++,
        notification_type: "mention",
        message: `${peer.username} mentioned you in #${room.name}`,
        room_id: roomId,
        created_at: new Date().toISOString(),
        is_read: false,
      };
      mockNotifications.unshift(notification);
      publish({ type: "notification.new", notification });
    }
  }, 45_000);
}

// =============================================================================
// Auth
//
// Session-cookie auth: login/register set request.session via Django's
// session middleware (same as the old server-rendered views), and every
// other /api/v1/ call already sends that cookie automatically
// (credentials: "same-origin" in apiFetch above). The only extra step is
// CSRF: DRF's SessionAuthentication requires an X-CSRFToken header on
// unsafe methods, which apiFetch reads from the `csrftoken` cookie — but
// that cookie only exists once something has set it, so getCsrfToken()
// hits the dedicated /auth/csrf/ endpoint (@ensure_csrf_cookie) first.
// =============================================================================

export async function getCsrfToken(): Promise<void> {
  if (USE_MOCK) return;
  if (getCookie("csrftoken")) return;
  await apiFetch<{ detail: string }>("/auth/csrf/");
}

export async function login(username: string, password: string, remember = true): Promise<User> {
  if (USE_MOCK) {
    await delay(300);
    return mockCurrentUser;
  }
  await getCsrfToken();
  return apiFetch<User>("/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password, remember }),
  });
}

export async function register(username: string, password1: string, password2: string): Promise<User> {
  if (USE_MOCK) {
    await delay(300);
    return mockCurrentUser;
  }
  await getCsrfToken();
  return apiFetch<User>("/auth/register/", {
    method: "POST",
    body: JSON.stringify({ username, password1, password2 }),
  });
}

// requestPasswordReset always resolves (never throws for "user not found") —
// the backend intentionally returns the same generic 200 response either
// way, to avoid leaking which usernames exist.
export async function requestPasswordReset(username: string): Promise<{ detail: string }> {
  if (USE_MOCK) {
    await delay(300);
    return { detail: "If that account exists, a password reset link has been sent." };
  }
  await getCsrfToken();
  return apiFetch<{ detail: string }>("/auth/password-reset/", {
    method: "POST",
    body: JSON.stringify({ username }),
  });
}

export async function confirmPasswordReset(
  uid: string,
  token: string,
  password1: string,
  password2: string
): Promise<User> {
  if (USE_MOCK) {
    await delay(300);
    if (password1 !== password2) {
      throw new ApiError(400, "The two password fields didn't match.", {
        password2: ["The two password fields didn't match."],
      });
    }
    return mockCurrentUser;
  }
  await getCsrfToken();
  return apiFetch<User>("/auth/password-reset-confirm/", {
    method: "POST",
    body: JSON.stringify({ uid, token, password1, password2 }),
  });
}

export async function logout(): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    return;
  }
  await apiFetch<void>("/auth/logout/", { method: "POST" });
}

// =============================================================================
// Rooms
// =============================================================================

export async function listRooms(params?: { topic?: string; q?: string }): Promise<Room[]> {
  if (USE_MOCK) {
    await delay(250);
    let rooms = mockRooms;
    if (params?.topic) rooms = rooms.filter((r) => r.topic?.name === params.topic);
    if (params?.q) {
      const q = params.q.toLowerCase();
      rooms = rooms.filter((r) => r.name.toLowerCase().includes(q));
    }
    return rooms;
  }
  const qs = new URLSearchParams();
  if (params?.topic) qs.set("topic", params.topic);
  if (params?.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs}` : "";
  return apiFetch<Room[]>(`/rooms/${suffix}`);
}

export async function createRoom(data: {
  name: string;
  topic_id?: number;
  description?: string;
  is_private?: boolean;
}): Promise<Room> {
  if (USE_MOCK) {
    await delay(300);
    if (!data.name.trim()) throw new ApiError(400, "Room name is required.");
    const topic = mockTopics.find((t) => t.id === data.topic_id) ?? null;
    const room: Room = {
      id: Math.max(...mockRooms.map((r) => r.id)) + 1,
      name: data.name.trim(),
      topic,
      description: data.description,
      member_count: 1,
      unread_count: 0,
      is_private: data.is_private ?? false,
    };
    mockRooms.push(room);
    mockMessages[room.id] = [];
    return room;
  }
  return apiFetch<Room>("/rooms/", { method: "POST", body: JSON.stringify(data) });
}

export async function getRoom(id: number): Promise<Room> {
  if (USE_MOCK) {
    await delay(150);
    const room = mockRooms.find((r) => r.id === id);
    if (!room) throw new ApiError(404, "Room not found.");
    return room;
  }
  return apiFetch<Room>(`/rooms/${id}/`);
}

export async function updateRoom(
  id: number,
  patch: Partial<Pick<Room, "name" | "description" | "is_private">>
): Promise<Room> {
  if (USE_MOCK) {
    await delay(250);
    const room = mockRooms.find((r) => r.id === id);
    if (!room) throw new ApiError(404, "Room not found.");
    Object.assign(room, patch);
    return room;
  }
  return apiFetch<Room>(`/rooms/${id}/`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function deleteRoom(id: number): Promise<void> {
  if (USE_MOCK) {
    await delay(200);
    const idx = mockRooms.findIndex((r) => r.id === id);
    if (idx >= 0) mockRooms.splice(idx, 1);
    return;
  }
  await apiFetch<void>(`/rooms/${id}/`, { method: "DELETE" });
}

export async function archiveRoom(id: number): Promise<Room> {
  if (USE_MOCK) {
    await delay(200);
    const room = mockRooms.find((r) => r.id === id);
    if (!room) throw new ApiError(404, "Room not found.");
    room.is_archived = true;
    return room;
  }
  return apiFetch<Room>(`/rooms/${id}/archive/`, { method: "POST" });
}

export async function unarchiveRoom(id: number): Promise<Room> {
  if (USE_MOCK) {
    await delay(200);
    const room = mockRooms.find((r) => r.id === id);
    if (!room) throw new ApiError(404, "Room not found.");
    room.is_archived = false;
    return room;
  }
  return apiFetch<Room>(`/rooms/${id}/unarchive/`, { method: "POST" });
}

// =============================================================================
// Room members
// =============================================================================

const mockRoomMembers: Record<number, RoomMember[]> = {};
let nextMembershipId = 1;

function getMockMembers(roomId: number): RoomMember[] {
  if (!mockRoomMembers[roomId]) {
    mockRoomMembers[roomId] = [
      { id: nextMembershipId++, user: mockCurrentUser, role: "admin", joined_at: minutesAgo(1000) },
    ];
  }
  return mockRoomMembers[roomId];
}

export async function listRoomMembers(roomId: number): Promise<RoomMember[]> {
  if (USE_MOCK) {
    await delay(200);
    return [...getMockMembers(roomId)];
  }
  return apiFetch<RoomMember[]>(`/rooms/${roomId}/members/`);
}

export async function addRoomMember(
  roomId: number,
  userId: number,
  role: "admin" | "member" = "member"
): Promise<RoomMember> {
  if (USE_MOCK) {
    await delay(200);
    const members = getMockMembers(roomId);
    const user = mockUsers.find((u) => u.id === userId);
    if (!user) throw new ApiError(404, "User not found.");
    const membership: RoomMember = { id: nextMembershipId++, user, role, joined_at: new Date().toISOString() };
    members.push(membership);
    return membership;
  }
  return apiFetch<RoomMember>(`/rooms/${roomId}/members/`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, role }),
  });
}

export async function updateRoomMemberRole(
  roomId: number,
  userId: number,
  role: "admin" | "member"
): Promise<RoomMember> {
  if (USE_MOCK) {
    await delay(150);
    const members = getMockMembers(roomId);
    const membership = members.find((m) => m.user.id === userId);
    if (!membership) throw new ApiError(404, "Member not found.");
    membership.role = role;
    return membership;
  }
  return apiFetch<RoomMember>(`/rooms/${roomId}/members/${userId}/`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function removeRoomMember(roomId: number, userId: number): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    const members = getMockMembers(roomId);
    const idx = members.findIndex((m) => m.user.id === userId);
    if (idx >= 0) members.splice(idx, 1);
    return;
  }
  await apiFetch<void>(`/rooms/${roomId}/members/${userId}/`, { method: "DELETE" });
}

// =============================================================================
// Messages
// =============================================================================

export interface MessagePage {
  messages: Message[];
  /** Opaque cursor for the next (older) page, or null if there isn't one.
   * Extracted from DRF CursorPagination's `next` URL — see extractCursor(). */
  nextCursor: string | null;
}

/** Pull the `cursor` query param off a DRF CursorPagination `next`/`previous`
 * URL, so callers can pass just the cursor value back into listMessages()
 * rather than juggling a full (possibly cross-origin-looking) URL. */
function extractCursor(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url, window.location.origin).searchParams.get("cursor");
  } catch {
    return null;
  }
}

/** List a room's messages, cursor-paginated newest-first server-side (see
 * playground/pagination.py). Without a cursor this returns the latest page;
 * pass the previous call's `nextCursor` to page further into the past
 * ("load older messages"). Results are reversed here so callers always get
 * chronological (oldest-first) order, matching how the message list and the
 * rest of this app already assume messages are ordered. */
export async function listMessages(roomId: number, cursor?: string): Promise<MessagePage> {
  if (USE_MOCK) {
    await delay(300);
    annotateMockThreadFields(roomId);
    return { messages: [...(mockMessages[roomId] ?? [])], nextCursor: null };
  }
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const page = await apiFetch<{ results: Message[]; next: string | null }>(
    `/rooms/${roomId}/messages/${qs}`
  );
  return { messages: [...page.results].reverse(), nextCursor: extractCursor(page.next) };
}

export async function createMessage(
  roomId: number,
  body: string,
  opts?: { replyTo?: number | string; attachment?: File; clientId?: string }
): Promise<Message> {
  if (USE_MOCK) {
    await delay(150);
    const trimmed = body.trim();
    if (!trimmed && !opts?.attachment) throw new ApiError(400, "Message cannot be empty.");
    const mentions = [...trimmed.matchAll(/@(\w+)/g)]
      .map((m) => mockUsers.find((u) => u.username.toLowerCase() === m[1].toLowerCase())?.id)
      .filter((id): id is number => typeof id === "number");
    // Threading: resolve onto the thread root (not the immediate message
    // clicked "reply" on), mirroring the server — see
    // resolveMockReplyRoot's docstring.
    const resolvedReplyTo = resolveMockReplyRoot(roomId, opts?.replyTo);
    const replyPreview =
      resolvedReplyTo != null
        ? (mockMessages[roomId] ?? []).find((m) => m.id === resolvedReplyTo)
        : undefined;
    const message: Message = {
      id: nextMessageId++,
      user: mockCurrentUser,
      body: trimmed,
      mentions,
      created: new Date().toISOString(),
      reply_to: resolvedReplyTo,
      client_id: opts?.clientId,
      reply_to_preview: replyPreview
        ? { id: replyPreview.id as number, user: replyPreview.user, body: replyPreview.body, is_deleted: !!replyPreview.is_deleted }
        : null,
      attachments: opts?.attachment
        ? [
            {
              id: nextMessageId,
              // Mock mode has no backend to mint a real signed URL from —
              // an object URL is a fine stand-in for local preview/testing.
              download_url: URL.createObjectURL(opts.attachment),
              original_filename: opts.attachment.name,
              content_type: opts.attachment.type,
              size_bytes: opts.attachment.size,
              uploaded_at: new Date().toISOString(),
            },
          ]
        : undefined,
      reactions: [],
    };
    (mockMessages[roomId] ??= []).push(message);
    if (resolvedReplyTo != null) {
      // Posting a reply auto-follows the thread (mock mode only ever acts
      // as the current user, so this is the replier-follows-thread half of
      // the server's auto-follow behavior — see
      // MessageListCreateView._handle_thread_reply).
      mockFollowedThreadIds.add(resolvedReplyTo);
    }
    annotateMockThreadFields(roomId);
    publish({ type: "message.new", room_id: roomId, message });
    maybeTriggerAi(roomId, trimmed);
    return message;
  }
  if (opts?.attachment) {
    const form = new FormData();
    form.set("body", body);
    if (opts.replyTo != null) form.set("reply_to", String(opts.replyTo));
    if (opts.clientId) form.set("client_id", opts.clientId);
    form.set("attachment", opts.attachment);
    return apiFetch<Message>(`/rooms/${roomId}/messages/`, { method: "POST", body: form });
  }
  const payload: Record<string, unknown> = { body };
  if (opts?.replyTo != null) payload.reply_to = opts.replyTo;
  if (opts?.clientId) payload.client_id = opts.clientId;
  return apiFetch<Message>(`/rooms/${roomId}/messages/`, { method: "POST", body: JSON.stringify(payload) });
}

export async function editMessage(roomId: number, messageId: number, body: string): Promise<Message> {
  if (USE_MOCK) {
    await delay(150);
    const msg = (mockMessages[roomId] ?? []).find((m) => m.id === messageId);
    if (!msg) throw new ApiError(404, "Message not found.");
    msg.body = body;
    msg.edited_at = new Date().toISOString();
    return msg;
  }
  return apiFetch<Message>(`/rooms/${roomId}/messages/${messageId}/`, {
    method: "PATCH",
    body: JSON.stringify({ body }),
  });
}

export async function deleteMessage(roomId: number, messageId: number): Promise<Message> {
  if (USE_MOCK) {
    await delay(150);
    const msg = (mockMessages[roomId] ?? []).find((m) => m.id === messageId);
    if (!msg) throw new ApiError(404, "Message not found.");
    msg.is_deleted = true;
    msg.deleted_at = new Date().toISOString();
    msg.body = "";
    return msg;
  }
  return apiFetch<Message>(`/rooms/${roomId}/messages/${messageId}/`, { method: "DELETE" });
}

export async function toggleReaction(roomId: number, messageId: number, emoji: string): Promise<Reaction[]> {
  if (USE_MOCK) {
    await delay(120);
    const msg = (mockMessages[roomId] ?? []).find((m) => m.id === messageId);
    if (!msg) throw new ApiError(404, "Message not found.");
    const reactions = [...(msg.reactions ?? [])];
    const uid = mockCurrentUser.id;
    const idx = reactions.findIndex((r) => r.emoji === emoji);
    if (idx >= 0 && reactions[idx].user_ids.includes(uid)) {
      const nextUserIds = reactions[idx].user_ids.filter((id) => id !== uid);
      if (nextUserIds.length === 0) reactions.splice(idx, 1);
      else reactions[idx] = { ...reactions[idx], user_ids: nextUserIds, count: nextUserIds.length };
    } else if (idx >= 0) {
      const nextUserIds = [...reactions[idx].user_ids, uid];
      reactions[idx] = { ...reactions[idx], user_ids: nextUserIds, count: nextUserIds.length };
    } else {
      reactions.push({ emoji, count: 1, user_ids: [uid] });
    }
    msg.reactions = reactions;
    return reactions;
  }
  const res = await apiFetch<{ reactions: Reaction[] }>(`/rooms/${roomId}/messages/${messageId}/reactions/`, {
    method: "POST",
    body: JSON.stringify({ emoji }),
  });
  return res.reactions;
}

// =============================================================================
// Threads
//
// A "thread" is just the set of messages sharing the same root `reply_to`
// (see Message.reply_to's server-side docstring) — there's no separate
// Thread model/id, so these are keyed by the root message's id.
// =============================================================================

/** Root message + every reply, oldest first. `messageId` may be the root
 * itself or one of its replies — both resolve to the same thread (mirrors
 * MessageThreadView's server-side resolution). */
export async function getThread(roomId: number, messageId: number | string): Promise<Message[]> {
  if (USE_MOCK) {
    await delay(200);
    annotateMockThreadFields(roomId);
    const msgs = mockMessages[roomId] ?? [];
    const target = msgs.find((m) => m.id === messageId);
    if (!target) throw new ApiError(404, "Message not found.");
    const rootId = typeof target.reply_to === "number" ? target.reply_to : (target.id as number);
    return msgs
      .filter((m) => m.id === rootId || m.reply_to === rootId)
      .sort((a, b) => new Date(a.created).getTime() - new Date(b.created).getTime());
  }
  return apiFetch<Message[]>(`/rooms/${roomId}/messages/${messageId}/thread/`);
}

function mockResolveThreadRoot(roomId: number, messageId: number | string): number | undefined {
  const target = (mockMessages[roomId] ?? []).find((m) => m.id === messageId);
  if (!target) return undefined;
  return typeof target.reply_to === "number" ? target.reply_to : (target.id as number);
}

export async function followThread(roomId: number, messageId: number | string): Promise<boolean> {
  if (USE_MOCK) {
    await delay(120);
    const rootId = mockResolveThreadRoot(roomId, messageId);
    if (rootId != null) mockFollowedThreadIds.add(rootId);
    annotateMockThreadFields(roomId);
    return true;
  }
  const res = await apiFetch<{ following: boolean }>(`/rooms/${roomId}/messages/${messageId}/follow/`, {
    method: "POST",
  });
  return res.following;
}

export async function unfollowThread(roomId: number, messageId: number | string): Promise<boolean> {
  if (USE_MOCK) {
    await delay(120);
    const rootId = mockResolveThreadRoot(roomId, messageId);
    if (rootId != null) mockFollowedThreadIds.delete(rootId);
    annotateMockThreadFields(roomId);
    return false;
  }
  const res = await apiFetch<{ following: boolean }>(`/rooms/${roomId}/messages/${messageId}/follow/`, {
    method: "DELETE",
  });
  return res.following;
}

// =============================================================================
// Topics
// =============================================================================

export async function listTopics(): Promise<Topic[]> {
  if (USE_MOCK) {
    await delay(150);
    return mockTopics;
  }
  return apiFetch<Topic[]>("/topics/");
}

// =============================================================================
// Users
// =============================================================================

export async function listUsers(): Promise<User[]> {
  if (USE_MOCK) {
    await delay(150);
    return mockUsers;
  }
  return apiFetch<User[]>("/users/");
}

/** One user's public profile (username/avatar/bio/role/is_online) — not
 * scoped to shared rooms/conversations, any authenticated user can view any
 * other's (see playground/api_views.py's UserViewSet). Backs
 * UserProfileCard's "view profile" flow from Chat/RoomSettingsPanel/
 * Directory. */
export async function getUser(id: number): Promise<User> {
  if (USE_MOCK) {
    await delay(120);
    const user = mockUsers.find((u) => u.id === id);
    if (!user) throw new ApiError(404, "User not found.");
    return user;
  }
  return apiFetch<User>(`/users/${id}/`);
}

export async function getMe(): Promise<User> {
  if (USE_MOCK) {
    await delay(100);
    return mockCurrentUser;
  }
  return apiFetch<User>("/users/me/");
}

export async function updateMe(patch: { bio?: string; avatar?: File }): Promise<User> {
  if (USE_MOCK) {
    await delay(300);
    if (patch.bio !== undefined) mockCurrentUser.bio = patch.bio;
    if (patch.avatar) mockCurrentUser.avatar = URL.createObjectURL(patch.avatar);
    return mockCurrentUser;
  }
  const form = new FormData();
  if (patch.bio !== undefined) form.set("bio", patch.bio);
  if (patch.avatar) form.set("avatar", patch.avatar);
  // Note: /users/me/ (used by getMe() above) is a read-only action on the
  // directory ViewSet — GET only. The updatable endpoint is /profile/me/
  // (ProfileView, supports PATCH), same UserProfileSerializer shape either way.
  return apiFetch<User>("/profile/me/", { method: "PATCH", body: form });
}

// =============================================================================
// Notifications
// =============================================================================

export async function listNotifications(): Promise<Notification[]> {
  if (USE_MOCK) {
    await delay(200);
    return [...mockNotifications];
  }
  return apiFetch<Notification[]>("/notifications/");
}

export async function markNotificationRead(id: number): Promise<void> {
  if (USE_MOCK) {
    await delay(100);
    const n = mockNotifications.find((n) => n.id === id);
    if (n) n.is_read = true;
    return;
  }
  await apiFetch<void>(`/notifications/${id}/read/`, { method: "POST" });
}

export async function markAllNotificationsRead(): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    mockNotifications.forEach((n) => (n.is_read = true));
    return;
  }
  await apiFetch<void>("/notifications/read-all/", { method: "POST" });
}

export async function getUnreadCount(): Promise<number> {
  if (USE_MOCK) {
    await delay(100);
    return mockNotifications.filter((n) => !n.is_read).length;
  }
  const res = await apiFetch<{ count: number }>("/notifications/unread-count/");
  return res.count;
}

// =============================================================================
// Notification preferences (global + per-room)
// =============================================================================

let mockNotificationPreference: NotificationPreference = {
  mentions: true,
  channel_wide: true,
  thread_replies: true,
};

export async function getNotificationPreference(): Promise<NotificationPreference> {
  if (USE_MOCK) {
    await delay(150);
    return { ...mockNotificationPreference };
  }
  return apiFetch<NotificationPreference>("/notifications/preferences/");
}

export async function updateNotificationPreference(
  patch: Partial<NotificationPreference>
): Promise<NotificationPreference> {
  if (USE_MOCK) {
    await delay(150);
    mockNotificationPreference = { ...mockNotificationPreference, ...patch };
    return { ...mockNotificationPreference };
  }
  return apiFetch<NotificationPreference>("/notifications/preferences/", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

const mockRoomNotificationSettings: Record<number, RoomNotificationSetting> = {};

export async function getRoomNotificationSetting(roomId: number): Promise<RoomNotificationSetting> {
  if (USE_MOCK) {
    await delay(150);
    return { ...(mockRoomNotificationSettings[roomId] ?? { room: roomId, setting: "default" }) };
  }
  return apiFetch<RoomNotificationSetting>(`/rooms/${roomId}/notification-setting/`);
}

export async function updateRoomNotificationSetting(
  roomId: number,
  setting: RoomNotificationSetting["setting"]
): Promise<RoomNotificationSetting> {
  if (USE_MOCK) {
    await delay(150);
    const updated: RoomNotificationSetting = { room: roomId, setting };
    mockRoomNotificationSettings[roomId] = updated;
    return { ...updated };
  }
  return apiFetch<RoomNotificationSetting>(`/rooms/${roomId}/notification-setting/`, {
    method: "PATCH",
    body: JSON.stringify({ setting }),
  });
}

// =============================================================================
// Webhooks (integrations/webhooks slice) — room admin/workspace admin only.
// Backs RoomSettingsPanel's "Webhooks" section: outgoing (target URL, active
// toggle, delete) and the room's one incoming URL+token. USE_MOCK keeps a
// small in-memory per-room list, same pattern as mockRoomMembers above.
// =============================================================================

const mockWebhooks: Record<number, Webhook[]> = {};
let nextWebhookId = 1;

function mockToken(): string {
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
}

export async function listWebhooks(roomId: number): Promise<Webhook[]> {
  if (USE_MOCK) {
    await delay(200);
    return [...(mockWebhooks[roomId] ?? [])];
  }
  return apiFetch<Webhook[]>(`/rooms/${roomId}/webhooks/`);
}

export async function createWebhook(roomId: number, targetUrl: string): Promise<Webhook> {
  if (USE_MOCK) {
    await delay(250);
    const webhook: Webhook = {
      id: nextWebhookId++,
      room: roomId,
      target_url: targetUrl,
      event_types: ["message.created"],
      is_active: true,
      created_by: mockCurrentUser,
      created_at: new Date().toISOString(),
      incoming_token: mockToken(),
      incoming_url: `${window.location.origin}/api/v1/webhooks/incoming/mock-${nextWebhookId}/`,
      secret: `mock-secret-${mockToken()}`,
    };
    mockWebhooks[roomId] = [webhook, ...(mockWebhooks[roomId] ?? [])];
    return webhook;
  }
  return apiFetch<Webhook>(`/rooms/${roomId}/webhooks/`, {
    method: "POST",
    body: JSON.stringify({ target_url: targetUrl }),
  });
}

export async function updateWebhook(roomId: number, id: number, is_active: boolean): Promise<Webhook> {
  if (USE_MOCK) {
    await delay(150);
    const webhook = (mockWebhooks[roomId] ?? []).find((w) => w.id === id);
    if (!webhook) throw new ApiError(404, "Webhook not found.");
    webhook.is_active = is_active;
    return { ...webhook };
  }
  return apiFetch<Webhook>(`/rooms/${roomId}/webhooks/${id}/`, {
    method: "PATCH",
    body: JSON.stringify({ is_active }),
  });
}

export async function deleteWebhook(roomId: number, id: number): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    mockWebhooks[roomId] = (mockWebhooks[roomId] ?? []).filter((w) => w.id !== id);
    return;
  }
  await apiFetch<void>(`/rooms/${roomId}/webhooks/${id}/`, { method: "DELETE" });
}

// =============================================================================
// Conversations (private 1:1)
//
// Wire shapes here follow playground/api_views.py's PrivateConversationViewSet
// + PrivateMessageListCreateView exactly (UUID ids, `content`/`sender` on
// messages, `participant_ids` on create, server-computed `display_name`/
// `unread_count` on the conversation) rather than mirroring the Room/Message
// shapes. The `to*` helpers below normalize server responses into the app's
// shared `Message`/`Conversation` shapes so the rest of the app (Chat.tsx,
// Sidebar) doesn't need to know DMs and rooms have different wire formats.
// =============================================================================

interface RawPrivateMessage {
  id: string;
  conversation: string;
  sender: User;
  content: string;
  created_at: string;
  client_id?: string | null;
}

interface RawConversation {
  id: string;
  participants: User[];
  last_message: RawPrivateMessage | null;
  display_name: string | null;
  unread_count: number;
  created_at: string;
  updated_at: string;
}

function toMessage(raw: RawPrivateMessage): Message {
  return {
    id: raw.id,
    user: raw.sender,
    body: raw.content,
    mentions: [],
    created: raw.created_at,
    client_id: raw.client_id ?? undefined,
  };
}

function toConversation(raw: RawConversation): Conversation {
  return {
    id: raw.id,
    participants: raw.participants,
    last_message: raw.last_message ? toMessage(raw.last_message) : null,
    display_name: raw.display_name,
    unread_count: raw.unread_count,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

export async function listConversations(): Promise<Conversation[]> {
  if (USE_MOCK) {
    await delay(200);
    return mockConversations.map((c) => ({ ...c }));
  }
  const raw = await apiFetch<RawConversation[]>("/conversations/");
  return raw.map(toConversation);
}

/** Start a 1:1 or group conversation. Pass a single id for a 1:1 DM (kept
 * get-or-create against any existing 1:1 with that user) or multiple ids to
 * start a group (always creates a new conversation — see
 * PrivateConversationViewSet.create's docstring for why group DMs aren't
 * deduped the way 1:1s are). */
export async function createConversation(participantIds: number | number[]): Promise<Conversation> {
  const ids = Array.isArray(participantIds) ? participantIds : [participantIds];
  if (USE_MOCK) {
    await delay(200);
    if (ids.length === 1) {
      const existing = mockConversations.find(
        (c) => c.participants.length === 2 && c.participants.some((p) => p.id === ids[0])
      );
      if (existing) return existing;
    }
    const others = ids.map((id) => mockUsers.find((u) => u.id === id)).filter((u): u is User => !!u);
    if (others.length === 0) throw new ApiError(404, "User not found.");
    const participants = [mockCurrentUser, ...others];
    const conversation: Conversation = {
      id: `conv-${mockConversations.length + 1}`,
      participants,
      last_message: null,
      display_name: others.map((u) => u.username).join(", "),
      unread_count: 0,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    mockConversations.unshift(conversation);
    mockConversationMessages[conversation.id] = [];
    return conversation;
  }
  const raw = await apiFetch<RawConversation>("/conversations/", {
    method: "POST",
    body: JSON.stringify({ participant_ids: ids }),
  });
  return toConversation(raw);
}

export async function deleteConversation(id: string): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    const idx = mockConversations.findIndex((c) => c.id === id);
    if (idx >= 0) mockConversations.splice(idx, 1);
    delete mockConversationMessages[id];
    return;
  }
  await apiFetch<void>(`/conversations/${id}/`, { method: "DELETE" });
}

export async function listConversationMessages(conversationId: string): Promise<Message[]> {
  if (USE_MOCK) {
    await delay(200);
    return [...(mockConversationMessages[conversationId] ?? [])];
  }
  // Also cursor-paginated server-side now (see pagination.py), newest-first
  // — unlike room messages, DMs don't yet have a "load older" trigger in
  // the UI (out of scope for this slice), so this just takes the first
  // page's `results` and reverses it back to chronological order. A
  // long-running DM thread will only show its most recent messages until a
  // future slice adds pagination here too.
  const page = await apiFetch<{ results: RawPrivateMessage[] }>(
    `/conversations/${conversationId}/messages/`
  );
  return [...page.results].reverse().map(toMessage);
}

export async function createConversationMessage(
  conversationId: string,
  body: string,
  opts?: { clientId?: string }
): Promise<Message> {
  if (USE_MOCK) {
    await delay(150);
    const trimmed = body.trim();
    if (!trimmed) throw new ApiError(400, "Message cannot be empty.");
    const message: Message = {
      id: nextMessageId++,
      user: mockCurrentUser,
      body: trimmed,
      mentions: [],
      created: new Date().toISOString(),
      client_id: opts?.clientId,
    };
    (mockConversationMessages[conversationId] ??= []).push(message);
    const convo = mockConversations.find((c) => c.id === conversationId);
    if (convo) {
      convo.last_message = message;
      convo.updated_at = message.created;
    }
    return message;
  }
  // Note: the create serializer's writable field is `content`, matching the
  // wire shape, not `body` (that's only the app-internal Message shape).
  const payload: Record<string, unknown> = { content: body };
  if (opts?.clientId) payload.client_id = opts.clientId;
  const raw = await apiFetch<RawPrivateMessage>(`/conversations/${conversationId}/messages/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return toMessage(raw);
}

// =============================================================================
// Invitations (admin/owner only) — used by ProfilePanel's admin section to
// generate + manage invite links. USE_MOCK keeps a tiny in-memory list so
// the panel is exercisable without a live backend.
// =============================================================================

let mockInvitations: Invitation[] = [];
let nextInvitationId = 1;

export async function listInvitations(): Promise<Invitation[]> {
  if (USE_MOCK) {
    await delay(200);
    return [...mockInvitations];
  }
  return apiFetch<Invitation[]>("/invitations/");
}

export async function createInvitation(data: { role: WorkspaceRole; note?: string }): Promise<Invitation> {
  if (USE_MOCK) {
    await delay(250);
    const invite: Invitation = {
      id: nextInvitationId++,
      note: data.note ?? "",
      invite_link: `${window.location.origin}/join?token=mock-token-${nextInvitationId}`,
      invited_by: mockCurrentUser,
      role: data.role,
      created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
      accepted_by: null,
      accepted_at: null,
      is_expired: false,
    };
    mockInvitations = [invite, ...mockInvitations];
    return invite;
  }
  return apiFetch<Invitation>("/invitations/", { method: "POST", body: JSON.stringify(data) });
}

export async function revokeInvitation(id: number): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    mockInvitations = mockInvitations.filter((i) => i.id !== id);
    return;
  }
  await apiFetch<void>(`/invitations/${id}/`, { method: "DELETE" });
}

// =============================================================================
// Admin / moderation (workspace admin/owner only) — backs AdminPanel.tsx.
// USE_MOCK keeps small in-memory lists, same pattern as mockInvitations
// above, so the panel is exercisable without a live backend.
// =============================================================================

export interface AuditLogPage {
  events: AuditEvent[];
  nextCursor: string | null;
}

let mockAuditEvents: AuditEvent[] = [];
let nextAuditEventId = 1;
let mockModerationActions: ModerationAction[] = [];
let nextModerationActionId = 1;
let mockBlockedKeywords: BlockedKeyword[] = [];
let nextBlockedKeywordId = 1;

function mockLogAudit(
  actor: User,
  action: AuditEvent["action"],
  opts?: { target_user?: User | null; detail?: string }
) {
  mockAuditEvents = [
    {
      id: nextAuditEventId++,
      actor,
      action,
      target_user: opts?.target_user ?? null,
      target_room: null,
      target_room_name: null,
      target_message: null,
      detail: opts?.detail ?? "",
      created_at: new Date().toISOString(),
    },
    ...mockAuditEvents,
  ];
}

export async function listAuditLog(params?: {
  cursor?: string;
  action?: string;
  actor?: number;
  target_user?: number;
}): Promise<AuditLogPage> {
  if (USE_MOCK) {
    await delay(200);
    let events = [...mockAuditEvents];
    if (params?.action) events = events.filter((e) => e.action === params.action);
    if (params?.actor) events = events.filter((e) => e.actor?.id === params.actor);
    if (params?.target_user) events = events.filter((e) => e.target_user?.id === params.target_user);
    return { events, nextCursor: null };
  }
  const qs = new URLSearchParams();
  if (params?.cursor) qs.set("cursor", params.cursor);
  if (params?.action) qs.set("action", params.action);
  if (params?.actor) qs.set("actor", String(params.actor));
  if (params?.target_user) qs.set("target_user", String(params.target_user));
  const suffix = qs.toString() ? `?${qs}` : "";
  const page = await apiFetch<{ results: AuditEvent[]; next: string | null }>(`/admin/audit-log/${suffix}`);
  return { events: page.results, nextCursor: extractCursor(page.next) };
}

export async function listModerationHistory(targetUserId?: number): Promise<ModerationAction[]> {
  if (USE_MOCK) {
    await delay(200);
    return targetUserId
      ? mockModerationActions.filter((a) => a.target_user.id === targetUserId)
      : [...mockModerationActions];
  }
  const qs = targetUserId ? `?target_user=${targetUserId}` : "";
  return apiFetch<ModerationAction[]>(`/admin/moderation/${qs}`);
}

export async function issueModerationAction(
  actor: User,
  data: { target_user: User; action_type: ModerationActionType; reason?: string; expires_at?: string }
): Promise<ModerationAction> {
  if (USE_MOCK) {
    await delay(250);
    const action: ModerationAction = {
      id: nextModerationActionId++,
      target_user: data.target_user,
      action_type: data.action_type,
      issued_by: actor,
      reason: data.reason ?? "",
      created_at: new Date().toISOString(),
      expires_at: data.expires_at ?? null,
    };
    mockModerationActions = [action, ...mockModerationActions];
    const auditAction: AuditEvent["action"] =
      data.action_type === "warning" ? "user_warned" : data.action_type === "timeout" ? "user_timed_out" : "user_banned";
    mockLogAudit(actor, auditAction, { target_user: data.target_user, detail: data.reason });
    return action;
  }
  return apiFetch<ModerationAction>("/admin/moderation/", {
    method: "POST",
    body: JSON.stringify({
      target_user: data.target_user.id,
      action_type: data.action_type,
      reason: data.reason,
      expires_at: data.expires_at,
    }),
  });
}

export async function unbanUser(actor: User, targetUser: User, reason?: string): Promise<ModerationAction> {
  if (USE_MOCK) {
    await delay(200);
    const action: ModerationAction = {
      id: nextModerationActionId++,
      target_user: targetUser,
      action_type: "unban",
      issued_by: actor,
      reason: reason ?? "",
      created_at: new Date().toISOString(),
      expires_at: null,
    };
    mockModerationActions = [action, ...mockModerationActions];
    mockLogAudit(actor, "user_unbanned", { target_user: targetUser, detail: reason });
    return action;
  }
  return apiFetch<ModerationAction>("/admin/moderation/unban/", {
    method: "POST",
    body: JSON.stringify({ target_user: targetUser.id, reason }),
  });
}

export async function listBlockedKeywords(): Promise<BlockedKeyword[]> {
  if (USE_MOCK) {
    await delay(150);
    return [...mockBlockedKeywords];
  }
  return apiFetch<BlockedKeyword[]>("/admin/blocked-keywords/");
}

// ---------------------------------------------------------------------------
// AI activity (workspace admin/owner only) — backs AdminPanel's "AI Activity"
// tab. Mock list is empty by default (nothing to seed it from in mock mode,
// since mock message sends never go through the real AI trigger path).
// ---------------------------------------------------------------------------

export interface AIInteractionPage {
  interactions: AIInteraction[];
  nextCursor: string | null;
}

export async function listAIInteractions(params?: {
  cursor?: string;
  trigger_type?: string;
}): Promise<AIInteractionPage> {
  if (USE_MOCK) {
    await delay(150);
    return { interactions: [], nextCursor: null };
  }
  const qs = new URLSearchParams();
  if (params?.cursor) qs.set("cursor", params.cursor);
  if (params?.trigger_type) qs.set("trigger_type", params.trigger_type);
  const suffix = qs.toString() ? `?${qs}` : "";
  const page = await apiFetch<{ results: AIInteraction[]; next: string | null }>(
    `/admin/ai-interactions/${suffix}`
  );
  return { interactions: page.results, nextCursor: extractCursor(page.next) };
}

export async function addBlockedKeyword(actor: User, phrase: string): Promise<BlockedKeyword> {
  if (USE_MOCK) {
    await delay(200);
    const keyword: BlockedKeyword = {
      id: nextBlockedKeywordId++,
      phrase,
      added_by: actor,
      created_at: new Date().toISOString(),
    };
    mockBlockedKeywords = [keyword, ...mockBlockedKeywords];
    mockLogAudit(actor, "keyword_added", { detail: `phrase: ${phrase}` });
    return keyword;
  }
  return apiFetch<BlockedKeyword>("/admin/blocked-keywords/", {
    method: "POST",
    body: JSON.stringify({ phrase }),
  });
}

export async function removeBlockedKeyword(actor: User, id: number): Promise<void> {
  if (USE_MOCK) {
    await delay(150);
    const keyword = mockBlockedKeywords.find((k) => k.id === id);
    mockBlockedKeywords = mockBlockedKeywords.filter((k) => k.id !== id);
    if (keyword) mockLogAudit(actor, "keyword_removed", { detail: `phrase: ${keyword.phrase}` });
    return;
  }
  await apiFetch<void>(`/admin/blocked-keywords/${id}/`, { method: "DELETE" });
}

// =============================================================================
// Search
//
// GET /api/v1/search/?q=&type=all — combined preview (top ~20 per category,
// see playground/api_views.py's SearchView). Mock mode searches the same
// fixture arrays the rest of this file already uses, so SearchCommand.tsx
// is exercisable without a live backend.
// =============================================================================

export async function search(query: string): Promise<SearchResults> {
  const q = query.trim();
  if (!q) return { messages: [], rooms: [], users: [] };

  if (USE_MOCK) {
    await delay(200);
    const ql = q.toLowerCase();
    const rooms = mockRooms.filter(
      (r) => r.name.toLowerCase().includes(ql) || (r.description ?? "").toLowerCase().includes(ql)
    );
    const users = mockUsers.filter(
      (u) => u.username.toLowerCase().includes(ql) || (u.bio ?? "").toLowerCase().includes(ql)
    );
    const messages: SearchMessageResult[] = [];
    for (const room of mockRooms) {
      for (const m of mockMessages[room.id] ?? []) {
        if (m.is_deleted) continue;
        if (m.body.toLowerCase().includes(ql) && typeof m.id === "number") {
          messages.push({ id: m.id, user: m.user, room: { id: room.id, name: room.name }, body: m.body, created: m.created });
        }
      }
    }
    messages.sort((a, b) => new Date(b.created).getTime() - new Date(a.created).getTime());
    return { messages: messages.slice(0, 20), rooms: rooms.slice(0, 20), users: users.slice(0, 20) };
  }

  const qs = new URLSearchParams({ q, type: "all" });
  return apiFetch<SearchResults>(`/search/?${qs}`);
}

// =============================================================================
// WebSocket stream — one connection for the whole app.
// =============================================================================

export interface StreamHandle {
  send: (event: ClientEvent) => void;
  close: () => void;
}

// Application-level heartbeat: the browser WS API doesn't surface
// low-level ping/pong frames to JS, and some proxies/NATs silently drop
// idle connections without ever sending a close frame — so the client
// pings periodically and expects *some* message back (a "pong", or
// anything else) within a timeout window, or it tears the socket down
// itself so the reconnect logic below can take over rather than trusting
// the browser's own (slow, unreliable) dead-connection detection.
const PING_INTERVAL_MS = 25_000;
const PONG_TIMEOUT_MS = Math.round(PING_INTERVAL_MS * 1.5);

// Reconnect with exponential backoff + jitter: base 1s, doubling each
// attempt, capped at 30s. Judgment call: retries forever at the capped
// interval rather than giving up after N attempts — this is a chat app
// people leave open for hours, and an indefinite-but-capped retry loop is
// simpler than building a "give up + manual retry" affordance while still
// behaving reasonably (it never hammers the server harder than once every
// ~30s once backed off).
const BASE_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

export function connectStream(
  onEvent: (event: ServerEvent) => void,
  onStatusChange: (status: ConnectionStatus) => void
): StreamHandle {
  if (USE_MOCK) {
    onStatusChange("connecting");
    const timer = window.setTimeout(() => onStatusChange("open"), 400);
    busListeners.add(onEvent);
    startPeerActivity();
    return {
      send: (_event: ClientEvent) => {
        // room.join / room.leave are no-ops in mock mode: the fixture
        // already holds every room's messages client-side.
      },
      close: () => {
        window.clearTimeout(timer);
        busListeners.delete(onEvent);
        onStatusChange("closed");
      },
    };
  }

  let socket: WebSocket | null = null;
  let closedByCaller = false;
  let reconnectAttempt = 0;
  let hasConnectedBefore = false;
  let reconnectTimer: number | undefined;
  let pingTimer: number | undefined;
  let pongTimeoutTimer: number | undefined;

  function clearHeartbeatTimers() {
    if (pingTimer != null) window.clearInterval(pingTimer);
    if (pongTimeoutTimer != null) window.clearTimeout(pongTimeoutTimer);
    pingTimer = undefined;
    pongTimeoutTimer = undefined;
  }

  // Any inbound frame (pong or otherwise) proves the connection is still
  // alive, so it cancels the pending "did we hear back?" timeout.
  function noteActivity() {
    if (pongTimeoutTimer != null) {
      window.clearTimeout(pongTimeoutTimer);
      pongTimeoutTimer = undefined;
    }
  }

  function armHeartbeat() {
    clearHeartbeatTimers();
    pingTimer = window.setInterval(() => {
      if (socket?.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({ type: "ping" }));
      pongTimeoutTimer = window.setTimeout(() => {
        // No message heard back within the timeout window — treat the
        // connection as dead even though the browser hasn't noticed yet.
        socket?.close();
      }, PONG_TIMEOUT_MS);
    }, PING_INTERVAL_MS);
  }

  function scheduleReconnect() {
    if (closedByCaller) return;
    const delay = Math.min(BASE_RECONNECT_DELAY_MS * 2 ** reconnectAttempt, MAX_RECONNECT_DELAY_MS);
    const jitter = delay * 0.2 * (Math.random() * 2 - 1); // ±20%
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(connect, Math.max(0, delay + jitter));
  }

  function connect() {
    if (closedByCaller) return;
    onStatusChange(hasConnectedBefore ? "reconnecting" : "connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws/stream/`);

    socket.addEventListener("open", () => {
      reconnectAttempt = 0;
      hasConnectedBefore = true;
      onStatusChange("open");
      armHeartbeat();
    });
    socket.addEventListener("close", () => {
      clearHeartbeatTimers();
      if (closedByCaller) {
        onStatusChange("closed");
        return;
      }
      onStatusChange("reconnecting");
      scheduleReconnect();
    });
    socket.addEventListener("error", () => {
      // The browser also fires "close" right after "error" for a failed/
      // dropped connection — let that single close handler own the
      // reconnect decision instead of double-scheduling here.
      socket?.close();
    });
    socket.addEventListener("message", (evt) => {
      noteActivity();
      try {
        const data = JSON.parse(evt.data);
        if (data.type === "pong") return;
        // The backend's private-message push carries the raw PrivateMessage
        // serializer shape (sender/content/created_at), same as REST — reuse
        // the same conversion the REST path already applies so callers only
        // ever see the app's normalized Message shape.
        if (data.type === "private_message.new") {
          data.message = toMessage(data.message);
        }
        onEvent(data);
      } catch {
        // ignore malformed frames
      }
    });
  }

  connect();

  return {
    send: (event: ClientEvent) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(event));
    },
    close: () => {
      closedByCaller = true;
      clearHeartbeatTimers();
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer);
      socket?.close();
    },
  };
}
