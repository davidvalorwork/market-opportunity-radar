package whatsapp

import (
	"context"
	"io"
	"maps"
	"slices"
	"strconv"
	"sync"
	"time"

	"radar.local/radar/internal/contract"
)

// FakeClient is an in-memory Client: no network, no whatsmeow, synthetic data only.
// Its pending list and Chats stand in for the session SQLite of wameow.
type FakeClient struct {
	JID            string
	Code           string
	Messages       []contract.Message       // incoming: the next Sync receives (and "acks") them into the pending list
	Chats          map[string]contract.Chat // chats known to the session, by ref
	NotOnWhatsApp  map[string]bool          // phones ResolveChat reports as not registered
	SyncFail       error                    // Sync receives Messages, then returns this instead of reaching OfflineSyncCompleted
	BlockSync      bool                     // Sync receives Messages, then blocks until ctx ends (deadline first): ErrTimeout
	Err            map[string]error         // injected per call name: connect, pair, wait, sync, ack, has_chat, list_chats, resolve, send, logout, snapshot
	BlockWait      bool                     // WaitPaired blocks until ctx ends
	PanicAfterSend bool                     // simulates a crash after the provider accepted the message
	Calls          map[string]int
	WaitDeadline   time.Time

	pending  []Pending
	seq      int64
	resolved map[string]string // phone -> ref, so a phone keeps one ref
}

func (f *FakeClient) call(ctx context.Context, name string) error {
	if f.Calls == nil {
		f.Calls = map[string]int{}
	}
	f.Calls[name]++
	if err := f.Err[name]; err != nil {
		return err
	}
	return ctx.Err()
}

func (f *FakeClient) Connect(ctx context.Context) error { return f.call(ctx, "connect") }

func (f *FakeClient) PairPhone(ctx context.Context, _ string) (string, error) {
	return f.Code, f.call(ctx, "pair")
}

func (f *FakeClient) WaitPaired(ctx context.Context) (string, error) {
	f.WaitDeadline, _ = ctx.Deadline()
	if err := f.call(ctx, "wait"); err != nil {
		return "", err
	}
	if f.BlockWait {
		<-ctx.Done()
		return "", ctx.Err()
	}
	return f.JID, nil
}

func (f *FakeClient) Sync(ctx context.Context, enabled []string, limit int) ([]Pending, bool, error) {
	if err := f.call(ctx, "sync"); err != nil {
		return nil, false, err
	}
	for _, m := range f.Messages { // received and acked to the provider: from now on only the pending list holds them
		f.seq++
		f.pending = append(f.pending, Pending{Cursor(f.seq), m})
		f.known(m.ChatRef, &m.ObservedAt)
	}
	f.Messages = nil
	if f.BlockSync {
		<-ctx.Done()
		return nil, false, ErrTimeout
	}
	if f.SyncFail != nil {
		return nil, false, f.SyncFail
	}
	f.pending = slices.DeleteFunc(f.pending, func(p Pending) bool { return !slices.Contains(enabled, p.ChatRef) })
	n := min(limit, len(f.pending))
	return slices.Clone(f.pending[:n]), n < len(f.pending), nil
}

func (f *FakeClient) known(ref string, at *contract.Time) {
	if f.Chats == nil {
		f.Chats = map[string]contract.Chat{}
	}
	c := f.Chats[ref]
	c.ChatRef = ref
	if at != nil && (c.LastMessageAt == nil || at.After(c.LastMessageAt.Time)) {
		t := *at
		c.LastMessageAt = &t
	}
	f.Chats[ref] = c
}

// Pending returns a copy of the messages still held for a later page (test helper).
func (f *FakeClient) Pending() []Pending { return slices.Clone(f.pending) }

func (f *FakeClient) Ack(ctx context.Context, cursor string) error {
	if err := f.call(ctx, "ack"); err != nil {
		return err
	}
	seq, ok := CursorSeq(cursor)
	if !ok || seq > f.seq {
		return ErrBadCursor
	}
	f.pending = slices.DeleteFunc(f.pending, func(p Pending) bool { n, _ := CursorSeq(p.Cursor); return n <= seq })
	return nil
}

func (f *FakeClient) HasChat(ctx context.Context, ref string) (bool, error) {
	if err := f.call(ctx, "has_chat"); err != nil {
		return false, err
	}
	_, ok := f.Chats[ref]
	return ok, nil
}

func (f *FakeClient) ListChats(ctx context.Context, after string, limit int) ([]contract.Chat, bool, error) {
	if err := f.call(ctx, "list_chats"); err != nil {
		return nil, false, err
	}
	var out []contract.Chat
	for _, ref := range slices.Sorted(maps.Keys(f.Chats)) {
		if ref > after {
			out = append(out, f.Chats[ref])
		}
	}
	n := min(limit, len(out))
	return out[:n], n < len(out), nil
}

// ResolveChat simulates IsOnWhatsApp: found unless the phone is in NotOnWhatsApp. Never sends.
func (f *FakeClient) ResolveChat(ctx context.Context, phone string) (string, error) {
	if err := f.call(ctx, "resolve"); err != nil {
		return "", err
	}
	if f.NotOnWhatsApp[phone] {
		return "", ErrNotOnWhatsApp
	}
	if ref, ok := f.resolved[phone]; ok {
		return ref, nil
	}
	if f.resolved == nil {
		f.resolved = map[string]string{}
	}
	ref := "wachat:resolved-" + strconv.Itoa(len(f.resolved)+1) + "x"
	f.resolved[phone] = ref
	f.known(ref, nil)
	return ref, nil
}

