import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { Message, Reaction, User } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { AiTag, MentionText, REACTION_EMOJI } from "./MentionBadge";
import styles from "./Chat.module.css";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatDay(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  if (isToday) return "Today";
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const GROUP_WINDOW_MS = 5 * 60_000;

export interface SendOptions {
  replyTo?: number | string;
  attachment?: File;
}

interface ChatProps {
  /** Room id or conversation id — only used to key the scroll-to-bottom effect. */
  threadId: number | string;
  currentUser: User;
  users: User[];
  messages: Message[];
  status: "idle" | "loading" | "ready" | "error";
  error?: string;
  sending: boolean;
  onSend: (body: string, opts?: SendOptions) => Promise<void>;
  onRetry: () => void;
  /** Copy for the loading/empty states + composer placeholder — lets this
   * same component serve both room chat and DM threads without knowing
   * which one it's rendering. */
  loadingBody: string;
  emptyIcon: string;
  emptyTitle: string;
  emptyBody: string;
  placeholder: string;
  // The following are only meaningful for room chat (not DMs — private
  // messages don't have edit/delete/reactions/reply/attachments yet, a
  // different model with none of those fields). Leaving them undefined
  // hides the corresponding affordances entirely.
  onEdit?: (messageId: number, body: string) => Promise<void>;
  onDelete?: (messageId: number) => Promise<void>;
  onToggleReaction?: (messageId: number, emoji: string) => Promise<void>;
  onLoadOlder?: () => void;
  hasOlder?: boolean;
  loadingOlder?: boolean;
  /** True if the current user may delete anyone's message in this room
   * (room admin / workspace admin), not just their own. */
  canModerate?: boolean;
  /** Allow attaching a file to a new message. */
  allowAttachments?: boolean;
  /** Re-send a message stuck in `status: "failed"`, identified by its
   * client_id (the temporary id an optimistically-sent message uses — see
   * useWorkspace.ts's sendMessage/retrySendMessage). Absent entirely means
   * no optimistic-send state exists for this thread's messages. */
  onRetryMessage?: (clientId: string) => void;
  /** Open the thread panel for a root message with 1+ replies (see the
   * "N replies" summary row below). Room chat only — DMs don't have
   * threading. */
  onOpenThread?: (messageId: number) => void;
  /** Opens UserProfileCard for a message author's user id — wired to a
   * click on the author's avatar/username in the row below (not the
   * message body itself, which stays a plain click-to-reply-target-free
   * area). Optional so this component doesn't require Layout.tsx's
   * profile-card plumbing to render at all. */
  onViewProfile?: (userId: number) => void;
  /** Set by SearchCommand.tsx's "jump to message" flow (see Layout.tsx):
   * once this message is present in `messages`, it's scrolled into view
   * and briefly highlighted. There's no server-side "load messages around
   * this id" endpoint — if the target predates the room's initially-loaded
   * page and the user hasn't paged back far enough with "Load older
   * messages", it simply won't be found and nothing happens (a known,
   * accepted limitation rather than new deep-linking infrastructure). */
  highlightMessageId?: number | string | null;
}

export function Chat({
  threadId,
  currentUser,
  users,
  messages,
  status,
  error,
  sending,
  onSend,
  onRetry,
  loadingBody,
  emptyIcon,
  emptyTitle,
  emptyBody,
  placeholder,
  onEdit,
  onDelete,
  onToggleReaction,
  onLoadOlder,
  hasOlder,
  loadingOlder,
  canModerate,
  allowAttachments,
  onRetryMessage,
  onOpenThread,
  onViewProfile,
  highlightMessageId,
}: ChatProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const [replyTarget, setReplyTarget] = useState<Message | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [openReactionPickerId, setOpenReactionPickerId] = useState<number | string | null>(null);
  const [pulseId, setPulseId] = useState<number | string | null>(null);

  // Render in stable chronological order rather than relying purely on
  // array-append order: an optimistic pending message, the REST response
  // replacing it, and a WS "message.new" echo for that same message can all
  // touch state in an order that doesn't match `created` timestamps.
  const sortedMessages = useMemo(
    () => [...messages].sort((a, b) => new Date(a.created).getTime() - new Date(b.created).getTime()),
    [messages]
  );

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [sortedMessages.length, threadId]);

  useEffect(() => {
    setReplyTarget(null);
    setEditingId(null);
  }, [threadId]);

  // Jump-to-message from search (see the highlightMessageId prop's
  // docstring above): scroll the target into view and pulse it for a
  // couple seconds once it's actually present in the loaded page. Re-runs
  // as messages stream in so a target that isn't loaded yet on the first
  // pass still gets caught the moment it appears.
  useEffect(() => {
    if (highlightMessageId == null) return;
    const found = sortedMessages.some((m) => String(m.id) === String(highlightMessageId));
    if (!found) return;
    const el = listRef.current?.querySelector(
      `[data-message-id="${CSS.escape(String(highlightMessageId))}"]`
    );
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
    setPulseId(highlightMessageId);
    const timer = window.setTimeout(() => setPulseId(null), 2200);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightMessageId, sortedMessages.length]);

  const byId = useMemo(() => {
    const map = new Map<number | string, Message>();
    sortedMessages.forEach((m) => map.set(m.id, m));
    return map;
  }, [sortedMessages]);

  return (
    <div className={styles.chat}>
      <div className={styles.messages} ref={listRef}>
        {status === "ready" && onLoadOlder && hasOlder && (
          <div className={styles.loadOlderRow}>
            <Button variant="secondary" loading={!!loadingOlder} onClick={onLoadOlder}>
              Load older messages
            </Button>
          </div>
        )}
        {status === "loading" && <EmptyState icon="…" title="Loading messages" body={loadingBody} />}
        {status === "error" && (
          <EmptyState
            variant="error"
            icon="!"
            title="Couldn't load messages"
            body={error ?? "Something went wrong talking to the server."}
            action={<Button variant="secondary" onClick={onRetry}>Retry</Button>}
          />
        )}
        {status === "ready" && sortedMessages.length === 0 && (
          <EmptyState icon={emptyIcon} title={emptyTitle} body={emptyBody} />
        )}
        {status === "ready" &&
          sortedMessages.map((m, i) => {
            const prev = sortedMessages[i - 1];
            const isOwn = m.user.id === currentUser.id;
            const sameDay = prev && formatDay(prev.created) === formatDay(m.created);
            const grouped =
              !!prev &&
              prev.user.id === m.user.id &&
              new Date(m.created).getTime() - new Date(prev.created).getTime() < GROUP_WINDOW_MS &&
              sameDay;

            // A locally-pending/failed optimistic message (id is its
            // client_id string, not a server-assigned number yet) doesn't
            // support edit/delete/react/reply until the server confirms it.
            const isConfirmed = typeof m.id === "number";
            const canEdit = !!onEdit && isOwn && !m.is_deleted && isConfirmed;
            const canDelete = !!onDelete && !m.is_deleted && (isOwn || !!canModerate) && isConfirmed;
            const canReact = !!onToggleReaction && !m.is_deleted && isConfirmed;
            const canReply = !m.is_deleted && isConfirmed;
            const isEditing = editingId === m.id && typeof m.id === "number";

            return (
              <div key={m.id}>
                {!sameDay && <div className={styles.dayDivider}>{formatDay(m.created)}</div>}
                <div
                  data-message-id={String(m.id)}
                  className={[
                    styles.row,
                    isOwn ? styles.own : "",
                    grouped ? styles.grouped : "",
                    pulseId != null && String(pulseId) === String(m.id) ? styles.highlighted : "",
                  ].join(" ")}
                >
                  <div className={styles.avatarSlot}>
                    {!grouped &&
                      (onViewProfile ? (
                        <button
                          type="button"
                          className={styles.avatarBtn}
                          aria-label={`View ${m.user.username}'s profile`}
                          onClick={() => onViewProfile(m.user.id)}
                        >
                          <Avatar user={m.user} size="md" />
                        </button>
                      ) : (
                        <Avatar user={m.user} size="md" />
                      ))}
                  </div>
                  <div className={styles.bubbleCol}>
                    {!grouped && (
                      <div className={styles.meta}>
                        {onViewProfile ? (
                          <button
                            type="button"
                            className={styles.usernameBtn}
                            onClick={() => onViewProfile(m.user.id)}
                          >
                            <span className={styles.username}>{isOwn ? "You" : m.user.username}</span>
                          </button>
                        ) : (
                          <span className={styles.username}>{isOwn ? "You" : m.user.username}</span>
                        )}
                        {m.user.is_bot && <AiTag />}
                        <span className={styles.time}>{formatTime(m.created)}</span>
                      </div>
                    )}

                    {!m.is_deleted && m.reply_to_preview && (
                      <div className={styles.replyPreview}>
                        <span className={styles.replyPreviewLabel}>
                          {m.reply_to_preview.user.username}
                        </span>
                        <span className={styles.replyPreviewBody}>
                          {m.reply_to_preview.is_deleted ? "(message deleted)" : m.reply_to_preview.body}
                        </span>
                      </div>
                    )}

                    <div className={styles.bubbleRow}>
                      {isEditing ? (
                        <EditBox
                          initial={m.body}
                          onCancel={() => setEditingId(null)}
                          onSave={async (body) => {
                            if (onEdit && typeof m.id === "number") await onEdit(m.id, body);
                            setEditingId(null);
                          }}
                        />
                      ) : (
                        <div
                          className={[
                            styles.bubble,
                            m.is_deleted ? styles.bubbleDeleted : "",
                            m.status === "sending" ? styles.bubblePending : "",
                            m.status === "failed" ? styles.bubbleFailed : "",
                          ].join(" ")}
                        >
                          {m.is_deleted ? (
                            <em>This message was deleted.</em>
                          ) : (
                            <>
                              <MentionText text={m.body} />
                              {m.edited_at && <span className={styles.editedTag}>(edited)</span>}
                            </>
                          )}
                        </div>
                      )}

                      {m.status === "failed" && onRetryMessage && m.client_id && (
                        <button
                          type="button"
                          className={styles.retryBtn}
                          title="Failed to send — click to retry"
                          aria-label="Retry sending message"
                          onClick={() => onRetryMessage(m.client_id as string)}
                        >
                          ⟳ Retry
                        </button>
                      )}

                      {!isEditing && (canEdit || canDelete || canReact || canReply) && (
                        <div className={styles.actionsBar}>
                          {canReact && (
                            <div className={styles.reactionPickerAnchor}>
                              <button
                                type="button"
                                className={styles.actionBtn}
                                title="Add reaction"
                                aria-label="Add reaction"
                                onClick={() =>
                                  setOpenReactionPickerId((cur) => (cur === m.id ? null : m.id))
                                }
                              >
                                🙂
                              </button>
                              {openReactionPickerId === m.id && (
                                <ReactionPicker
                                  onPick={(emoji) => {
                                    if (onToggleReaction && typeof m.id === "number") {
                                      onToggleReaction(m.id, emoji);
                                    }
                                    setOpenReactionPickerId(null);
                                  }}
                                  onClose={() => setOpenReactionPickerId(null)}
                                />
                              )}
                            </div>
                          )}
                          {canReply && (
                            <button
                              type="button"
                              className={styles.actionBtn}
                              title="Reply"
                              aria-label="Reply"
                              onClick={() => setReplyTarget(m)}
                            >
                              ↩
                            </button>
                          )}
                          {canEdit && (
                            <button
                              type="button"
                              className={styles.actionBtn}
                              title="Edit"
                              aria-label="Edit message"
                              onClick={() => typeof m.id === "number" && setEditingId(m.id)}
                            >
                              ✎
                            </button>
                          )}
                          {canDelete && (
                            <button
                              type="button"
                              className={styles.actionBtn}
                              title="Delete"
                              aria-label="Delete message"
                              onClick={() => {
                                if (typeof m.id !== "number") return;
                                if (window.confirm("Delete this message?")) onDelete?.(m.id);
                              }}
                            >
                              🗑
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {!m.is_deleted && m.attachments && m.attachments.length > 0 && (
                      <div className={styles.attachments}>
                        {m.attachments.map((a) => (
                          <a
                            key={a.id}
                            href={a.download_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={styles.attachment}
                          >
                            {a.content_type.startsWith("image/") ? (
                              <img src={a.download_url} alt={a.original_filename} className={styles.attachmentImg} />
                            ) : (
                              <span className={styles.attachmentFile}>
                                📎 {a.original_filename} · {formatSize(a.size_bytes)}
                              </span>
                            )}
                          </a>
                        ))}
                      </div>
                    )}

                    {!m.is_deleted && m.reactions && m.reactions.length > 0 && (
                      <div className={styles.reactions}>
                        {m.reactions.map((r) => (
                          <ReactionPill
                            key={r.emoji}
                            reaction={r}
                            mine={r.user_ids.includes(currentUser.id)}
                            onClick={() => {
                              if (onToggleReaction && typeof m.id === "number") {
                                onToggleReaction(m.id, r.emoji);
                              }
                            }}
                          />
                        ))}
                      </div>
                    )}

                    {!m.is_deleted &&
                      onOpenThread &&
                      m.is_thread_root &&
                      typeof m.id === "number" &&
                      (m.reply_count ?? 0) > 0 && (
                        <button
                          type="button"
                          className={styles.threadSummary}
                          onClick={() => onOpenThread(m.id as number)}
                        >
                          💬 {m.reply_count} {m.reply_count === 1 ? "reply" : "replies"}
                        </button>
                      )}
                  </div>
                </div>
              </div>
            );
          })}
      </div>

      <Composer
        users={users}
        disabled={status !== "ready"}
        sending={sending}
        onSend={onSend}
        placeholder={placeholder}
        replyTarget={replyTarget}
        onCancelReply={() => setReplyTarget(null)}
        allowAttachments={!!allowAttachments}
        lookupMessage={(id) => byId.get(id)}
      />
    </div>
  );
}

function ReactionPill({
  reaction,
  mine,
  onClick,
}: {
  reaction: Reaction;
  mine: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={[styles.reactionPill, mine ? styles.reactionPillMine : ""].join(" ")}
      onClick={onClick}
      title={mine ? "Remove your reaction" : "React"}
    >
      <span>{reaction.emoji}</span>
      <span className={styles.reactionCount}>{reaction.count}</span>
    </button>
  );
}

function ReactionPicker({ onPick, onClose }: { onPick: (emoji: string) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div ref={ref} className={styles.reactionPicker} role="menu">
      {REACTION_EMOJI.map((emoji) => (
        <button
          key={emoji}
          type="button"
          role="menuitem"
          className={styles.reactionPickerOption}
          onClick={() => onPick(emoji)}
        >
          {emoji}
        </button>
      ))}
    </div>
  );
}

function EditBox({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (body: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.setSelectionRange(text.length, text.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    if (!text.trim() || saving) return;
    setSaving(true);
    try {
      await onSave(text.trim());
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.editBox}>
      <textarea
        ref={ref}
        className={styles.editTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            save();
          }
          if (e.key === "Escape") onCancel();
        }}
      />
      <div className={styles.editActions}>
        <Button variant="primary" loading={saving} disabled={!text.trim()} onClick={save}>
          Save
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Composer({
  users,
  disabled,
  sending,
  onSend,
  placeholder,
  replyTarget,
  onCancelReply,
  allowAttachments,
  lookupMessage,
}: {
  users: User[];
  disabled: boolean;
  sending: boolean;
  onSend: (body: string, opts?: SendOptions) => Promise<void>;
  placeholder: string;
  replyTarget: Message | null;
  onCancelReply: () => void;
  allowAttachments: boolean;
  lookupMessage: (id: number | string) => Message | undefined;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [attachment, setAttachment] = useState<File | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // @channel / @here alongside real usernames in mention autocomplete.
  const mentionCandidates = useMemo(
    () => [
      { id: -1, username: "channel", avatar: null } as User,
      { id: -2, username: "here", avatar: null } as User,
      ...users,
    ],
    [users]
  );

  const mentionMatches = useMemo(() => {
    if (mentionQuery === null) return [];
    return mentionCandidates
      .filter((u) => u.username.toLowerCase().startsWith(mentionQuery.toLowerCase()))
      .slice(0, 6);
  }, [mentionQuery, mentionCandidates]);

  function handleChange(value: string) {
    setText(value);
    const cursor = textareaRef.current?.selectionStart ?? value.length;
    const upToCursor = value.slice(0, cursor);
    const match = upToCursor.match(/(?:^|\s)@(\w*)$/);
    setMentionQuery(match ? match[1] : null);
    setActiveIndex(0);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }

  function selectMention(username: string) {
    const cursor = textareaRef.current?.selectionStart ?? text.length;
    const upToCursor = text.slice(0, cursor);
    const replaced = upToCursor.replace(/@(\w*)$/, `@${username} `);
    const next = replaced + text.slice(cursor);
    setText(next);
    setMentionQuery(null);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function submit() {
    if ((!text.trim() && !attachment) || sending) return;
    setError(null);
    const body = text;
    const opts: SendOptions = {};
    if (replyTarget) opts.replyTo = replyTarget.id;
    if (attachment) opts.attachment = attachment;
    setText("");
    setMentionQuery(null);
    const sentAttachment = attachment;
    setAttachment(null);
    onCancelReply();
    try {
      await onSend(body, opts);
    } catch (err) {
      setText(body);
      setAttachment(sentAttachment);
      setError(err instanceof Error ? err.message : "Message failed to send.");
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (mentionQuery !== null && mentionMatches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, mentionMatches.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        selectMention(mentionMatches[activeIndex].username);
        return;
      }
      if (e.key === "Escape") {
        setMentionQuery(null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const replySourceMessage = replyTarget ? lookupMessage(replyTarget.id) ?? replyTarget : null;

  return (
    <div className={styles.composerWrap}>
      {replySourceMessage && (
        <div className={styles.replyChip}>
          <span className={styles.replyChipLabel}>
            Replying to <strong>{replySourceMessage.user.username}</strong>
          </span>
          <span className={styles.replyChipBody}>{replySourceMessage.body}</span>
          <button type="button" className={styles.replyChipClose} onClick={onCancelReply} aria-label="Cancel reply">
            ×
          </button>
        </div>
      )}

      {attachment && (
        <div className={styles.attachmentChip}>
          <span>📎 {attachment.name} ({formatSize(attachment.size)})</span>
          <button
            type="button"
            className={styles.replyChipClose}
            onClick={() => {
              setAttachment(null);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
            aria-label="Remove attachment"
          >
            ×
          </button>
        </div>
      )}

      {mentionQuery !== null && mentionMatches.length > 0 && (
        <div className={styles.mentionMenu} role="listbox">
          {mentionMatches.map((u, i) => (
            <button
              key={u.id}
              type="button"
              role="option"
              aria-selected={i === activeIndex}
              className={[styles.mentionOption, i === activeIndex ? styles.mentionOptionActive : ""].join(" ")}
              onMouseDown={(e) => {
                e.preventDefault();
                selectMention(u.username);
              }}
            >
              {u.username === "channel" || u.username === "here" ? (
                <span className={styles.mentionBroadcastIcon}>@</span>
              ) : (
                <Avatar user={u} size="sm" />
              )}
              {u.username}
            </button>
          ))}
        </div>
      )}
      <div className={styles.composer}>
        {allowAttachments && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              className={styles.fileInput}
              onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className={styles.attachButton}
              disabled={disabled}
              aria-label="Attach a file"
              title="Attach a file"
              onClick={() => fileInputRef.current?.click()}
            >
              📎
            </button>
          </>
        )}
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          rows={1}
          placeholder={disabled ? "Loading…" : placeholder}
          value={text}
          disabled={disabled}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Message"
        />
        <Button
          variant="primary"
          loading={sending}
          disabled={disabled || (!text.trim() && !attachment)}
          onClick={submit}
        >
          Send
        </Button>
      </div>
      {error && <p className={styles.errorBanner}>{error}</p>}
      <p className={styles.hint}>Enter to send · Shift+Enter for a new line</p>
    </div>
  );
}
