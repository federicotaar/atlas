import { useEffect, useMemo, useState } from "react";
import { api, type ModelSettings, type ProviderInfo } from "../api";

// Ollama si presenta come UNA voce; alla selezione chiede Locale o Cloud
// (due provider distinti lato backend: ollama_local / ollama_cloud).
type UiProvider = "extractive" | "ollama" | "groq" | "gemini" | "custom";

const UI_ORDER: Array<{ id: UiProvider; title: string; sub: string }> = [
  { id: "extractive", title: "Estrattivo (offline)", sub: "Nessun modello AI: risposte estratte dalle tue memorie. Privato, sempre disponibile." },
  { id: "ollama", title: "Ollama", sub: "Modelli aperti, sul tuo PC o su Ollama cloud." },
  { id: "groq", title: "Groq", sub: "Modelli aperti veloci via API Groq (serve la tua chiave)." },
  { id: "gemini", title: "Gemini", sub: "Modelli Google via API (serve la tua chiave)." },
  { id: "custom", title: "Personalizzato", sub: "Qualsiasi endpoint compatibile OpenAI (base URL + modello)." },
];

function uiToBackend(p: UiProvider, ollamaMode: "local" | "cloud"): string {
  if (p === "ollama") return ollamaMode === "local" ? "ollama_local" : "ollama_cloud";
  return p;
}

function backendToUi(p: string): { ui: UiProvider; ollamaMode: "local" | "cloud" } {
  if (p === "ollama_local") return { ui: "ollama", ollamaMode: "local" };
  if (p === "ollama_cloud") return { ui: "ollama", ollamaMode: "cloud" };
  return { ui: p as UiProvider, ollamaMode: "local" };
}

