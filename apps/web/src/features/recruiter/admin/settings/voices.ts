import type { VoiceOption } from "../types";

/**
 * Kokoro voices served by the TTS service (`GET /v1/voices`, 54 voices).
 * Used when the API does not expose its own voice list.
 */
const RAW: Array<[string, string, "female" | "male"]> = [
  ["af_alloy", "American English", "female"],
  ["af_aoede", "American English", "female"],
  ["af_bella", "American English", "female"],
  ["af_heart", "American English", "female"],
  ["af_jessica", "American English", "female"],
  ["af_kore", "American English", "female"],
  ["af_nicole", "American English", "female"],
  ["af_nova", "American English", "female"],
  ["af_river", "American English", "female"],
  ["af_sarah", "American English", "female"],
  ["af_sky", "American English", "female"],
  ["am_adam", "American English", "male"],
  ["am_echo", "American English", "male"],
  ["am_eric", "American English", "male"],
  ["am_fenrir", "American English", "male"],
  ["am_liam", "American English", "male"],
  ["am_michael", "American English", "male"],
  ["am_onyx", "American English", "male"],
  ["am_puck", "American English", "male"],
  ["am_santa", "American English", "male"],
  ["bf_alice", "British English", "female"],
  ["bf_emma", "British English", "female"],
  ["bf_isabella", "British English", "female"],
  ["bf_lily", "British English", "female"],
  ["bm_daniel", "British English", "male"],
  ["bm_fable", "British English", "male"],
  ["bm_george", "British English", "male"],
  ["bm_lewis", "British English", "male"],
  ["ef_dora", "Spanish", "female"],
  ["em_alex", "Spanish", "male"],
  ["em_santa", "Spanish", "male"],
  ["ff_siwis", "French", "female"],
  ["hf_alpha", "Hindi", "female"],
  ["hf_beta", "Hindi", "female"],
  ["hm_omega", "Hindi", "male"],
  ["hm_psi", "Hindi", "male"],
  ["if_sara", "Italian", "female"],
  ["im_nicola", "Italian", "male"],
  ["jf_alpha", "Japanese", "female"],
  ["jf_gongitsune", "Japanese", "female"],
  ["jf_nezumi", "Japanese", "female"],
  ["jf_tebukuro", "Japanese", "female"],
  ["jm_kumo", "Japanese", "male"],
  ["pf_dora", "Brazilian Portuguese", "female"],
  ["pm_alex", "Brazilian Portuguese", "male"],
  ["pm_santa", "Brazilian Portuguese", "male"],
  ["zf_xiaobei", "Mandarin Chinese", "female"],
  ["zf_xiaoni", "Mandarin Chinese", "female"],
  ["zf_xiaoxiao", "Mandarin Chinese", "female"],
  ["zf_xiaoyi", "Mandarin Chinese", "female"],
  ["zm_yunjian", "Mandarin Chinese", "male"],
  ["zm_yunxi", "Mandarin Chinese", "male"],
  ["zm_yunxia", "Mandarin Chinese", "male"],
  ["zm_yunyang", "Mandarin Chinese", "male"],
];

export const DEFAULT_VOICE = "af_heart";

export const KOKORO_VOICES: VoiceOption[] = RAW.map(([id, language_name, gender]) => ({ id, language_name, gender, default: id === DEFAULT_VOICE }));

/** "af_heart" -> "Heart". */
export function voiceDisplayName(id: string): string {
  const name = id.includes("_") ? id.slice(id.indexOf("_") + 1) : id;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export const VOICE_SAMPLE_TEXT =
  "Hi, I'm your interviewer today. Take your time with each answer, and let me know if you'd like me to repeat a question.";
