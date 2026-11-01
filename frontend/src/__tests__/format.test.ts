import { healthColor, healthStroke, humanize, money, num, pct } from "../lib/format";

describe("format helpers", () => {
  it("formats percentages and handles missing values", () => {
    expect(pct(0.1234, 1)).toBe("12.3%");
    expect(pct(null)).toBe("—");
  });
  it("formats numbers, money and labels", () => {
    expect(num(1.23456, 2)).toBe("1.23");
    expect(num(Number.NaN)).toBe("—");
    expect(money(1500)).toContain("1,500");
    expect(humanize("bearing_wear")).toBe("bearing wear");
  });
  it("maps health scores to literal Tailwind classes", () => {
    expect(healthColor(95)).toBe("bg-emerald-500");
    expect(healthColor(10)).toBe("bg-red-600");
    expect(healthStroke(75)).toBe("stroke-amber-500");
  });
});
