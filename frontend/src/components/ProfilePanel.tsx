import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import type { Invitation, NotificationPreference, User, WorkspaceRole } from "../types";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import styles from "./ProfilePanel.module.css";

interface ProfilePanelProps {
  user: User | null;
  onClose: () => void;
  onSave: (patch: { bio?: string; avatar?: File }) => Promise<User>;
  onLogout: () => Promise<void>;
  /** null while the initial fetch (in useWorkspace) hasn't resolved yet. */
  notificationPreference: NotificationPreference | null;
  onUpdateNotificationPreference: (patch: Partial<NotificationPreference>) => Promise<NotificationPreference>;
}

/** Global per-category notification toggles — everyone gets this section
 * (unlike InviteAdminSection below, it's not role-gated; it's a personal
 * preference). Only covers categories that actually produce a
 * `Notification` row today (see playground/models.py's
 * NotificationPreference docstring) — direct messages stay WS-only, so
 * there's no DM toggle here. */
function NotificationPreferencesSection({
  preference,
  onUpdate,
}: {
  preference: NotificationPreference | null;
  onUpdate: (patch: Partial<NotificationPreference>) => Promise<NotificationPreference>;
}) {
  const [saving, setSaving] = useState<keyof NotificationPreference | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(key: keyof NotificationPreference) {
    if (!preference) return;
    setSaving(key);
    setError(null);
    try {
      await onUpdate({ [key]: !preference[key] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that setting.");
    } finally {
      setSaving(null);
    }
  }

  const rows: { key: keyof NotificationPreference; label: string; hint: string }[] = [
    { key: "mentions", label: "Direct @mentions", hint: "Someone @names you specifically." },
    { key: "channel_wide", label: "@channel / @here", hint: "A room-wide broadcast in a room you're in." },
    { key: "thread_replies", label: "Thread replies", hint: "New replies in threads you're following." },
  ];

  return (
    <div className={styles.adminSection}>
      <span className={styles.sectionTitle}>Notifications</span>
      {!preference ? (
        <p className={styles.inviteMetaSub}>Loading your notification settings…</p>
      ) : (
        rows.map((row) => (
          <label
            key={row.key}
            className={styles.label}
            style={{ display: "flex", alignItems: "center", gap: "0.5em", textTransform: "none" }}
          >
            <input
              type="checkbox"
              checked={preference[row.key]}
              disabled={saving === row.key}
              onChange={() => toggle(row.key)}
            />
            <span>
              {row.label}
              <span className={styles.inviteMetaSub} style={{ display: "block" }}>
                {row.hint}
              </span>
            </span>
          </label>
        ))
      )}
      {error && <span className={[styles.feedback, styles.feedbackError].join(" ")}>{error}</span>}
    </div>
  );
}

/** Admin/owner-only: generate invite links and see who's pending. Kept
 * deliberately small — a full admin dashboard is a later slice's job, this
 * is just enough to make invitations usable from the UI. */
function InviteAdminSection() {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loadStatus, setLoadStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [role, setRole] = useState<WorkspaceRole>("member");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  useEffect(() => {
    setLoadStatus("loading");
    api
      .listInvitations()
      .then((data) => {
        setInvitations(data);
        setLoadStatus("ready");
      })
      .catch(() => setLoadStatus("error"));
  }, []);

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      const invite = await api.createInvitation({ role });
      setInvitations((prev) => [invite, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't create invite.");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: number) {
    setInvitations((prev) => prev.filter((i) => i.id !== id));
    await api.revokeInvitation(id).catch(() => {});
  }

  async function handleCopy(invite: Invitation) {
    try {
      await navigator.clipboard.writeText(invite.invite_link);
      setCopiedId(invite.id);
      setTimeout(() => setCopiedId((prev) => (prev === invite.id ? null : prev)), 1500);
    } catch {
      // Clipboard permission denied or unavailable — silently no-op, the
      // link is still visible/selectable in the row itself.
    }
  }

  return (
    <div className={styles.adminSection}>
      <span className={styles.sectionTitle}>Invite people</span>

      <div className={styles.inviteRow}>
        <select
          className={styles.inviteSelect}
          value={role}
          onChange={(e) => setRole(e.target.value as WorkspaceRole)}
          aria-label="Role for invitee"
        >
          <option value="member">Member</option>
          <option value="admin">Admin</option>
          <option value="guest">Guest</option>
        </select>
        <Button variant="secondary" loading={creating} onClick={handleCreate}>
          Generate invite link
        </Button>
      </div>
      {error && <span className={[styles.feedback, styles.feedbackError].join(" ")}>{error}</span>}

      {loadStatus === "loading" && <p className={styles.inviteMetaSub}>Loading invitations…</p>}
      {loadStatus === "error" && <p className={styles.inviteMetaSub}>Couldn't load invitations.</p>}
      {loadStatus === "ready" && invitations.length === 0 && (
        <p className={styles.inviteMetaSub}>No pending invitations.</p>
      )}

      {loadStatus === "ready" && invitations.length > 0 && (
        <ul className={styles.inviteList}>
          {invitations.map((invite) => (
            <li key={invite.id} className={styles.inviteItem}>
              <span className={styles.inviteMeta}>
                <span>{invite.role}{invite.note ? ` — ${invite.note}` : ""}</span>
                <span className={styles.inviteMetaSub}>
                  Expires {new Date(invite.expires_at).toLocaleDateString()}
                </span>
              </span>
              <span className={styles.inviteActions}>
                <button type="button" className={styles.linkBtn} onClick={() => handleCopy(invite)}>
                  {copiedId === invite.id ? "Copied!" : "Copy link"}
                </button>
                <button type="button" className={styles.linkBtn} onClick={() => handleRevoke(invite.id)}>
                  Revoke
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ProfilePanel({
  user,
  onClose,
  onSave,
  onLogout,
  notificationPreference,
  onUpdateNotificationPreference,
}: ProfilePanelProps) {
  const [bio, setBio] = useState(user?.bio ?? "");
  const [pendingAvatar, setPendingAvatar] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  if (!user) {
    return (
      <div className={styles.panel} role="dialog" aria-label="Your profile">
        <div className={styles.head}>
          <span className={styles.title}>Profile</span>
          <button type="button" className={styles.closeBtn} aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <EmptyState icon="…" title="Loading profile" body="Fetching your account details." />
      </div>
    );
  }

  const displayUser = preview ? { ...user, avatar: preview } : user;

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setPendingAvatar(file);
    setPreview(URL.createObjectURL(file));
  }

  async function handleSave() {
    setSaving(true);
    setFeedback(null);
    try {
      await onSave({ bio, avatar: pendingAvatar ?? undefined });
      setFeedback({ kind: "success", text: "Saved." });
      setPendingAvatar(null);
    } catch (err) {
      setFeedback({ kind: "error", text: err instanceof Error ? err.message : "Couldn't save changes." });
    } finally {
      setSaving(false);
    }
  }

  async function handleLogout() {
    setLoggingOut(true);
    setLogoutError(null);
    try {
      await onLogout();
    } catch (err) {
      setLogoutError(err instanceof Error ? err.message : "Couldn't log out.");
      setLoggingOut(false);
    }
    // On success, the parent flips authStatus to "anonymous" and this panel
    // unmounts along with the rest of the workspace shell — no need to
    // reset `loggingOut` here.
  }

  return (
    <div className={styles.panel} role="dialog" aria-label="Your profile">
      <div className={styles.head}>
        <span className={styles.title}>Profile</span>
        <button type="button" className={styles.closeBtn} aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <div className={styles.body}>
        <div className={styles.avatarRow}>
          <Avatar user={displayUser} size="lg" />
          <div>
            <p className={styles.username}>{user.username}</p>
            <button type="button" className={styles.changePhoto} onClick={() => fileRef.current?.click()}>
              Change photo
            </button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={handleFile} />
          </div>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="profile-bio">
            Bio
          </label>
          <textarea
            id="profile-bio"
            className={styles.textarea}
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            placeholder="Tell your team a little about yourself"
          />
        </div>

        <div className={styles.footer}>
          <Button variant="primary" loading={saving} onClick={handleSave}>
            Save changes
          </Button>
          {feedback && (
            <span
              className={[styles.feedback, feedback.kind === "error" ? styles.feedbackError : ""].join(" ")}
            >
              {feedback.text}
            </span>
          )}
        </div>

        <NotificationPreferencesSection
          preference={notificationPreference}
          onUpdate={onUpdateNotificationPreference}
        />

        {(user.role === "admin" || user.role === "owner") && <InviteAdminSection />}

        <div className={styles.logoutRow}>
          <Button variant="secondary" loading={loggingOut} onClick={handleLogout}>
            Log out
          </Button>
          {logoutError && <span className={[styles.feedback, styles.feedbackError].join(" ")}>{logoutError}</span>}
        </div>
      </div>
    </div>
  );
}
