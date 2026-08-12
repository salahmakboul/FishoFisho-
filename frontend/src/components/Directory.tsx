import { useMemo, useState } from "react";
import type { User } from "../types";
import { Avatar } from "./Avatar";
import { EmptyState } from "./EmptyState";
import styles from "./Directory.module.css";

/**
 * User directory aside panel — the successor to the old server-rendered
 * `users_directory` page. Lets you browse everyone in the workspace and
 * start (or jump back into) a direct-message conversation with them.
 * Rendered the same way as AIAssistantPanel/ProfilePanel: an aside slotted
 * into Layout, closed with the × button or Escape.
 */
export function Directory({
  users,
  currentUser,
  onClose,
  onStartConversation,
  onViewProfile,
}: {
  users: User[];
  currentUser: User | null;
  onClose: () => void;
  onStartConversation: (userIds: number | number[]) => Promise<unknown>;
  /** Opens UserProfileCard for a clicked row's avatar/username — kept
   * distinct from the row's "Message" action (which starts a DM directly)
   * so the row isn't ambiguous between "open profile" and "start DM",
   * matching the same avatar/username-vs-actions split used in
   * Chat.tsx/RoomSettingsPanel.tsx. */
  onViewProfile?: (userId: number) => void;
}) {
  const [query, setQuery] = useState("");
  const [startingId, setStartingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Group mode: pick 2+ people to start a group DM instead of the default
  // click-to-message single-person flow. Extends this existing picker
  // rather than building a separate one, per the DM/group-messaging slice.
  const [groupMode, setGroupMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [starting, setStarting] = useState(false);

  const filtered = useMemo(() => {
    const others = users.filter((u) => u.id !== currentUser?.id);
    const q = query.trim().toLowerCase();
    return q ? others.filter((u) => u.username.toLowerCase().includes(q)) : others;
  }, [users, currentUser, query]);

  async function handleStart(user: User) {
    setStartingId(user.id);
    setError(null);
    try {
      await onStartConversation(user.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start that conversation.");
    } finally {
      setStartingId(null);
    }
  }

  function toggleSelected(userId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  async function handleStartGroup() {
    if (selected.size < 2) {
      setError("Pick at least 2 people to start a group.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      await onStartConversation([...selected]);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start that group conversation.");
    } finally {
      setStarting(false);
    }
  }

  function toggleGroupMode() {
    setGroupMode((v) => !v);
    setSelected(new Set());
    setError(null);
  }

  return (
    <div className={styles.panel} role="dialog" aria-label="People directory">
      <div className={styles.head}>
        <span className={styles.title}>People</span>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5em" }}>
          <button type="button" className={styles.action} onClick={toggleGroupMode}>
            {groupMode ? "Cancel group" : "New group"}
          </button>
          <button type="button" className={styles.closeBtn} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
      </div>
      <div className={styles.body}>
        <input
          className={styles.search}
          type="text"
          placeholder="Search people"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search people"
          autoFocus
        />
        {error && <p className={styles.error}>{error}</p>}
        {groupMode && (
          <button
            type="button"
            className={styles.item}
            disabled={starting || selected.size < 2}
            onClick={handleStartGroup}
          >
            <span className={styles.itemBody}>
              <span className={styles.username}>
                {selected.size === 0 ? "Select people to message" : `Start group with ${selected.size} people`}
              </span>
            </span>
            <span className={styles.action}>{starting ? "…" : "Start"}</span>
          </button>
        )}

        {filtered.length === 0 ? (
          <EmptyState
            icon="@"
            title="No one found"
            body={query ? "Try a different search." : "There's no one else in the workspace yet."}
          />
        ) : (
          <ul className={styles.list}>
            {filtered.map((user) =>
              groupMode ? (
                <li key={user.id}>
                  <button
                    type="button"
                    className={styles.item}
                    disabled={starting}
                    aria-pressed={selected.has(user.id)}
                    onClick={() => toggleSelected(user.id)}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(user.id)}
                      readOnly
                      aria-hidden="true"
                      tabIndex={-1}
                    />
                    <Avatar user={user} size="md" />
                    <span className={styles.itemBody}>
                      <span className={styles.username}>{user.username}</span>
                      {user.bio && <span className={styles.bio}>{user.bio}</span>}
                    </span>
                  </button>
                </li>
              ) : (
                // Split click target, unlike groupMode's whole-row button
                // above: avatar/username opens the profile card (matching
                // Chat.tsx/RoomSettingsPanel.tsx's convention), while the
                // separate "Message" action starts the DM directly — the row
                // itself is a div, not a button, since it now hosts two
                // independent interactive elements rather than one.
                <li key={user.id}>
                  <div className={styles.item}>
                    <button
                      type="button"
                      className={styles.itemMain}
                      onClick={() => (onViewProfile ? onViewProfile(user.id) : handleStart(user))}
                    >
                      <Avatar user={user} size="md" />
                      <span className={styles.itemBody}>
                        <span className={styles.username}>{user.username}</span>
                        {user.bio && <span className={styles.bio}>{user.bio}</span>}
                      </span>
                    </button>
                    <button
                      type="button"
                      className={styles.action}
                      disabled={startingId === user.id}
                      onClick={() => handleStart(user)}
                    >
                      {startingId === user.id ? "…" : "Message"}
                    </button>
                  </div>
                </li>
              )
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
