import { useState, useEffect, useRef } from "react";

export type FullscreenTab = { title: string; content: string };

export const FullscreenLogModal = ({ title, content, tabs, closeModal }: { title: string; content?: string; tabs?: FullscreenTab[]; closeModal?: () => void }) => {
  const bWasPressed = useRef(false);
  const [activeTab, setActiveTab] = useState(0);
  const visibleTabs = tabs && tabs.length ? tabs : [{ title: "Log", content: content || "" }];
  const activeContent = visibleTabs[Math.min(activeTab, visibleTabs.length - 1)]?.content || "";

  useEffect(() => {
    const close = () => closeModal?.();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.key === "Backspace" || event.key.toLowerCase() === "b") {
        event.preventDefault();
        close();
      }
    };

    let frame = 0;
    const pollGamepad = () => {
      const pads = navigator.getGamepads?.() || [];
      const bPressed = pads.some((pad) => !!pad?.buttons?.[1]?.pressed);
      if (bPressed && !bWasPressed.current) {
        close();
      }
      bWasPressed.current = bPressed;
      frame = requestAnimationFrame(pollGamepad);
    };

    window.addEventListener("keydown", onKeyDown, true);
    frame = requestAnimationFrame(pollGamepad);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      cancelAnimationFrame(frame);
    };
  }, [closeModal]);

  return (
    <div style={{ 
      position: "fixed", 
      left: 0, 
      right: 0, 
      top: "48px", 
      bottom: 0, 
      background: "#101113", 
      color: "#d4d4d4", 
      zIndex: 999999, 
      display: "flex", 
      flexDirection: "column" 
    }}>
      <div style={{ 
        height: "52px", 
        display: "flex", 
        alignItems: "center", 
        justifyContent: "space-between", 
        padding: "0 16px", 
        borderBottom: "1px solid rgba(255,255,255,0.16)", 
        background: "#17191d", 
        boxSizing: "border-box" 
      }}>
        <div style={{ fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: "12px" }}>{title}</div>
        <button style={{ width: "36px", height: "36px", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.22)", background: "rgba(255,255,255,0.08)", color: "white", fontSize: "18px" }} onClick={closeModal}>x</button>
      </div>
      {visibleTabs.length > 1 && (
        <div style={{ display: "flex", gap: "8px", padding: "8px 16px", borderBottom: "1px solid rgba(255,255,255,0.12)", background: "#14161a" }}>
          {visibleTabs.map((tab, index) => (
            <button key={tab.title} onClick={() => setActiveTab(index)} style={{ padding: "6px 10px", borderRadius: "4px", border: "1px solid rgba(255,255,255,0.18)", background: activeTab === index ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.06)", color: "white" }}>{tab.title}</button>
          ))}
        </div>
      )}
      <pre style={{ flex: 1, minHeight: 0, margin: 0, padding: "14px 16px 72px", overflow: "auto", whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "12px", lineHeight: 1.35, fontFamily: "monospace", boxSizing: "border-box" }}>{activeContent}</pre>
    </div>
  );
};
