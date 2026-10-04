// Package wameow implements whatsapp.Client over go.mau.fi/whatsmeow with a pure-Go
// SQLite store (modernc.org/sqlite, CGO_ENABLED=0). It never logs: whatsmeow gets a
// no-op logger and this package records only states in memory, never text, phones or JIDs.
package wameow

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCompanionReg"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
	_ "modernc.org/sqlite" // registers driver "sqlite"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/whatsapp"
)

const (
	// DBFile is the protocol SQLite inside the caller's session directory.
	DBFile = "whatsmeow.db"
	// DisplayName must be "Browser (OS)" with a common browser/OS or the server answers 400 (B-F021).
	DisplayName = "Chrome (Linux)"
	ClientType  = whatsmeow.PairClientChrome
	// PostPairWait bounds the wait for the reconnect whatsmeow does after PairSuccess.
	PostPairWait = 30 * time.Second
	chatPrefix   = "wachat:c" // opaque refs need a letter in the value
	// tsLayout keeps nanoseconds fixed-width so stored timestamps compare as text (max()).
	tsLayout = "2006-01-02T15:04:05.000000000Z"
)

var (
	ErrNoSessionDir  = errors.New("session directory required")
	ErrAlreadyPaired = errors.New("session already paired")
	ErrUnknownChat   = errors.New("chat ref not known to this session")
	errPair          = errors.New("pairing failed locally")
	errBanned        = errors.New("temporary_ban")
	errOutdated      = errors.New("client_outdated")
)

var _ whatsapp.Client = (*Client)(nil)

type Client struct {
	dir       string
	db        *sql.DB
	container *sqlstore.Container
	cli       *whatsmeow.Client

	ready, connected, paired, offline, dead latch

	mu            sync.Mutex
	fatal         error
	pairedUser    string
	closeOnce     sync.Once
	captureFilter bool
	enabledChats  map[string]bool
}

// latch is a one-shot broadcast: set closes ch once.
type latch struct {
	ch   chan struct{}
	once sync.Once
}

func newLatch() latch { return latch{ch: make(chan struct{})} }
func (l *latch) set() { l.once.Do(func() { close(l.ch) }) }

func dsn(path string) string {
	return path + "?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)"
}

