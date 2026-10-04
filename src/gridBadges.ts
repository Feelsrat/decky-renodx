// Small HDR icons on the cover art tiles of the Steam library grid (and Home's recent games).
//
// Steam has no hook for its tiles, so this works on the page itself, like other badge
// plugins: watch the Big Picture window for tiles, read the AppID from each tile's
// artwork URL (".../assets/<appid>/library_600x900.jpg", or "/customimages/<appid>p.png"
// for custom art), and add a small absolutely positioned icon to the tile.
// If Steam changes its markup, the icons simply stop appearing; nothing else is touched.
import { findSP, libraryAssetImageClasses } from "@decky/ui";
import { api, type Badge } from "./backend";
import { BADGE_STYLES, badgesEnabled } from "./components/LibraryBadge";

const MARK = "data-decky-renodx";
const ART = /\/assets\/(\d+)\/(?!library_hero|logo)[^/?]+\.(?:jpg|png|webp)|\/customimages\/(\d+)p\.(?:jpg|png|webp)/i;
const known = new Map<string, Pick<Badge, "level" | "label">>();
const pending = new Set<string>();

let observer: MutationObserver | null = null;
let timer = 0;
let retry = 0;

function appidOf(img: HTMLImageElement): string {
  const match = ART.exec(img.currentSrc || img.src || "");
  return match ? match[1] || match[2] : "";
}

function titleOf(appid: string): string {
  try {
    return (window as any).appStore?.GetAppOverviewByAppID?.(Number(appid))?.display_name || "";
  } catch {
    return "";
  }
}

function tiles(doc: Document): { appid: string; box: HTMLElement }[] {
  const container = libraryAssetImageClasses?.Container;
  const found: { appid: string; box: HTMLElement }[] = [];
  for (const img of Array.from(doc.querySelectorAll<HTMLImageElement>("img"))) {
    const appid = appidOf(img);
    if (!appid) continue;
    // Only cover art inside Steam's tile component, not hero banners or screenshots.
    const box = container ? img.closest<HTMLElement>(`.${container}`) : img.parentElement;
    if (box && box.offsetWidth > 40 && box.offsetWidth < 700) found.push({ appid, box });
  }
  return found;
}

function draw(doc: Document) {
  for (const { appid, box } of tiles(doc)) {
    const badge = known.get(appid);
    const existing = box.querySelector<HTMLElement>(`[${MARK}]`);
    if (!badge || badge.level === "none") {
      existing?.remove();
      continue;
    }
    if (existing?.getAttribute(MARK) === `${appid}:${badge.level}`) continue;
    existing?.remove();
    const style = BADGE_STYLES[badge.level];
    const icon = doc.createElement("div");
    icon.setAttribute(MARK, `${appid}:${badge.level}`);
    icon.title = badge.label || "";
    icon.textContent = style.icon;
    Object.assign(icon.style, {
      position: "absolute", top: "6px", left: "6px", zIndex: "5", width: "22px", height: "22px",
      display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "50%",
      background: "rgba(14,20,27,0.85)", border: `1.5px solid ${style.border}`, color: style.border,
      fontSize: "12px", lineHeight: "1", pointerEvents: "none", boxShadow: "0 1px 3px rgba(0,0,0,0.6)",
    });
    if (getComputedStyle(box).position === "static") box.style.position = "relative";
    box.appendChild(icon);
  }
}

async function scan() {
  timer = 0;
  const win = findSP();
  if (!win?.document || !badgesEnabled()) return;
  const doc = win.document;
  const missing = [...new Set(tiles(doc).map((tile) => tile.appid))].filter((appid) => !known.has(appid) && !pending.has(appid));
  draw(doc);
  if (!missing.length) return;
  missing.forEach((appid) => pending.add(appid));
  try {
    const result = await api.libraryBadges(missing.map((appid) => ({ appid, title: titleOf(appid) })));
    if (result.status === "success") {
      for (const appid of missing) known.set(appid, result.badges[appid] || { level: "none" });
    }
  } catch {
    // backend busy or reloading: try these again on the next change
  } finally {
    missing.forEach((appid) => pending.delete(appid));
  }
  draw(doc);
}

function schedule() {
  if (!timer) timer = window.setTimeout(scan, 250);
}

/** Start watching the library. Safe to call again (e.g. after the setting is turned back on). */
export function startGridBadges() {
  if (observer) return;
  const win = findSP();
  if (!win?.document?.body) {
    // Big Picture isn't up yet when Decky loads the plugin at boot.
    retry = window.setTimeout(startGridBadges, 2000);
    return;
  }
  const Observer: typeof MutationObserver = (win as any).MutationObserver || MutationObserver;
  observer = new Observer(schedule);
  observer.observe(win.document.body, { childList: true, subtree: true });
  schedule();
}

export function stopGridBadges() {
  window.clearTimeout(retry);
  window.clearTimeout(timer);
  timer = 0;
  observer?.disconnect();
  observer = null;
  findSP()?.document?.querySelectorAll(`[${MARK}]`).forEach((node) => node.remove());
}

/** Forget cached results (after installing or removing HDR) and redraw. */
export function refreshGridBadges(appid?: string) {
  if (appid) known.delete(appid);
  else known.clear();
  if (observer) schedule();
}
