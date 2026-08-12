import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import * as api from "../lib/api";
import type { Room, SearchMessageResult, SearchResults, User } from "../types";
import { Avatar } from "./Avatar";
import styles from "./SearchCommand.module.css";

const DEBOUNCE_MS = 280;

type ResultKind = "room" | "message" | "user";

/** One flattened, keyboard-navigable row — built from SearchResults (or,
 * for the empty-query default, from `recentRooms`) so ArrowUp/ArrowDown
 * can move through Rooms/Messages/People as a single linear list without
 * caring which section a row came from. */
interface Row {
  kind: ResultKind;
  key: string;
  room?: Room;
  message?: SearchMessageResult;
  user?: User;
}

/** Wraps every case-insensitive occurrence of `query` in `text` with a
 * <mark>, client-side — see SearchView's docstring in api_views.py for why
 * highlighting is done here rather than the server pre-computing snippet
 * position markers: a plain `icontains` match is a one-line client-side
 * split, not worth duplicating on both ends. */
function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim();
  if (!q) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark className={styles.mark}>{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  );
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export function SearchCommand({
  open,
  onClose,
  recentRooms,
  onSelectRoom,
  onSelectMessage,
  onSelectUser,
}: {
  open: boolean;
  onClose: () => void;
  /** Shown as a lightweight default when the query is empty, instead of a
   * blank overlay — the workspace's own room list, most-recently-updated
   * first (matches Room's default ordering). */
  recentRooms: Room[];
  onSelectRoom: (roomId: number) => void;
  onSelectMessage: (roomId: number, messageId: number) => void;
  onSelectUser: (userId: number) => Promise<unknown> | void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // Guards against a slow earlier request clobbering a faster later one.
  const requestIdRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults(null);
    setStatus("idle");
    setActiveIndex(0);
    // Focus after the overlay has actually mounted/animated in.
    const t = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const trimmed = query.trim();
    if (!trimmed) {
      setResults(null);
      setStatus("idle");
      return;
    }
    setStatus("loading");
    const requestId = ++requestIdRef.current;
    const timer = window.setTimeout(() => {
      api
        .search(trimmed)
        .then((data) => {
          if (requestIdRef.current !== requestId) return; // stale
          setResults(data);
          setStatus("ready");
        })
        .catch(() => {
          if (requestIdRef.current !== requestId) return;
          setStatus("error");
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, open]);

  const rows: Row[] = useMemo(() => {
    if (!query.trim()) {
      return recentRooms.slice(0, 8).map((r) => ({ kind: "room" as const, key: `room-${r.id}`, room: r }));
    }
    if (!results) return [];
    return [
      ...results.rooms.map((r) => ({ kind: "room" as const, key: `room-${r.id}`, room: r })),
      ...results.messages.map((m) => ({ kind: "message" as const, key: `message-${m.id}`, message: m })),
      ...results.users.map((u) => ({ kind: "user" as const, key: `user-${u.id}`, user: u })),
    ];
  }, [query, results, recentRooms]);

  useEffect(() => {
    setActiveIndex(0);
  }, [rows.length]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-row-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  function activate(row: Row) {
    if (row.kind === "room" && row.room) {
      onSelectRoom(row.room.id);
    } else if (row.kind === "message" && row.message) {
      onSelectMessage(row.message.room.id, row.message.id);
    } else if (row.kind === "user" && row.user) {
      onSelectUser(row.user.id);
    }
    onClose();
  }

  function handleInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[activeIndex];
      if (row) activate(row);
    }
  }

  if (!open) return null;

  const trimmedQuery = query.trim();
  const showEmptyDefault = !trimmedQuery;
  const showNoResults = !showEmptyDefault && status === "ready" && rows.length === 0;

  return (
    <div className={styles.overlay} onMouseDown={onClose}>
      <div
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.inputRow}>
          <span className={styles.searchIcon} aria-hidden="true">⌕</span>
          <input
            ref={inputRef}
            className={styles.input}
            type="text"
            role="combobox"
            aria-expanded={rows.length > 0}
            aria-activedescendant={rows[activeIndex] ? `search-row-${activeIndex}` : undefined}
            placeholder="Search rooms, messages, and people…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleInputKeyDown}
          />
          <button type="button" className={styles.closeBtn} aria-label="Close search" onClick={onClose}>
            Esc
          </button>
        </div>

        <div className={styles.results} ref={listRef} role="listbox">
          {showEmptyDefault && rows.length > 0 && (
            <div className={styles.section}>
              <div className={styles.sectionLabel}>Recent rooms</div>
              {rows.map((row, i) => (
                <ResultRow key={row.key} row={row} index={i} active={i === activeIndex} query={trimmedQuery} onSelect={activate} />
              ))}
            </div>
          )}

          {showEmptyDefault && rows.length === 0 && (
            <p className={styles.hint}>Start typing to search rooms, messages, and people.</p>
          )}

          {status === "loading" && <p className={styles.hint}>Searching…</p>}
          {status === "error" && <p className={styles.hint}>Something went wrong. Try again.</p>}
          {showNoResults && <p className={styles.hint}>No results for “{trimmedQuery}”.</p>}

          {!showEmptyDefault && status === "ready" && results && (
            <>
              {results.rooms.length > 0 && (
                <Section
                  label="Rooms"
                  rows={rows.filter((r) => r.kind === "room")}
                  allRows={rows}
                  activeIndex={activeIndex}
                  query={trimmedQuery}
                  onSelect={activate}
                />
              )}
              {results.messages.length > 0 && (
                <Section
                  label="Messages"
                  rows={rows.filter((r) => r.kind === "message")}
                  allRows={rows}
                  activeIndex={activeIndex}
                  query={trimmedQuery}
                  onSelect={activate}
                />
              )}
              {results.users.length > 0 && (
                <Section
                  label="People"
                  rows={rows.filter((r) => r.kind === "user")}
                  allRows={rows}
                  activeIndex={activeIndex}
                  query={trimmedQuery}
                  onSelect={activate}
                />
              )}
            </>
          )}
        </div>

        <div className={styles.footer}>
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>Enter</kbd> select</span>
          <span><kbd>Esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}

function Section({
  label,
  rows,
  allRows,
  activeIndex,
  query,
  onSelect,
}: {
  label: string;
  rows: Row[];
  allRows: Row[];
  activeIndex: number;
  query: string;
  onSelect: (row: Row) => void;
}) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionLabel}>{label}</div>
      {rows.map((row) => {
        const index = allRows.indexOf(row);
        return (
          <ResultRow key={row.key} row={row} index={index} active={index === activeIndex} query={query} onSelect={onSelect} />
        );
      })}
    </div>
  );
}

