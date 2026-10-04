package wameow

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waCompanionReg"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/whatsapp"
)

// All tests are offline: New never dials; only Connect does, and no test calls it.
// Phone numbers are synthetic (+10000000000 range).

func newClient(t *testing.T) (*Client, string) {
	t.Helper()
	dir := t.TempDir()
	c, err := New(context.Background(), dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	return c, dir
}

func msg(chat types.JID, m *waE2E.Message, fromMe bool) *events.Message {
	return &events.Message{
		Info:    types.MessageInfo{MessageSource: types.MessageSource{Chat: chat, Sender: chat, IsFromMe: fromMe}, ID: "SYNTH1", Timestamp: time.Date(2026, 10, 3, 12, 0, 0, 0, time.UTC)},
		Message: m,
	}
}

var seller = types.NewJID("10000000001", types.DefaultUserServer)

func TestNewRequiresSessionDir(t *testing.T) {
	for _, dir := range []string{"", filepath.Join(t.TempDir(), "missing")} {
		if _, err := New(context.Background(), dir); !errors.Is(err, ErrNoSessionDir) {
			t.Fatalf("dir %q: %v", dir, err)
		}
	}
	f := filepath.Join(t.TempDir(), "file")
	os.WriteFile(f, nil, 0o600)
	if _, err := New(context.Background(), f); !errors.Is(err, ErrNoSessionDir) {
		t.Fatalf("file as dir: %v", err)
	}
}

func TestConfig(t *testing.T) {
	c, _ := newClient(t)
	if !regexp.MustCompile(`^(Chrome|Firefox|Edge|Safari|Opera) \((Linux|Windows|Mac OS)\)$`).MatchString(DisplayName) {
		t.Fatalf("display name %q not in Browser (OS) form", DisplayName)
	}
	if ClientType != "1" || store.DeviceProps.GetPlatformType() != waCompanionReg.DeviceProps_CHROME || store.DeviceProps.GetRequireFullSync() {
		t.Fatal("pair client type / device props not Chrome without full sync")
	}
	if !c.cli.ManualHistorySyncDownload || !c.cli.SynchronousAck {
		t.Fatal("history download must stay manual (never called) and acks synchronous")
	}
	var fk bool
	if c.db.QueryRow(`PRAGMA foreign_keys`).Scan(&fk); !fk {
		t.Fatal("foreign keys off")
	}
}

func TestMapEvent(t *testing.T) {
	cases := []struct {
		evt  any
		want error
	}{
		{&events.LoggedOut{}, whatsapp.ErrLoggedOut},
		{&events.LoggedOut{OnConnect: true, Reason: events.ConnectFailureLoggedOut}, whatsapp.ErrLoggedOut},
		{&events.StreamReplaced{}, whatsapp.ErrStreamReplaced},
		{&events.ConnectFailure{Reason: events.ConnectFailureServiceUnavailable}, whatsapp.ErrTimeout},
		{&events.PairError{}, errPair},
		{&events.TemporaryBan{}, errBanned},
		{&events.ClientOutdated{}, errOutdated},
		{&events.Connected{}, nil},
		{&events.OfflineSyncCompleted{}, nil},
		{&events.HistorySync{}, nil},
	}
	for _, c := range cases {
		if got := mapEvent(c.evt); !errors.Is(got, c.want) || (c.want == nil) != (got == nil) {
			t.Fatalf("%T: got %v want %v", c.evt, got, c.want)
		}
	}
}

func TestFatalEventsEndWaits(t *testing.T) {
	// Sentinels reach the result through the worker's existing toError mapping (needs_reauth, session_conflict).
	c, _ := newClient(t)
	c.handle(&events.StreamReplaced{})
	c.handle(&events.LoggedOut{}) // the first fatal event wins
	if _, _, err := c.Sync(context.Background(), ""); !errors.Is(err, whatsapp.ErrStreamReplaced) {
		t.Fatalf("sync after StreamReplaced: %v", err)
	}
	c2, _ := newClient(t)
	c2.handle(&events.LoggedOut{})
	if _, err := c2.WaitPaired(context.Background()); !errors.Is(err, whatsapp.ErrLoggedOut) {
		t.Fatalf("wait after LoggedOut: %v", err)
	}
}

func TestExtract(t *testing.T) {
	group := types.NewJID("120363000000000001", types.GroupServer)
	cases := []struct {
		name string
		evt  *events.Message
		text string
	}{
		{"conversation", msg(seller, &waE2E.Message{Conversation: proto.String("hola")}, false), "hola"},
		{"extended text", msg(seller, &waE2E.Message{ExtendedTextMessage: &waE2E.ExtendedTextMessage{Text: proto.String("precio?")}}, false), "precio?"},
		{"group text", msg(group, &waE2E.Message{Conversation: proto.String("g")}, false), "g"},
		{"image ignored", msg(seller, &waE2E.Message{ImageMessage: &waE2E.ImageMessage{Caption: proto.String("cap")}}, false), ""},
		{"from me ignored", msg(seller, &waE2E.Message{Conversation: proto.String("mine")}, true), ""},
		{"status ignored", msg(types.StatusBroadcastJID, &waE2E.Message{Conversation: proto.String("s")}, false), ""},
		{"nil message", &events.Message{}, ""},
	}
	for _, c := range cases {
		chat, text, at, ok := extract(c.evt)
		if text != c.text || ok != (c.text != "") {
			t.Fatalf("%s: got %q %v", c.name, text, ok)
		}
		if ok && (at.Location() != time.UTC || at.IsZero() || chat.Device != 0) {
			t.Fatalf("%s: bad time/chat", c.name)
		}
	}
	ad := msg(types.NewADJID("10000000001", 0, 7), &waE2E.Message{Conversation: proto.String("x")}, false)
	if chat, _, _, _ := extract(ad); chat != seller {
		t.Fatal("device part not stripped from chat")
	}
}

func TestMessagesRefsAndSync(t *testing.T) {
	c, _ := newClient(t)
	other := types.NewJID("10000000002", types.DefaultUserServer)
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("uno")}, false))
	c.handle(msg(other, &waE2E.Message{Conversation: proto.String("dos")}, false))
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("tres")}, false))
	c.handle(msg(seller, &waE2E.Message{ImageMessage: &waE2E.ImageMessage{}}, false))
	c.handle(&events.OfflineSyncCompleted{Count: 3})
	msgs, done, err := c.Sync(context.Background(), "")
	if err != nil || !done || len(msgs) != 3 {
		t.Fatalf("sync %v %v %d", err, done, len(msgs))
	}
	if msgs[0].ChatRef != msgs[2].ChatRef || msgs[0].ChatRef == msgs[1].ChatRef {
		t.Fatal("refs not stable per chat")
	}
	// What the adapter emits must pass the consumer's decoder, and refs carry no phone digits.
	if _, err := contract.DecodeMessagesPrivate(mustJSON(t, contract.MessagesPrivate{SchemaVersion: 1, Messages: msgs})); err != nil {
		t.Fatal(err)
	}
	for _, m := range msgs {
		if strings.Contains(m.ChatRef, "1000000000") || !strings.HasPrefix(m.ChatRef, "wachat:") {
			t.Fatalf("ref %q", m.ChatRef)
		}
	}
	if jid, err := c.jidFor(context.Background(), msgs[0].ChatRef); err != nil || jid != seller {
		t.Fatalf("reverse lookup %v %v", jid, err)
	}
	// Unknown ref is refused before any network call (none is possible here).
	if _, err := c.Send(context.Background(), "wachat:cunknown", "x"); !errors.Is(err, ErrUnknownChat) {
		t.Fatalf("send unknown ref: %v", err)
	}
}

