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
	"unicode/utf8"

	"go.mau.fi/whatsmeow/proto/waCompanionReg"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
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
	if _, _, err := c.Sync(context.Background(), []string{"wachat:cany"}, 10); !errors.Is(err, whatsapp.ErrStreamReplaced) {
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
	page, more, err := c.Sync(context.Background(), refs(t, c, seller, other), 100)
	if err != nil || more || len(page) != 3 {
		t.Fatalf("sync %v %v %d", err, more, len(page))
	}
	msgs := messages(page)
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
	page, more, err := c.Sync(ctx, refs(t, c, seller), 10)
	if !errors.Is(err, whatsapp.ErrTimeout) || more || page != nil {
		t.Fatalf("deadline sync: %v %v %v", page, more, err)
	}
	if err := c.wait(ctx, &c.paired); !errors.Is(err, whatsapp.ErrTimeout) {
		t.Fatalf("wait after deadline: %v", err)
	}
}

// B7b: a message stored (and so acked) before the deadline is in the snapshot the worker
// takes after a timed-out Sync, and pages out of the restored session.
func TestSnapshotAfterSyncDeadlineKeepsPending(t *testing.T) {
	c, _ := newClient(t)
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("uno")}, false))
	enabled := refs(t, c, seller)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, _, err := c.Sync(ctx, enabled, 10); !errors.Is(err, whatsapp.ErrTimeout) {
		t.Fatalf("deadline sync: %v", err)
	}
	var buf bytes.Buffer
	if err := c.Snapshot(&buf); err != nil {
		t.Fatal(err)
	}
	dir2 := t.TempDir()
	os.WriteFile(filepath.Join(dir2, DBFile), buf.Bytes(), 0o600)
	c2, err := New(context.Background(), dir2)
	if err != nil {
		t.Fatal(err)
	}
	defer c2.Close()
	page, more, err := pendingPage(context.Background(), c2.db, enabled, 10)
	if err != nil || more || len(page) != 1 || page[0].Text != "uno" {
		t.Fatalf("restored pending %+v %v %v", page, more, err)
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
	msgs, _, _ := c.Sync(context.Background(), refs(t, c, seller), 10)
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
	// Not acknowledged yet: the message is still pending after the snapshot round trip (gap a).
	again, more, err := pendingPage(context.Background(), c2.db, []string{msgs[0].ChatRef}, 10)
	if err != nil || more || len(again) != 1 || again[0] != msgs[0] {
		t.Fatalf("restored pending %+v %v %v", again, more, err)
	}
}

// refs returns the session refs of the given chats (created on first use, stable after).
func refs(t *testing.T, c *Client, chats ...types.JID) []string {
	t.Helper()
	out := make([]string, len(chats))
	for i, chat := range chats {
		ref, err := c.chatRef(context.Background(), chat)
		if err != nil {
			t.Fatal(err)
		}
		out[i] = ref
	}
	return out
}

func messages(page []whatsapp.Pending) []contract.Message {
	out := make([]contract.Message, len(page))
	for i, p := range page {
		out[i] = p.Message
	}
	return out
}

// Paging: a page smaller than what arrived keeps the rest pending (nothing is lost after
// WhatsApp got its ack); Ack removes only what the cursor covers; non-enabled chats are dropped.
func TestPendingPagingAndAck(t *testing.T) {
	c, _ := newClient(t)
	other := types.NewJID("10000000002", types.DefaultUserServer)
	for _, txt := range []string{"uno", "dos", "tres"} {
		c.handle(msg(seller, &waE2E.Message{Conversation: proto.String(txt)}, false))
		c.handle(msg(other, &waE2E.Message{Conversation: proto.String("ajeno-" + txt)}, false))
	}
	c.handle(&events.OfflineSyncCompleted{})
	ctx := context.Background()
	enabled := refs(t, c, seller)
	page, more, err := c.Sync(ctx, enabled, 2)
	if err != nil || !more || len(page) != 2 || page[0].Text != "uno" || page[1].Text != "dos" {
		t.Fatalf("first page %+v %v %v", page, more, err)
	}
	var left int
	c.db.QueryRow(`SELECT count(*) FROM radar_pending WHERE chat_ref <> ?`, enabled[0]).Scan(&left)
	if left != 0 {
		t.Fatalf("%d messages of a non-enabled chat kept", left)
	}
	// Redelivery of the same page without an ack returns the same messages.
	if again, _, _ := pendingPage(ctx, c.db, enabled, 2); len(again) != 2 || again[0] != page[0] {
		t.Fatalf("unacked page changed: %+v", again)
	}
	if err := c.Ack(ctx, page[1].Cursor); err != nil {
		t.Fatal(err)
	}
	rest, more, err := pendingPage(ctx, c.db, enabled, 2)
	if err != nil || more || len(rest) != 1 || rest[0].Text != "tres" {
		t.Fatalf("second page %+v %v %v", rest, more, err)
	}
	for _, bad := range []string{"p999", "x1", "p0", "p01", ""} {
		if err := c.Ack(ctx, bad); !errors.Is(err, whatsapp.ErrBadCursor) {
			t.Fatalf("cursor %q: %v", bad, err)
		}
	}
	if err := c.Ack(ctx, page[1].Cursor); err != nil { // replayed cursor: idempotent
		t.Fatal(err)
	}
}

