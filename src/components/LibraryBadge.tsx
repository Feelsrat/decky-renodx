import { type RefObject, useEffect, useRef, useState } from "react";
import { Focusable, Navigation, QuickAccessTab, appDetailsClasses, appDetailsHeaderClasses } from "@decky/ui";
import { api, type Badge } from "../backend";

export const BADGES_SETTING = "decky-renodx:library-badges";
/** Ask the panel to show a game (it listens for this when the Quick Access Menu opens). */
export const SELECT_EVENT = "decky-renodx:select";

export function badgesEnabled(): boolean {
  try {
    return localStorage.getItem(BADGES_SETTING) !== "off";
  } catch {
    return true;
  }
}

export const BADGE_STYLES: Record<Badge["level"], { color: string; border: string; icon: string }> = {
  on: { color: "#2ecc71", border: "#2ecc71", icon: "●" },
  renodx: { color: "#fff", border: "#8e7cff", icon: "★" },
  native: { color: "#fff", border: "#3cc4d6", icon: "◆" },
  engine: { color: "#f5c35b", border: "#f5c35b", icon: "◇" },
  none: { color: "", border: "", icon: "" },
};

// Session cache: badges are cheap to recompute, but the page re-renders often.
const cache = new Map<string, Badge>();

/** Drop a game's cached badge after HDR is installed or removed. */
export function forgetBadge(appid: string) {
  cache.delete(appid);
}

function titleOf(appid: string): string {
  try {
    return (window as any).appStore?.GetAppOverviewByAppID?.(Number(appid))?.display_name || "";
  } catch {
    return "";
  }
}

/** The library page header switches to a "fullscreen" layout while scrolling; hide the badge then. */
function useHiddenInFullscreen(ref: RefObject<HTMLDivElement | null>) {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const parent = ref.current?.parentElement;
    const header = parent && Array.from(parent.children).find((child) => child.className.includes(appDetailsClasses?.Header));
    const capsule = header && Array.from(header.children).find((child) => child.className.includes(appDetailsHeaderClasses?.TopCapsule));
    if (!capsule) return;
    const classes = appDetailsHeaderClasses;
    const observer = new MutationObserver(() => {
      const name = capsule.className;
      const entering = [classes.FullscreenEnterStart, classes.FullscreenEnterActive, classes.FullscreenEnterDone, classes.FullscreenExitStart, classes.FullscreenExitActive]
        .some((cls) => cls && name.includes(cls));
      setHidden(entering && !(classes.FullscreenExitDone && name.includes(classes.FullscreenExitDone)));
    });
    observer.observe(capsule, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return hidden;
}

export function LibraryBadge({ appid, inline }: { appid: string; inline?: boolean }) {
  const [badge, setBadge] = useState<Badge | undefined>(cache.get(appid));
  const ref = useRef<HTMLDivElement>(null);
  const hidden = useHiddenInFullscreen(ref);

  useEffect(() => {
    let current = true;
    api.libraryBadge(appid, titleOf(appid))
      .then((result) => {
        if (result.status !== "success") return;
        cache.set(appid, result);
        if (current) setBadge(result);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [appid]);

  const style = badge && BADGE_STYLES[badge.level];
  const open = () => {
    window.dispatchEvent(new CustomEvent(SELECT_EVENT, { detail: appid }));
    Navigation.OpenQuickAccessMenu(QuickAccessTab.Decky);
  };
  return (
    <div ref={ref} style={inline ? undefined : { position: "absolute", top: 60, right: 20, zIndex: 10 }}>
      {badge && style && badge.level !== "none" && !hidden && (
        <Focusable
          className="decky-renodx-badge"
          onActivate={open}
          onClick={open}
          style={{
            display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 14,
            background: "rgba(14,20,27,0.82)", border: `1px solid ${style.border}`, color: style.color,
            fontSize: 13, fontWeight: 600, cursor: "pointer", backdropFilter: "blur(6px)",
          }}
        >
          <style>{".decky-renodx-badge.gpfocus{outline:2px solid #fff;outline-offset:2px}"}</style>
          <span style={{ color: style.border }}>{style.icon}</span>
          <span>{badge.label}</span>
        </Focusable>
      )}
    </div>
  );
}
