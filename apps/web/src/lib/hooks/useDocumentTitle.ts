import { useEffect } from "react";

import { PRODUCT_NAME } from "@/lib/brand";

export const APP_NAME = PRODUCT_NAME;

/** Sets `document.title` to "<title> | Swayam" while mounted. */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    if (typeof document === "undefined") return;
    const previous = document.title;
    document.title = title ? `${title} | ${APP_NAME}` : APP_NAME;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
