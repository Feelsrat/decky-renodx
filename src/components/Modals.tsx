import { useEffect, useState } from "react";
import { ButtonItem, DialogButton, DropdownItem, Focusable, ModalRoot, Navigation, PanelSectionRow } from "@decky/ui";
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

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>Import a RenoDX mod for {title}</div>
      <div style={{ fontSize: "0.85em", opacity: 0.8, marginBottom: 8 }}>
        {manual?.message || "Download the mod's .addon64/.addon32 file (or a .zip/.7z containing it) to ~/Downloads, then import it."}
        {" "}Game Mode's browser cannot save files; Nexus downloads need Desktop Mode.
      </div>
      {manual?.url && (
        <ButtonItem layout="below" onClick={() => openLink(manual.url)}>
          Open mod page
        </ButtonItem>
      )}
      {!manual?.url && (
        <ButtonItem layout="below" onClick={() => openLink(search)}>
          Search for a RenoDX mod
        </ButtonItem>
      )}
      <PanelSectionRow>
        {files.length ? (
          <DropdownItem
            label="Downloaded file"
            rgOptions={files.map((file) => ({ data: file.path, label: `${file.name} · ${new Date(file.modified * 1000).toLocaleDateString()}` }))}
            selectedOption={selected}
            onChange={(option) => setSelected(String(option.data))}
          />
        ) : (
          <div style={{ fontSize: "0.85em", opacity: 0.6, padding: "8px 0" }}>No addon or archive found in ~/Downloads yet.</div>
        )}
      </PanelSectionRow>
      <ButtonItem layout="below" onClick={scan} disabled={working}>
        Rescan downloads
      </ButtonItem>
      <ButtonItem
        layout="below"
        disabled={working || !selected}
        onClick={async () => {
          setWorking(true);
          const ok = await onImport(selected);
          setWorking(false);
          if (ok) closeModal?.();
        }}
      >
        {working ? "Importing…" : "Import selected file"}
      </ButtonItem>
    </ModalRoot>
  );
}