// New opens (or creates) dir/whatsmeow.db. dir must already exist: in Lambda it is
// /tmp restored from the age blob; New never picks a location by itself.
func New(ctx context.Context, dir string) (*Client, error) {
	if dir == "" {
		return nil, ErrNoSessionDir
	}
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		return nil, ErrNoSessionDir
	}
	db, err := sql.Open("sqlite", dsn(filepath.Join(dir, DBFile)))
	if err != nil {
		return nil, err
	}
	container := sqlstore.NewWithDB(db, "sqlite", waLog.Noop)
	if err := container.Upgrade(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("session store: %w", err)
	}
	// radar_chat_ref: opaque ref <-> JID. radar_chat_seen: last incoming message per chat (list_chats).
	// radar_pending: received (already acked to WhatsApp) messages kept until a cursor acknowledges
	// them, so a page that does not fit never loses messages. All travel inside the encrypted snapshot.
	if _, err := db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS radar_chat_ref (ref TEXT PRIMARY KEY, jid TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS radar_chat_seen (ref TEXT PRIMARY KEY, last_message_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS radar_pending (seq INTEGER PRIMARY KEY AUTOINCREMENT, chat_ref TEXT NOT NULL, text TEXT NOT NULL, observed_at TEXT NOT NULL)`); err != nil {
		db.Close()
		return nil, err
	}
	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		db.Close()
		return nil, err
	}
	store.DeviceProps.PlatformType = waCompanionReg.DeviceProps_CHROME.Enum()
	store.DeviceProps.RequireFullSync = proto.Bool(false)
	cli := whatsmeow.NewClient(device, waLog.Noop)
	cli.ManualHistorySyncDownload = true // never download history blobs (privacy); the receipt is still sent
	cli.SynchronousAck = true            // ack a message only after handle() stored it
	c := &Client{dir: dir, db: db, container: container, cli: cli,
		ready: newLatch(), connected: newLatch(), paired: newLatch(), offline: newLatch(), dead: newLatch()}
	cli.AddEventHandler(c.handle)
	return c, nil
}

func (c *Client) fail(err error) {
	c.mu.Lock()
	if c.fatal == nil {
		c.fatal = err
	}
	c.mu.Unlock()
	c.dead.set()
}

// mapEvent returns the sentinel error for session-ending events, nil otherwise.
func mapEvent(evt any) error {
	switch evt.(type) {
	case *events.LoggedOut:
		return whatsapp.ErrLoggedOut
	case *events.StreamReplaced:
		return whatsapp.ErrStreamReplaced
	case *events.ConnectFailure:
		return fmt.Errorf("%w: connect failure", whatsapp.ErrTimeout)
	case *events.PairError:
		return errPair
	case *events.TemporaryBan:
		return errBanned
	case *events.ClientOutdated:
		return errOutdated
	}
	return nil
}

func (c *Client) handle(evt any) {
	if err := mapEvent(evt); err != nil {
		c.fail(err)
		return
	}
	switch e := evt.(type) {
	case *events.QR: // unpaired socket is up; the QR codes themselves are ignored, never stored
		c.ready.set()
	case *events.Connected:
		c.ready.set()
		c.connected.set()
	case *events.PairSuccess:
		c.mu.Lock()
		c.pairedUser = e.ID.User
		c.mu.Unlock()
		c.paired.set()
	case *events.OfflineSyncCompleted:
		c.offline.set()
	case *events.Message:
		// Provider IDs remain private in a local journal; public Message is unchanged.
		if err := c.capture(context.Background(), e); err != nil {
			c.mu.Lock()
			strict := c.captureFilter
			c.mu.Unlock()
			if strict {
				c.fail(errors.New("message_capture_failed"))
			}
		}
	}
	// events.HistorySync never arrives (ManualHistorySyncDownload) and would be ignored anyway.
}

// extract keeps incoming plain or extended text from direct/group chats; media,
// reactions, own messages and status/broadcast are ignored.
func extract(e *events.Message) (chat types.JID, text string, at time.Time, ok bool) {
	if e == nil || e.Message == nil || e.Info.IsFromMe || e.Info.Chat.Server == types.BroadcastServer {
		return chat, "", at, false
	}
	text = e.Message.GetConversation()
	if text == "" {
		text = e.Message.GetExtendedTextMessage().GetText()
	}
	if text == "" {
		return chat, "", at, false
	}
	at = e.Info.Timestamp.UTC()
	if e.Info.Timestamp.IsZero() {
		at = time.Now().UTC()
	}
	return e.Info.Chat.ToNonAD(), text, at, true
}

// chatRef maps a chat JID to a stable random opaque ref kept in the session DB, so
// refs travel with the encrypted snapshot and never contain phone digits.
func (c *Client) chatRef(ctx context.Context, chat types.JID) (string, error) {
	if chat.Server == types.HiddenUserServer && c.cli.Store.LIDs != nil {
		if pn, err := c.cli.Store.LIDs.GetPNForLID(ctx, chat); err == nil && !pn.IsEmpty() {
			chat = pn.ToNonAD()
		}
	}
	b := make([]byte, 12)
	rand.Read(b)
	if _, err := c.db.ExecContext(ctx, `INSERT INTO radar_chat_ref (ref, jid) VALUES (?, ?) ON CONFLICT (jid) DO NOTHING`, chatPrefix+hex.EncodeToString(b), chat.String()); err != nil {
		return "", err
	}
	var ref string
	return ref, c.db.QueryRowContext(ctx, `SELECT ref FROM radar_chat_ref WHERE jid = ?`, chat.String()).Scan(&ref)
}

// store keeps one incoming message pending and records when its chat last wrote.
func (c *Client) store(ctx context.Context, chat types.JID, text string, at time.Time) error {
	ref, err := c.chatRef(ctx, chat)
	if err != nil {
		return err
	}
	ts := at.UTC().Format(tsLayout)
	if _, err := c.db.ExecContext(ctx, `INSERT INTO radar_pending (chat_ref, text, observed_at) VALUES (?, ?, ?)`, ref, text, ts); err != nil {
		return err
	}
	_, err = c.db.ExecContext(ctx, `INSERT INTO radar_chat_seen (ref, last_message_at) VALUES (?, ?)
ON CONFLICT (ref) DO UPDATE SET last_message_at = max(last_message_at, excluded.last_message_at)`, ref, ts)
	return err
}

func (c *Client) jidFor(ctx context.Context, ref string) (types.JID, error) {
	var s string
	if err := c.db.QueryRowContext(ctx, `SELECT jid FROM radar_chat_ref WHERE ref = ?`, ref).Scan(&s); err != nil {
		return types.JID{}, ErrUnknownChat
	}
	return types.ParseJID(s)
}

// wait blocks until ch closes, a session-ending event arrives or ctx ends (timeout).
func (c *Client) wait(ctx context.Context, l *latch) error {
	select {
	case <-l.ch:
		return nil
	case <-c.dead.ch:
		c.mu.Lock()
		defer c.mu.Unlock()
		return c.fatal
	case <-ctx.Done():
		return fmt.Errorf("%w: %v", whatsapp.ErrTimeout, ctx.Err())
	}
}

// Connect dials and waits until the socket is usable: QR event when unpaired
// (PairPhone may follow at once), Connected when the stored session logged in.
func (c *Client) Connect(ctx context.Context) error {
	if err := c.cli.ConnectContext(ctx); err != nil {
		return fmt.Errorf("%w: connect: %v", whatsapp.ErrTimeout, err)
	}
	return c.wait(ctx, &c.ready)
}

func (c *Client) PairPhone(ctx context.Context, phone string) (string, error) {
	if c.cli.Store.ID != nil {
		return "", ErrAlreadyPaired
	}
	return c.cli.PairPhone(ctx, phone, true, ClientType, DisplayName)
}

// WaitPaired returns the paired account's types.JID.User (phone digits, no device or server).
func (c *Client) WaitPaired(ctx context.Context) (string, error) {
	if err := c.wait(ctx, &c.paired); err != nil {
		return "", err
	}
	// whatsmeow reconnects after PairSuccess; give it a bounded chance before the snapshot.
	pctx, cancel := context.WithTimeout(ctx, PostPairWait)
	defer cancel()
	c.wait(pctx, &c.connected)
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.pairedUser, nil
}

// Sync waits for OfflineSyncCompleted, then disconnects before reading the pending table:
// with SynchronousAck, anything handled after that cannot be acked and is redelivered next
// time (at-least-once; a message may repeat, it should not be lost). Messages of chats not
// in enabled are deleted (never returned, never kept); the oldest limit of the rest are
// returned and stay pending until Ack.
func (c *Client) Sync(ctx context.Context, enabled []string, limit int) ([]whatsapp.Pending, bool, error) {
	err := c.wait(ctx, &c.offline)
	c.cli.Disconnect()
	if err != nil {
		return nil, false, err // deadline: ErrTimeout; the worker still snapshots what was stored and reports timeout
	}
	return pendingPage(ctx, c.db, enabled, limit)
}

func pendingPage(ctx context.Context, db *sql.DB, enabled []string, limit int) ([]whatsapp.Pending, bool, error) {
	if len(enabled) == 0 {
		return nil, false, errors.New("no enabled chats")
	}
	args := make([]any, len(enabled))
	for i, e := range enabled {
		args[i] = e
	}
	in := "(?" + strings.Repeat(",?", len(enabled)-1) + ")"
	if _, err := db.ExecContext(ctx, `DELETE FROM radar_pending WHERE chat_ref NOT IN `+in, args...); err != nil {
		return nil, false, err
	}
	rows, err := db.QueryContext(ctx, `SELECT seq, chat_ref, text, observed_at FROM radar_pending ORDER BY seq LIMIT ?`, limit+1)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	var page []whatsapp.Pending
	for rows.Next() {
		var seq int64
		var p whatsapp.Pending
		var at string
		if err := rows.Scan(&seq, &p.ChatRef, &p.Text, &at); err != nil {
			return nil, false, err
		}
		t, err := time.Parse(time.RFC3339Nano, at)
		if err != nil {
			return nil, false, err
		}
		p.Cursor, p.ObservedAt = whatsapp.Cursor(seq), contract.Time{Time: t}
		page = append(page, p)
	}
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	if len(page) > limit {
		return page[:limit], true, nil
	}
	return page, false, nil
}

// Ack deletes pending messages up to cursor. A cursor beyond the highest sequence ever
// issued is refused. ponytail: an issued-but-unreturned cursor is accepted (consumers only
// echo next_cursor); track a returned high-water mark if cursors ever come from elsewhere.
func (c *Client) Ack(ctx context.Context, cursor string) error {
	seq, ok := whatsapp.CursorSeq(cursor)
	if !ok {
		return whatsapp.ErrBadCursor
	}
	var issued int64
	c.db.QueryRowContext(ctx, `SELECT seq FROM sqlite_sequence WHERE name = 'radar_pending'`).Scan(&issued)
	if seq > issued {
		return whatsapp.ErrBadCursor
	}
	_, err := c.db.ExecContext(ctx, `DELETE FROM radar_pending WHERE seq <= ?`, seq)
	return err
}

func (c *Client) HasChat(ctx context.Context, ref string) (bool, error) {
	_, err := c.jidFor(ctx, ref)
	return err == nil, nil
}

// ListChats reads radar_chat_ref; display names come from whatsmeow's contact store
// (saved contact name, else business or push name), never from the network. Group names
// are not stored locally and are left out.
func (c *Client) ListChats(ctx context.Context, after string, limit int) ([]contract.Chat, bool, error) {
	rows, err := c.db.QueryContext(ctx, `SELECT r.ref, r.jid, s.last_message_at FROM radar_chat_ref r
LEFT JOIN radar_chat_seen s ON s.ref = r.ref WHERE r.ref > ? ORDER BY r.ref LIMIT ?`, after, limit+1)
	if err != nil {
		return nil, false, err
	}
	type row struct {
		chat contract.Chat
		jid  string
	}
	var list []row
	for rows.Next() {
		var r row
		var at sql.NullString
		if err := rows.Scan(&r.chat.ChatRef, &r.jid, &at); err != nil {
			rows.Close()
			return nil, false, err
		}
		if at.Valid {
			t, err := time.Parse(time.RFC3339Nano, at.String)
			if err != nil {
				rows.Close()
				return nil, false, err
			}
			r.chat.LastMessageAt = &contract.Time{Time: t}
		}
		list = append(list, r)
	}
	rows.Close() // before the contact lookups below reuse the pool
	if err := rows.Err(); err != nil {
		return nil, false, err
	}
	more := len(list) > limit
	if more {
		list = list[:limit]
	}
	chats := make([]contract.Chat, len(list))
	for i, r := range list {
		chats[i] = r.chat
		if jid, err := types.ParseJID(r.jid); err == nil && jid.Server == types.DefaultUserServer && c.cli.Store.Contacts != nil {
			if info, err := c.cli.Store.Contacts.GetContact(ctx, jid); err == nil && info.Found {
				chats[i].DisplayName = displayName(info)
			}
		}
	}
	return chats, more, nil
}

func displayName(info types.ContactInfo) string {
	for _, n := range []string{info.FullName, info.FirstName, info.BusinessName, info.PushName} {
		if n = strings.TrimSpace(n); n != "" {
			if utf8.RuneCountInString(n) > 128 {
				n = string([]rune(n)[:128])
			}
			return n
		}
	}
	return ""
}

// ResolveChat asks WhatsApp whether phone has an account (IsOnWhatsApp, one number per
// call) and maps the answer to a stable chat ref. It sends no message.
func (c *Client) ResolveChat(ctx context.Context, phone string) (string, error) {
	resp, err := c.cli.IsOnWhatsApp(ctx, []string{phone})
	if err != nil {
		return "", err
	}
	jid, ok := registeredJID(resp)
	if !ok {
		return "", whatsapp.ErrNotOnWhatsApp
	}
	return c.chatRef(ctx, jid)
}

// registeredJID picks the chat JID from an IsOnWhatsApp answer: the phone-number JID when
// known (chatRef maps @lid to it anyway), else the canonical one.
func registeredJID(resp []types.IsOnWhatsAppResponse) (types.JID, bool) {
	if len(resp) != 1 || !resp[0].IsIn {
		return types.JID{}, false
	}
	jid := resp[0].PhoneNumber
	if jid.IsEmpty() {
		jid = resp[0].JID
	}
	return jid.ToNonAD(), !jid.IsEmpty()
}

func (c *Client) Send(ctx context.Context, chat, text string) (string, error) {
	jid, err := c.jidFor(ctx, chat)
	if err != nil {
		return "", err
	}
	resp, err := c.cli.SendMessage(ctx, jid, &waE2E.Message{Conversation: proto.String(text)})
	if err != nil {
		return "", err
	}
	return resp.ID, nil
}

// Logout unlinks the device on WhatsApp's servers and deletes it from the local store.
func (c *Client) Logout(ctx context.Context) error { return c.cli.Logout(ctx) }

func (c *Client) Close() error {
	var err error
	c.closeOnce.Do(func() {
		c.cli.Disconnect()
		err = c.container.Close()
	})
	return err
}

// Snapshot disconnects and streams a consistent single-file copy (VACUUM INTO),
// never the live file with a pending WAL.
func (c *Client) Snapshot(w io.Writer) error {
	c.cli.Disconnect()
	return snapshotDB(c.db, c.dir, w)
}

func snapshotDB(db *sql.DB, dir string, w io.Writer) error {
	b := make([]byte, 8)
	rand.Read(b)
	tmp := filepath.Join(dir, "snapshot-"+hex.EncodeToString(b)+".db")
	defer os.Remove(tmp)
	if _, err := db.Exec(`VACUUM INTO ?`, tmp); err != nil {
		return fmt.Errorf("snapshot: %w", err)
	}
	f, err := os.Open(tmp)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(w, f)
	return err
}
