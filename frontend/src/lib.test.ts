import { describe, expect, it } from "vitest";
import { formatCitation, isIdkAnswer } from "./lib";
import type { Citation } from "./api";

const cit: Citation = {
  ref: "M1",
  memory_id: "00000000-0000-0000-0000-000000000001",
  content: "Il progetto usa PostgreSQL",
  kind: "fact",
  origin: "stated",
  confidence: 0.9,
  recorded_at: "2026-11-07T10:00:00Z",
  source: { id: "s1", kind: "chat", label: "chat", created_at: "2026-11-07T09:00:00Z" },
};

describe("formatCitation", () => {
  it("include ref, tipo, origine, confidenza e fonte", () => {
    const out = formatCitation(cit);
    expect(out).toContain("[M1]");
    expect(out).toContain("fact/stated");
    expect(out).toContain("0.90");
    expect(out).toContain("chat");
    expect(out).toContain("PostgreSQL");
  });

  it("gestisce fonte mancante", () => {
    const out = formatCitation({ ...cit, source: null });
    expect(out).toContain("fonte sconosciuta");
  });
});

describe("isIdkAnswer", () => {
  it("riconosce la risposta onesta", () => {
    expect(isIdkAnswer("Non lo so: non ho memorie pertinenti.")).toBe(true);
    expect(isIdkAnswer("  non lo so, mi dispiace")).toBe(true);
  });
  it("non confonde risposte normali", () => {
    expect(isIdkAnswer("Dalle tue memorie: ...")).toBe(false);
  });
});
