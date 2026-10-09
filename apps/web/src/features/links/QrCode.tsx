import { Download } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

import { encodeQr, qrPath, type QrMatrix } from "./qr";

/*
 * A QR code is a machine-read image: scanners expect dark modules on a light field
 * with a quiet zone, whatever the app theme. The two fills below are therefore fixed
 * colours (not theme tokens), like the labels drawn over camera tiles.
 */
const QR_DARK = "#000000";
const QR_LIGHT = "#ffffff";
const QUIET = 4;

export interface QrCodeProps {
  value: string;
  /** Rendered size in CSS pixels. */
  size?: number;
  /** File name (without extension) for the PNG download. */
  downloadName?: string;
  className?: string;
}

function drawPng(matrix: QrMatrix, scale: number): Promise<Blob | null> {
  const canvas = document.createElement("canvas");
  const modules = matrix.size + QUIET * 2;
  canvas.width = modules * scale;
  canvas.height = modules * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  ctx.fillStyle = QR_LIGHT;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = QR_DARK;
  matrix.modules.forEach((row, r) =>
    row.forEach((dark, c) => {
      if (dark) ctx.fillRect((c + QUIET) * scale, (r + QUIET) * scale, scale, scale);
    }),
  );
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/png"));
}

export async function downloadQrPng(matrix: QrMatrix, name: string): Promise<void> {
  const blob = await drawPng(matrix, 16);
  if (!blob) return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${name}.png`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** QR code for a share URL with a "Download PNG" action. Renders nothing for text it can't encode. */
export function QrCode({ value, size = 176, downloadName = "interview-link-qr", className }: QrCodeProps) {
  const matrix = useMemo(() => {
    try {
      return encodeQr(value);
    } catch {
      return null;
    }
  }, [value]);
  if (!matrix) return null;
  const view = matrix.size + QUIET * 2;
  return (
    <figure className={cn("grid justify-items-start gap-3", className)}>
      <svg
        data-testid="link-qr"
        role="img"
        aria-label="QR code for the interview link"
        viewBox={`0 0 ${view} ${view}`}
        width={size}
        height={size}
        shapeRendering="crispEdges"
        className="rounded-md ring-1 ring-line"
      >
        <rect width={view} height={view} fill={QR_LIGHT} />
        <path d={qrPath(matrix, QUIET)} fill={QR_DARK} />
      </svg>
      <Button variant="outline" size="sm" leftIcon={<Download />} onClick={() => void downloadQrPng(matrix, downloadName)} data-testid="link-qr-download">
        Download PNG
      </Button>
    </figure>
  );
}
