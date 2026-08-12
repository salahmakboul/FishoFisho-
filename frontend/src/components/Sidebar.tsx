import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { Conversation, Room, User } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { CountBadge } from "./MentionBadge";
import styles from "./Sidebar.module.css";

type AsyncStatus = "idle" | "loading" | "ready" | "error";
type Tab = "rooms" | "dm";

interface SidebarProps {
  rooms: Room[];
  status: AsyncStatus;
  error?: string;
  activeRoomId: number | null;
  onSelectRoom: (id: number) => void;
  onCreateRoom: (name: string, isPrivate: boolean) => Promise<unknown>;

  currentUser: User | null;
  conversations: Conversation[];
  conversationsStatus: AsyncStatus;
  conversationsError?: string;
  activeConversationId: string | null;
  unreadConversationCount: number;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onOpenDirectory: () => void;
}

/** The DM partner to display for a conversation row — everyone but "me",
 * falling back to the first participant so a malformed/self conversation
 * still renders something instead of a blank row. Exported for Layout.tsx,
 * which needs the same lookup to build the DM thread header. */
export function otherParticipant(conversation: Conversation, currentUser: User | null): User | undefined {
  return conversation.participants.find((p) => p.id !== currentUser?.id) ?? conversation.participants[0];
}

export function Sidebar({
  rooms,
  status,
  error,
  activeRoomId,
  onSelectRoom,
  onCreateRoom,
  currentUser,
  conversations,
  conversationsStatus,
  conversationsError,
  activeConversationId,
  unreadConversationCount,
  onSelectConversation,
  onDeleteConversation,
  onOpenDirectory,
}: SidebarProps) {
  const [tab, setTab] = useState<Tab>("rooms");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newIsPrivate, setNewIsPrivate] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const dmItemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const filtered = rooms.filter((r) => r.name.toLowerCase().includes(query.toLowerCase()));

  function handleListKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      itemRefs.current[Math.min(index + 1, filtered.length - 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      itemRefs.current[Math.max(index - 1, 0)]?.focus();
    }
  }

  function handleDmListKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      dmItemRefs.current[Math.min(index + 1, conversations.length - 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      dmItemRefs.current[Math.max(index - 1, 0)]?.focus();
    }
  }

  async function submitNewRoom() {
    if (!newName.trim()) {
      setCreateError("Room name is required.");
      return;
    }
    setSubmitting(true);
    setCreateError(null);
    try {
      await onCreateRoom(newName, newIsPrivate);
      setNewName("");
      setNewIsPrivate(false);
      setCreating(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Could not create room.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <nav className={styles.sidebar} aria-label="Workspace navigation">
      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "rooms"}
          className={[styles.tab, tab === "rooms" ? styles.tabActive : ""].join(" ")}
          onClick={() => setTab("rooms")}
        >
          Rooms
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "dm"}
          className={[styles.tab, tab === "dm" ? styles.tabActive : ""].join(" ")}
          onClick={() => setTab("dm")}
        >
          Direct messages
          <CountBadge count={unreadConversationCount} />
        </button>
      </div>

      {tab === "rooms" && (
        <>
          <div className={styles.head}>
            <span className={styles.title}>Rooms</span>
            <Button variant="ghost" icon aria-label="Create room" onClick={() => setCreating((v) => !v)}>
              +
            </Button>
          </div>

          <div className={styles.searchWrap}>
            <input
              className={styles.search}
              type="text"
              placeholder="Search rooms"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search rooms"
            />
          </div>

          {status === "loading" && <p className={styles.status}>Loading rooms…</p>}
          {status === "error" && <p className={styles.status}><span className={styles.errorText}>{error ?? "Couldn't load rooms."}</span></p>}
          {status === "ready" && filtered.length === 0 && (
            <p className={styles.status}>{query ? "No rooms match your search." : "No rooms yet."}</p>
          )}

          {status === "ready" && filtered.length > 0 && (
            <ul className={styles.list}>
              {filtered.map((room, i) => (
                <li key={room.id}>
                  <button
                    ref={(el) => (itemRefs.current[i] = el)}
                    type="button"
                    className={[styles.item, room.id === activeRoomId ? styles.itemActive : ""].join(" ")}
                    aria-current={room.id === activeRoomId}
                    onClick={() => onSelectRoom(room.id)}
                    onKeyDown={(e) => handleListKeyDown(e, i)}
                  >
                    <span className={styles.hash}>{room.is_private ? "🔒" : "#"}</span>
                    <span className={styles.name}>{room.name}</span>
                    <CountBadge count={room.unread_count} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {tab === "dm" && (
        <>
          <div className={styles.head}>
            <span className={styles.title}>Direct messages</span>
            <Button variant="ghost" icon aria-label="Start a new conversation" onClick={onOpenDirectory}>
              +
            </Button>
          </div>

          {conversationsStatus === "loading" && <p className={styles.status}>Loading conversations…</p>}
          {conversationsStatus === "error" && (
            <p className={styles.status}>
              <span className={styles.errorText}>{conversationsError ?? "Couldn't load conversations."}</span>
            </p>
          )}
          {conversationsStatus === "ready" && conversations.length === 0 && (
            <div className={styles.dmEmpty}>
              <p className={styles.status}>No conversations yet.</p>
              <Button variant="secondary" onClick={onOpenDirectory}>
                Browse people to message
              </Button>
            </div>
          )}

          {conversationsStatus === "ready" && conversations.length > 0 && (
            <ul className={styles.list}>
              {conversations.map((conversation, i) => {
                const other = otherParticipant(conversation, currentUser);
                if (!other) return null;
                // Groups (3+ participants) show the server-derived comma
                // list (see PrivateConversationSerializer.get_display_name);
                // 1:1 DMs keep showing just the other person's username.
                // Presence only makes sense for a single other person, so
                // the dot is limited to that case too.
                const isGroup = conversation.participants.length > 2;
                const label = isGroup ? conversation.display_name ?? other.username : other.username;
                const last = conversation.last_message;
                const preview = last
                  ? `${last.user.id === currentUser?.id ? "You: " : ""}${last.body}`
                  : "No messages yet";
                return (
                  <li key={conversation.id} className={styles.dmRow}>
                    <button
                      ref={(el) => (dmItemRefs.current[i] = el)}
                      type="button"
                      className={[styles.item, conversation.id === activeConversationId ? styles.itemActive : ""].join(" ")}
                      aria-current={conversation.id === activeConversationId}
                      onClick={() => onSelectConversation(conversation.id)}
                      onKeyDown={(e) => handleDmListKeyDown(e, i)}
                    >
                      <span className={styles.avatarWrap}>
                        <Avatar user={other} size="sm" />
                        {!isGroup && (
                          <span
                            className={[styles.presenceDot, other.is_online ? styles.presenceOnline : ""].join(" ")}
                            aria-label={other.is_online ? "Online" : "Offline"}
                            title={other.is_online ? "Online" : "Offline"}
                          />
                        )}
                      </span>
                      <span className={styles.dmBody}>
                        <span className={styles.name}>{label}</span>
                        <span className={styles.preview}>{preview}</span>
                      </span>
                      <CountBadge count={conversation.unread_count} />
                    </button>
                    <button
                      type="button"
                      className={styles.deleteBtn}
                      aria-label={`Delete conversation with ${other.username}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteConversation(conversation.id);
                      }}
                    >
                      ×
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      {creating && (
        <div className={styles.overlay} onMouseDown={() => setCreating(false)}>
          <div
            className={styles.modalPanel}
            role="dialog"
            aria-modal="true"
            aria-label="Create room"
            onMouseDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setCreating(false)}
          >
            <div className={styles.modalHead}>
              <span className={styles.modalTitle}>Create a room</span>
              <button type="button" className={styles.closeBtn} aria-label="Close" onClick={() => setCreating(false)}>
                Esc
              </button>
            </div>
            <div className={styles.modalBody}>
              <input
                className={styles.formInput}
                autoFocus
                placeholder="room-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submitNewRoom()}
                aria-label="New room name"
                aria-invalid={!!createError}
              />
              <label className={styles.privateToggle}>
                <input
                  type="checkbox"
                  checked={newIsPrivate}
                  onChange={(e) => setNewIsPrivate(e.target.checked)}
                />
                Private room
              </label>
              {createError && (
                <p className={styles.status}>
                  <span className={styles.errorText}>{createError}</span>
                </p>
              )}
              <Button variant="primary" loading={submitting} onClick={submitNewRoom}>
                Create
              </Button>
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
