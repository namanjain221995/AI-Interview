import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RegistrationForm } from "../components/RegistrationForm";
import type { DataUse, PublicFormField } from "../types";

const DATA_USE: DataUse = {
  video_recording: false,
  audio_recording: true,
  screen_recording: false,
  camera_monitoring: false,
  screen_monitoring: true,
  candidate_report_mode: "never",
  retention_days: { resume: 30, audio: 30, video: 0, transcript: 60, report: 60 },
  notice_markdown: "",
  ai_assessment: true,
  human_decision: true,
};

const f = (key: string, type: PublicFormField["type"], label: string, extra: Partial<PublicFormField> = {}): PublicFormField => ({
  key,
  type,
  label,
  help_text: null,
  placeholder: null,
  required: false,
  options: [],
  min: null,
  max: null,
  max_length: null,
  ...extra,
});

const FIELDS: PublicFormField[] = [
  f("full_name", "text", "Full name", { required: true }),
  f("email", "email", "Email", { required: true }),
  f("years_experience", "number", "Years of experience", { min: 0, max: 60, help_text: "Whole or half years" }),
  f("c_langs", "multiselect", "Languages you work in", { options: ["English", "Hindi", "Marathi"], max: 2 }),
  f("available_from", "date", "Available from"),
  f("c_confirm", "checkbox", "I confirm my details are correct", { required: true }),
];

describe("RegistrationForm", () => {
  it("renders labels, help text, native controls and the hidden honeypot", () => {
    render(<RegistrationForm fields={FIELDS} dataUse={DATA_USE} organizationName="Demo Corp" requiresAccessCode={false} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText(/Full name/)).toHaveAttribute("autocomplete", "name");
    const years = screen.getByTestId("open-link-field-years_experience");
    expect(years).toHaveAttribute("type", "number");
    expect(years).toHaveAttribute("max", "60");
    expect(years.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText("Whole or half years")).toHaveAttribute("id", years.getAttribute("aria-describedby")!.split(" ")[0]);
    expect(screen.getByTestId("open-link-field-available_from")).toHaveAttribute("type", "date");
    const honeypot = document.querySelector<HTMLInputElement>('input[name="website"]')!;
    expect(honeypot).toHaveAttribute("tabindex", "-1");
    expect(honeypot).toHaveAttribute("autocomplete", "off");
    expect(honeypot.closest("[aria-hidden='true']")).not.toBeNull();
    // Data use: no video, tab changes noted, retention list.
    const panel = screen.getByTestId("open-link-data-use");
    expect(within(panel).getByText(/records your voice and keeps a written transcript/)).toBeInTheDocument();
    expect(within(panel).getByText(/switching tabs or windows is noted/)).toBeInTheDocument();
    expect(within(panel).getByText(/your résumé for 30 days/)).toBeInTheDocument();
  });

  it("validates on submit and sends typed answers, multiselect limits and the confirmation", () => {
    const onSubmit = vi.fn();
    render(<RegistrationForm fields={FIELDS} dataUse={DATA_USE} organizationName="Demo Corp" requiresAccessCode onSubmit={onSubmit} />);
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("open-link-field-error-email")).toHaveTextContent("Fill in this field.");
    expect(screen.getByTestId("open-link-field-error-c_confirm")).toHaveTextContent("Tick this box");
    expect(screen.getByText(/access code you were given/)).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("open-link-field-full_name"), { target: { value: "Jane Doe" } });
    fireEvent.change(screen.getByTestId("open-link-field-email"), { target: { value: "jane@example.com" } });
    fireEvent.change(screen.getByTestId("open-link-field-years_experience"), { target: { value: "70" } });
    const langs = screen.getByTestId("open-link-field-c_langs");
    fireEvent.click(within(langs).getByRole("checkbox", { name: "English" }));
    fireEvent.click(within(langs).getByRole("checkbox", { name: "Hindi" }));
    fireEvent.click(within(langs).getByRole("checkbox", { name: "Marathi" }));
    fireEvent.click(screen.getByTestId("open-link-field-c_confirm"));
    fireEvent.change(screen.getByTestId("open-link-access-code-input"), { target: { value: " AB12CD " } });
    fireEvent.click(screen.getByTestId("open-link-privacy-ack"));
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("open-link-field-error-years_experience")).toHaveTextContent("60 or less");
    expect(screen.getByTestId("open-link-field-error-c_langs")).toHaveTextContent("at most 2");

    fireEvent.change(screen.getByTestId("open-link-field-years_experience"), { target: { value: "4.5" } });
    fireEvent.click(within(langs).getByRole("checkbox", { name: "Marathi" }));
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(onSubmit).toHaveBeenCalledWith({
      answers: { full_name: "Jane Doe", email: "jane@example.com", years_experience: 4.5, c_langs: ["English", "Hindi"], available_from: null, c_confirm: true },
      privacy_ack: true,
      access_code: "AB12CD",
    });
  });

  it("sends the honeypot value when a bot fills it", () => {
    const onSubmit = vi.fn();
    render(<RegistrationForm fields={FIELDS.slice(0, 2)} dataUse={DATA_USE} organizationName="Demo Corp" requiresAccessCode={false} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByTestId("open-link-field-full_name"), { target: { value: "Bot" } });
    fireEvent.change(screen.getByTestId("open-link-field-email"), { target: { value: "bot@example.com" } });
    fireEvent.change(document.querySelector('input[name="website"]')!, { target: { value: "http://spam.example" } });
    fireEvent.click(screen.getByTestId("open-link-privacy-ack"));
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ website: "http://spam.example" }));
  });

  it("never submits in preview mode", () => {
    const onSubmit = vi.fn();
    render(<RegistrationForm fields={FIELDS} dataUse={DATA_USE} organizationName="Demo Corp" requiresAccessCode={false} mode="preview" onSubmit={onSubmit} />);
    expect(screen.getByTestId("open-link-submit")).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "Registration form preview" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
