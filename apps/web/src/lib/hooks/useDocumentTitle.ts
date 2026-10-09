import { useEffect } from "react";

export const APP_NAME = "Interview Studio";

/** Sets `document.title` to "<title> | Interview Studio" while mounted. */
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
