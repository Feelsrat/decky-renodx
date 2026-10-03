// Browser stand-ins for the @decky/ui components the plugin uses, styled roughly like
// the Steam Deck Quick Access Menu. Only for the local dev harness (pnpm dev).
import { cloneElement, createContext, isValidElement, useContext, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

const C = {
  panel: "#0e141b",
  row: "#1a1f27",
  rowHover: "#23262e",
  text: "#dcdedf",
  dim: "#8b929a",
  accent: "#1a9fff",
  sep: "rgba(255,255,255,0.08)",
  button: "#3d4450",
  buttonHover: "#5e6573",
};

export const staticClasses = { Title: "dk-title", PanelSectionTitle: "dk-section-title", Text: "dk-text" };

export function PanelSection({ title, children }: { title?: string; children?: ReactNode }) {
  return (
    <div style={{ padding: "8px 0" }}>
      {title && <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", color: C.dim, padding: "4px 16px" }}>{title}</div>}
      <div>{children}</div>
    </div>
  );
}

export function PanelSectionRow({ children }: { children?: ReactNode }) {
  return <div style={{ padding: "4px 16px" }}>{children}</div>;
}

function Description({ children }: { children?: ReactNode }) {
  return children ? <div style={{ fontSize: 12, color: C.dim, marginTop: 4, lineHeight: 1.3 }}>{children}</div> : null;
}

export function DialogButton({ children, onClick, disabled, style }: { children?: ReactNode; onClick?: () => void; disabled?: boolean; style?: CSSProperties }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: "100%", minHeight: 36, padding: "6px 12px", border: "none", borderRadius: 2, fontSize: 14,
        background: disabled ? "#2a2f38" : hover ? C.buttonHover : C.button, color: disabled ? "#6b717a" : C.text,
        cursor: disabled ? "default" : "pointer", fontFamily: "inherit", ...style,
      }}
    >
      {children}
    </button>
  );
}

export function ButtonItem({ children, onClick, disabled, description, label, layout }: { children?: ReactNode; onClick?: () => void; disabled?: boolean; description?: ReactNode; label?: ReactNode; layout?: string; bottomSeparator?: string }) {
  return (
    <div style={{ padding: "6px 0", borderBottom: `1px solid ${C.sep}` }}>
      {label && layout !== "below" && <div style={{ fontSize: 14, marginBottom: 6 }}>{label}</div>}
      <DialogButton onClick={onClick} disabled={disabled}>{children}</DialogButton>
      <Description>{description}</Description>
    </div>
  );
}

