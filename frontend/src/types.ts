// Shared types matching the /api/v1/ REST + WS contract.

export type WorkspaceRole = "owner" | "admin" | "member" | "guest";

export interface User {
  id: number;
  username: string;
  avatar: string | null;
  bio?: string;
  is_bot?: boolean;
  role?: WorkspaceRole;
  // Minimal presence (see playground/models.py's UserProfile.is_online):
  // true if a "last seen" heartbeat arrived within the last ~45s. Optional
  // because mock-mode fixtures and older cached data may not set it.
  is_online?: boolean;
  // Moderation slice (see playground/models.py's UserProfile.is_banned).
  // Optional for the same reason as is_online above.
  is_banned?: boolean;
}

export interface Topic {
  id: number;
  name: string;
}

export interface Room {
  id: number;
  name: string;
  topic: Topic | null;
  description?: string;
  member_count?: number;
  unread_count: number;
  is_private?: boolean;
  is_archived?: boolean;
  // The requesting user's RoomMembership role in this room ("admin" |
  // "member"), or null if they're not a member (e.g. viewing a public room
  // they haven't posted in yet). Server-computed (RoomSerializer.get_my_role)
  // so the frontend can gate the room-settings affordance without a second
  // request per room.
  my_role?: "admin" | "member" | null;
}

export interface RoomMember {
  id: number;
  user: User;
  role: "admin" | "member";
  joined_at: string;
}

export interface Invitation {
  id: number;
  note: string;
  invite_link: string;
  invited_by: User;
  role: WorkspaceRole;
  created_at: string;
  expires_at: string;
  accepted_by: User | null;
  accepted_at: string | null;
  is_expired: boolean;
}

// ---------------------------------------------------------------------------
// Webhooks (integrations/webhooks slice — see playground/models.py's
// Webhook/WebhookDelivery). One row covers both directions for a room:
// outgoing (target_url + event_types, HMAC-signed with a secret shown once
// at creation) and incoming (incoming_token/incoming_url, always visible —
// same "always readable" treatment as Invitation.invite_link above).
// ---------------------------------------------------------------------------
export interface Webhook {
  id: number;
  room: number;
  target_url: string;
  event_types: string[];
  is_active: boolean;
  created_by: User | null;
  created_at: string;
  incoming_token: string;
  incoming_url: string;
  /** Only ever present on the response to createWebhook() — the signing
   * secret is shown exactly once and never returned by any later list/GET
   * (see WebhookSerializer's docstring on the backend). */
  secret?: string;
}

// ---------------------------------------------------------------------------
// Admin / moderation slice (see playground/models.py's AuditEvent,
// ModerationAction, BlockedKeyword)
// ---------------------------------------------------------------------------

export type AuditAction =
  | "role_changed"
  | "member_removed"
  | "room_archived"
  | "room_deleted"
  | "message_removed"
  | "user_warned"
  | "user_timed_out"
  | "user_banned"
  | "user_unbanned"
  | "invitation_created"
  | "invitation_revoked"
  | "keyword_added"
  | "keyword_removed";

export interface AuditEvent {
  id: number;
  actor: User | null;
  action: AuditAction;
  target_user: User | null;
  target_room: number | null;
  target_room_name: string | null;
  target_message: number | null;
  detail: string;
  created_at: string;
}

export type ModerationActionType = "warning" | "timeout" | "ban" | "unban";

export interface ModerationAction {
  id: number;
  target_user: User;
  action_type: ModerationActionType;
  issued_by: User | null;
  reason: string;
  created_at: string;
  expires_at: string | null;
}

export interface BlockedKeyword {
  id: number;
  phrase: string;
  added_by: User | null;
  created_at: string;
}

export type AITriggerType = "mention" | "random" | "summarize";

/** Audit row for one FishoAI invocation — backs the AdminPanel "AI Activity"
 * tab. Mirrors playground.models.AIInteraction/AIInteractionSerializer. */
export interface AIInteraction {
  id: number;
  user: User | null;
  room: number;
  room_name: string | null;
  trigger_type: AITriggerType;
  prompt_context_message_count: number;
  response_message: number | null;
  succeeded: boolean;
  created_at: string;
}

/** One emoji's aggregate reaction count on a message. `user_ids` (rather
 * than a single `reacted_by_me` bool) is what the server sends, so it works
 * for both the REST response and the shared "message.reactions_changed" WS
 * broadcast (which can't be personalized per-recipient) — the frontend
 * derives "did I react?" itself by checking its own id against the list. */
export interface Reaction {
  emoji: string;
  count: number;
  user_ids: number[];
}

export interface MessageAttachment {
  id: number;
  // Signed, time-limited download URL (see playground/signing.py on the
  // backend) — NOT a raw media path. Freshly generated every time the
  // message is (re)serialized, so it's always valid for a fresh window as
  // of whenever the message list/detail was last fetched. Since the link
  // expires (~10 minutes server-side), don't cache a message list across a
  // long-lived session without re-fetching — this app already re-fetches
  // the room's message list on room open, which is enough (see Chat.tsx).
  download_url: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
}

/** Compact quoted-message preview for `Message.reply_to` — just enough to
 * render the "replying to: ..." chip. Null when the message isn't a reply,
 * or when it was a reply but the original was since deleted (reply_to uses
 * SET_NULL server-side, so the reply survives but loses its preview). */
export interface ReplyPreview {
  id: number;
  user: User;
  body: string;
  is_deleted: boolean;
}

