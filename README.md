# FishoFisho

A real-time team chat platform — rooms (channels), threads, direct/group messaging, an in-context AI assistant, search, and workspace administration — built as a Django REST + WebSocket API behind a React single-page app.

Single implicit workspace (not multi-tenant): everyone in the database shares one workspace, with real role-based access control (`owner` / `admin` / `member` / `guest`) layered on top.

---

## Stack

| Layer | Technology |
|---|---|
| Backend API | Django 5.2 + Django REST Framework |
| Real-time | Django Channels 4 (WebSocket), served via Daphne |
| Frontend | React 18 + TypeScript + Vite |
| Database | SQLite (local dev) / PostgreSQL (production, via `DATABASE_URL`) |
| Auth | Django session auth + CSRF (no JWT) |
| AI | Google Gemini (with an offline mock-response fallback when no API key is configured) |
| Design system | Strict monochrome (no color), IBM Plex Sans, CSS Modules, no UI kit |

No task queue (Celery/RQ), no Redis, no cloud storage (S3) are configured — see **Known limitations** below for what that means and how to upgrade later.

---

## Running it locally

```powershell
# Backend
venv\Scripts\python manage.py migrate
venv\Scripts\python manage.py runserver 127.0.0.1:8001

# Frontend (build once, Django serves the built files directly)
cd frontend
npm install
npm run build
```
Open **http://127.0.0.1:8001/** — `/` is the public landing page, `/app` is the authenticated workspace (you're bounced between them automatically based on login state).

For frontend hot-reload while developing UI instead:
```powershell
cd frontend
$env:VITE_USE_MOCK = "false"
npm run dev
```
→ **http://localhost:5173** (Vite proxies `/api` and `/ws` to Django on :8001).

Omitting `VITE_USE_MOCK=false` runs the frontend against an in-memory mock fixture with no backend required — useful for pure UI work.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | insecure placeholder (app refuses to boot with it when `DEBUG=False`) |
| `DEBUG` | `True`/`False` | `False` (only set `True` in local `.env`) |
| `DATABASE_URL` | Postgres connection string | falls back to local SQLite if unset |
| `GEMINI_API_KEY` | Google Gemini API key for the AI assistant | mock responses if unset |
| `FRONTEND_URL` | Base URL used to build password-reset links | `http://localhost:5173` |

---

## Architecture

```
Browser
  │
  ├── GET /            → React SPA shell (frontend/dist/index.html, served by Django)
  ├── GET /login /register  → React SPA (client-side auth screens, not Django templates)
  ├── /api/v1/*         → Django REST Framework (session auth + CSRF)
  ├── /ws/stream/       → Django Channels (single WebSocket per connected user)
  └── /admin/           → Django's built-in admin (staff/superuser only)
```

- **One WebSocket connection per user**, not per room — it joins a personal `user_<id>` group (for notifications/DMs) and per-room `room_<id>` groups on demand. All real-time events (new/edited/deleted messages, reactions, notifications, presence) flow through this single connection.
- **Event flow**: a model's `post_save` (in `playground/signals.py`) pushes onto the Channels layer → `StreamConsumer` relays it to whichever connected clients are in the relevant group. Outgoing webhooks hook into this exact same signal point.
- **Authorization is enforced server-side**, not just hidden in the UI — every room/message/thread/DM endpoint re-checks membership and role on the backend (see `playground/permissions.py`).

---

## Database — 21 models across one app (`playground`)

| Model | Purpose |
|---|---|
| `Topic` | Category tag a room can belong to |
| `Room` | A channel — public or private, archivable |
| `RoomMembership` | Per-room membership + per-room role (admin/member) |
| `Message` | Room message — supports edit, soft-delete, reply-to-thread-root, client-side idempotency key |
| `Reaction` | Emoji reaction on a message |
| `MessageAttachment` | Uploaded file on a message, served via signed URL |
| `ThreadFollow` | Who's following a thread (for reply notifications) |
| `Notification` | In-app notification (mention, thread reply, etc.) |
| `NotificationPreference` | Per-user global notification category toggles |
| `RoomNotificationSetting` | Per-user, per-room notification override (mute/all/mentions-only) |
| `UserProfile` | Avatar, bio, workspace role, ban/timeout state |
| `Invitation` | Shareable invite link with a pre-assigned role |
| `PrivateConversation` | 1:1 or group DM |
| `PrivateMessage` | A message within a DM/group conversation |
| `ConversationRead` | Per-participant last-read tracking (per-participant unread counts) |
| `AuditEvent` | Immutable log of admin/moderation actions |
| `ModerationAction` | A warning/timeout/ban/unban issued to a user |
| `BlockedKeyword` | Workspace-wide filtered phrase list |
| `AIInteraction` | Audit record of every AI assistant invocation |
| `Webhook` | Outgoing (signed HTTP POST) + incoming (token URL) integration endpoint |
| `WebhookDelivery` | Delivery attempt log for outgoing webhooks |

23 migrations, applied cleanly against both SQLite and Postgres.

---

## Backend modules (`playground/`)

