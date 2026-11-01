import { render, screen } from "@testing-library/react";
import { FactorList, RecommendationList } from "../components/PredictionPanel";
import { ErrorBox, HealthBar, RiskBadge } from "../components/ui";
import { ApiError } from "../api/client";

describe("UI components", () => {
  it("shows risk badge or 'not scored'", () => {
    const { rerender } = render(<RiskBadge risk="critical" />);
    expect(screen.getByText("critical")).toBeInTheDocument();
    rerender(<RiskBadge risk={null} />);
    expect(screen.getByText("not scored")).toBeInTheDocument();
  });

  it("health bar exposes an accessible label", () => {
    render(<HealthBar score={42} />);
    expect(screen.getByLabelText("Health score 42 of 100")).toBeInTheDocument();
  });

  it("explanation factors show direction of effect", () => {
    render(<FactorList factors={[
      { signal: "vibration_rms", feature: "vibration_rms__z_24h", value: 4, contribution: 2.5, effect: "increases risk", text: "Vibration is 4σ above normal" },
      { signal: "flow_rate", feature: "flow_rate__z_24h", value: 0, contribution: -0.5, effect: "decreases risk", text: "Flow is normal" },
    ]} />);
    expect(screen.getByText("Vibration is 4σ above normal")).toBeInTheDocument();
    expect(screen.getByText("▲ raises risk")).toBeInTheDocument();
    expect(screen.getByText("▼ lowers risk")).toBeInTheDocument();
  });

  it("recommendations are labelled as rule-based, not AI", () => {
    render(<RecommendationList recs={[{ source: "rule", priority: "high", suspected_failure_mode: "bearing_wear", action: "Inspect bearings." }]} />);
    expect(screen.getByText("Rule-based recommendation")).toBeInTheDocument();
    expect(screen.getByText("Inspect bearings.")).toBeInTheDocument();
  });

  it("error box lists validation details", () => {
    render(<ErrorBox error={new ApiError(422, "Validation error", [{ loc: ["body", "email"], msg: "invalid email" }])} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Validation error");
    expect(screen.getByRole("alert")).toHaveTextContent("invalid email (email)");
  });
});