func TestSyncDeadline(t *testing.T) {
	c, _ := newClient(t)
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("uno")}, false))
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	msgs, done, err := c.Sync(ctx, "")
	if err != nil || done || msgs != nil {
		t.Fatalf("deadline sync: %v %v %v", msgs, done, err)
	}
	if err := c.wait(ctx, &c.paired); !errors.Is(err, whatsapp.ErrTimeout) {
		t.Fatalf("wait after deadline: %v", err)
	}
}

func TestWaitPairedReturnsJIDUser(t *testing.T) {
	c, _ := newClient(t)
	c.handle(&events.PairSuccess{ID: types.NewADJID("10000000000", 0, 12), LID: types.NewJID("200000000000001", types.HiddenUserServer)})
	c.handle(&events.Connected{})
	user, err := c.WaitPaired(context.Background())
	if err != nil || user != "10000000000" {
		t.Fatalf("got %q %v", user, err)
	}
}

func TestPairPhoneRefusesPairedSession(t *testing.T) {
	c, _ := newClient(t)
	c.cli.Store.ID = &seller
	if _, err := c.PairPhone(context.Background(), "+10000000000"); !errors.Is(err, ErrAlreadyPaired) {
		t.Fatal(err)
	}
}

func TestSnapshotFromWAL(t *testing.T) {
	dir := t.TempDir()
	live := filepath.Join(dir, "live.db")
	db, err := sql.Open("sqlite", live+"?_pragma=journal_mode(WAL)&_pragma=wal_autocheckpoint(0)")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1) // keep the connection (and its WAL) open while copying
	if _, err := db.Exec(`CREATE TABLE t (v TEXT); INSERT INTO t VALUES ('a'), ('b'), ('c')`); err != nil {
		t.Fatal(err)
	}
	if st, err := os.Stat(live + "-wal"); err != nil || st.Size() == 0 {
		t.Fatalf("expected pending WAL: %v", err)
	}
	var buf bytes.Buffer
	if err := snapshotDB(db, dir, &buf); err != nil {
		t.Fatal(err)
	}
	if left, _ := filepath.Glob(filepath.Join(dir, "snapshot-*")); len(left) != 0 {
		t.Fatalf("temp snapshot left behind: %v", left)
	}
	// A naive copy of the live main file alone misses the WAL rows: why VACUUM INTO.
	raw, _ := os.ReadFile(live)
	if count(t, raw) == 3 {
		t.Fatal("naive copy unexpectedly complete; test does not exercise the WAL")
	}
	if n := count(t, buf.Bytes()); n != 3 {
		t.Fatalf("snapshot rows %d", n)
	}
}

