import type { Citation } from "./api";

export function formatCitation(c: Citation): string {
  const src = c.source ? c.source.label : "fonte sconosciuta";
  const when = new Date(c.recorded_at).toLocaleDateString("it-IT");
  return `[${c.ref}] (${c.kind}/${c.origin}, confidenza ${c.confidence.toFixed(2)}, fonte: ${src}, ${when}) ${c.content}`;
}

export function isIdkAnswer(answer: string): boolean {
  return answer.trim().toLowerCase().startsWith("non lo so");
}
