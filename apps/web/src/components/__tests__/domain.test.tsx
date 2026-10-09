import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { EvidenceSignalIcon } from "@/components/domain/EvidenceSignalIcon";
import { HUMAN_DECISION_NOTE, RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { ScoreBar } from "@/components/domain/ScoreBar";
import { ScoreRing } from "@/components/domain/ScoreRing";
import { scoreTone } from "@/components/domain/scoreBands";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { RECOMMENDATION_LABELS } from "@/lib/labels";
import type { Recommendation } from "@/lib/types";

describe("RecommendationBadge", () => {
  it.each(Object.keys(RECOMMENDATION_LABELS) as Recommendation[])("%s is always paired with the human-decision note", (value) => {
    render(<RecommendationBadge value={value} />);
    expect(screen.getByText(RECOMMENDATION_LABELS[value])).toBeInTheDocument();
    expect(screen.getByText(HUMAN_DECISION_NOTE)).toBeInTheDocument();
  });

  it("keeps the note when there is no suggestion yet", () => {
    render(<RecommendationBadge value={null} />);
    expect(screen.getByText("No suggestion yet")).toBeInTheDocument();
    expect(screen.getByText(HUMAN_DECISION_NOTE)).toBeInTheDocument();
  });

  it("never uses wording about the person", () => {
    for (const label of Object.values(RECOMMENDATION_LABELS)) {
      expect(label.toLowerCase()).not.toMatch(/hire|reject|candidate is|fit|smart|weak/);
    }
  });
});

describe("score visuals", () => {
  it("bands scores by the default passing guidance", () => {
    expect(scoreTone(85)).toBe("success");
    expect(scoreTone(70)).toBe("accent");
    expect(scoreTone(55)).toBe("warning");
    expect(scoreTone(30)).toBe("danger");
    expect(scoreTone(7.2, 10)).toBe("accent");
    expect(scoreTone(null)).toBe("neutral");
  });

  it("ScoreRing describes the score for screen readers", () => {
    render(<ScoreRing value={72} label="Overall" />);
    expect(screen.getByRole("img", { name: "Overall: 72 out of 100" })).toBeInTheDocument();
  });

  it("ScoreRing handles missing scores", () => {
    render(<ScoreRing value={null} ariaLabel="Overall score" />);
    expect(screen.getByRole("img", { name: "Overall score: not available yet" })).toBeInTheDocument();
  });

  it("ScoreBar exposes a meter with value text and evidence details", () => {
    render(<ScoreBar label="System design" value={7.4} confidence={0.82} evidenceCount={5} />);
    const meter = screen.getByRole("meter", { name: "System design" });
    expect(meter).toHaveAttribute("aria-valuenow", "7.4");
    expect(meter).toHaveAttribute("aria-valuetext", "7.4 out of 10");
    expect(screen.getByText("5 evidence items")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Confidence: High (82%)" })).toBeInTheDocument();
  });

  it("ConfidenceMeter covers the no-evidence case", () => {
    render(<ConfidenceMeter value={0} />);
    expect(screen.getByRole("img", { name: "Confidence: no evidence yet" })).toBeInTheDocument();
  });
});

describe("badges", () => {
  it("labels statuses in reader terms", () => {
    render(
      <>
        <StatusBadge kind="session" value="live" />
        <StatusBadge kind="session" value="aborted" />
        <StatusBadge kind="resume" value="ocr" />
        <StatusBadge kind="integrity_review" value="confirmed_concern" />
      </>,
    );
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Ended early")).toBeInTheDocument();
    expect(screen.getByText("Scanning pages")).toBeInTheDocument();
    expect(screen.getByText("Confirmed concern")).toBeInTheDocument();
  });

  it("encodes evidence signal and difficulty accessibly", () => {
    render(
      <>
        <EvidenceSignalIcon signal="negative" />
        <DifficultyBadge value="hard" />
      </>,
    );
    expect(screen.getByRole("img", { name: "Gap or concern" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Difficulty: Hard" })).toBeInTheDocument();
  });
});