| File | Responsibility |
|---|---|
| `models.py` | All 21 models |
| `serializers.py` | DRF serializers |
| `api_views.py` | Most REST endpoints (rooms, messages, threads, DMs, search, invitations…) |
| `admin_views.py` | Admin-only endpoints (audit log, moderation, blocked keywords, AI activity) |
| `api_urls.py` / `urls.py` | URL routing |
| `permissions.py` | The authorization vocabulary shared by every view (`user_can_access_room`, `IsWorkspaceAdminOrOwner`, `IsRoomAdminOrWorkspaceAdmin`, guest restrictions, ban checks) |
| `consumers.py` | The single WebSocket consumer (`StreamConsumer`) |
| `routing.py` | Channels URL routing |
| `signals.py` | `post_save` → WebSocket broadcast + outgoing webhook dispatch |
| `audit.py` | `log_audit_event` helper |
| `signing.py` | Signed, time-limited attachment download tokens |
| `pagination.py` | Cursor pagination classes |
| `ai_helper.py` | `AIAssistant` — Gemini wrapper with mock fallback, room-scoped context building |
| `views.py` | The three surviving server-rendered concerns: nothing app-specific (auth is now pure API) |
| `tests.py` | 33 test classes, 202 test methods |

## Frontend components (`frontend/src/components/`)

| Component | Purpose |
|---|---|
| `Landing.tsx` | Public marketing page (logged-out `/`) |
| `Auth.tsx` | Login / register / forgot-password / reset-password |
| `Layout.tsx` | App shell — coordinates every panel/modal |
| `Sidebar.tsx` | Room list + DM list + create-room modal |
| `Header.tsx` | Active room/DM title, search, notifications, connection status |
| `Chat.tsx` | Message list + composer (edit, delete, react, reply, attach, mention-autocomplete, optimistic send) |
| `ThreadPanel.tsx` | Thread replies + follow/unfollow |
| `Directory.tsx` | Browse all users, start DM/group |
| `UserProfileCard.tsx` | Click-through profile popup from anywhere |
| `RoomSettingsPanel.tsx` | Room rename/archive/members/webhooks/notification settings |
| `ProfilePanel.tsx` | Your own profile, notification preferences, invite management |
| `AdminPanel.tsx` | Audit log, moderation, blocked keywords, AI activity |
| `SearchCommand.tsx` | `Ctrl/Cmd+K` global search |
| `AIAssistantPanel.tsx` | AI assistant info + "Summarize this room" |
| `Avatar.tsx`, `Button.tsx`, `MentionBadge.tsx`, `EmptyState.tsx` | Shared primitives |

---

## Feature summary

- **Auth**: register/login/logout, password recovery (console-logged email), session expiry with "remember me"
- **Workspace/RBAC**: owner/admin/member/guest roles, invite links, private rooms with real membership
- **Rooms**: create/edit/archive/delete, per-room admin, member management, notification mute
- **Messaging**: edit, soft-delete, reactions, inline reply, `@mention`/`@channel`/`@here`, attachments (signed download URLs), cursor pagination
- **Real-time**: single WebSocket, heartbeat + exponential-backoff reconnect, idempotent sends, true optimistic UI
- **Threads**: reply counts, thread panel, follow/unfollow, thread-reply notifications
- **DMs**: 1:1 and group conversations, per-participant unread counts, presence (online/offline)
- **Notifications**: real notification center, global + per-room preferences (mentions/channel-wide/threads modeled separately, not one toggle)
- **Permissions**: server-side authorization on every endpoint, guest restrictions, audited in a dedicated slice
- **Files**: attachments stored outside public media, served only via signed, time-limited, permission-checked URLs
- **Search**: rooms/messages/people, respects room privacy, keyboard-navigable
- **Admin/moderation**: audit log, warnings/timeouts/bans, keyword filtering, rate limiting
- **AI**: `@FishoAI` mentions, room-scoped summarization, usage limits, every invocation audited
- **Integrations**: outgoing webhooks (HMAC-signed) and incoming webhook URLs, built as a boundary for future integrations

---

## Known limitations (by design, not oversights)

These were deliberately deferred rather than adding infrastructure the project doesn't have yet:

- **Email is console-only** — password reset links print to the server log, they aren't actually emailed. Needs a real SMTP/transactional-email provider.
- **No task queue** — outgoing webhook delivery uses a bare background thread with no retry. Celery/RQ is the natural upgrade.
- **No Redis** — the WebSocket channel layer is in-process only; fine for one server instance, won't work across multiple instances/machines.
- **No cloud storage** — attachments and avatars live on local disk. The signed-URL system for attachments is built to swap onto S3 (`django-storages`) without a rewrite.
- **No Postgres full-text search** — search uses portable `icontains` matching; upgrading to `SearchVector` is a reasonable next step once running on Postgres consistently.

---

## Deploying

Set in your hosting provider's environment (Railway, etc.):
- `SECRET_KEY` — a real random value, generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DATABASE_URL` — provided automatically by most Postgres addons
- `GEMINI_API_KEY` — if you want real AI responses instead of the mock fallback
- Do **not** set `DEBUG` — it correctly defaults to off

The app boots over HTTPS correctly via `SECURE_PROXY_SSL_HEADER` (trusts `X-Forwarded-Proto` from the platform's proxy), with secure cookies and HSTS enabled automatically whenever `DEBUG` is off.
