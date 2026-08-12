import { useEffect, useState } from "react";
import * as api from "../lib/api";
import type { Room, RoomMember, RoomNotificationLevel, User, Webhook } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import profileStyles from "./ProfilePanel.module.css";
import listStyles from "./Directory.module.css";

interface RoomSettingsPanelProps {
  room: Room;
  currentUser: User;
  users: User[];
  /** Whether the CURRENT user manages this room (that room's own admin, or a
   * workspace admin/owner) — gates the rename/describe/private-toggle,
   * archive, and member-management sections below. The Notifications
   * section further down is intentionally NOT gated by this: it's a
   * personal preference every member gets, same as ProfilePanel's global
   * notification toggles. */
  isRoomAdmin: boolean;
  onClose: () => void;
  onUpdateRoom: (patch: Partial<Pick<Room, "name" | "description" | "is_private">>) => Promise<Room>;
  onArchive: () => Promise<Room>;
  onUnarchive: () => Promise<Room>;
  /** Opens UserProfileCard for a clicked member row — wired to the
   * avatar/username area only, kept separate from the role-change/remove
   * buttons that are the row's other (admin-only) actions. */
  onViewProfile?: (userId: number) => void;
}

const ROOM_NOTIFICATION_LEVELS: { value: RoomNotificationLevel; label: string; hint: string }[] = [
  { value: "default", label: "Default", hint: "Use your global notification settings." },
  { value: "all", label: "All activity", hint: "Notify me for everything in this room." },
  { value: "mentions_only", label: "Mentions only", hint: "Only notify me when I'm directly @mentioned." },
  { value: "muted", label: "Muted", hint: "No general notifications — direct @mentions still get through." },
];

/** Personal, per-room notification override — visible to ANY room member,
 * not just admins (see RoomSettingsPanelProps.isRoomAdmin's docstring). */