export default function ModelPicker({ onClose, onSaved }: {
  onClose: () => void;
  onSaved: (s: ModelSettings) => void;
}) {
  const [loaded, setLoaded] = useState<ModelSettings | null>(null);
  const [ui, setUi] = useState<UiProvider>("extractive");
  const [ollamaMode, setOllamaMode] = useState<"local" | "cloud">("local");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [consent, setConsent] = useState(false);
  // Provider (backend id) a cui appartiene il consenso cloud dato in questa
  // sessione: cambiando destinazione serve un consenso nuovo, mai ereditato.
  const [consentFor, setConsentFor] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Errore di CARICAMENTO (separato da quello di salvataggio): se le
  // impostazioni non arrivano, il form non deve apparire mezzo vuoto -
  // i campi chiave/consenso dipendono dai metadati del provider caricati.
  const [loadError, setLoadError] = useState<string | null>(null);

  function load() {
    setLoadError(null);
    api.getModelSettings().then((s) => {
      setLoaded(s);
      const b = backendToUi(s.provider);
      setUi(b.ui);
      setOllamaMode(b.ollamaMode);
      setModel(s.model);
      setBaseUrl(s.base_url);
      setConsent(s.consent_cloud);
      setConsentFor(s.consent_cloud ? (s.provider === "custom" ? `custom:${s.base_url}` : s.provider) : null);
    }).catch((e) => setLoadError(e instanceof Error ? e.message : "Errore"));
  }

  useEffect(load, []);

  const backend = uiToBackend(ui, ollamaMode);
  // Destinazione effettiva dei dati: per gli endpoint personalizzati e' la
  // base URL scritta dall'utente, non la sola categoria "custom".
  const dest = backend === "custom" ? `custom:${baseUrl.trim()}` : backend;

  // Il consenso vale solo per il provider per cui e' stato dato: selezionando
  // un provider cloud diverso la spunta si azzera e Salva resta disabilitato.
  useEffect(() => {
    setConsent(consentFor === dest);
  }, [dest, consentFor]);

  // Stato specifico del provider: cambiando provider il modello, la base URL e
  // la chiave inserita NON si trascinano sul provider nuovo (si salverebbe un
  // modello/chiave destinati a un altro servizio). Tornando al provider gia'
  // configurato, si ripristinano i valori salvati.
  useEffect(() => {
    if (!loaded) return;
    if (backend === loaded.provider) {
      setModel(loaded.model);
      setBaseUrl(loaded.base_url);
    } else {
      setModel("");
      setBaseUrl("");
    }
    setApiKey("");
    setKeyTouched(false);
  }, [backend, loaded]);
  const meta: ProviderInfo | undefined = useMemo(
    () => loaded?.providers.find((p) => p.id === backend), [loaded, backend]);

  // Elenco modelli del provider scelto: filtrato dalla casella di ricerca.
  useEffect(() => {
    setSuggestions([]);
    if (backend === "extractive" || !loaded) return;
    api.listModels(backend)
      .then((r) => setSuggestions(r.models))
      .catch(() => setSuggestions([]));
  }, [backend, loaded]);

  const filtered = useMemo(() => {
    const q = model.trim().toLowerCase();
    if (!q) return suggestions.slice(0, 50);
    return suggestions.filter((m) => m.toLowerCase().includes(q)).slice(0, 50);
  }, [suggestions, model]);

  const isCloud = meta?.kind === "cloud";
  const needsKey = meta?.needs_key ?? false;
  const needsBaseUrl = meta?.needs_base_url ?? false;
  const hasKey = loaded?.provider === backend && loaded.has_key;
  const keyUnusable = loaded?.provider === backend && loaded.key_unusable;

  async function save() {
    if (!loaded || !meta) return;
    setBusy(true);
    setError(null);
    try {
      const body: Parameters<typeof api.putModelSettings>[0] = {
        provider: backend,
        model: model.trim(),
        base_url: needsBaseUrl ? baseUrl.trim() : "",
        consent_cloud: isCloud ? consent : false,
      };
      // api_key write-only: la inviamo solo se l'utente l'ha toccata
      // (stringa vuota = rimuovi chiave salvata).
      if (keyTouched) body.api_key = apiKey;
      const s = await api.putModelSettings(body);
      onSaved(s);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Errore");
      setBusy(false);
    }
  }

  // Requisiti per salvare, dichiarati all'utente PRIMA del click: il tasto
  // disabilitato senza spiegazione si legge come "non funziona".
  const keyMissing = !!meta && meta.needs_key && !hasKey && apiKey.trim() === "";
  const baseUrlMissing = !!meta && meta.needs_base_url && baseUrl.trim() === "";
  const consentMissing = !!meta && meta.kind === "cloud" && !consent;
  const canSave = !busy && !!meta && !keyMissing && !baseUrlMissing && !consentMissing;

  if (!loaded) {
    return (
      <div className="mp-overlay" onClick={onClose}>
        <div className="mp-card" onClick={(e) => e.stopPropagation()}>
          <div className="mp-title">Modello</div>
          {loadError ? (
            <>
              <div className="err">{loadError}</div>
              <p className="mp-note">
                Le impostazioni del modello non si sono caricate. Se il problema
                continua dopo Riprova, esci e accedi di nuovo.
              </p>
              <div className="mp-actions">
                <button type="button" className="ghost" onClick={onClose}>Annulla</button>
                <button type="button" className="primary" onClick={load}>Riprova</button>
              </div>
            </>
          ) : (
            <p className="mp-note">Caricamento…</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="mp-overlay" onClick={onClose}>
      <div className="mp-card" onClick={(e) => e.stopPropagation()}>
        <div className="mp-title">Modello</div>
        <div className="mp-sub">Scegli come ATLAS genera le risposte. La chiave API resta sul tuo PC, cifrata.</div>

        <div className="mp-providers">
          {UI_ORDER.map((p) => (
            <button key={p.id} type="button"
                    className={`mp-provider ${ui === p.id ? "sel" : ""}`}
                    onClick={() => { setUi(p.id); setError(null); }}>
              <span className="mp-provider-name">{p.title}</span>
              <span className="mp-provider-sub">{p.sub}</span>
            </button>
          ))}
        </div>

        {ui === "ollama" && (
          <div className="mp-ollama-mode">
            <button type="button" className={`ghost ${ollamaMode === "local" ? "sel" : ""}`}
                    onClick={() => setOllamaMode("local")}>Locale</button>
            <button type="button" className={`ghost ${ollamaMode === "cloud" ? "sel" : ""}`}
                    onClick={() => setOllamaMode("cloud")}>Cloud</button>
          </div>
        )}

        {backend !== "extractive" && (
          <>
            <input value={model} onChange={(e) => setModel(e.target.value)}
                   placeholder={meta?.default_model ? `Cerca o scrivi il modello (default: ${meta.default_model})` : "Cerca o scrivi il modello"}
                   spellCheck={false} />
            {filtered.length > 0 && (
              <div className="mp-models">
                {filtered.map((m) => (
                  <button key={m} type="button" className="mp-model" onClick={() => setModel(m)}>{m}</button>
                ))}
              </div>
            )}
          </>
        )}

        {needsBaseUrl && (
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                 placeholder="Base URL (es. https://api.openai.com/v1)" spellCheck={false} />
        )}

        {needsKey && (
          <>
            <input type="password" value={apiKey}
                   onChange={(e) => { setApiKey(e.target.value); setKeyTouched(true); }}
                   placeholder={hasKey ? "Chiave salvata - scrivi per sostituirla, svuota e salva per rimuoverla" : "La tua API key"}
                   spellCheck={false} autoComplete="off" />
            {hasKey && !keyTouched && <div className="mp-note">Chiave salvata (cifrata, mai mostrata).</div>}
            {keyUnusable && !keyTouched && (
              <div className="mp-note mp-warn" role="alert">
                La chiave migrata non e&apos; decifrabile su questa installazione e non
                viene usata: reinseriscila qui per riattivare il provider.
              </div>
            )}
          </>
        )}

        {isCloud && (
          <label className="mp-consent">
            <input type="checkbox" checked={consent} onChange={(e) => { const v = e.target.checked; setConsent(v); setConsentFor(v ? dest : null); }} />
            <span>Ho capito che le mie domande e le memorie usate per rispondere saranno
              inviate a {meta?.label} e che l'uso viene addebitato sul MIO account {meta?.label},
              secondo i suoi prezzi.</span>
          </label>
        )}

        {error && <div className="err">{error}</div>}

        {meta && !canSave && !busy && (
          <div className="mp-note" role="note">
            Per salvare: {""}
            {[
              keyMissing ? `inserisci la tua API key ${meta.label} (resta cifrata su questo PC)` : null,
              baseUrlMissing ? "inserisci la base URL del servizio" : null,
              consentMissing ? "spunta la casella del consenso qui sopra" : null,
            ].filter(Boolean).join("; ")}.
          </div>
        )}

        <div className="mp-actions">
          <button type="button" className="ghost" onClick={onClose}>Annulla</button>
          <button type="button" className="primary" disabled={!canSave} onClick={save}>
            {busy ? "Salvataggio..." : "Salva"}
          </button>
        </div>
      </div>
    </div>
  );
}
