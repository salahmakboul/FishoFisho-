import { useEffect, useState } from "react";
import type { Workspace } from "../hooks/useWorkspace";
import { AdminPanel } from "./AdminPanel";
import { AIAssistantPanel } from "./AIAssistantPanel";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import { Chat } from "./Chat";
import { Directory } from "./Directory";
import { EmptyState } from "./EmptyState";
import { Header } from "./Header";
import { ProfilePanel } from "./ProfilePanel";
import { RoomSettingsPanel } from "./RoomSettingsPanel";
import { SearchCommand } from "./SearchCommand";
import { Sidebar, otherParticipant } from "./Sidebar";
import { ThreadPanel } from "./ThreadPanel";
import { UserProfileCard } from "./UserProfileCard";
import styles from "./Layout.module.css";

type PanelKind = "ai" | "profile" | "directory" | "room-settings" | "thread" | "admin" | null;

export function Layout({
  workspace,
  onLoggedOut,
}: {
  workspace: Workspace;
  /** Called after the server-side session is actually cleared, so App.tsx
   * can send the browser back to "/" — keeps the /app <-> / URL split
   * accurate the moment auth state changes, not just on next load. */
  onLoggedOut: () => void;
}) {
  const [panel, setPanel] = useState<PanelKind>(null);
  // Which thread's panel is open (only meaningful while panel === "thread").
  // Kept separate from `panel` itself so switching rooms/closing the panel
  // doesn't need to juggle a union of "which kind + which id".
  const [threadRootId, setThreadRootId] = useState<number | null>(null);
  // SearchCommand overlay — deliberately its own boolean rather than folded
  // into `panel`: it's a centered modal over everything (including whatever
  // aside is open), not one more slot competing for the aside position.
  const [searchOpen, setSearchOpen] = useState(false);
  // Set by a search "message" result's onSelectMessage — passed to Chat as
  // highlightMessageId so it can scroll/pulse the target once it's loaded
  // (see Chat.tsx's highlightMessageId prop docstring).
  const [pendingHighlightId, setPendingHighlightId] = useState<number | null>(null);
  // Which user's profile card is open — a centered modal like SearchCommand
  // (not a side panel/PanelKind slot), so it can be opened from on top of
  // whatever aside panel is already showing (Chat, RoomSettingsPanel, and
  // Directory all trigger this same state via onViewProfile).
  const [viewingUserId, setViewingUserId] = useState<number | null>(null);
  const activeRoom = workspace.rooms.find((r) => r.id === workspace.activeRoomId) ?? null;
  const activeConversation =
    workspace.conversations.find((c) => c.id === workspace.activeConversationId) ?? null;
  const dmOther = activeConversation ? otherParticipant(activeConversation, workspace.currentUser) : null;

  // The room-settings panel's ADMIN content (rename/describe/private
  // toggle, archive, member management) is only offered to that room's own
  // admin (Room.my_role, server-computed) or a workspace admin/owner —
  // mirrors the backend's IsRoomAdminOrWorkspaceAdmin permission exactly,
  // so those controls never appear for someone who'd just get a 403 from
  // them. RoomSettingsPanel itself, though, is openable by ANY room member
  // (see canOpenRoomSettings below) since it also hosts a personal, non-
  // admin-gated notification-preferences section (mute/all/mentions-only
  // for this one room) that every member should be able to reach.
  const canManageActiveRoom =
    workspace.focusMode === "room" &&
    !!activeRoom &&
    !!workspace.currentUser &&
    (activeRoom.my_role === "admin" ||
      workspace.currentUser.role === "admin" ||
      workspace.currentUser.role === "owner");

  const canOpenRoomSettings = workspace.focusMode === "room" && !!activeRoom && !!workspace.currentUser;

  // Admin panel: workspace admin/owner only, from anywhere (not room- or
  // conversation-scoped like the two checks above) — mirrors the backend's
  // IsWorkspaceAdminOrOwner gate on every /api/v1/admin/* endpoint.
  const canOpenAdminPanel =
    !!workspace.currentUser &&
    (workspace.currentUser.role === "admin" || workspace.currentUser.role === "owner");

  useEffect(() => {
    if (panel === "room-settings" && !canOpenRoomSettings) setPanel(null);
  }, [panel, canOpenRoomSettings]);

  useEffect(() => {
    if (panel === "admin" && !canOpenAdminPanel) setPanel(null);
  }, [panel, canOpenAdminPanel]);

  // A thread panel is scoped to one room's message; switching rooms (or
  // leaving room-chat focus entirely) invalidates it.
  useEffect(() => {
    setThreadRootId(null);
    setPanel((p) => (p === "thread" ? null : p));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.activeRoomId]);

  useEffect(() => {
    if (!panel) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPanel(null);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panel]);

  // Global "open search" shortcut (Ctrl+K / Cmd+K) — a command-palette-style
  // overlay is exactly the kind of "quick navigation" primitive people
  // expect a keyboard shortcut for, on top of the Header button.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function handleSelectSearchRoom(roomId: number) {
    workspace.setActiveRoomId(roomId);
  }

  function handleSelectSearchMessage(roomId: number, messageId: number) {
    workspace.setActiveRoomId(roomId);
    setPendingHighlightId(messageId);
  }

  function handleSelectSearchUser(userId: number) {
    return workspace.startConversation(userId);
  }

  let headerTitle = "FishoFisho";
  let headerSubtitle: string | undefined;
  let headerMeta: string | undefined;
  if (workspace.focusMode === "room" && activeRoom) {
    headerTitle = `#${activeRoom.name}`;
    headerSubtitle = activeRoom.description;
    headerMeta = activeRoom.member_count != null ? `${activeRoom.member_count} members` : undefined;
  } else if (workspace.focusMode === "conversation" && activeConversation && dmOther) {
    const isGroup = activeConversation.participants.length > 2;
    headerTitle = isGroup ? activeConversation.display_name ?? dmOther.username : dmOther.username;
    headerSubtitle = isGroup ? "Group conversation" : "Direct message";
  }

  return (
    <div className={styles.app}>
      <div className={styles.rail}>
        <span className={styles.logo} aria-hidden="true">F</span>
        <div className={styles.railSpacer} />
        {workspace.currentUser && (
          <button
            type="button"
            className={styles.railButton}
            aria-label="Your profile"
            aria-pressed={panel === "profile"}
            onClick={() => setPanel((p) => (p === "profile" ? null : "profile"))}
          >
            <Avatar user={workspace.currentUser} size="sm" />
          </button>
        )}
      </div>

      <Sidebar
        rooms={workspace.rooms}
        status={workspace.roomsStatus}
        error={workspace.roomsError ?? undefined}
        activeRoomId={workspace.activeRoomId}
        onSelectRoom={workspace.setActiveRoomId}
        onCreateRoom={(name, isPrivate) => workspace.createRoom({ name, is_private: isPrivate })}
        currentUser={workspace.currentUser}
        conversations={workspace.conversations}
        conversationsStatus={workspace.conversationsStatus}
        conversationsError={workspace.conversationsError ?? undefined}
        activeConversationId={workspace.activeConversationId}
        unreadConversationCount={workspace.unreadConversationCount}
        onSelectConversation={workspace.setActiveConversationId}
        onDeleteConversation={workspace.removeConversation}
        onOpenDirectory={() => setPanel("directory")}
      />

      <div className={styles.main}>
        <div className={styles.content}>
          <Header
            title={headerTitle}
            subtitle={headerSubtitle}
            meta={headerMeta}
            connectionStatus={workspace.connectionStatus}
            notifications={workspace.notifications}
            unreadCount={workspace.unreadNotificationCount}
            onMarkNotificationRead={workspace.markNotificationRead}
            onMarkAllRead={workspace.markAllNotificationsRead}
            aiOpen={panel === "ai"}
            onToggleAi={() => setPanel((p) => (p === "ai" ? null : "ai"))}
            roomSettingsOpen={panel === "room-settings"}
            onToggleRoomSettings={
              canOpenRoomSettings ? () => setPanel((p) => (p === "room-settings" ? null : "room-settings")) : undefined
            }
            adminOpen={panel === "admin"}
            onToggleAdmin={canOpenAdminPanel ? () => setPanel((p) => (p === "admin" ? null : "admin")) : undefined}
            onOpenSearch={() => setSearchOpen(true)}
          />

          {workspace.focusMode === "room" && (
            <>
              {workspace.roomsStatus === "loading" && (
                <EmptyState icon="…" title="Loading your workspace" body="Fetching rooms and recent activity." />
              )}
              {workspace.roomsStatus === "error" && (
                <EmptyState
                  variant="error"
                  icon="!"
                  title="Couldn't load rooms"
                  body={workspace.roomsError ?? "Something went wrong talking to the server."}
                  action={<Button variant="secondary" onClick={workspace.retryLoadRooms}>Retry</Button>}
                />
              )}
              {workspace.roomsStatus === "ready" && workspace.rooms.length === 0 && (
                <EmptyState
                  icon="#"
                  title="No rooms yet"
                  body="Create your first room from the sidebar to start a conversation."
                />
              )}
              {activeRoom && workspace.currentUser && (
                <Chat
                  threadId={activeRoom.id}
                  currentUser={workspace.currentUser}
                  users={workspace.users}
                  messages={workspace.messages}
                  status={workspace.messagesStatus}
                  error={workspace.messagesError}
                  sending={workspace.sending}
                  onSend={workspace.sendMessage}
                  onRetry={() => workspace.retryLoadMessages(activeRoom.id)}
                  onRetryMessage={(clientId) => workspace.retrySendMessage(activeRoom.id, clientId)}
                  loadingBody={`Fetching the latest in #${activeRoom.name}.`}
                  emptyIcon="#"
                  emptyTitle={`Welcome to #${activeRoom.name}`}
                  emptyBody={
                    activeRoom.description
                      ? activeRoom.description
                      : "This is the very start of the room. Say hello to get things going."
                  }
                  placeholder="Message this room — try @FishoAI, @channel, or @username"
                  onEdit={workspace.editMessage}
                  onDelete={workspace.deleteMessage}
                  onToggleReaction={workspace.toggleReaction}
                  onLoadOlder={() => workspace.loadOlderMessages(activeRoom.id)}
                  hasOlder={workspace.hasOlderMessages}
                  loadingOlder={workspace.loadingOlderMessages}
                  canModerate={canManageActiveRoom}
                  allowAttachments
                  onOpenThread={(id) => {
                    setThreadRootId(id);
                    setPanel("thread");
                  }}
                  highlightMessageId={pendingHighlightId}
                  onViewProfile={setViewingUserId}
                />
              )}
            </>
          )}

          {workspace.focusMode === "conversation" && (
            <>
              {!activeConversation && (
                <EmptyState
                  icon="@"
                  title="Select a conversation"
                  body="Pick someone from Direct messages in the sidebar, or browse the directory to start a new one."
                  action={<Button variant="secondary" onClick={() => setPanel("directory")}>Browse people</Button>}
                />
              )}
              {activeConversation && workspace.currentUser && (
                <Chat
                  threadId={activeConversation.id}
                  currentUser={workspace.currentUser}
                  users={workspace.users}
                  messages={workspace.conversationMessages}
                  status={workspace.conversationMessagesStatus}
                  error={workspace.conversationMessagesError}
                  sending={workspace.sendingDm}
                  onSend={workspace.sendConversationMessage}
                  onRetry={() => workspace.retryLoadConversationMessages(activeConversation.id)}
                  onRetryMessage={(clientId) => workspace.retrySendConversationMessage(activeConversation.id, clientId)}
                  loadingBody={`Fetching your conversation with ${dmOther?.username ?? "them"}.`}
                  emptyIcon="@"
                  emptyTitle={dmOther ? `Say hello to ${dmOther.username}` : "Start the conversation"}
                  emptyBody="This is the very start of your private conversation — say hello to get things going."
                  placeholder={dmOther ? `Message ${dmOther.username}` : "Message"}
                  onViewProfile={setViewingUserId}
                />
              )}
            </>
          )}
        </div>

        {panel === "ai" && (
          <aside className={styles.aside}>
            <AIAssistantPanel
              onClose={() => setPanel(null)}
              roomName={activeRoom?.name ?? null}
              onSummarize={
                activeRoom ? () => workspace.sendMessage("@fishoai summarize this room") : undefined
              }
            />
          </aside>
        )}
        {panel === "profile" && (
          <aside className={styles.aside}>
            <ProfilePanel
              user={workspace.currentUser}
              onClose={() => setPanel(null)}
              onSave={workspace.updateProfile}
              onLogout={() => workspace.logout().then(onLoggedOut)}
              notificationPreference={workspace.notificationPreference}
              onUpdateNotificationPreference={workspace.updateNotificationPreference}
            />
          </aside>
        )}
        {panel === "directory" && (
          <aside className={styles.aside}>
            <Directory
              users={workspace.users}
              currentUser={workspace.currentUser}
              onClose={() => setPanel(null)}
              onStartConversation={workspace.startConversation}
              onViewProfile={setViewingUserId}
            />
          </aside>
        )}
        {panel === "thread" && threadRootId != null && activeRoom && workspace.currentUser && (
          <aside className={styles.aside}>
            <ThreadPanel
              roomId={activeRoom.id}
              rootId={threadRootId}
              currentUser={workspace.currentUser}
              roomMessages={workspace.messages}
              onClose={() => setPanel(null)}
              onSendReply={(body) => workspace.sendMessage(body, { replyTo: threadRootId })}
            />
          </aside>
        )}
        {panel === "admin" && workspace.currentUser && canOpenAdminPanel && (
          <aside className={styles.aside}>
            <AdminPanel currentUser={workspace.currentUser} users={workspace.users} onClose={() => setPanel(null)} />
          </aside>
        )}
        {panel === "room-settings" && activeRoom && workspace.currentUser && canOpenRoomSettings && (
          <aside className={styles.aside}>
            <RoomSettingsPanel
              room={activeRoom}
              currentUser={workspace.currentUser}
              users={workspace.users}
              isRoomAdmin={canManageActiveRoom}
              onClose={() => setPanel(null)}
              onUpdateRoom={(patch) => workspace.updateRoomSettings(activeRoom.id, patch)}
              onArchive={() => workspace.archiveRoom(activeRoom.id)}
              onUnarchive={() => workspace.unarchiveRoom(activeRoom.id)}
              onViewProfile={setViewingUserId}
            />
          </aside>
        )}
      </div>

      <SearchCommand
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        recentRooms={workspace.rooms}
        onSelectRoom={handleSelectSearchRoom}
        onSelectMessage={handleSelectSearchMessage}
        onSelectUser={handleSelectSearchUser}
      />

      {viewingUserId != null && (
        <UserProfileCard
          userId={viewingUserId}
          currentUser={workspace.currentUser}
          onClose={() => setViewingUserId(null)}
          onStartConversation={workspace.startConversation}
        />
      )}
    </div>
  );
}
