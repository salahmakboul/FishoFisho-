import { useEffect, useState } from "react";
import * as api from "../lib/api";
import type { User } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import styles from "./UserProfileCard.module.css";
import sidebarStyles from "./Sidebar.module.css";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  guest: "Guest",
};

/**
 * Centered modal showing one user's public profile — avatar, role, online
 * status, bio — with a "Message" action that starts/opens a DM with them.
 * The single click-target for "view someone's profile" across the app:
 * Chat.tsx (message author), RoomSettingsPanel.tsx (member row), and
 * Directory.tsx (person row) all open this same card for a user id, rather
 * than each growing its own bespoke profile display.
 *
 * Reuses Sidebar.module.css's overlay/modalPanel recipe (the same centered-
 * dialog, click-outside/Escape-to-close pattern as the create-room popup)
 * so every modal in the app reads as one system.
 */
export function UserProfileCard({
  userId,
  currentUser,
  onClose,
  onStartConversation,
}: {
  userId: number;
  currentUser: User | null;
  onClose: () => void;
  onStartConversation: (userId: number) => Promise<unknown>;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [messaging, setMessaging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setUser(null);
    setError(null);
    api
      .getUser(userId)
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        setStatus("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Couldn't load this profile.");
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  async function handleMessage() {
    setMessaging(true);
    setError(null);
    try {
      await onStartConversation(userId);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start that conversation.");
    } finally {
      setMessaging(false);
    }
  }

  const isSelf = currentUser?.id === userId;

  return (
    <div className={sidebarStyles.overlay} onMouseDown={onClose}>
      <div
        className={sidebarStyles.modalPanel}
        role="dialog"
        aria-modal="true"
        aria-label="User profile"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      >
        <div className={sidebarStyles.modalHead}>
          <span className={sidebarStyles.modalTitle}>Profile</span>
          <button type="button" className={sidebarStyles.closeBtn} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className={sidebarStyles.modalBody}>
          {status === "loading" && <p className={styles.status}>Loading…</p>}
          {status === "error" && <p className={styles.status}>{error ?? "Couldn't load this profile."}</p>}
          {status === "ready" && user && (
            <>
              <div className={styles.head}>
                <Avatar user={user} size="lg" />
                <div className={styles.identity}>
                  <span className={styles.username}>{user.username}</span>
                  <span className={styles.badges}>
                    {user.role && <span className={styles.roleBadge}>{ROLE_LABEL[user.role] ?? user.role}</span>}
                    <span className={styles.presence}>
                      <span className={[styles.dot, user.is_online ? styles.dotOnline : ""].join(" ")} />
                      {user.is_online ? "Online" : "Offline"}
                    </span>
                  </span>
                </div>
              </div>

              <p className={styles.bio}>{user.bio ? user.bio : "No bio yet."}</p>

              {error && <p className={styles.error}>{error}</p>}

              {!isSelf && (
                <Button variant="primary" loading={messaging} onClick={handleMessage}>
                  Message
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
