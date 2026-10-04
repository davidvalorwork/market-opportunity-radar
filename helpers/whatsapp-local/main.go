// Local private IPC bridge. Authority and durable approval belong to the host,
// never to a seeded MemLedger, user payload, CLI fake or a provider message.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"radar.local/radar/internal/whatsapp"
	"radar.local/radar/internal/whatsapp/wameow"
)

const maxFrame = 256 << 10

var phonePattern = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)

type request struct {
	Version        int             `json:"v"`
	ID             string          `json:"id"`
	Owner          string          `json:"owner_ref"`
	Account        string          `json:"account_ref"`
	Session        string          `json:"session_ref"`
	SessionVersion int             `json:"session_version"`
	Deadline       time.Time       `json:"deadline"`
	Method         string          `json:"method"`
	Body           json.RawMessage `json:"body"`
}

type response struct {
	Version int    `json:"v"`
	ID      string `json:"id"`
	Event   string `json:"event"`
	Body    any    `json:"body,omitempty"`
	Code    string `json:"code,omitempty"`
}

// All frames use private stdio pipes. No logger, stderr body, phone argv or files.
type channel struct {
	input  *bufio.Scanner
	output *json.Encoder
	id     string
}

func decode(data []byte, target any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(target); err != nil {
		return errors.New("invalid_input")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("invalid_input")
	}
	return nil
}

func (c *channel) emit(event string, body any) error {
	return c.output.Encode(response{Version: 1, ID: c.id, Event: event, Body: body})
}

func (c *channel) gate(event string, body any) error {
	if err := c.emit(event, body); err != nil {
		return err
	}
	if !c.input.Scan() {
		return errors.New("host_gate_unavailable")
	}
	var ack struct {
		Version int    `json:"v"`
		ID      string `json:"id"`
		Event   string `json:"event"`
		Allowed bool   `json:"allowed"`
	}
	if decode(c.input.Bytes(), &ack) != nil || ack.Version != 1 || ack.ID != c.id || ack.Event != "continue" || !ack.Allowed {
		return errors.New("host_gate_rejected")
	}
	return nil
}

