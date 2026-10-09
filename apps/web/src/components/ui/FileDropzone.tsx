import { CheckCircle2, FileText, RotateCcw, UploadCloud, X } from "lucide-react";
import { useCallback, useId, useRef, useState, type DragEvent, type InputHTMLAttributes, type ReactNode } from "react";

import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Button } from "./Button";
import { Progress } from "./Progress";

export interface FileRejection {
  file: File;
  reason: "type" | "size" | "count" | "empty";
  message: string;
}

export interface FileValidationOptions {
  /** MIME types ("application/pdf", "image/*") and/or extensions (".docx"). */
  accept?: readonly string[];
  maxSizeBytes?: number;
  /** Human description of accepted types, used in messages ("PDF or DOCX"). */
  acceptLabel?: string;
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index).toLowerCase();
}

export function isAcceptedType(file: File, accept: readonly string[] | undefined): boolean {
  if (!accept || accept.length === 0) return true;
  const type = (file.type || "").toLowerCase();
  const ext = extensionOf(file.name);
  return accept.some((rule) => {
    const r = rule.trim().toLowerCase();
    if (!r) return false;
    if (r.startsWith(".")) return ext === r;
    if (r.endsWith("/*")) return type.startsWith(r.slice(0, -1));
    return type === r;
  });
}

/** Returns a rejection for the first failed rule, or null when the file is acceptable. */
export function validateFile(file: File, { accept, maxSizeBytes, acceptLabel }: FileValidationOptions): FileRejection | null {
  if (file.size === 0) return { file, reason: "empty", message: `${file.name} is empty.` };
  if (!isAcceptedType(file, accept)) {
    return { file, reason: "type", message: acceptLabel ? `${file.name} isn't a supported file. Use ${acceptLabel}.` : `${file.name} isn't a supported file type.` };
  }
  if (maxSizeBytes !== undefined && file.size > maxSizeBytes) {
    return { file, reason: "size", message: `${file.name} is ${formatBytes(file.size)}. The limit is ${formatBytes(maxSizeBytes)}.` };
  }
  return null;
}

export type DropzoneStatus = "idle" | "uploading" | "success" | "error";

/**
 * Extra attributes for the hidden `<input type="file">` (test ids, aria, name).
 * The dropzone keeps control of the behaviour-critical ones.
 */
export type DropzoneInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "type" | "accept" | "multiple" | "disabled" | "onChange" | "onClick" | "id" | "className" | "tabIndex" | "value" | "defaultValue"
> & { [dataAttribute: `data-${string}`]: string | number | boolean | undefined };

export interface FileDropzoneProps extends FileValidationOptions {
  onFiles: (files: File[]) => void;
  onReject?: (rejections: FileRejection[]) => void;
  multiple?: boolean;
  disabled?: boolean;
  /** Current upload state for the selected file. */
  status?: DropzoneStatus;
  /** 0-100 while uploading; null for indeterminate. */
  progress?: number | null;
  /** The file being uploaded / uploaded. */
  file?: { name: string; size: number } | null;
  /** Error to show (upload failure or server rejection). */
  error?: ReactNode;
  onCancel?: () => void;
  onRetry?: () => void;
  title?: ReactNode;
  hint?: ReactNode;
  className?: string;
  id?: string;
  /** Passed to the hidden file input, e.g. `{ "data-testid": "resume-file-input" }`. */
  inputProps?: DropzoneInputProps;
}

