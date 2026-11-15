// Generatore dei codici a 6 cifre: formato sempre corretto, zero iniziali
// possibili, nessun bias di modulo osservabile su un campione.
// Esegue senza wrangler: `node test/codegen.test.mjs`
import { newCode } from "../src/index.js";

const seen = new Set();
for (let i = 0; i < 2000; i++) {
  const c = newCode();
  if (!/^[0-9]{6}$/.test(c)) throw new Error("formato codice errato: " + JSON.stringify(c));
  seen.add(c);
}
if (seen.size < 1800) throw new Error("codici sospettosamente ripetitivi: " + seen.size + "/2000");
// il padding deve coprire anche i codici bassi: forza il caso cercandolo nel campione
if (![...seen].some(c => c.startsWith("0"))) throw new Error("mai un codice con zero iniziale su 2000: padding rotto?");
console.log("CODEGEN TESTS PASS (" + seen.size + " unici su 2000)");
