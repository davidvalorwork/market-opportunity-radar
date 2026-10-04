package whatsapp

import (
	"context"
	"io"
	"strconv"
	"sync"
	"time"

	"radar.local/radar/internal/contract"
)

// FakeClient is an in-memory Client: no network, no whatsmeow, synthetic data only.
type FakeClient struct {
	JID            string
	Code           string
	Messages       []contract.Message
	SyncIncomplete bool             // never reaches OfflineSyncCompleted
	Err            map[string]error // injected per call name: connect, pair, wait, sync, send, logout, snapshot
	BlockWait      bool             // WaitPaired blocks until ctx ends
	PanicAfterSend bool             // simulates a crash after the provider accepted the message
	Calls          map[string]int
	WaitDeadline   time.Time
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

func (f *FakeClient) Sync(ctx context.Context, _ string) ([]contract.Message, bool, error) {
	if err := f.call(ctx, "sync"); err != nil {
		return nil, false, err
	}
	return f.Messages, !f.SyncIncomplete, nil
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

// MemSessions is an in-memory SessionStore with version CAS.
type MemSessions struct {
	mu       sync.Mutex
	Versions map[string]int
}

func (m *MemSessions) Save(_ context.Context, ref string, expected int, _ []byte) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Versions == nil {
		m.Versions = map[string]int{}
	}
	if m.Versions[ref] != expected {
		return 0, ErrConflict
	}
	m.Versions[ref] = expected + 1
	return expected + 1, nil
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
