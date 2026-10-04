// Adds the HDR badge to each game's library page, the way ProtonDB Badges does it:
// patch the /library/app/:appid route and insert our element into the page container.
// Steam's page structure changes between client versions; when it isn't found, nothing is added.
import type { ReactElement } from "react";
import { afterPatch, appDetailsClasses, createReactTreePatcher, findInReactTree } from "@decky/ui";
import { routerHook } from "@decky/api";
import { LibraryBadge, badgesEnabled } from "./components/LibraryBadge";

const ROUTE = "/library/app/:appid";

export function patchLibrary() {
  return routerHook.addPatch(ROUTE, (tree: any) => {
    try {
      const routeProps = findInReactTree(tree, (node: any) => node?.renderFunc);
      if (!routeProps) return tree;
      const handler = createReactTreePatcher(
        [(node: any) => findInReactTree(node, (x: any) => x?.props?.children?.props?.overview)?.props?.children],
        (_: unknown, ret?: ReactElement) => {
          try {
            const overview = findInReactTree(ret, (x: any) => x?.props?.overview?.appid)?.props?.overview;
            const container = findInReactTree(
              ret,
              (x: any) => Array.isArray(x?.props?.children) && x?.props?.className?.includes(appDetailsClasses.InnerContainer),
            );
            const appid = overview?.appid ?? routeAppid();
            if (!badgesEnabled() || !appid || typeof container !== "object" || container.props.children.some((child: any) => child?.key === "decky-renodx-badge")) {
              return ret;
            }
            container.props.children.splice(1, 0, <LibraryBadge key="decky-renodx-badge" appid={String(appid)} />);
          } catch (error) {
            console.error("Decky RenoDX: library badge patch failed", error);
          }
          return ret;
        },
      );
      afterPatch(routeProps, "renderFunc", handler);
    } catch (error) {
      console.error("Decky RenoDX: library route patch failed", error);
    }
    return tree;
  });
}

export function unpatchLibrary(patch: ReturnType<typeof patchLibrary>) {
  routerHook.removePatch(ROUTE, patch);
}

function routeAppid(): string {
  const match = /\/library\/app\/(\d+)/.exec(window.location?.pathname || "");
  return match ? match[1] : "";
}