func TestListChatsAndHasChat(t *testing.T) {
	c, _ := newClient(t)
	ctx := context.Background()
	other := types.NewJID("10000000002", types.DefaultUserServer)
	c.handle(msg(seller, &waE2E.Message{Conversation: proto.String("uno")}, false))
	c.handle(msg(other, &waE2E.Message{Conversation: proto.String("dos")}, false))
	own := types.NewJID("10000000000", types.DefaultUserServer)
	c.cli.Store.Contacts = sqlstore.NewSQLStore(c.container, own) // a paired device has a contact store; synthetic, offline
	c.db.SetMaxOpenConns(1)                                       // the pragma below applies to this one connection
	c.db.Exec(`PRAGMA foreign_keys = OFF`)                        // no whatsmeow_device row without real pairing
	if _, _, err := c.cli.Store.Contacts.PutPushName(ctx, seller, "Tienda Sintetica"); err != nil {
		t.Fatal(err)
	}
	c.db.Exec(`PRAGMA foreign_keys = ON`)
	resolved, err := c.chatRef(ctx, types.NewJID("10000000003", types.DefaultUserServer)) // as ResolveChat stores it
	if err != nil {
		t.Fatal(err)
	}
	var all []contract.Chat
	after := ""
	for {
		chats, more, err := c.ListChats(ctx, after, 1)
		if err != nil || len(chats) != 1 {
			t.Fatalf("list %v %v", chats, err)
		}
		all = append(all, chats...)
		after = chats[0].ChatRef
		if !more {
			break
		}
	}
	if len(all) != 3 {
		t.Fatalf("chats %+v", all)
	}
	names := map[string]string{}
	for _, ch := range all {
		names[ch.ChatRef] = ch.DisplayName
		if strings.Contains(ch.ChatRef, "1000000000") || (ch.ChatRef == resolved) != (ch.LastMessageAt == nil) {
			t.Fatalf("chat %+v", ch)
		}
	}
	if names[refs(t, c, seller)[0]] != "Tienda Sintetica" || names[resolved] != "" {
		t.Fatalf("names %v", names)
	}
	if _, err := contract.DecodeChatsPrivate(mustJSON(t, contract.ChatsPrivate{SchemaVersion: 1, Chats: all})); err != nil {
		t.Fatal(err)
	}
	if ok, _ := c.HasChat(ctx, resolved); !ok {
		t.Fatal("resolved chat unknown")
	}
	if ok, _ := c.HasChat(ctx, "wachat:cunknown"); ok {
		t.Fatal("unknown chat reported known")
	}
}

func TestDisplayNameTruncated(t *testing.T) {
	if got := displayName(types.ContactInfo{PushName: strings.Repeat("ñ", 200)}); utf8.RuneCountInString(got) != 128 {
		t.Fatalf("len %d", utf8.RuneCountInString(got))
	}
	if got := displayName(types.ContactInfo{FullName: "Full", PushName: "Push"}); got != "Full" {
		t.Fatal(got)
	}
}

// IsOnWhatsApp answers are mapped offline; the call itself needs a connected session.
func TestRegisteredJID(t *testing.T) {
	pn := types.NewJID("10000000004", types.DefaultUserServer)
	lid := types.NewJID("200000000000004", types.HiddenUserServer)
	cases := []struct {
		name string
		resp []types.IsOnWhatsAppResponse
		want types.JID
		ok   bool
	}{
		{"registered with pn", []types.IsOnWhatsAppResponse{{JID: lid, PhoneNumber: pn, IsIn: true}}, pn, true},
		{"registered without pn", []types.IsOnWhatsAppResponse{{JID: pn, IsIn: true}}, pn, true},
		{"not registered", []types.IsOnWhatsAppResponse{{JID: pn, IsIn: false}}, types.JID{}, false},
		{"no answer", nil, types.JID{}, false},
		{"two answers for one phone", []types.IsOnWhatsAppResponse{{JID: pn, IsIn: true}, {JID: pn, IsIn: true}}, types.JID{}, false},
	}
	for _, c := range cases {
		if got, ok := registeredJID(c.resp); ok != c.ok || got != c.want {
			t.Fatalf("%s: %v %v", c.name, got, ok)
		}
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
