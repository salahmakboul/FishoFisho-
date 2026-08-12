import { useState } from "react";
import { Avatar } from "./Avatar";
import { Button } from "./Button";
import styles from "./AIAssistantPanel.module.css";

const AI_USER = { id: 999, username: "FishoAI", avatar: null, is_bot: true };

/** `roomName`/`onSummarize` are optional so this still renders fine when
 * opened outside a room (e.g. no active room selected) — the "Summarize
 * this room" button just doesn't show in that case. `onSummarize` is wired
 * by Layout.tsx to `workspace.sendMessage("@fishoai summarize this room")`,
 * i.e. it goes through the exact same
 * MessageListCreateView._maybe_trigger_ai_reply -> "summarize" command path
 * as a member typing the magic phrase themselves — this button is just a
 * shortcut so people don't have to know it. */
export function AIAssistantPanel({
  onClose,
  roomName,
  onSummarize,
}: {
  onClose: () => void;
  roomName?: string | null;
  onSummarize?: () => Promise<void> | void;
}) {
  const [summarizing, setSummarizing] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function handleSummarize() {
    if (!onSummarize) return;
    setSummarizing(true);
    setFeedback(null);
    try {
      await onSummarize();
      setFeedback("Asked FishoAI to summarize — check the room for its reply.");
    } catch {
      setFeedback("Couldn't send that just now — try again.");
    } finally {
      setSummarizing(false);
    }
  }

  return (
    <div className={styles.panel} role="dialog" aria-label="AI assistant">
      <div className={styles.head}>
        <span className={styles.title}>AI Assistant</span>
        <button type="button" className={styles.closeBtn} aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <div className={styles.body}>
        <div className={styles.intro}>
          <Avatar user={AI_USER} size="lg" />
          <div className={styles.introText}>
            <strong>FishoAI</strong>
            <p>
              FishoAI posts in the room like any other member — there's nothing extra to set up. Mention it and
              it'll reply inline, tagged with a small "AI" label so it's always clear which messages are
              generated.
            </p>
          </div>
        </div>

        <div className={styles.section}>
          <p className={styles.sectionTitle}>Try it in any room</p>
          <code className={styles.example}>@FishoAI summarize this room</code>
          <code className={styles.example}>@FishoAI draft a reply for me</code>
        </div>

        {roomName && onSummarize && (
          <div className={styles.section}>
            <p className={styles.sectionTitle}>Quick action</p>
            <Button variant="secondary" loading={summarizing} onClick={handleSummarize}>
              Summarize #{roomName}
            </Button>
            {feedback && (
              <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>{feedback}</p>
            )}
          </div>
        )}

        <div className={styles.section}>
          <p className={styles.sectionTitle}>Where it can see</p>
          <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
            FishoAI only responds inside the room where it's mentioned — it doesn't read other rooms or private
            conversations.
          </p>
        </div>
      </div>
    </div>
  );
}
