import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/Button";
import { MultiSelect } from "@/components/ui/Combobox";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { FileDropzone, isAcceptedType, validateFile } from "@/components/ui/FileDropzone";
import { Stepper, stepStatus } from "@/components/ui/Stepper";
import { TooltipProvider } from "@/components/ui/Tooltip";

describe("Button", () => {
  it("disables itself and announces busy state while loading", () => {
    const onClick = vi.fn();
    render(
      <Button loading loadingText="Saving…" onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: /saving/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to type=button so it never submits forms by accident", () => {
    render(<Button>Cancel</Button>);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute("type", "button");
  });

  it("renders its child element with asChild", () => {
    render(
      <Button asChild>
        <a href="/app/jobs">Jobs</a>
      </Button>,
    );
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/app/jobs");
  });
});

interface Row {
  id: string;
  name: string;
  score: number | null;
}

const rows: Row[] = Array.from({ length: 25 }, (_, i) => ({ id: `r${i}`, name: `Candidate ${String(i).padStart(2, "0")}`, score: i % 5 === 0 ? null : i }));

const columns: DataTableColumn<Row>[] = [
  { id: "name", header: "Name", cell: (r) => r.name, sortValue: (r) => r.name },
  { id: "score", header: "Score", numeric: true, cell: (r) => (r.score === null ? "—" : String(r.score)), sortValue: (r) => r.score },
];

function bodyRows() {
  const table = screen.getByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

describe("DataTable", () => {
  it("paginates client-side", () => {
    render(<DataTable columns={columns} data={rows} getRowId={(r) => r.id} pageSize={10} />);
    expect(bodyRows()).toHaveLength(10);
    expect(screen.getByText(/1–10 of 25/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText(/11–20 of 25/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(bodyRows()).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("sorts ascending, descending, then clears; nulls stay last", () => {
    render(<DataTable columns={columns} data={rows} getRowId={(r) => r.id} pageSize={30} />);
    const header = screen.getByRole("columnheader", { name: /score/i });
    fireEvent.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(within(bodyRows()[0]!).getByText("1")).toBeInTheDocument();
    expect(within(bodyRows()[24]!).getByText("—")).toBeInTheDocument();
    fireEvent.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(within(bodyRows()[0]!).getByText("24")).toBeInTheDocument();
    fireEvent.click(within(header).getByRole("button"));
    expect(header).toHaveAttribute("aria-sort", "none");
  });

  it("supports keyboard row activation and ignores clicks on inner controls", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        columns={[...columns, { id: "act", header: "", cell: () => <button type="button">Menu</button> }]}
        data={rows.slice(0, 2)}
        getRowId={(r) => r.id}
        onRowClick={onRowClick}
        rowLabel={(r) => `Open ${r.name}`}
      />,
    );
    const row = screen.getByRole("row", { name: "Open Candidate 00" });
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
    fireEvent.click(within(row).getByRole("button", { name: "Menu" }));
    expect(onRowClick).toHaveBeenCalledTimes(1);
    fireEvent.click(row);
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it("shows skeletons while loading and the empty state when there is nothing", () => {
    const { rerender, container } = render(<DataTable columns={columns} data={undefined} getRowId={(r) => r.id} loading skeletonRows={3} />);
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll("tbody tr")).toHaveLength(3);
    rerender(<DataTable columns={columns} data={[]} getRowId={(r) => r.id} empty={<p>No candidates yet</p>} />);
    expect(screen.getByText("No candidates yet")).toBeInTheDocument();
  });

  it("renders an error with a retry action", () => {
    const onRetry = vi.fn();
    render(<DataTable columns={columns} data={undefined} getRowId={(r) => r.id} error={new Error("Boom")} onRetry={onRetry} />);
    expect(screen.getByText("Couldn't load this list")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("delegates paging in server mode", () => {
    const onPageChange = vi.fn();
    render(<DataTable columns={columns} data={rows.slice(0, 10)} getRowId={(r) => r.id} pageSize={10} total={95} page={2} onPageChange={onPageChange} />);
    expect(screen.getByText(/21–30 of 95/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });
});

describe("FileDropzone validation", () => {
  const pdf = new File(["%PDF"], "Resume.PDF", { type: "application/pdf" });
  const docx = new File(["PK"], "cv.docx", { type: "" });
  const exe = new File(["MZ"], "setup.exe", { type: "application/x-msdownload" });

  it("matches MIME types, wildcards and extensions", () => {
    expect(isAcceptedType(pdf, ["application/pdf"])).toBe(true);
    expect(isAcceptedType(docx, [".docx"])).toBe(true);
    expect(isAcceptedType(new File(["x"], "a.png", { type: "image/png" }), ["image/*"])).toBe(true);
    expect(isAcceptedType(exe, ["application/pdf", ".docx"])).toBe(false);
  });

  it("rejects wrong types, oversize and empty files with readable messages", () => {
    const opts = { accept: ["application/pdf", ".docx"], maxSizeBytes: 3, acceptLabel: "PDF or DOCX" };
    expect(validateFile(exe, opts)).toMatchObject({ reason: "type", message: "setup.exe isn't a supported file. Use PDF or DOCX." });
    expect(validateFile(new File(["12345"], "big.pdf", { type: "application/pdf" }), opts)).toMatchObject({ reason: "size" });
    expect(validateFile(new File([], "empty.pdf", { type: "application/pdf" }), opts)).toMatchObject({ reason: "empty" });
    expect(validateFile(pdf, { ...opts, maxSizeBytes: 100 })).toBeNull();
  });

  it("passes accepted files through and reports rejections", () => {
    const onFiles = vi.fn();
    const onReject = vi.fn();
    const { container } = render(
      <FileDropzone accept={["application/pdf"]} acceptLabel="PDF" maxSizeBytes={1024} onFiles={onFiles} onReject={onReject} hint="PDF up to 1 KB" />,
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [exe] } });
    expect(onFiles).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith([expect.objectContaining({ reason: "type" })]);
    expect(screen.getByText("setup.exe isn't a supported file. Use PDF.")).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [pdf] } });
    expect(onFiles).toHaveBeenCalledWith([pdf]);
  });

  it("passes input props (test ids, labels) to the file input but keeps control of its behaviour", () => {
    const onFiles = vi.fn();
    render(
      <FileDropzone
        accept={["application/pdf"]}
        onFiles={onFiles}
        inputProps={{ "data-testid": "resume-file-input", "aria-label": "Choose your résumé file", name: "resume" }}
      />,
    );
    const input = screen.getByTestId("resume-file-input") as HTMLInputElement;
    expect(input.type).toBe("file");
    expect(input).toHaveAttribute("accept", "application/pdf");
    expect(input).toHaveAttribute("name", "resume");
    expect(input).toHaveAccessibleName("Choose your résumé file");
    fireEvent.change(input, { target: { files: [pdf] } });
    expect(onFiles).toHaveBeenCalledWith([pdf]);
  });

  it("shows upload progress for the current file", () => {
    render(<FileDropzone onFiles={() => {}} status="uploading" progress={42} file={{ name: "resume.pdf", size: 2048 }} />);
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Uploading resume.pdf" })).toHaveAttribute("aria-valuenow", "42");
  });
});

describe("Stepper", () => {
  const steps = [
    { key: "welcome", label: "Welcome" },
    { key: "consent", label: "Consent" },
    { key: "interview", label: "Interview" },
  ];

  it("derives statuses from the current step", () => {
    expect(stepStatus(steps, "welcome", "consent")).toBe("complete");
    expect(stepStatus(steps, "consent", "consent")).toBe("current");
    expect(stepStatus(steps, "interview", "consent")).toBe("upcoming");
    expect(stepStatus(steps, "welcome", "consent", [])).toBe("upcoming");
  });

  it("marks the current step for assistive technology", () => {
    render(<Stepper steps={steps} current="consent" aria-label="Interview steps" />);
    const nav = screen.getByRole("navigation", { name: "Interview steps" });
    const current = within(nav).getAllByRole("listitem").find((li) => li.getAttribute("aria-current") === "step");
    expect(current).toHaveTextContent("Consent");
    expect(within(nav).getByText("(completed)")).toBeInTheDocument();
  });
});

describe("MultiSelect", () => {
  function SkillsField({ initial = [] as string[] }) {
    const [value, setValue] = useState<string[]>(initial);
    return (
      <TooltipProvider>
        <MultiSelect
          aria-label="Skills"
          creatable
          value={value}
          onChange={setValue}
          options={[
            { value: "kubernetes", label: "Kubernetes" },
            { value: "postgres", label: "PostgreSQL" },
          ]}
        />
        <output data-testid="value">{value.join(",")}</output>
      </TooltipProvider>
    );
  }

  it("adds suggestions and free-text tags, removes with Backspace", () => {
    render(<SkillsField />);
    const input = screen.getByRole("combobox", { name: "Skills" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "kube" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("value")).toHaveTextContent("kubernetes");

    fireEvent.change(input, { target: { value: "  Rust  " } });
    fireEvent.keyDown(input, { key: "," });
    expect(screen.getByTestId("value")).toHaveTextContent("kubernetes,Rust");

    fireEvent.change(input, { target: { value: "rust" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("value")).toHaveTextContent("kubernetes,Rust");

    fireEvent.keyDown(input, { key: "Backspace" });
    expect(screen.getByTestId("value")).toHaveTextContent("kubernetes");
  });

  it("removes a tag with its button", () => {
    render(<SkillsField initial={["postgres"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove PostgreSQL" }));
    expect(screen.getByTestId("value")).toHaveTextContent("");
  });
});
