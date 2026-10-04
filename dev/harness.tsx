// Local stand-in for the Steam Deck Quick Access Menu: renders the real plugin UI,
// fakes the SteamClient launch-option API, and talks to dev/server.py.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

const STORE = "dk-launch-options";
const listeners = new Set<() => void>();
const load = (): Record<string, string> => JSON.parse(localStorage.getItem(STORE) || "{}");
const save = (value: Record<string, string>) => {
  localStorage.setItem(STORE, JSON.stringify(value));
  listeners.forEach((listener) => listener());
};
const setOptions = (appid: number, options: string) => save({ ...load(), [appid]: options });

(window as any).SteamClient = {
  Apps: {
    SetAppLaunchOptions: setOptions,
    SetShortcutLaunchOptions: setOptions,
    RegisterForAppDetails(appid: number, callback: (data: { strLaunchOptions: string }) => void) {
      window.setTimeout(() => callback({ strLaunchOptions: load()[appid] || "" }), 50);
      return { unregister() {} };
    },
  },
};
(window as any).appDetailsStore = { GetAppDetails: (appid: number) => ({ strLaunchOptions: load()[appid] || "" }) };

function LaunchOptionsPanel() {
  const [options, setState] = useState(load());
  useEffect(() => {
    const listener = () => setState(load());
    listeners.add(listener);
    return () => void listeners.delete(listener);
  }, []);
  return (
    <div className="side">
      <h3>Steam launch options (fake)</h3>
      {Object.keys(options).length === 0 && <div className="dim">None set yet.</div>}
      {Object.entries(options).map(([appid, value]) => (
        <div key={appid} className="opt">
          <b>{appid}</b>
          <input value={value} onChange={(event) => setOptions(Number(appid), event.target.value)} />
        </div>
      ))}
      <button onClick={() => save({})}>Clear all</button>
      <p className="dim">Add <code>?running=APPID</code> to the URL to simulate a running game.</p>
    </div>
  );
}

/** What src/library.tsx adds to each game's library page, for every game in the fake library. */
function LibraryPreview() {
  const [badge, setBadge] = useState<any>(null);
  useEffect(() => {
    import("../src/components/LibraryBadge").then((module) => setBadge(() => module.LibraryBadge));
  }, []);
  const appids = ["1000", "2000", "3000", "4000", "7000", "8000", "9000"];
  const Badge = badge;
  return (
    <div className="side">
      <h3>Library page badges</h3>
      {Badge && appids.map((appid) => (
        <div key={appid} style={{ display: "flex", alignItems: "center", gap: 12, margin: "6px 0" }}>
          <code style={{ width: 48 }}>{appid}</code>
          <Badge appid={appid} inline />
        </div>
      ))}
      <p className="dim">9000 is an uninstalled game that PCGamingWiki says uses Unreal.</p>
      <h3>Library grid (fake tiles)</h3>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {appids.map((appid) => (
          <div key={appid} className="dk-capsule" style={{ position: "relative", width: 90, height: 135, background: "#2a3a4c", borderRadius: 4, overflow: "hidden" }}>
            <img src={`/assets/${appid}/library_600x900.jpg`} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", color: "transparent" }} />
            <div style={{ position: "absolute", bottom: 4, left: 6, fontSize: 11, color: "#9ab" }}>{appid}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

async function main() {
  const plugin = (await import("../src/index")).default as any;
  createRoot(document.getElementById("app")!).render(
    <div className="layout">
      <div className="qam">
        <div className="qam-title">{plugin.titleView}</div>
        <div className="qam-body">{plugin.content}</div>
      </div>
      <div>
        <LaunchOptionsPanel />
        <LibraryPreview />
      </div>
    </div>,
  );
}

main();