func (f *FakeClient) Send(ctx context.Context, _, _ string) (string, error) {
	if err := f.call(ctx, "send"); err != nil {
		return "", err
	}
	if f.PanicAfterSend {
		panic("fault injection: crash after send")
	}
	return "fake-msg-" + strconv.Itoa(f.Calls["send"]), nil
}

func (f *FakeClient) Logout(ctx context.Context) error { return f.call(ctx, "logout") }
func (f *FakeClient) Close() error                     { return f.call(context.Background(), "close") }

func (f *FakeClient) Snapshot(w io.Writer) error {
	if err := f.call(context.Background(), "snapshot"); err != nil {
		return err
	}
	_, err := w.Write([]byte("SQLite format 3\x00synthetic-fixture"))
	return err
}

type lease struct {
	owner string
	token int64
	until time.Time
}

// MemLeases is an in-memory LeaseStore with monotonic tokens.
type MemLeases struct {
	mu        sync.Mutex
	next      int64
	held      map[string]lease
	FailCheck bool // simulates the lease expiring between claim and send
}

func (m *MemLeases) Acquire(_ context.Context, ref, owner string, ttl time.Duration) (int64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.held == nil {
		m.held = map[string]lease{}
	}
	if l, ok := m.held[ref]; ok && l.owner != owner && time.Now().Before(l.until) {
		return 0, ErrConflict
	}
	m.next++
	m.held[ref] = lease{owner, m.next, time.Now().Add(ttl)}
	return m.next, nil
}

func (m *MemLeases) Check(_ context.Context, ref, owner string, token int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	l, ok := m.held[ref]
	if m.FailCheck || !ok || l.owner != owner || l.token != token || !time.Now().Before(l.until) {
		return ErrLeaseLost
	}
	return nil
}

func (m *MemLeases) Release(_ context.Context, ref, owner string, token int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if l, ok := m.held[ref]; ok && l.owner == owner && l.token == token {
		delete(m.held, ref)
	}
	return nil
}

// Held reports whether any owner holds ref (test helper).
func (m *MemLeases) Held(ref string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	_, ok := m.held[ref]
	return ok
}

// MemLedger is an in-memory Ledger with conditional transitions.
type MemLedger struct {
	mu              sync.Mutex
	Records         map[string]Record
	AfterTransition func(op string, to State) // fault injection hook, called after the write
}

func (m *MemLedger) Get(_ context.Context, op string) (Record, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	rec, ok := m.Records[op]
	if !ok {
		return Record{}, ErrNotFound
	}
	return rec, nil
}

func (m *MemLedger) Transition(_ context.Context, op string, from, to State, meta Meta) error {
	m.mu.Lock()
	rec, ok := m.Records[op]
	if !ok || rec.State != from {
		m.mu.Unlock()
		return ErrConflict
	}
	rec.State = to
	if meta.LeaseToken != 0 {
		rec.LeaseToken = meta.LeaseToken
	}
	if meta.ProviderMessageID != "" {
		rec.ProviderMessageID = meta.ProviderMessageID
	}
	m.Records[op] = rec
	m.mu.Unlock()
	if m.AfterTransition != nil {
		m.AfterTransition(op, to)
	}
	return nil
}

// MemSessions is an in-memory SessionStore with version CAS. Like a real store it fails
// on an ended ctx; Err simulates the snapshot blob store failing, Block a store that hangs.
type MemSessions struct {
	mu       sync.Mutex
	Versions map[string]int
	Err      error
	Block    bool
}

func (m *MemSessions) Save(ctx context.Context, ref string, expected int, _ []byte) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Err != nil {
		return 0, m.Err
	}
	if m.Block {
		<-ctx.Done()
	}
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	if m.Versions == nil {
		m.Versions = map[string]int{}
	}
	if m.Versions[ref] != expected {
		return 0, ErrConflict
	}
	m.Versions[ref] = expected + 1
	return expected + 1, nil
}

// MemBlobs is an in-memory BlobStore. Tests may edit Blobs directly to tamper with a blob.
type MemBlobs struct {
	mu     sync.Mutex
	Blobs  map[string][]byte
	PutErr error
}

func (m *MemBlobs) Put(_ context.Context, key string, ct []byte) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.PutErr != nil {
		return m.PutErr
	}
	if m.Blobs == nil {
		m.Blobs = map[string][]byte{}
	}
	m.Blobs[key] = append([]byte(nil), ct...)
	return nil
}

func (m *MemBlobs) Get(_ context.Context, key string) ([]byte, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	ct, ok := m.Blobs[key]
	if !ok {
		return nil, ErrNotFound
	}
	return append([]byte(nil), ct...), nil
}

// FakeNotifier records delivered codes in memory (tests only inspect them).
type FakeNotifier struct {
	Codes []string
	Err   error
}

func (f *FakeNotifier) DeliverCode(_ context.Context, _, code string) error {
	if f.Err != nil {
		return f.Err
	}
	f.Codes = append(f.Codes, code)
	return nil
}
