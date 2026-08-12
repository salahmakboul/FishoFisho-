import { useEffect, useState } from "react";
import * as api from "../lib/api";
import type {
  AIInteraction,
  AuditAction,
  AuditEvent,
  BlockedKeyword,
  ModerationAction,
  ModerationActionType,
  User,
} from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import styles from "./AdminPanel.module.css";
import profileStyles from "./ProfilePanel.module.css";

type Tab = "audit" | "moderation" | "keywords" | "ai";

const TRIGGER_LABELS: Record<AIInteraction["trigger_type"], string> = {
  mention: "Mention",
  random: "Random",
  summarize: "Summarize",
};

const ACTION_LABELS: Record<AuditAction, string> = {
  role_changed: "Role changed",
  member_removed: "Member removed",
  room_archived: "Room archived",
  room_deleted: "Room deleted",
  message_removed: "Message removed",
  user_warned: "User warned",
  user_timed_out: "User timed out",
  user_banned: "User banned",
  user_unbanned: "User unbanned",
  invitation_created: "Invitation created",
  invitation_revoked: "Invitation revoked",
  keyword_added: "Keyword added",
  keyword_removed: "Keyword removed",
};

function timestamp(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** Consolidated admin/moderation surface — audit log, user moderation
 * (warn/timeout/ban/unban + history), and workspace-wide blocked keywords.
 * Only ever rendered for a workspace admin/owner (see Layout.tsx's
 * canOpenAdminPanel gate, mirroring the backend's IsWorkspaceAdminOrOwner)
 * — this component doesn't re-check the role itself. Follows the same aside
 * shell (ProfilePanel.module.css) the other panels use, with its own small
 * AdminPanel.module.css for the tab strip and list rows. */
export function AdminPanel({ currentUser, users, onClose }: { currentUser: User; users: User[]; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("audit");

  return (
    <div className={profileStyles.panel} role="dialog" aria-label="Admin">
      <div className={profileStyles.head}>
        <span className={profileStyles.title}>Admin</span>
        <button type="button" className={profileStyles.closeBtn} aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <div className={styles.tabRow}>
        {(
          [
            ["audit", "Audit log"],
            ["moderation", "Moderation"],
            ["keywords", "Blocked keywords"],
            ["ai", "AI activity"],
          ] as [Tab, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={[styles.tabBtn, tab === value ? styles.tabBtnActive : ""].join(" ")}
            aria-pressed={tab === value}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={profileStyles.body}>
        {tab === "audit" && <AuditLogTab />}
        {tab === "moderation" && <ModerationTab currentUser={currentUser} users={users} />}
        {tab === "keywords" && <BlockedKeywordsTab currentUser={currentUser} />}
        {tab === "ai" && <AIActivityTab />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

function AuditLogTab() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [actionFilter, setActionFilter] = useState<string>("");

  function load(filter: string) {
    setStatus("loading");
    api
      .listAuditLog({ action: filter || undefined })
      .then((page) => {
        setEvents(page.events);
        setNextCursor(page.nextCursor);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }

  useEffect(() => {
    load(actionFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actionFilter]);

  async function loadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listAuditLog({ action: actionFilter || undefined, cursor: nextCursor });
      setEvents((prev) => [...prev, ...page.events]);
      setNextCursor(page.nextCursor);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.row}>
        <select
          className={styles.select}
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          aria-label="Filter by action"
        >
          <option value="">All actions</option>
          {(Object.keys(ACTION_LABELS) as AuditAction[]).map((a) => (
            <option key={a} value={a}>
              {ACTION_LABELS[a]}
            </option>
          ))}
        </select>
      </div>

      {status === "loading" && <p className={profileStyles.inviteMetaSub}>Loading audit log…</p>}
      {status === "error" && (
        <EmptyState variant="error" icon="!" title="Couldn't load the audit log" body="Something went wrong talking to the server." />
      )}
      {status === "ready" && events.length === 0 && (
        <EmptyState icon="≡" title="No events yet" body="Admin/moderation actions will show up here as they happen." />
      )}
      {status === "ready" && events.length > 0 && (
        <>
          <ul className={styles.section} style={{ listStyle: "none" }}>
            {events.map((event) => (
              <li key={event.id} className={styles.eventItem}>
                <div className={styles.eventTop}>
                  <span className={styles.eventAction}>{ACTION_LABELS[event.action] ?? event.action}</span>
                  <span className={styles.eventMeta}>{timestamp(event.created_at)}</span>
                </div>
                <span className={styles.eventMeta}>
                  {event.actor ? event.actor.username : "system"}
                  {event.target_user ? ` → ${event.target_user.username}` : ""}
                  {event.target_room_name ? ` in #${event.target_room_name}` : ""}
                </span>
                {event.detail && <span className={profileStyles.inviteMetaSub}>{event.detail}</span>}
              </li>
            ))}
          </ul>
          {nextCursor && (
            <Button variant="secondary" loading={loadingMore} onClick={loadMore}>
              Load more
            </Button>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Moderation
// ---------------------------------------------------------------------------

function ModerationTab({ currentUser, users }: { currentUser: User; users: User[] }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<User | null>(null);
  const [history, setHistory] = useState<ModerationAction[]>([]);
  const [historyStatus, setHistoryStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [actionType, setActionType] = useState<ModerationActionType>("warning");
  const [reason, setReason] = useState("");
  const [timeoutHours, setTimeoutHours] = useState("24");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const results = query.trim()
    ? users.filter((u) => u.username.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8)
    : [];

  function loadHistory(user: User) {
    setHistoryStatus("loading");
    api
      .listModerationHistory(user.id)
      .then((data) => {
        setHistory(data);
        setHistoryStatus("ready");
      })
      .catch(() => setHistoryStatus("error"));
  }

  function selectUser(user: User) {
    setSelected(user);
    setFeedback(null);
    loadHistory(user);
  }

  async function handleIssue() {
    if (!selected) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      const expires_at =
        actionType === "timeout"
          ? new Date(Date.now() + Number(timeoutHours || "0") * 60 * 60 * 1000).toISOString()
          : undefined;
      await api.issueModerationAction(currentUser, {
        target_user: selected,
        action_type: actionType,
        reason: reason || undefined,
        expires_at,
      });
      setFeedback({ kind: "success", text: `${actionType} issued.` });
      setReason("");
      loadHistory(selected);
    } catch (err) {
      setFeedback({ kind: "error", text: err instanceof Error ? err.message : "Couldn't issue that action." });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUnban() {
    if (!selected) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      await api.unbanUser(currentUser, selected);
      setFeedback({ kind: "success", text: "Unbanned." });
      loadHistory(selected);
    } catch (err) {
      setFeedback({ kind: "error", text: err instanceof Error ? err.message : "Couldn't unban that user." });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.section}>
      <div className={profileStyles.field}>
        <label className={profileStyles.label} htmlFor="admin-user-search">
          Find a person
        </label>
        <input
          id="admin-user-search"
          className={styles.input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by username…"
        />
      </div>

      {results.length > 0 && (
        <ul className={styles.section} style={{ listStyle: "none" }}>
          {results.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                className={[styles.userResult, selected?.id === u.id ? styles.userResultActive : ""].join(" ")}
                onClick={() => selectUser(u)}
              >
                <span style={{ display: "flex", alignItems: "center", gap: "0.5em" }}>
                  <Avatar user={u} size="sm" />
                  {u.username}
                </span>
                {u.is_banned && <span className={[styles.badge, styles.badgeDanger].join(" ")}>Banned</span>}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <>
          <hr className={styles.divider} />
          <div className={styles.section}>
            <span className={profileStyles.sectionTitle}>
              {selected.username}
              {selected.is_banned ? " — currently banned" : ""}
            </span>

            {selected.is_banned ? (
              <Button variant="secondary" loading={submitting} onClick={handleUnban}>
                Unban {selected.username}
              </Button>
            ) : (
              <div className={styles.section}>
                <div className={styles.row}>
                  <select
                    className={styles.select}
                    value={actionType}
                    onChange={(e) => setActionType(e.target.value as ModerationActionType)}
                    aria-label="Action type"
                  >
                    <option value="warning">Warning</option>
                    <option value="timeout">Timeout</option>
                    <option value="ban">Ban</option>
                  </select>
                  {actionType === "timeout" && (
                    <input
                      className={styles.input}
                      style={{ width: "6em" }}
                      type="number"
                      min={1}
                      value={timeoutHours}
                      onChange={(e) => setTimeoutHours(e.target.value)}
                      aria-label="Timeout length in hours"
                    />
                  )}
                  {actionType === "timeout" && <span className={profileStyles.inviteMetaSub}>hours</span>}
                </div>
                <textarea
                  className={styles.textarea}
                  placeholder="Reason (optional)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <Button variant="primary" loading={submitting} onClick={handleIssue}>
                  Issue {actionType}
                </Button>
              </div>
            )}

            {feedback && (
              <span className={[profileStyles.feedback, feedback.kind === "error" ? profileStyles.feedbackError : ""].join(" ")}>
                {feedback.text}
              </span>
            )}

            <span className={profileStyles.sectionTitle}>History</span>
            {historyStatus === "loading" && <p className={profileStyles.inviteMetaSub}>Loading…</p>}
            {historyStatus === "error" && <p className={profileStyles.inviteMetaSub}>Couldn't load history.</p>}
            {historyStatus === "ready" && history.length === 0 && (
              <p className={profileStyles.inviteMetaSub}>No moderation history for this person.</p>
            )}
            {historyStatus === "ready" && history.length > 0 && (
              <ul className={styles.section} style={{ listStyle: "none" }}>
                {history.map((h) => (
                  <li key={h.id} className={styles.eventItem}>
                    <div className={styles.eventTop}>
                      <span className={styles.eventAction}>{h.action_type}</span>
                      <span className={styles.eventMeta}>{timestamp(h.created_at)}</span>
                    </div>
                    <span className={styles.eventMeta}>by {h.issued_by ? h.issued_by.username : "system"}</span>
                    {h.expires_at && <span className={profileStyles.inviteMetaSub}>until {timestamp(h.expires_at)}</span>}
                    {h.reason && <span className={profileStyles.inviteMetaSub}>{h.reason}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Blocked keywords
// ---------------------------------------------------------------------------

function BlockedKeywordsTab({ currentUser }: { currentUser: User }) {
  const [keywords, setKeywords] = useState<BlockedKeyword[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [phrase, setPhrase] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus("loading");
    api
      .listBlockedKeywords()
      .then((data) => {
        setKeywords(data);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  async function handleAdd() {
    const trimmed = phrase.trim();
    if (!trimmed) return;
    setAdding(true);
    setError(null);
    try {
      const keyword = await api.addBlockedKeyword(currentUser, trimmed);
      setKeywords((prev) => [keyword, ...prev]);
      setPhrase("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that phrase.");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(id: number) {
    setKeywords((prev) => prev.filter((k) => k.id !== id));
    await api.removeBlockedKeyword(currentUser, id).catch(() => {});
  }

  return (
    <div className={styles.section}>
      <p className={profileStyles.inviteMetaSub}>
        Any message containing one of these phrases (case-insensitive) is rejected outright when posted.
      </p>
      <div className={styles.row}>
        <input
          className={styles.input}
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          placeholder="Add a phrase to block…"
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
        />
        <Button variant="secondary" loading={adding} onClick={handleAdd}>
          Add
        </Button>
      </div>
      {error && <span className={[profileStyles.feedback, profileStyles.feedbackError].join(" ")}>{error}</span>}

      {status === "loading" && <p className={profileStyles.inviteMetaSub}>Loading…</p>}
      {status === "error" && <p className={profileStyles.inviteMetaSub}>Couldn't load blocked keywords.</p>}
      {status === "ready" && keywords.length === 0 && (
        <EmptyState icon="∅" title="No blocked keywords" body="Add a phrase above to start filtering messages." />
      )}
      {status === "ready" && keywords.length > 0 && (
        <ul className={profileStyles.inviteList}>
          {keywords.map((k) => (
            <li key={k.id} className={profileStyles.inviteItem}>
              <span className={profileStyles.inviteMeta}>
                <span>{k.phrase}</span>
                <span className={profileStyles.inviteMetaSub}>
                  Added by {k.added_by?.username ?? "someone"} · {timestamp(k.created_at)}
                </span>
              </span>
              <button type="button" className={profileStyles.linkBtn} onClick={() => handleRemove(k.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI activity
// ---------------------------------------------------------------------------

/** Audit view for FishoAI invocations (mention / random / summarize,
 * success or failure) — GET /api/v1/admin/ai-interactions/, workspace
 * admin/owner only. Simple list, matching AuditLogTab's shape rather than
 * building a separate dashboard-style view. */
function AIActivityTab() {
  const [interactions, setInteractions] = useState<AIInteraction[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [triggerFilter, setTriggerFilter] = useState<string>("");

  function load(filter: string) {
    setStatus("loading");
    api
      .listAIInteractions({ trigger_type: filter || undefined })
      .then((page) => {
        setInteractions(page.interactions);
        setNextCursor(page.nextCursor);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }

  useEffect(() => {
    load(triggerFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerFilter]);

  async function loadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await api.listAIInteractions({ trigger_type: triggerFilter || undefined, cursor: nextCursor });
      setInteractions((prev) => [...prev, ...page.interactions]);
      setNextCursor(page.nextCursor);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.row}>
        <select
          className={styles.select}
          value={triggerFilter}
          onChange={(e) => setTriggerFilter(e.target.value)}
          aria-label="Filter by trigger type"
        >
          <option value="">All triggers</option>
          {(Object.keys(TRIGGER_LABELS) as AIInteraction["trigger_type"][]).map((t) => (
            <option key={t} value={t}>
              {TRIGGER_LABELS[t]}
            </option>
          ))}
        </select>
      </div>

      {status === "loading" && <p className={profileStyles.inviteMetaSub}>Loading AI activity…</p>}
      {status === "error" && (
        <EmptyState variant="error" icon="!" title="Couldn't load AI activity" body="Something went wrong talking to the server." />
      )}
      {status === "ready" && interactions.length === 0 && (
        <EmptyState icon="✦" title="No AI activity yet" body="FishoAI invocations (mentions, summaries, and random replies) will show up here." />
      )}
      {status === "ready" && interactions.length > 0 && (
        <>
          <ul className={styles.section} style={{ listStyle: "none" }}>
            {interactions.map((interaction) => (
              <li key={interaction.id} className={styles.eventItem}>
                <div className={styles.eventTop}>
                  <span className={styles.eventAction}>{TRIGGER_LABELS[interaction.trigger_type] ?? interaction.trigger_type}</span>
                  <span className={styles.eventMeta}>{timestamp(interaction.created_at)}</span>
                </div>
                <span className={styles.eventMeta}>
                  {interaction.user ? interaction.user.username : "unknown"}
                  {interaction.room_name ? ` in #${interaction.room_name}` : ""}
                  {" · "}
                  {interaction.succeeded ? "succeeded" : "failed"}
                </span>
                <span className={profileStyles.inviteMetaSub}>
                  Context: {interaction.prompt_context_message_count} message(s)
                </span>
              </li>
            ))}
          </ul>
          {nextCursor && (
            <Button variant="secondary" loading={loadingMore} onClick={loadMore}>
              Load more
            </Button>
          )}
        </>
      )}
    </div>
  );
}