export interface Message {
  // Room messages use numeric ids; private messages (see Conversation below)
  // use UUID strings on the wire. Both are normalized into this same shape
  // by lib/api.ts so Chat.tsx can render either without caring which.
  id: number | string;
  user: User;
  body: string;
  mentions: number[];
  created: string;
  // Room messages only (private messages don't carry these yet) — optional
  // so the Conversation/DM path (toMessage() in api.ts) doesn't need to
  // fake them.
  room?: number;
  edited_at?: string | null;
  is_deleted?: boolean;
  deleted_at?: string | null;
  reply_to?: number | null;
  reply_to_preview?: ReplyPreview | null;
  // --- threading ---
  // Only meaningful on a thread root (reply_to is null/undefined) — a
  // reply's own reply_count is always 0 since replies never chain their own
  // replies (see server-side Message.reply_to's docstring). Absent on
  // locally-created optimistic-send placeholders until the server responds.
  reply_count?: number;
  is_thread_root?: boolean;
  is_following_thread?: boolean;
  reactions?: Reaction[];
  attachments?: MessageAttachment[];
  // Client-generated key sent with the create request (see lib/api.ts's
  // createMessage/createConversationMessage) — lets a retried POST be
  // recognized as "the same send attempt" server-side (idempotent dedup)
  // and lets the optimistic-send flow below match its local placeholder
  // back up to the server's real response. Absent on messages loaded from
  // history that predate this field, or that were never sent with one.
  client_id?: string;
  // Optimistic-send local state (see useWorkspace.ts's sendMessage /
  // sendConversationMessage). Undefined/absent for a normal,
  // server-confirmed message — only set on a message the client itself
  // just sent, while its outcome is still pending or has failed.
  status?: "sending" | "failed";
}

/** A message search hit (see GET /api/v1/search/). Deliberately compact
 * compared to the full Message shape — search results only need enough to
 * render a result row and jump to it, not reactions/attachments/thread
 * state. `room` carries both id and name (unlike Message.room, which is
 * just a numeric id) since the result needs to be labeled *before* the
 * frontend has necessarily loaded that room. */
export interface SearchMessageResult {
  id: number;
  user: User;
  room: { id: number; name: string };
  body: string;
  created: string;
}

/** GET /api/v1/search/?type=all's combined preview shape: a fixed-size
 * (top ~20) slice per category, unpaginated — see SearchView's docstring
 * in playground/api_views.py. */
export interface SearchResults {
  messages: SearchMessageResult[];
  rooms: Room[];
  users: User[];
}

export type NotificationType = "mention" | "message" | "system" | "thread_reply";

export interface Notification {
  id: number;
  notification_type: NotificationType;
  message: string;
  room_id: number | null;
  created_at: string;
  is_read: boolean;
}

/** The requesting user's own global per-category notification toggles.
 * Only covers categories that actually produce a `Notification` row today
 * (see playground/models.py's NotificationPreference docstring) — direct
 * messages stay WS-only (private_message.new + Conversation.unread_count)
 * and aren't included here. */
export interface NotificationPreference {
  mentions: boolean;
  channel_wide: boolean;
  thread_replies: boolean;
}

/** The requesting user's own per-room override — "default" falls back to
 * NotificationPreference above; the others customize/mute just this room. */
export type RoomNotificationLevel = "default" | "all" | "mentions_only" | "muted";

export interface RoomNotificationSetting {
  room: number;
  setting: RoomNotificationLevel;
}

export interface Conversation {
  // The backend's PrivateConversation model keys on a UUID, not an int.
  id: string;
  participants: User[];
  last_message: Message | null;
  // Server-computed (PrivateConversationSerializer.get_display_name):
  // the other participant's username for a 1:1 DM, or a comma-joined list
  // of usernames for a group (3+ participants). Sidebar/Layout can use this
  // directly instead of re-deriving it from `participants`.
  display_name: string | null;
  // Real per-participant unread count now (see playground/models.py's
  // ConversationRead + PrivateConversation.unread_count_for) — not the old
  // 0/1 approximation.
  unread_count: number;
  created_at: string;
  updated_at: string;
}

// ---- WebSocket envelope types ----

export type ClientEvent =
  | { type: "room.join"; room_id: number }
  | { type: "room.leave"; room_id: number }
  // Application-level heartbeat (see lib/api.ts's connectStream) — browsers
  // don't surface low-level WS ping/pong to JS, and some proxies/NATs
  // silently drop idle connections without a close frame, so the client
  // pings periodically and expects a "pong" back.
  | { type: "ping" };

export type ServerEvent =
  | { type: "message.new"; room_id: number; message: Message }
  // "message.updated"/"message.deleted"/"message.reactions_changed" don't
  // carry a top-level room_id from the server (see consumers.py) — the
  // frontend reads the room id off `message.room` (updated/deleted) or
  // looks the message up by id across loaded rooms (reactions_changed).
  | { type: "message.updated"; message: Message }
  | { type: "message.deleted"; message: Message }
  | { type: "message.reactions_changed"; message_id: number; reactions: Reaction[] }
  | { type: "notification.new"; notification: Notification }
  | { type: "private_message.new"; conversation_id: string; message: Message }
  | { type: "pong" };

// "reconnecting" is distinct from the initial "connecting": it's shown
// while a previously-open connection is being re-established after a drop
// (see lib/api.ts's connectStream exponential-backoff reconnect loop).
export type ConnectionStatus = "connecting" | "open" | "closed" | "reconnecting";
