import type { User } from "../types";
import styles from "./Avatar.module.css";

function initials(username: string): string {
  return username.slice(0, 2).toUpperCase();
}

export function Avatar({ user, size = "md" }: { user: User; size?: "sm" | "md" | "lg" }) {
  return (
    <span
      className={[styles.avatar, styles[size], user.is_bot ? styles.bot : ""].join(" ")}
      aria-hidden="true"
    >
      {user.avatar ? <img src={user.avatar} alt="" /> : initials(user.username)}
    </span>
  );
}
