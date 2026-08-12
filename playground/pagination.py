from rest_framework.pagination import CursorPagination


class RoomMessageCursorPagination(CursorPagination):
    """Cursor pagination for a room's message feed.

    Cursor (not page-number) pagination is the right fit for a
    live-appending feed: page-number pagination breaks/duplicates rows as
    new messages arrive between requests, cursor pagination doesn't.

    `ordering` must match MessageListCreateView.get_queryset()'s explicit
    `.order_by("-created")` — newest first, so the *first* page (no cursor)
    is "the latest N messages", matching how a chat UI actually opens. The
    "next" link then walks further into the past ("load older messages"),
    not forward into messages that don't exist yet.
    """

    page_size = 40
    ordering = "-created"


class ConversationMessageCursorPagination(CursorPagination):
    """Same idea as RoomMessageCursorPagination, for private-message
    conversations. `ordering` matches
    PrivateMessageListCreateView.get_queryset()'s `.order_by("-created_at")`."""

    page_size = 40
    ordering = "-created_at"


class RoomSearchCursorPagination(CursorPagination):
    """Cursor pagination for a single-category room search
    (``GET /api/v1/search/?type=rooms``). Plain alphabetical ordering — the
    "?type=all" combined preview additionally ranks exact-name matches
    first (see SearchView in api_views.py), but that ranking depends on the
    search term itself and isn't a stable, term-independent DB ordering, so
    it isn't something CursorPagination (which encodes a cursor purely from
    ordering-field values) can be built on for the "give me more" paginated
    case. Judgment call: paginated single-category results trade the
    exact-match-first heuristic for simple, stable alphabetical order."""

    page_size = 20
    ordering = "name"


class UserSearchCursorPagination(CursorPagination):
    """Same idea as RoomSearchCursorPagination, for a single-category user
    search (``GET /api/v1/search/?type=users``)."""

    page_size = 20
    ordering = "username"


class AuditLogCursorPagination(CursorPagination):
    """Cursor pagination for GET /api/v1/admin/audit-log/. `ordering` must
    match AuditLogView.get_queryset()'s implicit AuditEvent.Meta.ordering
    (``-created_at``) — newest action first, matching how an admin actually
    wants to read a moderation trail."""

    page_size = 30
    ordering = "-created_at"


class AIInteractionCursorPagination(CursorPagination):
    """Cursor pagination for GET /api/v1/admin/ai-interactions/. `ordering`
    matches AIInteraction.Meta.ordering (``-created_at``) — most recent AI
    trigger first, same "newest action first" convention as
    AuditLogCursorPagination."""

    page_size = 30
    ordering = "-created_at"
