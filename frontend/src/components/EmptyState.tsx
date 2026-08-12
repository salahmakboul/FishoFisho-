import type { ReactNode } from "react";
import styles from "./EmptyState.module.css";

/**
 * Shared shell for empty / error states across the app. Every usage supplies
 * a concrete title + explanation + next action — never a bare "nothing here".
 */
export function EmptyState({
  icon = "•",
  title,
  body,
  action,
  variant = "empty",
}: {
  icon?: string;
  title: string;
  body: string;
  action?: ReactNode;
  variant?: "empty" | "error";
}) {
  return (
    <div className={[styles.wrap, variant === "error" ? styles.error : ""].join(" ")}>
      <span className={styles.mark} aria-hidden="true">
        {icon}
      </span>
      <p className={styles.title}>{title}</p>
      <p className={styles.body}>{body}</p>
      {action && <div className={styles.actions}>{action}</div>}
    </div>
  );
}
