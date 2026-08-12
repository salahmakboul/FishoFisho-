import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import * as api from "../lib/api";
import type { Message, User } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { AiTag, MentionText } from "./MentionBadge";
import styles from "./ThreadPanel.module.css";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

interface ThreadPanelProps {
  roomId: number;
  rootId: number;
  currentUser: User;
  /** The active room's full loaded message list (see useWorkspace.ts's
   * `messages`) — used to live-merge new replies/edits/deletes/reaction
   * changes that arrive over the WS while this panel is open, since those
   * already land in this same array via the existing message.new/updated/
   * deleted/reactions_changed handlers. No separate WS wiring needed here. */
  roomMessages: Message[];
  onClose: () => void;
  onSendReply: (body: string) => Promise<void>;
}

/** Side panel showing a thread's root message + all replies, with a reply
 * composer scoped to that thread and a follow/unfollow toggle. Opened from
 * Chat.tsx's "N replies" summary row on a thread-root message (see
 * Layout.tsx, which owns the open/close state the same way it does for the
 * AI/profile/directory/room-settings panels). */
export function ThreadPanel({ roomId, rootId, currentUser, roomMessages, onClose, onSendReply }: ThreadPanelProps) {
  const [threadMessages, setThreadMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [following, setFollowing] = useState(false);
  const [followBusy, setFollowBusy] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Initial load of the full thread (covers root/replies that predate the
  // room's currently-loaded message window).
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    api
      .getThread(roomId, rootId)
      .then((msgs) => {
        if (cancelled) return;
        setThreadMessages(msgs);
        setStatus("ready");
        const root = msgs.find((m) => m.id === rootId);
        setFollowing(!!root?.is_following_thread);
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [roomId, rootId]);

  // Live-merge: any message belonging to this thread that shows up/changes
  // in the room's already-loaded list (new reply, edit, delete, reaction
  // change) gets reflected here too.
  useEffect(() => {
    const relevant = roomMessages.filter(
      (m) => typeof m.id === "number" && (m.id === rootId || m.reply_to === rootId)
    );
    if (relevant.length === 0) return;
    setThreadMessages((prev) => {
      const byId = new Map(prev.map((m) => [m.id, m]));
      let changed = false;
      relevant.forEach((m) => {
        const existing = byId.get(m.id);
        if (existing !== m) {
          byId.set(m.id, m);
          changed = true;
        }
      });
      if (!changed) return prev;
      return [...byId.values()].sort((a, b) => new Date(a.created).getTime() - new Date(b.created).getTime());
    });
  }, [roomMessages, rootId]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [threadMessages.length]);

  const root = useMemo(() => threadMessages.find((m) => m.id === rootId) ?? null, [threadMessages, rootId]);
  const replies = useMemo(() => threadMessages.filter((m) => m.id !== rootId), [threadMessages, rootId]);

  async function toggleFollow() {
    if (followBusy) return;
    setFollowBusy(true);
    const next = !following;
    setFollowing(next);
    try {
      if (next) await api.followThread(roomId, rootId);
      else await api.unfollowThread(roomId, rootId);
    } catch {
      setFollowing(!next); // revert on failure
    } finally {
      setFollowBusy(false);
    }
  }

  async function submit() {
    if (!text.trim() || sending) return;
    setError(null);
    const body = text.trim();
    setText("");
    setSending(true);
    try {
      await onSendReply(body);
    } catch (err) {
      setText(body);
      setError(err instanceof Error ? err.message : "Reply failed to send.");
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function renderMessage(m: Message, isRoot: boolean) {
    return (
      <div key={m.id} className={[styles.row, isRoot ? styles.rootRow : ""].join(" ")}>
        <Avatar user={m.user} size="sm" />
        <div className={styles.bubbleCol}>
          <div className={styles.meta}>
            <span className={styles.username}>{m.user.id === currentUser.id ? "You" : m.user.username}</span>
            {m.user.is_bot && <AiTag />}
            <span className={styles.time}>{formatTime(m.created)}</span>
          </div>
          <div className={[styles.bubble, m.is_deleted ? styles.bubbleDeleted : ""].join(" ")}>
            {m.is_deleted ? <em>This message was deleted.</em> : <MentionText text={m.body} />}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.panel} role="dialog" aria-label="Thread">
      <div className={styles.head}>
        <span className={styles.title}>Thread</span>
        <div className={styles.headActions}>
          {status === "ready" && root && (
            <button
              type="button"
              className={[styles.followBtn, following ? styles.followBtnActive : ""].join(" ")}
              onClick={toggleFollow}
              disabled={followBusy}
            >
              {following ? "Following" : "Follow"}
            </button>
          )}
          <button type="button" className={styles.closeBtn} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
      </div>

      <div className={styles.body} ref={listRef}>
        {status === "loading" && <EmptyState icon="…" title="Loading thread" body="Fetching replies." />}
        {status === "error" && (
          <EmptyState variant="error" icon="!" title="Couldn't load thread" body="Something went wrong." />
        )}
        {status === "ready" && root && (
          <>
            {renderMessage(root, true)}
            {replies.length > 0 && <div className={styles.divider}>{replies.length} {replies.length === 1 ? "reply" : "replies"}</div>}
            {replies.map((m) => renderMessage(m, false))}
          </>
        )}
      </div>

      {status === "ready" && (
        <div className={styles.composerWrap}>
          <div className={styles.composer}>
            <textarea
              className={styles.textarea}
              rows={1}
              placeholder="Reply in thread…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              aria-label="Reply in thread"
            />
            <Button variant="primary" loading={sending} disabled={!text.trim()} onClick={submit}>
              Reply
            </Button>
          </div>
          {error && <p className={styles.errorBanner}>{error}</p>}
        </div>
      )}
    </div>
  );
}
