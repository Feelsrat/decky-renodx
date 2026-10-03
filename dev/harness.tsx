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

async function main() {
  const plugin = (await import("../src/index")).default as any;
  createRoot(document.getElementById("app")!).render(
    <div className="layout">
      <div className="qam">
        <div className="qam-title">{plugin.titleView}</div>
        <div className="qam-body">{plugin.content}</div>
      </div>
      <LaunchOptionsPanel />
    </div>,
  );
}

main();
