import { describe, expect, it } from "vitest";
import { dollars, pct, pctPair, spreadLine } from "./format";

describe("market formatting", () => {
  it("rounds probabilities to whole percents and keeps pairs at 100", () => {
    expect(pct(0.475)).toBe("48%");
    expect(pct(null)).toBe("–");
    expect(pctPair(0.325)).toEqual(["33%", "67%"]);
    expect(pctPair(0.475)).toEqual(["48%", "52%"]);
  });

  it("writes spreads with a real minus sign", () => {
    expect(spreadLine(-2.5)).toBe("−2.5");
    expect(spreadLine(3.5)).toBe("+3.5");
    expect(spreadLine(0)).toBe("PK");
  });

  it("shortens dollar volumes", () => {
    expect(dollars(9.18)).toBe("$9");
    expect(dollars(6197.78)).toBe("$6.2K");
    expect(dollars(428215.77)).toBe("$428K");
    expect(dollars(1_250_000)).toBe("$1.3M");
  });
});
