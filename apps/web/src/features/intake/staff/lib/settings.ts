/** Organisation intake settings: validation mirroring the server and the PUT body. */

import type { IntakeOrgSettingsIn } from "../types";
import { COUNTRY_MAX, DURATION_MAX, DURATION_MIN, RECONNECTS_MAX } from "./createForm";

export const MAX_INFO = 30;

export function validateOrgSettings(value: IntakeOrgSettingsIn): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!value.program_name.trim()) errors.program_name = "Name the program. The interviewer says it in the opening and closing.";
  else if (value.program_name.length > 200) errors.program_name = "Keep it under 200 characters.";
  if (value.work_authorization.enabled) {
    const country = value.work_authorization.country?.trim() ?? "";
    if (!country) errors.country = "Name the country, as it should read in the question.";
    else if (country.length > COUNTRY_MAX) errors.country = `Keep it under ${COUNTRY_MAX} characters.`;
  }
  if (value.approved_info.length > MAX_INFO) errors.approved_info = `Up to ${MAX_INFO} entries.`;
  value.approved_info.forEach((item, i) => {
    if (!item.topic.trim()) errors[`approved_info.${i}.topic`] = "Add a topic.";
    else if (item.topic.length > 120) errors[`approved_info.${i}.topic`] = "Keep topics under 120 characters.";
    if (!item.answer.trim()) errors[`approved_info.${i}.answer`] = "Add the answer the interviewer may give.";
    else if (item.answer.length > 1200) errors[`approved_info.${i}.answer`] = "Keep answers under 1,200 characters.";
  });
  const d = value.defaults;
  if (d.duration_minutes < DURATION_MIN || d.duration_minutes > DURATION_MAX) errors.duration_minutes = `Between ${DURATION_MIN} and ${DURATION_MAX} minutes.`;
  if (!Number.isInteger(d.max_reconnects) || d.max_reconnects < 0 || d.max_reconnects > RECONNECTS_MAX) errors.max_reconnects = `Between 0 and ${RECONNECTS_MAX}.`;
  return errors;
}

export function payloadOf(value: IntakeOrgSettingsIn): IntakeOrgSettingsIn {
  return {
    program_name: value.program_name.trim(),
    work_authorization: value.work_authorization.enabled
      ? { ...value.work_authorization, country: value.work_authorization.country?.trim() || null }
      : { ...value.work_authorization, enabled: false },
    approved_info: value.approved_info.map((a) => ({ topic: a.topic.trim(), answer: a.answer.trim() })),
    defaults: { ...value.defaults },
  };
}

