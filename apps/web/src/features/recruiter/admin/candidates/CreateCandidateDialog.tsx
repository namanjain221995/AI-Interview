import { Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { z } from "zod";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Form, FormError, FormField, useZodForm } from "@/components/ui/Form";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";
import type { Candidate } from "@/lib/types";

import { useCreateCandidate } from "../api/candidates";
import { describeFailure, fieldErrorsOf } from "../lib/serverErrors";
import { ResumeUploader } from "./ResumeUpload";

export const candidateSchema = z.object({
  full_name: z.string().trim().min(1, "Enter the candidate's name.").max(200, "Keep the name under 200 characters."),
  email: z.string().trim().toLowerCase().email("Enter a valid email address."),
  phone: z.string().trim().max(40, "That phone number is too long.").optional(),
  location: z.string().trim().max(200).optional(),
  source: z.string().trim().max(100).optional(),
  notes: z.string().trim().max(4000, "Keep notes under 4,000 characters.").optional(),
});

type CandidateForm = z.input<typeof candidateSchema>;

const EMPTY: CandidateForm = { full_name: "", email: "", phone: "", location: "", source: "", notes: "" };

export function CreateCandidateDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [created, setCreated] = useState<Candidate | null>(null);
  /** The email belongs to an archived candidate: offer to open (and restore) them instead. */
  const [archivedMatch, setArchivedMatch] = useState<string | null>(null);
  const create = useCreateCandidate();
  const form = useZodForm(candidateSchema, { defaultValues: EMPTY });

  useEffect(() => {
    if (!open) {
      setCreated(null);
      setArchivedMatch(null);
      form.reset(EMPTY);
    }
  }, [open, form]);

  const submit = async (values: z.output<typeof candidateSchema>) => {
    setArchivedMatch(null);
    try {
      const candidate = await create.mutateAsync({
        full_name: values.full_name,
        email: values.email,
        phone: values.phone || null,
        location: values.location || null,
        source: values.source || null,
        notes: values.notes || null,
      });
      setCreated(candidate);
    } catch (error) {
      if (isApiError(error) && error.code === "candidate_exists" && error.details.archived === true && typeof error.details.candidate_id === "string") {
        setArchivedMatch(error.details.candidate_id);
      }
      const fields = fieldErrorsOf(error);
      let mapped = 0;
      for (const [path, message] of Object.entries(fields)) {
        if (path in EMPTY) {
          form.setError(path as keyof CandidateForm, { type: "server", message });
          mapped += 1;
        }
      }
      if (!mapped) form.setError("root.server", { type: "server", message: describeFailure(error) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle>Add {created.full_name.split(" ")[0]}'s resume</DialogTitle>
              <DialogDescription>Optional, but it powers the profile, job match and the questions the interviewer asks about past work.</DialogDescription>
            </DialogHeader>
            <DialogBody>
              <ResumeUploader candidateId={created.id} />
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              <Button
                onClick={() => {
                  onOpenChange(false);
                  navigate(`/app/candidates/${created.id}`);
                }}
              >
                Open candidate
              </Button>
            </DialogFooter>
          </>
        ) : (
          <Form form={form} onSubmit={submit} className="space-y-0">
            <DialogHeader>
              <DialogTitle>Add a candidate</DialogTitle>
              <DialogDescription>Add someone you want to interview. You can invite them to a job afterwards.</DialogDescription>
            </DialogHeader>
            <DialogBody className="grid gap-4 sm:grid-cols-2">
              <FormField<CandidateForm, "full_name"> name="full_name" label="Full name" required>
                {(field) => <Input {...field} value={field.value as string} autoFocus autoComplete="off" />}
              </FormField>
              <FormField<CandidateForm, "email"> name="email" label="Email" required>
                {(field) => <Input {...field} value={field.value as string} type="email" leading={<Mail />} autoComplete="off" />}
              </FormField>
              <FormField<CandidateForm, "phone"> name="phone" label="Phone" optional>
                {(field) => <Input {...field} value={field.value as string} type="tel" />}
              </FormField>
              <FormField<CandidateForm, "location"> name="location" label="Location" optional>
                {(field) => <Input {...field} value={field.value as string} />}
              </FormField>
              <FormField<CandidateForm, "source"> name="source" label="Source" optional description="e.g. Referral, LinkedIn, careers page" className="sm:col-span-2">
                {(field) => <Input {...field} value={field.value as string} />}
              </FormField>
              <FormField<CandidateForm, "notes"> name="notes" label="Notes" optional className="sm:col-span-2" description="Visible to your team only.">
                {(field) => <Textarea {...field} value={field.value as string} rows={3} />}
              </FormField>
              <FormError className="sm:col-span-2" />
              {archivedMatch ? (
                <Callout
                  tone="info"
                  className="sm:col-span-2"
                  title="This email belongs to an archived candidate"
                  actions={
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        onOpenChange(false);
                        navigate(`/app/candidates/${archivedMatch}`);
                      }}
                    >
                      Open archived candidate
                    </Button>
                  }
                >
                  Restore them instead of adding a duplicate, so their history stays in one place.
                </Callout>
              ) : null}
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={create.isPending} loadingText="Adding…">
                Add candidate
              </Button>
            </DialogFooter>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
