import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { User } from "../types";
import { Avatar } from "./Avatar";
import { AiTag, CountBadge, MentionText } from "./MentionBadge";
import buttonStyles from "./Button.module.css";
import styles from "./Landing.module.css";

/**
 * Public marketing page shown to logged-out visitors in place of the
 * workspace shell (see App.tsx). CTAs switch the app into <Auth/> (a
 * client-side login/register form) via the callbacks below rather than
 * navigating to a separate Django-rendered page — auth is now handled
 * entirely by the /api/v1/auth/ endpoints the SPA calls directly.
 */

interface LandingProps {
  onShowLogin: () => void;
  onShowRegister: () => void;
  /** True once the visitor is actually signed in. The marketing page still
   * renders in that case (this is "/" — the workspace only lives at "/app",
   * see App.tsx) but every CTA becomes a single explicit "enter the app"
   * action instead of auto-navigating on login, matching how most SaaS
   * marketing sites keep the homepage as a real page you can land on and
   * choose to leave, rather than a page that's redirected out from under
   * you the instant you're authenticated. */
  authenticated: boolean;
  onEnterApp: () => void;
}

const cx = (...parts: Array<string | false | undefined>) => parts.filter(Boolean).join(" ");

/** Sets a CSS custom property inline. Cast is required because TS's
 * CSSProperties type doesn't model arbitrary `--*` keys. */
const cssVar = (name: string, value: string): CSSProperties =>
  ({ [name]: value } as CSSProperties);

/** Fake directory entries used only to render the hero's product preview. */
const heroUsers: User[] = [
  { id: -1, username: "maria", avatar: null },
  { id: -2, username: "FishoAI", avatar: null, is_bot: true },
];

/** Fades + slides a section in the first time it scrolls into view. Skips the
 * effect (renders visible immediately) for users who prefer reduced motion,
 * and degrades to always-visible if IntersectionObserver isn't available. */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.15 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, visible] as const;
}

function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  const [ref, visible] = useReveal<HTMLDivElement>();
  return (
    <div ref={ref} className={cx(styles.reveal, visible && styles.revealVisible, className)}>
      {children}
    </div>
  );
}

/** Same on-scroll trigger as Reveal, but clips the heading in from a hard
 * edge instead of fading — reserved for section headings, which carry more
 * visual weight than the surrounding copy. */
function RevealHeading({ children, className }: { children: ReactNode; className?: string }) {
  const [ref, visible] = useReveal<HTMLHeadingElement>();
  return (
    <h2 ref={ref} className={cx(styles.revealMask, visible && styles.revealMaskVisible, className)}>
      {children}
    </h2>
  );
}

/** Three tabs shown in the hero's miniature live demo. Room name in the mock
 * header, the active pill, and the sliding indicator all derive from the
 * same index so they can never disagree. */
const DEMO_CHANNELS = [
  { id: "general", label: "general" },
  { id: "design", label: "design" },
  { id: "engineering", label: "engineering", badge: 3 },
] as const;

/** Drives the hero's miniature live demo: a deterministic, run-once sequence
 * (never a loop — the only perpetual motion on the page is the Live dot)
 * that switches the active room tab, then lands two message rows a beat
 * apart, ending with the AI reply. Same sequence every load, so it reads as
 * calm product evidence rather than a spectacle. */