func binding(r request) string {
	data, _ := json.Marshal([]any{r.Owner, r.Account, r.Session, r.SessionVersion, r.Method, r.Body})
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func journal(ctx context.Context, dir string, r request) (*sql.DB, string, error) {
	db, err := sql.Open("sqlite", filepath.Join(dir, "bridge.db")+"?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)")
	if err != nil {
		return nil, "", err
	}
	if _, err = db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS binding(owner TEXT,account TEXT,session TEXT,PRIMARY KEY(owner));
CREATE TABLE IF NOT EXISTS operations(owner TEXT,id TEXT,hash TEXT,state TEXT,provider TEXT,PRIMARY KEY(owner,id));`); err != nil {
		db.Close()
		return nil, "", err
	}
	if _, err = db.ExecContext(ctx, `CREATE UNIQUE INDEX IF NOT EXISTS bridge_single_binding ON binding((1))`); err != nil {
		db.Close()
		return nil, "", err
	}
	// One directory is bound to a single canonical owner/account/session forever.
	if _, err = db.ExecContext(ctx, `INSERT INTO binding SELECT ?,?,? WHERE NOT EXISTS(SELECT 1 FROM binding)`, r.Owner, r.Account, r.Session); err != nil {
		db.Close()
		return nil, "", err
	}
	var owner, account, session string
	if err = db.QueryRowContext(ctx, `SELECT owner,account,session FROM binding`).Scan(&owner, &account, &session); err != nil || owner != r.Owner || account != r.Account || session != r.Session {
		db.Close()
		return nil, "", errors.New("session_binding")
	}
	return db, binding(r), nil
}

func run(ctx context.Context, client whatsapp.Client, r request, c *channel, db *sql.DB, hash string) (any, error) {
	filter, ok := client.(interface {
		SetEnabledChats(context.Context, []string) error
	})
	if !ok {
		return nil, errors.New("capture_filter_unavailable")
	}
	if err := filter.SetEnabledChats(ctx, nil); err != nil {
		return nil, err
	}
	switch r.Method {
	case "pair":
		var body struct {
			Phone string `json:"phone"`
		}
		if decode(r.Body, &body) != nil || !phonePattern.MatchString(body.Phone) {
			return nil, errors.New("invalid_input")
		}
		if err := c.gate("preflight", nil); err != nil {
			return nil, err
		}
		if err := client.Connect(ctx); err != nil {
			return nil, err
		}
		code, err := client.PairPhone(ctx, body.Phone)
		if err != nil {
			return nil, err
		}
		if err = c.gate("pair_code", map[string]string{"code": code}); err != nil {
			return nil, err
		}
		jid, err := client.WaitPaired(ctx)
		if err != nil {
			return nil, err
		}
		if strings.TrimPrefix(body.Phone, "+") != jid {
			client.Logout(ctx)
			return nil, errors.New("paired_identity_mismatch")
		}
		return map[string]bool{"paired": true}, nil
	case "list_chats":
		var body struct {
			After string `json:"after"`
			Limit int    `json:"limit"`
		}
		if decode(r.Body, &body) != nil || body.Limit < 1 || body.Limit > 100 {
			return nil, errors.New("invalid_input")
		}
		if err := c.gate("preflight", nil); err != nil {
			return nil, err
		}
		selfClient, ok := client.(interface {
			SelfChatRef(context.Context) (string, error)
		})
		if !ok {
			return nil, errors.New("session_identity_unavailable")
		}
		self, err := selfClient.SelfChatRef(ctx)
		if err != nil {
			return nil, err
		}
		chats, more, err := client.ListChats(ctx, body.After, body.Limit)
		own := map[string]any{"chat_ref": self, "display_name": "", "is_self": true}
		others := make([]map[string]any, 0, len(chats))
		for _, chat := range chats {
			item := map[string]any{"chat_ref": chat.ChatRef, "display_name": chat.DisplayName, "is_self": chat.ChatRef == self}
			if chat.LastMessageAt != nil {
				item["last_message_at"] = chat.LastMessageAt
			}
			if chat.ChatRef == self {
				own = item
			} else {
				others = append(others, item)
			}
		}
		// Authenticated self is an identity anchor, not part of an exhaustive
		// cursor view. It counts toward the bound even when outside this page.
		items := append([]map[string]any{own}, others...)
		if len(items) > body.Limit {
			items = items[:body.Limit]
			more = true
		}
		return map[string]any{"chats": items, "has_more": more}, err
	case "sync":
		var body struct {
			Enabled []string `json:"enabled"`
			Limit   int      `json:"limit"`
			Ack     string   `json:"ack"`
		}
		if decode(r.Body, &body) != nil || body.Limit < 1 || body.Limit > 100 || len(body.Enabled) < 1 || len(body.Enabled) > 100 {
			return nil, errors.New("invalid_input")
		}
		if err := filter.SetEnabledChats(ctx, body.Enabled); err != nil {
			return nil, err
		}
		if err := c.gate("preflight", nil); err != nil {
			return nil, err
		}
		if body.Ack != "" {
			if err := client.Ack(ctx, body.Ack); err != nil {
				return nil, err
			}
		}
		if err := client.Connect(ctx); err != nil {
			return nil, err
		}
		page, more, err := client.Sync(ctx, body.Enabled, body.Limit)
		if err != nil {
			return nil, err
		}
		// Provider identity accessor is deliberately outside the published Message schema.
		ids, ok := client.(interface {
			ProviderMessageID(context.Context, string) (string, error)
		})
		if !ok {
			return nil, errors.New("provider_identity_unavailable")
		}
		messages := make([]map[string]any, 0, len(page))
		total := 0
		for _, p := range page {
			if len(p.Text) > 4096 || total+len(p.Text) > 65536 {
				if len(messages) == 0 {
					return nil, errors.New("private_message_limit")
				}
				more = true
				break
			}
			id, err := ids.ProviderMessageID(ctx, p.Cursor)
			if err != nil {
				return nil, err
			}
			messages = append(messages, map[string]any{"cursor": p.Cursor, "chat_ref": p.ChatRef, "provider_message_id": id, "text": p.Text, "observed_at": p.ObservedAt})
			total += len(p.Text)
		}
		return map[string]any{"messages": messages, "has_more": more}, nil
	case "send":
		var body struct {
			Chat        string `json:"chat_ref"`
			Text        string `json:"text"`
			ContentHash string `json:"content_hash"`
			Purpose     string `json:"purpose_ref"`
		}
		if decode(r.Body, &body) != nil || len(body.Text) < 1 || len(body.Text) > 4096 || body.Chat == "" || body.Purpose == "" {
			return nil, errors.New("invalid_input")
		}
		sum := sha256.Sum256([]byte(body.Text))
		if hex.EncodeToString(sum[:]) != body.ContentHash {
			return nil, errors.New("content_binding")
		}
		if err := c.gate("preflight", nil); err != nil {
			return nil, err
		}
		var previous, state, provider string
		err := db.QueryRowContext(ctx, `SELECT hash,state,provider FROM operations WHERE owner=? AND id=?`, r.Owner, r.ID).Scan(&previous, &state, &provider)
		if err == nil {
			if previous != hash {
				return nil, errors.New("operation_binding")
			}
			return map[string]string{"state": state, "provider_message_id": provider}, nil
		}
		if err != sql.ErrNoRows {
			return nil, err
		}
		selfClient, ok := client.(interface {
			SelfChatRef(context.Context) (string, error)
		})
		if !ok {
			return nil, errors.New("session_identity_unavailable")
		}
		self, err := selfClient.SelfChatRef(ctx)
		if err != nil {
			return nil, err
		}
		if body.Chat == self {
			// Capture only real provider echoes of this explicitly approved own
			// chat, installed before Connect. Send's response is never an inbox row.
			if err = filter.SetEnabledChats(ctx, []string{self}); err != nil {
				return nil, err
			}
		}
		known, err := client.HasChat(ctx, body.Chat)
		if err != nil || !known {
			return nil, errors.New("unknown_chat")
		}
		if err = client.Connect(ctx); err != nil {
			return nil, err
		}
		if err = c.gate("effect_gate", nil); err != nil {
			return nil, err
		}
		// Host already committed its A3 ledger claim; this second journal is durable
		// independently of host result persistence and never retries an uncertain send.
		if _, err = db.ExecContext(ctx, `INSERT INTO operations VALUES(?,?,?,'send_uncertain','')`, r.Owner, r.ID, hash); err != nil {
			return nil, err
		}
		provider, err = client.Send(ctx, body.Chat, body.Text)
		if err != nil {
			return nil, err
		}
		if provider == "" {
			return nil, errors.New("response_unavailable")
		}
		if _, err = db.ExecContext(ctx, `UPDATE operations SET state='provider_confirmed',provider=? WHERE owner=? AND id=? AND hash=?`, provider, r.Owner, r.ID, hash); err != nil {
			return nil, err
		}
		return map[string]string{"state": "provider_confirmed", "provider_message_id": provider}, nil
	default:
		return nil, errors.New("unsupported")
	}
}

func main() {
	dir := flag.String("session-dir", "", "Private operator-provisioned session directory")
	allow := flag.Bool("authorize-network", false, "Explicit host authorization for this helper invocation")
	flag.Parse()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), maxFrame)
	out := json.NewEncoder(os.Stdout)
	if !scanner.Scan() {
		return
	}
	var r request
	if decode(scanner.Bytes(), &r) != nil || r.Version != 1 || r.ID == "" || r.Owner == "" || r.Account == "" || r.Session == "" || r.SessionVersion < 1 || r.Deadline.IsZero() || r.Deadline.Location() != time.UTC || !time.Now().Before(r.Deadline) || time.Until(r.Deadline) > 150*time.Second {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "invalid_input"})
		return
	}
	if r.Method != "list_chats" && !*allow {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "live_disabled"})
		return
	}
	ctx, cancel := context.WithDeadline(context.Background(), r.Deadline)
	defer cancel()
	db, hash, err := journal(ctx, *dir, r)
	if err != nil {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "storage_or_binding_failed"})
		return
	}
	defer db.Close()
	client, err := wameow.New(ctx, *dir)
	if err != nil {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "session_unavailable"})
		return
	}
	defer client.Close()
	c := &channel{scanner, out, r.ID}
	body, err := run(ctx, client, r, c, db, hash)
	if err != nil {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "operation_uncertain"})
		return
	}
	if ctx.Err() != nil {
		out.Encode(response{Version: 1, ID: r.ID, Event: "error", Code: "operation_uncertain"})
		return
	}
	out.Encode(response{Version: 1, ID: r.ID, Event: "result", Body: body})
}