function RoomNotificationSection({ roomId }: { roomId: number }) {
  const [level, setLevel] = useState<RoomNotificationLevel>("default");
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus("loading");
    api
      .getRoomNotificationSetting(roomId)
      .then((setting) => {
        setLevel(setting.setting);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, [roomId]);

  async function handleChange(next: RoomNotificationLevel) {
    const previous = level;
    setLevel(next);
    setSaving(true);
    setError(null);
    try {
      await api.updateRoomNotificationSetting(roomId, next);
    } catch (err) {
      setLevel(previous);
      setError(err instanceof Error ? err.message : "Couldn't save that setting.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={profileStyles.adminSection}>
      <span className={profileStyles.sectionTitle}>Notifications</span>
      {status === "loading" && <p className={profileStyles.inviteMetaSub}>Loading…</p>}
      {status === "error" && <p className={profileStyles.inviteMetaSub}>Couldn't load your notification setting.</p>}
      {status === "ready" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5em" }}>
          {ROOM_NOTIFICATION_LEVELS.map((opt) => (
            <label
              key={opt.value}
              className={profileStyles.label}
              style={{ display: "flex", alignItems: "flex-start", gap: "0.5em", textTransform: "none" }}
            >
              <input
                type="radio"
                name={`room-notif-${roomId}`}
                checked={level === opt.value}
                disabled={saving}
                onChange={() => handleChange(opt.value)}
              />
              <span>
                {opt.label}
                <span className={profileStyles.inviteMetaSub} style={{ display: "block" }}>
                  {opt.hint}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      {error && <span className={[profileStyles.feedback, profileStyles.feedbackError].join(" ")}>{error}</span>}
    </div>
  );
}

/**
 * Outgoing+incoming webhook management for a room (integrations/webhooks
 * slice) — admin-only, rendered inside RoomSettingsPanel's `isRoomAdmin`
 * gate below. Each Webhook row covers both directions: an outgoing target
 * URL (active toggle, delete) and the room's own incoming URL+token
 * (always visible, copy-button affordance — same pattern
 * ProfilePanel.tsx's InviteAdminSection already established for invite
 * links: a `copiedId`-shaped state + navigator.clipboard.writeText +
 * setTimeout reset, no separate CopyButton component in this codebase to
 * reuse). The signing `secret` is the one truly "shown once" value: it
 * only ever appears in the response to creating a webhook (see
 * WebhookSerializer's docstring on the backend) and is kept in local state
 * only long enough for the admin to copy it, never persisted or re-fetched.
 */
function WebhookSection({ roomId }: { roomId: number }) {
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [targetUrl, setTargetUrl] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [revealedSecret, setRevealedSecret] = useState<{ webhookId: number; secret: string } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  useEffect(() => {
    setStatus("loading");
    api
      .listWebhooks(roomId)
      .then((data) => {
        setWebhooks(data);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, [roomId]);

  async function handleCopy(key: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey((prev) => (prev === key ? null : prev)), 1500);
    } catch {
      // Clipboard permission denied or unavailable — silently no-op.
    }
  }

  async function handleCreate() {
    if (!targetUrl.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const webhook = await api.createWebhook(roomId, targetUrl.trim());
      setWebhooks((prev) => [webhook, ...prev]);
      setTargetUrl("");
      if (webhook.secret) {
        setRevealedSecret({ webhookId: webhook.id, secret: webhook.secret });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create webhook.");
    } finally {
      setCreating(false);
    }
  }

  async function handleToggleActive(webhook: Webhook) {
    setBusyId(webhook.id);
    setError(null);
    try {
      const updated = await api.updateWebhook(roomId, webhook.id, !webhook.is_active);
      setWebhooks((prev) => prev.map((w) => (w.id === webhook.id ? updated : w)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't update webhook.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(webhook: Webhook) {
    setBusyId(webhook.id);
    setError(null);
    try {
      await api.deleteWebhook(roomId, webhook.id);
      setWebhooks((prev) => prev.filter((w) => w.id !== webhook.id));
      if (revealedSecret?.webhookId === webhook.id) setRevealedSecret(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't remove webhook.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className={profileStyles.adminSection}>
      <span className={profileStyles.sectionTitle}>Webhooks</span>
      <p className={profileStyles.inviteMetaSub}>
        Outgoing webhooks POST a signed payload to a URL whenever a message is posted in this room.
        Each webhook also gets its own incoming URL that external services can POST to (with a JSON
        body of the shape <code>{"{\"text\": \"...\"}"}</code>) to post a message here without a
        FishoFisho login.
      </p>

      {error && (
        <span className={[profileStyles.feedback, profileStyles.feedbackError].join(" ")}>{error}</span>
      )}

      {revealedSecret && (
        <div className={profileStyles.inviteMetaSub} style={{ border: "1px solid currentColor", borderRadius: 6, padding: "0.5em" }}>
          <strong>Signing secret (shown once — copy it now):</strong>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5em", marginTop: "0.25em" }}>
            <code style={{ wordBreak: "break-all" }}>{revealedSecret.secret}</code>
            <button
              type="button"
              className={profileStyles.linkBtn}
              onClick={() => handleCopy(`secret-${revealedSecret.webhookId}`, revealedSecret.secret)}
            >
              {copiedKey === `secret-${revealedSecret.webhookId}` ? "Copied!" : "Copy"}
            </button>
          </div>
          <p>This won't be shown again — store it wherever you verify the X-FishoFisho-Signature header.</p>
        </div>
      )}

      {status === "loading" && <p className={profileStyles.inviteMetaSub}>Loading webhooks…</p>}
      {status === "error" && <p className={profileStyles.inviteMetaSub}>Couldn't load webhooks.</p>}

      {status === "ready" && (
        <ul className={listStyles.list}>
          {webhooks.map((webhook) => {
            const incomingKey = `incoming-${webhook.id}`;
            return (
              <li key={webhook.id} className={listStyles.item} style={{ flexDirection: "column", alignItems: "stretch" }}>
                <span className={listStyles.itemBody}>
                  <span className={listStyles.username}>{webhook.target_url || "(incoming only)"}</span>
                  <span className={listStyles.bio}>
                    {webhook.is_active ? "Active" : "Inactive"} · {webhook.event_types.join(", ")}
                  </span>
                </span>
                <span className={profileStyles.inviteMetaSub} style={{ display: "flex", alignItems: "center", gap: "0.5em" }}>
                  Incoming URL: <code style={{ wordBreak: "break-all" }}>{webhook.incoming_url}</code>
                  <button
                    type="button"
                    className={profileStyles.linkBtn}
                    onClick={() => handleCopy(incomingKey, webhook.incoming_url)}
                  >
                    {copiedKey === incomingKey ? "Copied!" : "Copy link"}
                  </button>
                </span>
                <span style={{ display: "flex", gap: "0.75em" }}>
                  <button
                    type="button"
                    className={profileStyles.linkBtn}
                    disabled={busyId === webhook.id}
                    onClick={() => handleToggleActive(webhook)}
                  >
                    {webhook.is_active ? "Deactivate" : "Activate"}
                  </button>
                  <button
                    type="button"
                    className={profileStyles.linkBtn}
                    disabled={busyId === webhook.id}
                    onClick={() => handleDelete(webhook)}
                  >
                    Delete
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <div className={profileStyles.inviteRow}>
        <input
          className={profileStyles.textarea}
          style={{ minHeight: "auto" }}
          placeholder="https://example.com/hooks/fishofisho"
          value={targetUrl}
          onChange={(e) => setTargetUrl(e.target.value)}
          aria-label="Outgoing webhook target URL"
        />
        <Button variant="secondary" loading={creating} onClick={handleCreate}>
          Add webhook
        </Button>
      </div>
    </div>
  );
}

/**
 * Room-settings surface: a personal per-room notification override (any
 * member, always shown) plus — gated behind `isRoomAdmin` — rename/
 * describe/toggle-private the active room (existing updateRoom), archive/
 * unarchive it, and manage its member list (add/change role/remove/leave).
 * All in one aside panel, following the same shell (ProfilePanel.module.css)
 * and list styling (Directory.module.css) the other panels already use,
 * rather than introducing a parallel design. Layout.tsx renders this panel
 * for any room member (not just admins) so everyone can reach their own
 * notification setting; `isRoomAdmin` gates only the room-administration
 * controls within it.
 */
export function RoomSettingsPanel({
  room,
  currentUser,
  users,
  isRoomAdmin,
  onClose,
  onUpdateRoom,
  onArchive,
  onUnarchive,
  onViewProfile,
}: RoomSettingsPanelProps) {
  const [name, setName] = useState(room.name);
  const [description, setDescription] = useState(room.description ?? "");
  const [isPrivate, setIsPrivate] = useState(!!room.is_private);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const [members, setMembers] = useState<RoomMember[]>([]);
  const [membersStatus, setMembersStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [addUserId, setAddUserId] = useState("");
  const [memberError, setMemberError] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<number | null>(null);

  useEffect(() => {
    setName(room.name);
    setDescription(room.description ?? "");
    setIsPrivate(!!room.is_private);
    if (!isRoomAdmin) return;
    setMembersStatus("loading");
    api
      .listRoomMembers(room.id)
      .then((data) => {
        setMembers(data);
        setMembersStatus("ready");
      })
      .catch(() => setMembersStatus("error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.id, isRoomAdmin]);

  async function handleSave() {
    setSaving(true);
    setFeedback(null);
    try {
      await onUpdateRoom({ name, description, is_private: isPrivate });
      setFeedback({ kind: "success", text: "Saved." });
    } catch (err) {
      setFeedback({ kind: "error", text: err instanceof Error ? err.message : "Couldn't save changes." });
    } finally {
      setSaving(false);
    }
  }

  async function handleArchiveToggle() {
    setArchiving(true);
    setFeedback(null);
    try {
      if (room.is_archived) await onUnarchive();
      else await onArchive();
    } catch (err) {
      setFeedback({ kind: "error", text: err instanceof Error ? err.message : "Couldn't update room status." });
    } finally {
      setArchiving(false);
    }
  }

  const memberIds = new Set(members.map((m) => m.user.id));
  const addable = users.filter((u) => !memberIds.has(u.id));

  async function handleAddMember() {
    if (!addUserId) return;
    setMemberError(null);
    try {
      const membership = await api.addRoomMember(room.id, Number(addUserId));
      setMembers((prev) => [...prev, membership]);
      setAddUserId("");
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "Couldn't add member.");
    }
  }

  async function handleRoleChange(userId: number, role: "admin" | "member") {
    setBusyUserId(userId);
    setMemberError(null);
    try {
      const updated = await api.updateRoomMemberRole(room.id, userId, role);
      setMembers((prev) => prev.map((m) => (m.user.id === userId ? updated : m)));
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "Couldn't change role.");
    } finally {
      setBusyUserId(null);
    }
  }

  async function handleRemove(userId: number) {
    setBusyUserId(userId);
    setMemberError(null);
    try {
      await api.removeRoomMember(room.id, userId);
      setMembers((prev) => prev.filter((m) => m.user.id !== userId));
    } catch (err) {
      setMemberError(err instanceof Error ? err.message : "Couldn't remove member.");
    } finally {
      setBusyUserId(null);
    }
  }

  return (
    <div className={profileStyles.panel} role="dialog" aria-label={`${room.name} settings`}>
      <div className={profileStyles.head}>
        <span className={profileStyles.title}>Room settings</span>
        <button type="button" className={profileStyles.closeBtn} aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <div className={profileStyles.body}>
        {/* Personal preference, open to every room member — deliberately
            NOT wrapped in the `isRoomAdmin` gate below. */}
        <RoomNotificationSection roomId={room.id} />

        {isRoomAdmin && (
          <>
            <div className={profileStyles.field}>
              <label className={profileStyles.label} htmlFor="room-settings-name">
                Name
              </label>
              <input
                id="room-settings-name"
                className={profileStyles.textarea}
                style={{ minHeight: "auto" }}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className={profileStyles.field}>
              <label className={profileStyles.label} htmlFor="room-settings-desc">
                Description
              </label>
              <textarea
                id="room-settings-desc"
                className={profileStyles.textarea}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <label
              className={profileStyles.label}
              style={{ display: "flex", alignItems: "center", gap: "0.5em", textTransform: "none" }}
            >
              <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} />
              Private room
            </label>

            <div className={profileStyles.footer}>
              <Button variant="primary" loading={saving} onClick={handleSave}>
                Save changes
              </Button>
              {feedback && (
                <span
                  className={[
                    profileStyles.feedback,
                    feedback.kind === "error" ? profileStyles.feedbackError : "",
                  ].join(" ")}
                >
                  {feedback.text}
                </span>
              )}
            </div>

            <div className={profileStyles.adminSection}>
              <span className={profileStyles.sectionTitle}>{room.is_archived ? "Archived" : "Archive room"}</span>
              <p className={profileStyles.inviteMetaSub}>
                {room.is_archived
                  ? "This room is archived — hidden from the default room list and no longer accepts new messages, but its history is preserved."
                  : "Archiving hides this room from the default list and stops new messages, without deleting its history."}
              </p>
              <Button variant="secondary" loading={archiving} onClick={handleArchiveToggle}>
                {room.is_archived ? "Unarchive room" : "Archive room"}
              </Button>
            </div>

            <div className={profileStyles.adminSection}>
              <span className={profileStyles.sectionTitle}>Members</span>
              {membersStatus === "loading" && <p className={profileStyles.inviteMetaSub}>Loading members…</p>}
              {membersStatus === "error" && <p className={profileStyles.inviteMetaSub}>Couldn't load members.</p>}
              {memberError && (
                <span className={[profileStyles.feedback, profileStyles.feedbackError].join(" ")}>
                  {memberError}
                </span>
              )}

              {membersStatus === "ready" && (
                <ul className={listStyles.list}>
                  {members.map((m) => (
                    <li key={m.id} className={listStyles.item}>
                      {onViewProfile ? (
                        <button
                          type="button"
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.6em",
                            flex: 1,
                            minWidth: 0,
                            textAlign: "left",
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                            font: "inherit",
                            color: "inherit",
                            padding: 0,
                          }}
                          onClick={() => onViewProfile(m.user.id)}
                        >
                          <Avatar user={m.user} size="sm" />
                          <span className={listStyles.itemBody}>
                            <span className={listStyles.username}>{m.user.username}</span>
                            <span className={listStyles.bio}>{m.role}</span>
                          </span>
                        </button>
                      ) : (
                        <>
                          <Avatar user={m.user} size="sm" />
                          <span className={listStyles.itemBody}>
                            <span className={listStyles.username}>{m.user.username}</span>
                            <span className={listStyles.bio}>{m.role}</span>
                          </span>
                        </>
                      )}
                      <span style={{ display: "flex", gap: "0.75em", flexShrink: 0 }}>
                        <button
                          type="button"
                          className={profileStyles.linkBtn}
                          disabled={busyUserId === m.user.id}
                          onClick={() => handleRoleChange(m.user.id, m.role === "admin" ? "member" : "admin")}
                        >
                          {m.role === "admin" ? "Make member" : "Make admin"}
                        </button>
                        <button
                          type="button"
                          className={profileStyles.linkBtn}
                          disabled={busyUserId === m.user.id}
                          onClick={() => handleRemove(m.user.id)}
                        >
                          {m.user.id === currentUser.id ? "Leave" : "Remove"}
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {addable.length > 0 && (
                <div className={profileStyles.inviteRow}>
                  <select
                    className={profileStyles.inviteSelect}
                    value={addUserId}
                    onChange={(e) => setAddUserId(e.target.value)}
                    aria-label="Add a member"
                  >
                    <option value="">Add a member…</option>
                    {addable.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.username}
                      </option>
                    ))}
                  </select>
                  <Button variant="secondary" onClick={handleAddMember}>
                    Add
                  </Button>
                </div>
              )}
            </div>

            <WebhookSection roomId={room.id} />
          </>
        )}
      </div>
    </div>
  );
}