function useLiveDemo() {
  const [activeChannel, setActiveChannel] = useState(0);
  const [row1Visible, setRow1Visible] = useState(false);
  const [row2Visible, setRow2Visible] = useState(false);

  useEffect(() => {
    const timers = [
      window.setTimeout(() => setActiveChannel(1), 500),
      window.setTimeout(() => setRow1Visible(true), 950),
      window.setTimeout(() => setRow2Visible(true), 1650),
    ];
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, []);

  return { activeChannel, row1Visible, row2Visible };
}

/** Measures the active room pill and returns pixel geometry for the sliding
 * indicator behind it, re-measuring on tab change and viewport resize. */
function useTabIndicator(activeIndex: number) {
  const rowRef = useRef<HTMLDivElement>(null);
  const pillRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const [rect, setRect] = useState({ left: 0, top: 0, width: 0, height: 0 });

  useLayoutEffect(() => {
    const measure = () => {
      const pill = pillRefs.current[activeIndex];
      const row = rowRef.current;
      if (!pill || !row) return;
      setRect({
        left: pill.offsetLeft,
        top: pill.offsetTop,
        width: pill.offsetWidth,
        height: pill.offsetHeight,
      });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [activeIndex]);

  return { rowRef, pillRefs, rect };
}

export function Landing({ onShowLogin, onShowRegister, authenticated, onEnterApp }: LandingProps) {
  const [leaving, setLeaving] = useState(false);
  const demo = useLiveDemo();
  const tabIndicator = useTabIndicator(demo.activeChannel);

  /** "Open workspace" doesn't cut away instantly — a brief fade/lift plays
   * first so the swap to the app shell reads as a continuation rather than
   * an abrupt page replacement. Reduced-motion visitors skip straight
   * through. App.tsx's swap logic is untouched; this only delays the same
   * onEnterApp callback it already received. */
  function handleEnterApp() {
    if (leaving) return;
    const prefersReducedMotion =
      typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) {
      onEnterApp();
      return;
    }
    setLeaving(true);
    window.setTimeout(onEnterApp, 260);
  }

  return (
    <div className={cx(styles.page, leaving && styles.pageLeaving)}>
      <header className={styles.nav}>
        <div className={styles.navInner}>
          <div className={styles.brand}>
            <span className={styles.brandMark} aria-hidden="true">F</span>
            <span className={styles.brandName}>FishoFisho</span>
          </div>
          <nav className={styles.navLinks} aria-label="Primary">
            <a className={styles.navLink} href="#capabilities">Product</a>
            <a className={styles.navLink} href="#trust">Why FishoFisho</a>
          </nav>
          <div className={styles.navActions}>
            {authenticated ? (
              <button type="button" className={cx(buttonStyles.button, buttonStyles.primary)} onClick={handleEnterApp}>
                Open workspace
              </button>
            ) : (
              <>
                <button type="button" className={cx(buttonStyles.button, buttonStyles.ghost)} onClick={onShowLogin}>
                  Log in
                </button>
                <button type="button" className={cx(buttonStyles.button, buttonStyles.primary)} onClick={onShowRegister}>
                  Get started
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      <main>
        {/* ---------------------------------------------------------------- Hero */}
        <section className={styles.hero}>
          <div className={cx(styles.sectionInner, styles.heroInner)}>
            <div className={styles.heroCopy}>
              <p className={cx(styles.eyebrow, styles.heroReveal)} style={cssVar("--reveal-delay", "0ms")}>
                Team chat, built for focus
              </p>
              <h1 className={styles.h1}>
                <span className={styles.h1Line} style={cssVar("--reveal-delay", "90ms")}>
                  Where your team
                </span>
                <span className={styles.h1Line} style={cssVar("--reveal-delay", "180ms")}>
                  actually talks.
                </span>
              </h1>
              <p className={cx(styles.lead, styles.heroReveal)} style={cssVar("--reveal-delay", "280ms")}>
                Rooms for every project, messages and @mentions that land in real time, and an AI
                assistant that answers right inside the conversation — one calm workspace instead
                of five noisy tools.
              </p>
              <div className={cx(styles.heroActions, styles.heroReveal)} style={cssVar("--reveal-delay", "360ms")}>
                {authenticated ? (
                  <button
                    type="button"
                    className={cx(buttonStyles.button, buttonStyles.primary, styles.heroPrimary)}
                    onClick={handleEnterApp}
                  >
                    Go to your workspace
                    <span className={styles.ctaArrow} aria-hidden="true">→</span>
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className={cx(buttonStyles.button, buttonStyles.primary, styles.heroPrimary)}
                      onClick={onShowRegister}
                    >
                      Create your workspace
                      <span className={styles.ctaArrow} aria-hidden="true">→</span>
                    </button>
                    <button type="button" className={styles.heroSecondary} onClick={onShowLogin}>
                      Already have an account? Sign in
                    </button>
                  </>
                )}
              </div>
            </div>

            <div className={cx(styles.heroVisual, styles.heroVisualIn)} aria-hidden="true">
              <div className={styles.mockWindow}>
                <div className={styles.mockHead}>
                  <span className={styles.mockRoomName}>#{DEMO_CHANNELS[demo.activeChannel].label}</span>
                  <span className={styles.mockLive}>
                    <span className={styles.mockDot} />
                    Live
                  </span>
                </div>
                <div className={styles.mockRoomRow} ref={tabIndicator.rowRef}>
                  <span
                    className={styles.mockIndicator}
                    style={{
                      transform: `translate(${tabIndicator.rect.left}px, ${tabIndicator.rect.top}px)`,
                      width: tabIndicator.rect.width,
                      height: tabIndicator.rect.height,
                    }}
                  />
                  {DEMO_CHANNELS.map((channel, index) => (
                    <span
                      key={channel.id}
                      ref={(el) => {
                        tabIndicator.pillRefs.current[index] = el;
                      }}
                      className={cx(styles.mockPill, index === demo.activeChannel && styles.mockPillActive)}
                    >
                      # {channel.label}
                      {"badge" in channel && <CountBadge count={channel.badge} />}
                    </span>
                  ))}
                </div>
                <div className={styles.mockMessages}>
                  <div className={cx(styles.mockRow, demo.row1Visible && styles.mockRowVisible)}>
                    <Avatar user={heroUsers[0]} size="sm" />
                    <div className={styles.mockBubbleCol}>
                      <div className={styles.mockMeta}>
                        <span className={styles.mockName}>maria</span>
                        <span className={styles.mockTime}>9:41</span>
                      </div>
                      <p className={styles.mockBubble}>
                        <MentionText text="@FishoAI can you summarize the thread above?" />
                      </p>
                    </div>
                  </div>
                  <div className={cx(styles.mockRow, demo.row2Visible && styles.mockRowVisible)}>
                    <Avatar user={heroUsers[1]} size="sm" />
                    <div className={styles.mockBubbleCol}>
                      <div className={styles.mockMeta}>
                        <span className={styles.mockName}>FishoAI</span>
                        <AiTag />
                        <span className={styles.mockTime}>9:41</span>
                      </div>
                      <p className={styles.mockBubble}>
                        Three open questions, no blockers — full summary in the thread.
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------- Capabilities */}
        <section id="capabilities" className={styles.capabilities}>
          <Reveal className={styles.sectionInner}>
            <RevealHeading className={styles.h2}>
              Everything a fast-moving team needs, nothing it doesn&apos;t
            </RevealHeading>
            <div className={styles.capGrid}>
              <article className={styles.capCard}>
                <span className={styles.capMark} aria-hidden="true">#</span>
                <h3 className={styles.capTitle}>Where conversations live</h3>
                <p className={styles.capBody}>
                  Every conversation lives in a room, and rooms are grouped by topic — so it&apos;s
                  always obvious where a discussion belongs, instead of one long feed.
                </p>
              </article>
              <article className={styles.capCard}>
                <span className={styles.capMark} aria-hidden="true">@</span>
                <h3 className={styles.capTitle}>How real-time works</h3>
                <p className={styles.capBody}>
                  One live connection keeps every room current — messages appear the instant
                  they&apos;re sent, and mentioning a teammate notifies them immediately, no
                  refreshing.
                </p>
              </article>
              <article className={styles.capCard}>
                <span className={cx(styles.capMark, styles.capMarkText)} aria-hidden="true">AI</span>
                <h3 className={styles.capTitle}>Why the AI is useful</h3>
                <p className={styles.capBody}>
                  Mention @FishoAI in any room to ask a question or get a quick summary — it
                  replies right inside the conversation, so context never leaves the room.
                </p>
              </article>
            </div>
          </Reveal>
        </section>

        {/* ----------------------------------------------------------------- Trust */}
        <section id="trust" className={styles.trust}>
          <Reveal className={styles.sectionInner}>
            <p className={styles.trustLabel}>What&apos;s inside</p>
            <div className={styles.statRow}>
              <div className={styles.stat}>
                <span className={styles.statNum}>Real-time</span>
                <span className={styles.statCaption}>message delivery over WebSockets</span>
              </div>
              <div className={styles.stat}>
                <span className={styles.statNum}>One workspace</span>
                <span className={styles.statCaption}>rooms, mentions, and AI in a single place</span>
              </div>
              <div className={styles.stat}>
                <span className={styles.statNum}>Always on</span>
                <span className={styles.statCaption}>notified the moment you&apos;re mentioned</span>
              </div>
            </div>
            <p className={styles.trustNote}>
              Placeholder — swap in customer logos or usage stats here at launch.
            </p>
          </Reveal>
        </section>

        {/* --------------------------------------------------------------- Closing */}
        <section className={styles.closing}>
          <Reveal className={styles.sectionInner}>
            <RevealHeading className={styles.closingTitle}>Bring your team into one place.</RevealHeading>
            <p className={styles.closingBody}>
              Set up a workspace in a couple of minutes and give your team one place to talk,
              instead of five.
            </p>
            <div className={styles.closingActions}>
              {authenticated ? (
                <button
                  type="button"
                  className={cx(buttonStyles.button, buttonStyles.secondary, styles.closingPrimary)}
                  onClick={handleEnterApp}
                >
                  Open your workspace
                  <span className={styles.ctaArrow} aria-hidden="true">→</span>
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className={cx(buttonStyles.button, buttonStyles.secondary, styles.closingPrimary)}
                    onClick={onShowRegister}
                  >
                    Create your workspace
                    <span className={styles.ctaArrow} aria-hidden="true">→</span>
                  </button>
                  <button type="button" className={styles.closingSecondary} onClick={onShowLogin}>
                    Sign in instead
                  </button>
                </>
              )}
            </div>
          </Reveal>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={cx(styles.sectionInner, styles.footerInner)}>
          <span className={styles.footerBrand}>© {new Date().getFullYear()} FishoFisho</span>
          <div className={styles.footerLinks}>
            {authenticated ? (
              <button type="button" className={styles.footerLink} onClick={handleEnterApp}>Open workspace</button>
            ) : (
              <>
                <button type="button" className={styles.footerLink} onClick={onShowLogin}>Log in</button>
                <button type="button" className={styles.footerLink} onClick={onShowRegister}>Create account</button>
              </>
            )}
          </div>
        </div>
      </footer>
    </div>
  );
}
