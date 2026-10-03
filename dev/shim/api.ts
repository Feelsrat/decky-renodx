// Browser stand-in for @decky/api: callables hit the dev server, toasts render on the page.
export function callable<Args extends any[], Result>(name: string): (...args: Args) => Promise<Result> {
  return async (...args: Args) => {
    const response = await fetch(`/rpc/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
    if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
    return (await response.json()) as Result;
  };
}

export const toaster = {
  toast({ title, body, duration = 5000 }: { title: string; body?: string; duration?: number }) {
    const host = document.getElementById("toasts");
    if (!host) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.innerHTML = `<b></b><div></div>`;
    (toast.querySelector("b") as HTMLElement).textContent = title;
    (toast.querySelector("div") as HTMLElement).textContent = body || "";
    host.appendChild(toast);
    window.setTimeout(() => toast.remove(), duration);
  },
};

export function definePlugin<T>(factory: () => T): T {
  return factory();
}

export function routerHook() {}
