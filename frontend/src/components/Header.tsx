import { useEffect, useMemo, useRef, useState } from "react";
import type { ConnectionStatus, Notification, NotificationType } from "../types";
import { CountBadge } from "./MentionBadge";
import styles from "./Header.module.css";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

type NotifFilter = "all" | NotificationType;

// Server labels (Notification.NOTIFICATION_TYPES) mapped to short filter-chip
// text. "mention" also covers @channel/@here fan-out — the model doesn't
// carry a separate category for that (see playground/models.py's
// check_mentions, which files both under notification_type="mention") — so
// there's no distinct "channel-wide" filter chip to offer here.
const TYPE_LABELS: Record<NotificationType, string> = {
  mention: "Mentions",
  message: "Messages",
  system: "System",
  thread_reply: "Threads",
};

interface HeaderProps {
  /** Room name (e.g. "#general") or DM participant name — whatever's
   * currently focused in the main pane. Falls back to the app name when
   * nothing is selected yet. */
  title: string;
  subtitle?: string;
  meta?: string;
  connectionStatus: ConnectionStatus;
  notifications: Notification[];
  unreadCount: number;
  onMarkNotificationRead: (id: number) => void;
  onMarkAllRead: () => void;
  aiOpen: boolean;
  onToggleAi: () => void;
  /** Room-settings affordance next to the room name — only passed (and
   * rendered) when the current user is that room's admin or a workspace
   * admin/owner; absent entirely for DMs and for rooms the user can't
   * manage. */
  roomSettingsOpen?: boolean;
  onToggleRoomSettings?: () => void;
  /** Admin panel toggle (audit log / moderation / blocked keywords) — only
   * passed (and rendered) for a workspace admin/owner, same "absent
   * entirely rather than disabled" pattern as onToggleRoomSettings above. */
  adminOpen?: boolean;
  onToggleAdmin?: () => void;
  /** Opens the SearchCommand overlay (Layout.tsx owns its open/close
   * state, same pattern as the AI/notifications/profile panels). */
  onOpenSearch: () => void;
}

export function Header({
  title,
  subtitle,
  meta,
  connectionStatus,
  notifications,
  unreadCount,
  onMarkNotificationRead,
  onMarkAllRead,
  aiOpen,
  onToggleAi,
  roomSettingsOpen,
  onToggleRoomSettings,
  adminOpen,
  onToggleAdmin,
  onOpenSearch,
}: HeaderProps) {
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifFilter, setNotifFilter] = useState<NotifFilter>("all");
  const popRef = useRef<HTMLDivElement>(null);

  // Only offer filter chips for types actually present, so the row doesn't
  // show e.g. "Threads" when nobody's ever gotten a thread-reply notification.
  const typesPresent = useMemo(
    () => Array.from(new Set(notifications.map((n) => n.notification_type))),
    [notifications]
  );
  const filteredNotifications =
    notifFilter === "all" ? notifications : notifications.filter((n) => n.notification_type === notifFilter);

  useEffect(() => {
    if (!notifOpen) return;
    function onClick(e: MouseEvent) {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setNotifOpen(false);
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") setNotifOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [notifOpen]);

  const statusLabel =
    connectionStatus === "open"
      ? "Live"
      : connectionStatus === "connecting"
        ? "Connecting…"
        : connectionStatus === "reconnecting"
          ? "Reconnecting…"
          : "Offline";

  return (
    <header className={styles.header}>
      <div className={styles.roomInfo}>
        <span className={styles.roomName}>{title}</span>
        {subtitle && <span className={styles.roomDesc}>{subtitle}</span>}
        {onToggleRoomSettings && (
          <button
            type="button"
            className={[styles.iconButton, roomSettingsOpen ? styles.iconButtonActive : ""].join(" ")}
            aria-pressed={roomSettingsOpen}
            aria-label="Room settings"
            title="Room settings"
            onClick={onToggleRoomSettings}
          >
            ⚙
          </button>
        )}
      </div>

      <div className={styles.actions}>
        {meta && <span className={styles.memberCount}>{meta}</span>}
        <button
          type="button"
          className={styles.iconButton}
          aria-label="Search rooms, messages, and people"
          title="Search (Ctrl+K)"
          onClick={onOpenSearch}
        >
          ⌕
        </button>
        <span className={styles.statusLabel} title={statusLabel}>
          <span className={[styles.statusDot, connectionStatus === "open" ? styles.statusOpen : ""].join(" ")} />
          {statusLabel}
        </span>

        <button
          type="button"
          className={[styles.iconButton, aiOpen ? styles.iconButtonActive : ""].join(" ")}
          aria-pressed={aiOpen}
          aria-label="AI assistant"
          onClick={onToggleAi}
        >
          AI
        </button>

        {onToggleAdmin && (
          <button
            type="button"
            className={[styles.iconButton, adminOpen ? styles.iconButtonActive : ""].join(" ")}
            aria-pressed={adminOpen}
            aria-label="Admin"
            title="Admin"
            onClick={onToggleAdmin}
          >
            ⚑
          </button>
        )}

        <div ref={popRef} style={{ position: "relative" }}>
          <button
            type="button"
            className={[styles.iconButton, notifOpen ? styles.iconButtonActive : ""].join(" ")}
            aria-haspopup="true"
            aria-expanded={notifOpen}
            aria-label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ""}`}
            onClick={() => setNotifOpen((v) => !v)}
          >
            ●
            {unreadCount > 0 && <span className={styles.badgeCorner}><CountBadge count={unreadCount} /></span>}
          </button>

          {notifOpen && (
            <div className={styles.popover} role="menu">
              <div className={styles.popoverHead}>
                <span className={styles.popoverTitle}>Notifications</span>
                {notifications.some((n) => !n.is_read) && (
                  <button type="button" className={styles.markAllBtn} onClick={onMarkAllRead}>
                    Mark all read
                  </button>
                )}
              </div>
              {typesPresent.length > 1 && (
                <div className={styles.filterRow}>
                  <button
                    type="button"
                    className={[styles.filterBtn, notifFilter === "all" ? styles.filterBtnActive : ""].join(" ")}
                    onClick={() => setNotifFilter("all")}
                  >
                    All
                  </button>
                  {typesPresent.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={[styles.filterBtn, notifFilter === t ? styles.filterBtnActive : ""].join(" ")}
                      onClick={() => setNotifFilter(t)}
                    >
                      {TYPE_LABELS[t] ?? t}
                    </button>
                  ))}
                </div>
              )}
              {filteredNotifications.length === 0 ? (
                <p className={styles.notifItem}>
                  {notifications.length === 0
                    ? "You're all caught up — no notifications yet."
                    : "Nothing in this category."}
                </p>
              ) : (
                filteredNotifications.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    role="menuitem"
                    className={[styles.notifItem, !n.is_read ? styles.notifUnread : ""].join(" ")}
                    onClick={() => onMarkNotificationRead(n.id)}
                  >
                    <span>
                      {!n.is_read && <span className={styles.dot} aria-hidden="true" />}
                      {n.message}
                    </span>
                    <span className={styles.notifMeta}>{timeAgo(n.created_at)}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
