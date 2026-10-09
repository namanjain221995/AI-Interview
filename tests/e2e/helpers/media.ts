import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";

const AUDIO_DIR = path.resolve(__dirname, "../fixtures/audio");
const FAKE_MEDIA = path.resolve(__dirname, "../fixtures/fake-media.js");

export const clips = JSON.parse(fs.readFileSync(path.join(AUDIO_DIR, "clips.json"), "utf8")) as Record<
  string,
  { text: string; seconds: number }
>;

/** Install the controllable fake microphone/camera and serve audio clips. */
export async function installFakeMedia(page: Page): Promise<void> {
  await page.addInitScript({ path: FAKE_MEDIA });
  await page.route("**/__e2e/audio/*.wav", async (route) => {
    const name = path.basename(new URL(route.request().url()).pathname);
    const file = path.join(AUDIO_DIR, name);
    if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: "missing clip" });
    await route.fulfill({ status: 200, contentType: "audio/wav", body: fs.readFileSync(file) });
  });
}

/** Speak a pre-recorded candidate answer into the fake microphone and wait until it finishes. */
export async function say(page: Page, clip: keyof typeof clips | string): Promise<number> {
  return page.evaluate((url) => (window as unknown as { __e2eMedia: { say(u: string): Promise<number> } }).__e2eMedia.say(url), `/__e2e/audio/${clip}.wav`);
}
