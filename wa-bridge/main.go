// atlas-wa-bridge: ponte WhatsApp (linked device) per ATLAS.
//
// Modello di sicurezza:
//   - solo loopback, token condiviso in header X-Bridge-Token (generato da
//     chi avvia il processo);
//   - il pairing e' sempre e solo un'azione esplicita dell'utente: il QR
//     viene mostrato nella app e l'utente lo inquadra da WhatsApp;
//   - catturiamo e rispondiamo SOLO nella chat con se stessi (self-chat):
//     mai risposte automatiche ad altre persone;
//   - la sessione vive in un SQLite locale (schema whatsmeow); il file e' un
//     segreto bearer: non esce dal dispositivo.
//
// API HTTP (JSON):
//   GET  /healthz           -> {"ok":true,"linked":bool,"jid":"...","pushname":"..."}
//   POST /pair              -> avvia il flusso QR (no-op se gia' collegato)
//   GET  /qr                -> {"state":"qr","qr":"2@..."} | {"state":"paired"} | {"state":"expired"}
//   POST /send {"text":...} -> invia nella self-chat
//   GET  /events?after=N    -> {"events":[{"seq":N,"text":"...","ts":...}]}
//   POST /disconnect        -> logout e uscita (il chiamante cancella il db)
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	_ "modernc.org/sqlite"
	"google.golang.org/protobuf/proto"
)

type bridge struct {
	mu      sync.Mutex
	client  *whatsmeow.Client
	qr      string // ultimo codice QR ricevuto (testo), ruota ogni ~20s
	qrState string // idle | qr | paired | expired
	events  []inboundEvent
	nextSeq int
	seenIDs  map[string]bool // dedup replay/history/reconnect per ID messaggio
	sentByUs map[string]bool // ID dei messaggi inviati da QUESTO bridge (anti-eco)
	bootID   string          // casuale a ogni avvio: il backend azzera l'offset
	dbPath   string          // per il file .seen.json
	allowDMs bool            // -allow-dms: cattura anche i DM 1:1, non solo la self-chat
}

type inboundEvent struct {
	Seq    int    `json:"seq"`
	ID     string `json:"id"`
	Text   string `json:"text"`
	Ts     int64  `json:"ts"`
	From   string `json:"from"`    // numero del mittente (self-chat = account collegato)
	FromMe bool   `json:"from_me"` // sempre false dopo il filtro; il backend ricontrolla
}

func (b *bridge) handleEvent(evt interface{}) {
	switch v := evt.(type) {
	case *events.Connected:
		b.mu.Lock()
		b.qrState = "paired"
		b.qr = ""
		b.mu.Unlock()
	case *events.LoggedOut:
		b.mu.Lock()
		b.qrState = "expired"
		b.mu.Unlock()
	case *events.Message:
		// Anti-eco mirato: mai riemettere i messaggi inviati da QUESTO
		// bridge (le risposte di ATLAS finirebbero in loop). I messaggi
		// del proprietario dal telefono sono anch'essi IsFromMe=true ma
		// NON sono nel nostro insieme: vanno elaborati, sono l'input.
		b.mu.Lock()
		ours := b.sentByUs[v.Info.ID]
		b.mu.Unlock()
		if ours {
			return
		}
		if b.client == nil || b.client.Store.ID == nil {
			return
		}
		own := *b.client.Store.ID
		isSelfChat := v.Info.Chat == v.Info.Sender && v.Info.Chat.User == own.User
		if !isSelfChat {
			// Default v2.x: solo self-chat, tutto il resto mai catturato.
			// Con -allow-dms anche i DM 1:1 (chat utente, mai gruppi/canali)
			// diventano input: l'allowlist dei numeri resta CENTRALE nel
			// backend ATLAS, che decide chi riceve risposta.
			if !b.allowDMs {
				return
			}
			if v.Info.Chat.Server != types.DefaultUserServer {
				return // gruppi, canali, broadcast: mai
			}
			if v.Info.IsFromMe {
				return // l'account del ponte che scrive dal telefono non e' input
			}
		}
		text := v.Message.GetConversation()
		if ext := v.Message.GetExtendedTextMessage(); ext != nil {
			text = ext.GetText()
		}
		text = strings.TrimSpace(text)
		if text == "" {
			return
		}
		b.mu.Lock()
		if b.seenIDs[v.Info.ID] {
			b.mu.Unlock()
			return // replay/history/reconnect: ogni messaggio una volta sola
		}
		b.seenIDs[v.Info.ID] = true
		if len(b.seenIDs) > 2000 {
			b.seenIDs = map[string]bool{v.Info.ID: true}
		}
		b.saveSeenLocked()
		b.events = append(b.events, inboundEvent{Seq: b.nextSeq, ID: v.Info.ID, Text: text, Ts: v.Info.Timestamp.Unix(), From: v.Info.Sender.User, FromMe: v.Info.IsFromMe})
		b.nextSeq++
		if len(b.events) > 500 {
			b.events = append([]inboundEvent{}, b.events[len(b.events)-500:]...)
		}
		b.mu.Unlock()
	}
}

