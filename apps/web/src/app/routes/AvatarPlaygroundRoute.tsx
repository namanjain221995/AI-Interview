import { Box } from "lucide-react";
import type { ComponentType } from "react";

import { FullPageMessage } from "@/components/layout/FullPageStates";

/**
 * The avatar playground is owned by features/avatar. It is resolved through
 * import.meta.glob so this route builds whether or not that module exists:
 * when present it becomes its own lazy chunk; when absent the route explains.
 */
const avatarModules = import.meta.glob<{ default: ComponentType }>("/src/features/avatar/AvatarPlayground.tsx");

function AvatarPlaygroundUnavailable() {
  return (
    <FullPageMessage
      icon={<Box />}
      title="Avatar playground isn't in this build"
      description="The avatar module ships the playground at features/avatar/AvatarPlayground. Rebuild once it is available."
    />
  );
}

export async function loadAvatarPlayground(): Promise<{ Component: ComponentType }> {
  const loader = avatarModules["/src/features/avatar/AvatarPlayground.tsx"];
  if (!loader) return { Component: AvatarPlaygroundUnavailable };
  const module = await loader();
  return { Component: module.default ?? AvatarPlaygroundUnavailable };
}