func TestSnapshotRoundTrip(t *testing.T) {
	c, dir := newClient(t)
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("uno")}, false))
	c.handle(&events.OfflineSyncCompleted{})
	msgs, _, _ := c.Sync(context.Background(), "")
	var buf bytes.Buffer
	if err := c.Snapshot(&buf); err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(buf.Bytes(), []byte("SQLite format 3\x00")) {
		t.Fatal("not a SQLite file")
	}
	if left, _ := filepath.Glob(filepath.Join(dir, "snapshot-*")); len(left) != 0 {
		t.Fatalf("temp snapshot left behind: %v", left)
	}
	// Restore into a fresh dir with only the single file: no -wal needed.
	dir2 := t.TempDir()
	os.WriteFile(filepath.Join(dir2, DBFile), buf.Bytes(), 0o600)
	c2, err := New(context.Background(), dir2)
	if err != nil {
		t.Fatal(err)
	}
	defer c2.Close()
	if jid, err := c2.jidFor(context.Background(), msgs[0].ChatRef); err != nil || jid != seller {
		t.Fatalf("restored mapping %v %v", jid, err)
	}
}

func count(t *testing.T, file []byte) int {
	t.Helper()
	p := filepath.Join(t.TempDir(), "copy.db")
	os.WriteFile(p, file, 0o600)
	db, err := sql.Open("sqlite", p)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var n int
	if db.QueryRow(`SELECT count(*) FROM t`).Scan(&n) != nil {
		return -1
	}
	return n
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}