func (b *bridge) startPairing() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.client.IsLoggedIn() {
		b.qrState = "paired"
		return nil
	}
	qrChan, _ := b.client.GetQRChannel(context.Background())
	if err := b.client.Connect(); err != nil {
		return err
	}
	b.qrState = "qr"
	go func() {
		for evt := range qrChan {
			b.mu.Lock()
			switch evt.Event {
			case "code":
				b.qr = evt.Code
				b.qrState = "qr"
			case "success", "timeout", "error":
				if evt.Event == "success" {
					b.qrState = "paired"
				} else {
					b.qrState = "expired"
				}
				b.qr = ""
			}
			b.mu.Unlock()
		}
	}()
	return nil
}

// seenIDs persistito su disco (file accanto al db di sessione, JSON, max
// 2000): dopo un riavvio del bridge un messaggio gia' consegnato non viene
// rielaborato come nuovo input.
func (b *bridge) seenPath() string { return b.dbPath + ".seen.json" }

func (b *bridge) loadSeen() {
	data, err := os.ReadFile(b.seenPath())
	if err != nil {
		return
	}
	var ids []string
	if json.Unmarshal(data, &ids) != nil {
		return
	}
	for _, id := range ids {
		b.seenIDs[id] = true
	}
}

func (b *bridge) saveSeenLocked() {
	ids := make([]string, 0, len(b.seenIDs))
	for id := range b.seenIDs {
		ids = append(ids, id)
	}
	if len(ids) > 2000 {
		ids = ids[len(ids)-2000:]
	}
	data, err := json.Marshal(ids)
	if err != nil {
		return
	}
	_ = os.WriteFile(b.seenPath(), data, 0o600)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func main() {
	addr := flag.String("addr", "127.0.0.1:18741", "bind loopback")
	dbPath := flag.String("db", "wa-session.db", "sqlite sessione whatsmeow")
	token := flag.String("token", "", "token condiviso richiesto in X-Bridge-Token")
	allowDMs := flag.Bool("allow-dms", false, "cattura anche i DM 1:1 oltre alla self-chat (l'allowlist resta nel backend)")
	flag.Parse()
	if *token == "" {
		fmt.Fprintln(os.Stderr, "manca -token")
		os.Exit(2)
	}

	container, err := sqlstore.New(context.Background(), "sqlite",
		"file:"+*dbPath+"?_pragma=foreign_keys(1)", waLog.Noop)
	if err != nil {
		fmt.Fprintln(os.Stderr, "sqlstore:", err)
		os.Exit(1)
	}
	b := &bridge{qrState: "idle", nextSeq: 1, seenIDs: map[string]bool{},
		sentByUs: map[string]bool{}, bootID: fmt.Sprintf("%d", time.Now().UnixNano()),
		dbPath: *dbPath, allowDMs: *allowDMs}
	b.loadSeen()
	b.client = whatsmeow.NewClient(container.NewDevice(), waLog.Noop)
	b.client.AddEventHandler(b.handleEvent)

	// Sessione esistente: riconnessione automatica senza QR.
	if b.client.Store.ID != nil {
		if err := b.client.Connect(); err != nil {
			fmt.Fprintln(os.Stderr, "connect:", err)
			os.Exit(1)
		}
		b.qrState = "paired"
	}

	auth := func(h http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-Bridge-Token") != *token {
				writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
				return
			}
			h(w, r)
		}
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", auth(func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		out := map[string]any{"ok": true, "linked": b.client.IsLoggedIn(), "boot": b.bootID}
		if b.client.Store.ID != nil {
			out["jid"] = b.client.Store.ID.String()
			out["pushname"] = b.client.Store.PushName
		}
		writeJSON(w, http.StatusOK, out)
	}))
	mux.HandleFunc("POST /pair", auth(func(w http.ResponseWriter, r *http.Request) {
		if err := b.startPairing(); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"state": b.qrState})
	}))
	mux.HandleFunc("GET /qr", auth(func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]string{"state": b.qrState, "qr": b.qr})
	}))
	// Pairing con codice numerico invece del QR: il codice va digitato sul
	// telefono del NUMERO DELL'AGENTE (Dispositivi collegati > Collega con
	// numero di telefono). Utile quando il bridge non gira sullo stesso
	// schermo di chi scansiona.
	mux.HandleFunc("POST /pair-code", auth(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Phone string `json:"phone"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.Phone) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "numero mancante"})
			return
		}
		if b.client.IsLoggedIn() {
			writeJSON(w, http.StatusConflict, map[string]string{"error": "gia' collegato"})
			return
		}
		if !b.client.IsConnected() {
			if err := b.client.Connect(); err != nil {
				writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
				return
			}
		}
		code, err := b.client.PairPhone(context.Background(), strings.TrimSpace(body.Phone), true,
			whatsmeow.PairClientChrome, "Chrome (Linux)")
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		b.mu.Lock()
		b.qrState = "qr" // in attesa di conferma dal telefono
		b.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]string{"code": code})
	}))
	mux.HandleFunc("POST /send", auth(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Text string `json:"text"`
			To   string `json:"to"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.Text) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "testo mancante"})
			return
		}
		if !b.client.IsLoggedIn() || b.client.Store.ID == nil {
			writeJSON(w, http.StatusConflict, map[string]string{"error": "non collegato"})
			return
		}
		// Default: self-chat. "to" (solo numero, con o senza +) risponde in DM:
		// il backend lo usa per rispondere al mittente del messaggio in ingresso.
		target := types.NewJID(b.client.Store.ID.User, types.DefaultUserServer)
		if to := strings.TrimSpace(strings.TrimPrefix(body.To, "+")); to != "" {
			target = types.NewJID(to, types.DefaultUserServer)
		}
		msg := &waE2E.Message{Conversation: proto.String(body.Text)}
		// ID preassegnato e registrato PRIMA dell'invio: se l'eco del nostro
		// messaggio arriva prima della risposta di rete, e' gia' filtrato.
		msgID := b.client.GenerateMessageID()
		b.mu.Lock()
		b.sentByUs[string(msgID)] = true
		if len(b.sentByUs) > 2000 {
			b.sentByUs = map[string]bool{string(msgID): true}
		}
		b.mu.Unlock()
		if _, err := b.client.SendMessage(context.Background(), target, msg,
			whatsmeow.SendRequestExtra{ID: msgID}); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"sent": true, "id": string(msgID)})
	}))
	mux.HandleFunc("GET /events", auth(func(w http.ResponseWriter, r *http.Request) {
		after := 0
		fmt.Sscanf(r.URL.Query().Get("after"), "%d", &after)
		b.mu.Lock()
		defer b.mu.Unlock()
		out := []inboundEvent{}
		for _, e := range b.events {
			if e.Seq > after {
				out = append(out, e)
			}
		}
		writeJSON(w, http.StatusOK, map[string]any{"events": out, "boot": b.bootID})
	}))
	mux.HandleFunc("POST /disconnect", auth(func(w http.ResponseWriter, r *http.Request) {
		if b.client.IsLoggedIn() {
			if err := b.client.Logout(context.Background()); err != nil {
				writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
				return
			}
		}
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
		go func() { time.Sleep(200 * time.Millisecond); os.Exit(0) }()
	}))

	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintln(os.Stderr, "http:", err)
		os.Exit(1)
	}
}
