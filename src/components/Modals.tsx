import { useEffect, useState } from "react";
import { DialogButton, DropdownItem, Focusable, ModalRoot, Navigation, PanelSectionRow } from "@decky/ui";
import { toaster } from "@decky/api";
import { api, type DownloadFile, type ManualDownload } from "../backend";

export interface LogTab {
  title: string;
  content: string;
}

export function TextModal({ title, tabs, closeModal }: { title: string; tabs: LogTab[]; closeModal?: () => void }) {
  const [active, setActive] = useState(0);
  const tab = tabs[Math.min(active, tabs.length - 1)];
  return (
    <ModalRoot closeModal={closeModal} bAllowFullSize>
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{title}</div>
      {tabs.length > 1 && (
        <Focusable style={{ display: "flex", gap: 8, marginBottom: 8 }} flow-children="horizontal">
          {tabs.map((item, index) => (
            <DialogButton key={item.title} style={{ minWidth: 0, flex: 1, opacity: index === active ? 1 : 0.6 }} onClick={() => setActive(index)}>
              {item.title}
            </DialogButton>
          ))}
        </Focusable>
      )}
      <Focusable
        style={{ maxHeight: "60vh", overflowY: "auto", background: "rgba(0,0,0,0.35)", borderRadius: 4, padding: 8 }}
        onActivate={() => undefined}
      >
        <pre style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 11, lineHeight: 1.35 }}>{tab?.content || "Empty."}</pre>
      </Focusable>
    </ModalRoot>
  );
}

function openLink(url: string) {
  try {
    Navigation.NavigateToExternalWeb(url);
  } catch {
    api.openUrl(url).catch(() => undefined);
  }
}

export function ImportModal({
  title,
  manual,
  onImport,
  closeModal,
}: {
  title: string;
  manual: ManualDownload | null;
  onImport: (file: string) => Promise<boolean>;
  closeModal?: () => void;
}) {
  const [files, setFiles] = useState<DownloadFile[]>([]);
  const [selected, setSelected] = useState("");
  const [working, setWorking] = useState(false);

  const scan = async () => {
    try {
      const result = await api.downloads();
      const found = result.files || [];
      setFiles(found);
      setSelected((current) => (found.some((file) => file.path === current) ? current : found[0]?.path || ""));
    } catch (error) {
      toaster.toast({ title: "Download scan failed", body: String(error) });
    }
  };

  useEffect(() => {
    scan();
  }, []);

  const search = `https://www.google.com/search?q=${encodeURIComponent(`${title} RenoDX`)}`;
  const step = (n: number, text: string) => (
    <div style={{ fontWeight: 700, margin: "12px 0 4px" }}>
      <span style={{ display: "inline-block", width: 20, height: 20, lineHeight: "20px", textAlign: "center", borderRadius: 10, background: "#1a9fff", color: "#fff", fontSize: 12, marginRight: 8 }}>{n}</span>
      {text}
    </div>
  );

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ fontSize: 18, fontWeight: 700 }}>Install a RenoDX mod for {title}</div>
      {manual?.message && <div style={{ fontSize: 13, opacity: 0.75, marginTop: 4 }}>{manual.message}</div>}

      {step(1, manual?.url ? "Open the mod page" : "Find the mod")}
      <DialogButton onClick={() => openLink(manual?.url || search)}>{manual?.url ? "Open mod page" : "Search the web for a RenoDX mod"}</DialogButton>

      {step(2, "Download it to ~/Downloads")}
      <div style={{ fontSize: 13, opacity: 0.75 }}>
        You want the .addon64 or .addon32 file, or a .zip/.7z that contains it. Steam's Game Mode browser can't save files, so for
        Nexus or Discord use Desktop Mode.
      </div>

      {step(3, "Import it")}
      <PanelSectionRow>
        {files.length ? (
          <DropdownItem
            label="Downloaded file"
            rgOptions={files.map((file) => ({ data: file.path, label: `${file.name} · ${new Date(file.modified * 1000).toLocaleDateString()}` }))}
            selectedOption={selected}
            onChange={(option) => setSelected(String(option.data))}
          />
        ) : (
          <div style={{ fontSize: 13, opacity: 0.6, padding: "8px 0" }}>Nothing in ~/Downloads yet.</div>
        )}
      </PanelSectionRow>
      <Focusable style={{ display: "flex", gap: 8 }} flow-children="horizontal">
        <DialogButton style={{ flex: 1, minWidth: 0 }} onClick={scan} disabled={working}>Look again</DialogButton>
        <DialogButton
          style={{ flex: 2, minWidth: 0 }}
          disabled={working || !selected}
          onClick={async () => {
            setWorking(true);
            const ok = await onImport(selected);
            setWorking(false);
            if (ok) closeModal?.();
          }}
        >
          {working ? "Installing…" : "Install selected file"}
        </DialogButton>
      </Focusable>
    </ModalRoot>
  );
}