export function Field({ label, description, children }: { label?: ReactNode; description?: ReactNode; children?: ReactNode; focusable?: boolean; bottomSeparator?: string; icon?: ReactNode; inlineWrap?: string }) {
  return (
    <div style={{ padding: "8px 0", borderBottom: `1px solid ${C.sep}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <div style={{ fontSize: 14 }}>{label}</div>
        <div style={{ fontSize: 14 }}>{children}</div>
      </div>
      <Description>{description}</Description>
    </div>
  );
}

export function ToggleField({ label, description, checked, onChange, disabled }: { label?: ReactNode; description?: ReactNode; checked?: boolean; onChange?: (value: boolean) => void; disabled?: boolean; bottomSeparator?: string }) {
  return (
    <Field label={label} description={description}>
      <div
        onClick={() => !disabled && onChange?.(!checked)}
        style={{ width: 38, height: 20, borderRadius: 10, background: checked ? C.accent : "#4b525c", position: "relative", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1 }}
      >
        <div style={{ position: "absolute", top: 2, left: checked ? 20 : 2, width: 16, height: 16, borderRadius: 8, background: "white", transition: "left 0.1s" }} />
      </div>
    </Field>
  );
}

interface Option { data: any; label: ReactNode }

export function DropdownItem({ label, description, rgOptions, selectedOption, onChange, strDefaultLabel, disabled, onMenuWillOpen }: { label?: ReactNode; description?: ReactNode; rgOptions: Option[]; selectedOption?: any; onChange?: (option: Option) => void; strDefaultLabel?: string; disabled?: boolean; onMenuWillOpen?: (show: () => void) => void; bottomSeparator?: string; menuLabel?: string }) {
  const index = rgOptions.findIndex((option) => option.data === selectedOption);
  return (
    <div style={{ padding: "6px 0", borderBottom: `1px solid ${C.sep}` }}>
      {label && <div style={{ fontSize: 14, marginBottom: 6 }}>{label}</div>}
      <select
        disabled={disabled}
        value={index}
        onMouseDown={() => onMenuWillOpen?.(() => undefined)}
        onChange={(event) => onChange?.(rgOptions[Number(event.target.value)])}
        style={{ width: "100%", height: 36, background: C.button, color: C.text, border: "none", borderRadius: 2, padding: "0 8px", fontSize: 14, fontFamily: "inherit" }}
      >
        {index < 0 && <option value={-1}>{strDefaultLabel || "Select…"}</option>}
        {rgOptions.map((option, i) => <option key={i} value={i}>{typeof option.label === "string" ? option.label : String(option.data)}</option>)}
      </select>
      <Description>{description}</Description>
    </div>
  );
}

export function Focusable({ children, style, onActivate, ...rest }: { children?: ReactNode; style?: CSSProperties; onActivate?: () => void; [key: string]: any }) {
  void rest;
  return <div style={style} onClick={onActivate}>{children}</div>;
}

export function Spinner({ style }: { style?: CSSProperties }) {
  return <div style={{ width: 16, height: 16, border: "2px solid #ffffff33", borderTopColor: "white", borderRadius: "50%", animation: "dk-spin 0.8s linear infinite", ...style }} />;
}
export const SteamSpinner = Spinner;

// ---------------------------------------------------------------- modals

const ModalContext = createContext<() => void>(() => undefined);

export function ModalRoot({ children, closeModal, onCancel }: { children?: ReactNode; closeModal?: () => void; onCancel?: () => void; bAllowFullSize?: boolean }) {
  const close = useContext(ModalContext);
  const done = () => { onCancel?.(); (closeModal || close)(); };
  return (
    <div onClick={done} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
      <div onClick={(event) => event.stopPropagation()} style={{ width: 560, maxWidth: "90vw", maxHeight: "85vh", overflowY: "auto", background: "#23262e", borderRadius: 4, padding: 20, color: C.text, boxShadow: "0 8px 40px #000" }}>
        {children}
        <div style={{ marginTop: 12 }}><DialogButton onClick={done}>Close (B)</DialogButton></div>
      </div>
    </div>
  );
}

export function ConfirmModal({ strTitle, strDescription, strOKButtonText, onOK, closeModal }: { strTitle?: string; strDescription?: ReactNode; strOKButtonText?: string; onOK?: () => void; closeModal?: () => void }) {
  const close = useContext(ModalContext);
  const done = closeModal || close;
  return (
    <ModalRoot closeModal={done}>
      <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>{strTitle}</div>
      <div style={{ color: C.dim, marginBottom: 16 }}>{strDescription}</div>
      <DialogButton onClick={() => { onOK?.(); done(); }} style={{ background: C.accent }}>{strOKButtonText || "Confirm"}</DialogButton>
    </ModalRoot>
  );
}

export function showModal(modal: ReactNode) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const close = () => { root.unmount(); host.remove(); };
  // Like Decky, the modal element receives closeModal as a prop.
  const element = isValidElement(modal) ? cloneElement(modal as any, { closeModal: close }) : modal;
  root.render(<ModalContext.Provider value={close}>{element}</ModalContext.Provider>);
  return { Close: close, Update: () => undefined };
}

// ---------------------------------------------------------------- navigation / router

export const Navigation = {
  NavigateToExternalWeb(url: string) {
    window.open(url, "_blank");
  },
  Navigate() {},
  CloseSideMenus() {},
};

export const Router = {
  get MainRunningApp() {
    const appid = new URLSearchParams(location.search).get("running");
    return appid ? { appid: Number(appid), display_name: `App ${appid}` } : undefined;
  },
};

export function useQuickAccessVisible() {
  const [visible] = useState(true);
  useEffect(() => undefined, []);
  return visible;
}
