export function stripHdrLaunchTokens(options: string): string {
  return (options || "")
    .replace(/\bPROTON_ENABLE_HDR=\S+\s*/g, "")
    .replace(/\bDXVK_HDR=\S+\s*/g, "")
    .replace(/\bENABLE_HDR_WSI=\S+\s*/g, "")
    .replace(/\bENABLE_GAMESCOPE_WSI=\S+\s*/g, "")
    .replace(/\bPROTON_LOG=\S+\s*/g, "")
    // Special K delayed/global injection wrapper and its compat-path prefix.
    .replace(/\bSTEAM_COMPAT_DATA_PATH="[^"]*"\s*/g, "")
    .replace(/\bSTEAM_COMPAT_DATA_PATH=\S+\s*/g, "")
    .replace(/\bbash\s+"[^"]*specialk-delayed-launch\.sh"(?:\s+"[^"]*")*\s*/g, "")
    .replace(/\bWINEDLLOVERRIDES="[^"]*(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)[^"]*"\s*/g, "")
    .replace(/\bWINEDLLOVERRIDES=[^\s]*?(?:d3dcompiler_47|dxgi|d3d11|d3d12|d3d9|d3d8|ddraw|dinput8|opengl32)[^\s]*\s*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function mergeHdrLaunchOptions(existing: string, hdrOptions: string): string {
  const cleanExisting = stripHdrLaunchTokens(existing);
  const hdrPrefix = (hdrOptions || "").replace(/\s*%command%\s*/g, " ").replace(/\s+/g, " ").trim();
  
  if (!hdrPrefix) {
    return cleanExisting;
  }
  
  if (!cleanExisting) {
    return `${hdrPrefix} %command%`.trim();
  }
  
  if (cleanExisting.includes("%command%")) {
    return cleanExisting.replace("%command%", `${hdrPrefix} %command%`).replace(/\s+/g, " ").trim();
  }
  
  return `${hdrPrefix} ${cleanExisting}`.replace(/\s+/g, " ").trim();
}

export function hdrLaunchOptionsForDll(dll: string): string {
  const normalized = (dll || "dxgi").toLowerCase();
  if (normalized === "opengl32") {
    return "PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 %command%";
  }
  const compiler = normalized === "opengl32" ? "" : "d3dcompiler_47=n;";
  return `PROTON_LOG=1 PROTON_ENABLE_HDR=1 DXVK_HDR=1 ENABLE_HDR_WSI=1 ENABLE_GAMESCOPE_WSI=1 WINEDLLOVERRIDES="${compiler}${normalized}=n,b" %command%`;
}