function ResultRow({
  row,
  index,
  active,
  query,
  onSelect,
}: {
  row: Row;
  index: number;
  active: boolean;
  query: string;
  onSelect: (row: Row) => void;
}) {
  return (
    <button
      type="button"
      id={`search-row-${index}`}
      data-row-index={index}
      role="option"
      aria-selected={active}
      className={[styles.row, active ? styles.rowActive : ""].join(" ")}
      onMouseEnter={() => {
        /* pure hover highlight only — activeIndex is keyboard-driven; a
           click below still activates regardless of hover state */
      }}
      onMouseDown={(e) => {
        e.preventDefault();
        onSelect(row);
      }}
    >
      {row.kind === "room" && row.room && (
        <>
          <span className={styles.roomMark} aria-hidden="true">#</span>
          <span className={styles.rowBody}>
            <span className={styles.rowTitle}>
              <Highlight text={row.room.name} query={query} />
            </span>
            {row.room.description && <span className={styles.rowSub}>{row.room.description}</span>}
          </span>
          {row.room.is_private && <span className={styles.tag}>Private</span>}
        </>
      )}
      {row.kind === "message" && row.message && (
        <>
          <Avatar user={row.message.user} size="sm" />
          <span className={styles.rowBody}>
            <span className={styles.rowTitle}>
              {row.message.user.username} <span className={styles.rowSub}>in #{row.message.room.name}</span>
            </span>
            <span className={styles.rowSnippet}>
              <Highlight text={row.message.body} query={query} />
            </span>
          </span>
          <span className={styles.rowTime}>{timeAgo(row.message.created)}</span>
        </>
      )}
      {row.kind === "user" && row.user && (
        <>
          <Avatar user={row.user} size="sm" />
          <span className={styles.rowBody}>
            <span className={styles.rowTitle}>
              <Highlight text={row.user.username} query={query} />
            </span>
            {row.user.bio && <span className={styles.rowSub}>{row.user.bio}</span>}
          </span>
          <span className={styles.tag}>Message</span>
        </>
      )}
    </button>
  );
}