export function FileDropzone({
  onFiles,
  onReject,
  accept,
  acceptLabel,
  maxSizeBytes,
  multiple = false,
  disabled = false,
  status = "idle",
  progress = null,
  file,
  error,
  onCancel,
  onRetry,
  title = "Drop a file here or browse",
  hint,
  className,
  id,
  inputProps,
}: FileDropzoneProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const dragDepth = useRef(0);

  const handleFiles = useCallback(
    (list: FileList | File[] | null) => {
      if (!list) return;
      const files = Array.from(list);
      if (files.length === 0) return;
      const rejections: FileRejection[] = [];
      const accepted: File[] = [];
      const candidates = multiple ? files : files.slice(0, 1);
      if (!multiple && files.length > 1) {
        rejections.push({ file: files[1]!, reason: "count", message: "Choose one file at a time." });
      }
      for (const candidate of candidates) {
        const rejection = validateFile(candidate, { accept, maxSizeBytes, acceptLabel });
        if (rejection) rejections.push(rejection);
        else accepted.push(candidate);
      }
      setLocalError(rejections.length && accepted.length === 0 ? rejections[0]!.message : null);
      if (rejections.length) onReject?.(rejections);
      if (accepted.length) onFiles(accepted);
    },
    [accept, acceptLabel, maxSizeBytes, multiple, onFiles, onReject],
  );

  const busy = status === "uploading";
  const interactive = !disabled && !busy;

  const onDragEnter = (event: DragEvent) => {
    event.preventDefault();
    if (!interactive) return;
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragLeave = (event: DragEvent) => {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (!interactive) return;
    handleFiles(event.dataTransfer.files);
  };

  const shownError = error ?? localError;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;

  return (
    <div className={cn("grid content-start gap-3", className)}>
      <div
        onDragEnter={onDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => interactive && inputRef.current?.click()}
        data-dragging={dragging || undefined}
        aria-disabled={!interactive || undefined}
        className={cn(
          "group relative flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong bg-surface-subtle px-6 py-9 text-center transition-colors duration-150",
          "hover:border-line-emphasis hover:bg-surface",
          "data-[dragging]:border-accent data-[dragging]:bg-accent-soft",
          "aria-disabled:cursor-not-allowed aria-disabled:opacity-60 aria-disabled:hover:border-line-strong aria-disabled:hover:bg-surface-subtle",
          shownError && "border-danger-line",
        )}
      >
        <span className="flex size-10 items-center justify-center rounded-md border border-line bg-surface text-fg-muted shadow-[0_1px_2px_rgb(16_24_40/0.05)] group-data-[dragging]:text-accent">
          <UploadCloud className="size-5" aria-hidden="true" />
        </span>
        <p className="text-sm font-medium text-fg">{dragging ? "Release to upload" : title}</p>
        {hint ? (
          <p id={hintId} className="text-xs text-fg-muted">
            {hint}
          </p>
        ) : null}
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!interactive}
          className="mt-2"
          onClick={(event) => {
            event.stopPropagation();
            inputRef.current?.click();
          }}
          aria-describedby={[hint ? hintId : null, shownError ? errorId : null].filter(Boolean).join(" ") || undefined}
        >
          Browse files
        </Button>
        <input
          {...inputProps}
          ref={inputRef}
          id={inputId}
          type="file"
          className="sr-only"
          tabIndex={-1}
          multiple={multiple}
          disabled={!interactive}
          accept={accept?.join(",")}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {file ? (
        <div className="flex items-center gap-3 rounded-md border border-line bg-surface px-3.5 py-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-sm bg-neutral-soft text-fg-muted">
            {status === "success" ? <CheckCircle2 className="size-[18px] text-success" aria-hidden="true" /> : <FileText className="size-[18px]" aria-hidden="true" />}
          </span>
          <div className="grid min-w-0 flex-1 gap-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <p className="truncate text-sm font-medium text-fg">{file.name}</p>
              <p className="shrink-0 text-xs text-fg-muted tabular-nums">
                {status === "uploading" && typeof progress === "number" ? `${Math.round(progress)}%` : formatBytes(file.size)}
              </p>
            </div>
            {status === "uploading" ? <Progress value={progress} size="xs" aria-label={`Uploading ${file.name}`} /> : null}
            {status === "success" ? <p className="text-xs text-success-fg">Uploaded</p> : null}
          </div>
          {status === "uploading" && onCancel ? (
            <Button variant="ghost" size="sm" className="size-8 px-0" aria-label="Cancel upload" onClick={onCancel}>
              <X />
            </Button>
          ) : null}
          {status === "error" && onRetry ? (
            <Button variant="outline" size="sm" leftIcon={<RotateCcw />} onClick={onRetry}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}

      <div aria-live="polite" className="empty:hidden">
        {shownError ? (
          <p id={errorId} className="text-xs font-medium text-danger-fg">
            {shownError}
          </p>
        ) : null}
      </div>
    </div>
  );
}
