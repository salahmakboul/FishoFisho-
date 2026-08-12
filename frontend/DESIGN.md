# FishoFisho — Design System

Strictly monochrome. All hierarchy comes from contrast, size, weight, spacing,
and density — never color.

## Palette (`src/styles/tokens.css`)

7 steps between white and black:

`--white #fff → --gray-50 → --gray-100 → --gray-200 → --gray-300 → --gray-500 → --gray-700 → --gray-900 → --black #000`

- Surfaces: white / `gray-50` (hover) / `gray-100` (active/selected)
- Borders: `gray-100` (subtle) / `gray-200` (default) / `gray-900` (strong, e.g. inputs, focus)
- Text: `gray-900` (primary) / `gray-500` (secondary, ~4.6:1 on white)
- The rail is the one inverted surface (`gray-900` bg, white/gray-300 text) — it reads as a fixed anchor, like Slack/Discord's server rail, without introducing a second color.
- Errors/success are never color: error states use a dashed border + "!" mark + underlined text (see `EmptyState`, form errors); success is a checkmark-free plain "Saved." label plus button state, not a green flash.

## Type scale

`12 / 13 / 14(base) / 16 / 20 / 24 / 32` px, system font stack. Weight does most of the hierarchy work: 400 body, 600 emphasis, 700 headings/usernames.

## Spacing scale

4px base: `4 / 8 / 12 / 16 / 24 / 32 / 48` px (`--space-1`…`--space-7`).

## Motion

100–150ms opacity/transform only (button press scale, panel slide-in, popover fade). Nothing longer, nothing 3D, no particle/gimmick effects.

## Interactive states

Every interactive element (`Button`, room list item, notification item, composer) is designed for: default, hover, focus-visible (real 2px solid black ring, never suppressed), active/pressed (scale 0.97), selected (bold + filled background, room list / notifications), disabled (opacity 0.45), loading (inline spinner, label hidden not removed — layout doesn't jump), error (dashed border / underline, not color), empty (see below).

## Empty states

Every list has a real empty state with an explanation and next action, not a bare "No X yet": empty room ("Welcome to #room — say hello"), no rooms ("Create your first room"), no notifications ("You're all caught up"), profile loading, message-load failure with a Retry button.

## Spatial model

Global rail (60px, always visible: logo, current-user avatar) → room list (Sidebar, search + create) → active room (Header + Chat). Secondary actions (AI assistant, profile/settings) live behind two icons in the Header/rail that reveal a docked right-hand panel — they never compete with send/read/switch-room for attention. Own vs. others' messages are distinguished by alignment (own messages right-aligned, filled dark bubble) and label ("You" vs username), not color.

## Mock data flag

`src/lib/api.ts` exports `USE_MOCK` (`import.meta.env.VITE_USE_MOCK !== "false"`, so **on by default**). Every REST/WS function has both a mock branch (in-memory fixture, simulated latency, a small pub/sub bus so sending a message "broadcasts" back through the mock WebSocket the same way the real server would) and a real `fetch`/`WebSocket` branch behind the flag — switching to the live backend is `VITE_USE_MOCK=false npm run dev`, not a rewrite. Mock mode also simulates light peer activity (a teammate posting every ~45s, occasionally @-mentioning you) and an AI bot reply whenever a message contains `@FishoAI`, so the UI feels alive without a live backend.

## Judgment calls (flagged, not specified by the contract)

- **Room creation**: any signed-in user can create a room from the sidebar (`+`); no permission check exists server-side per the contract, so this assumes open creation rather than inventing a role system.
- **Leaving a room**: not implemented — the contract has no endpoint for it, so no UI affordance was added rather than guessing at one.
- **AI assistant**: no config UI beyond an explanatory panel, since the contract states it needs no special client protocol — it's just a bot user's messages, tagged with a small "AI" label.
- **Mention autocomplete**: implemented client-side against `GET /api/v1/users/` (typing `@` filters the directory) since the contract lists this endpoint explicitly for that purpose.
