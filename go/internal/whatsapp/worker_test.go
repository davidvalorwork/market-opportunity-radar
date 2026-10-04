package whatsapp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"radar.local/radar/internal/contract"
)

const ref = "whatsapp:fixture"

var text = "fixture text"

type fx struct {
	c        *FakeClient
	leases   *MemLeases
	ledger   *MemLedger
	sessions *MemSessions
	notes    *FakeNotifier
}

func newFx() *fx {
	return &fx{
		c:      &FakeClient{JID: "10000000000:7@s.whatsapp.net", Code: "FAKE1234"},
		leases: &MemLeases{},
		ledger: &MemLedger{Records: map[string]Record{"op-1": {
			State: Approved, OwnerRef: "owner-1", RecipientRef: "chat-1", ApprovalRef: "ap-1", ContentSHA256: contract.SHA256Hex(text),
		}}},
		sessions: &MemSessions{Versions: map[string]int{ref: 1}},
		notes:    &FakeNotifier{},
	}
}

func (f *fx) worker(owner string) *Worker {
	return &Worker{Client: f.c, Leases: f.leases, Ledger: f.ledger, Sessions: f.sessions, Notifier: f.notes, Owner: owner, LeaseTTL: time.Minute}
}

func envelope(kind string, version int) contract.Envelope {
	return contract.Envelope{SchemaVersion: 1, MessageID: "m-1", OperationID: "op-1", CorrelationID: "c-1", OwnerRef: "owner-1",
		Kind: kind, Deadline: time.Now().Add(time.Hour).UTC(), Attempt: 1, SessionRef: ref, ExpectedVersion: version}
}

func sendPayload() *contract.Send {
	return &contract.Send{RecipientRef: "chat-1", ApprovalRef: "ap-1", ContentSHA256: contract.SHA256Hex(text), Text: text}
}

// run executes one attempt and reports a simulated crash (panic) instead of a result.
func run(w *Worker, env contract.Envelope, p any) (r contract.Result, crashed bool) {
	defer func() {
		if recover() != nil {
			crashed = true
		}
	}()
	return w.Handle(context.Background(), env, p), false
}

func code(r contract.Result) string {
	if r.Error == nil {
		return ""
	}
	return r.Error.Code
}

func TestSend(t *testing.T) {
	cases := []struct {
		name      string
		setup     func(*fx, *contract.Envelope)
		crash     bool
		code      string
		state     State
		sends     int
		connected bool
	}{
		{name: "happy path", state: ProviderConfirmed, sends: 1, connected: true},
		{name: "lease lost before send", setup: func(f *fx, _ *contract.Envelope) { f.leases.FailCheck = true },
			code: contract.SendUncertain, state: SendUncertain, connected: true},
		{name: "crash after claim before send", setup: func(f *fx, _ *contract.Envelope) {
			f.ledger.AfterTransition = func(_ string, to State) {
				if to == DispatchCommitted {
					panic("fault injection: crash after claim")
				}
			}
		}, crash: true, state: DispatchCommitted, connected: true},
		{name: "crash after send before confirm", setup: func(f *fx, _ *contract.Envelope) { f.c.PanicAfterSend = true },
			crash: true, state: DispatchCommitted, sends: 1, connected: true},
		{name: "send timeout", setup: func(f *fx, _ *contract.Envelope) { f.c.Err = map[string]error{"send": ErrTimeout} },
			code: contract.SendUncertain, state: SendUncertain, sends: 1, connected: true},
		{name: "logged out during send", setup: func(f *fx, _ *contract.Envelope) { f.c.Err = map[string]error{"send": ErrLoggedOut} },
			code: contract.SendUncertain, state: SendUncertain, sends: 1, connected: true},
		{name: "logged out on connect keeps approval", setup: func(f *fx, _ *contract.Envelope) { f.c.Err = map[string]error{"connect": ErrLoggedOut} },
			code: contract.NeedsReauth, state: Approved, connected: true},
		{name: "stream replaced on connect", setup: func(f *fx, _ *contract.Envelope) { f.c.Err = map[string]error{"connect": ErrStreamReplaced} },
			code: contract.SessionConflict, state: Approved, connected: true},
		{name: "content hash mismatch", setup: func(f *fx, _ *contract.Envelope) {
			rec := f.ledger.Records["op-1"]
			rec.ContentSHA256 = contract.SHA256Hex("approved other text")
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "recipient mismatch", setup: func(f *fx, _ *contract.Envelope) {
			rec := f.ledger.Records["op-1"]
			rec.RecipientRef = "chat-2"
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "not approved", setup: func(f *fx, _ *contract.Envelope) {
			rec := f.ledger.Records["op-1"]
			rec.State = Proposed
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Proposed},
		{name: "expired deadline", setup: func(_ *fx, e *contract.Envelope) { e.Deadline = time.Now().Add(-time.Second).UTC() },
			code: contract.Timeout, state: Approved},
		{name: "lease held by another worker", setup: func(f *fx, _ *contract.Envelope) {
			f.leases.Acquire(context.Background(), ref, "worker-other", time.Minute)
		}, code: contract.SessionConflict, state: Approved},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			env := envelope(contract.KindSend, 1)
			if c.setup != nil {
				c.setup(f, &env)
			}
			r, crashed := run(f.worker("worker-a"), env, sendPayload())
			if crashed != c.crash || (!c.crash && code(r) != c.code) {
				t.Fatalf("crashed=%v result=%+v error=%+v", crashed, r, r.Error)
			}
			if got := f.ledger.Records["op-1"].State; got != c.state {
				t.Fatalf("ledger state %s, want %s", got, c.state)
			}
			if f.c.Calls["send"] != c.sends || (f.c.Calls["connect"] > 0) != c.connected {
				t.Fatalf("calls %v", f.c.Calls)
			}
			if !c.crash && f.leases.Held(ref) && c.name != "lease held by another worker" {
				t.Fatal("lease not released")
			}
			if c.code == "" && !c.crash && (r.ProviderMessageID == "" || r.SessionVersion != 2) {
				t.Fatalf("missing provider id or session version: %+v", r)
			}
			// A second delivery (another worker, faults cleared) must never send again once claimed.
			if c.state != Approved && c.state != Proposed {
				f.c.PanicAfterSend, f.c.Err, f.leases.FailCheck, f.ledger.AfterTransition = false, nil, false, nil
				r2, crashed2 := run(f.worker("worker-b"), envelope(contract.KindSend, 1), sendPayload())
				if crashed2 || f.c.Calls["send"] != c.sends {
					t.Fatalf("retry resent: calls %v", f.c.Calls)
				}
				if c.state == ProviderConfirmed {
					if r2.Status != contract.StatusSucceeded || r2.ProviderMessageID != r.ProviderMessageID {
						t.Fatalf("duplicate delivery result %+v", r2)
					}
				} else if code(r2) != contract.SendUncertain || f.ledger.Records["op-1"].State != SendUncertain {
					t.Fatalf("retry result %+v state %s", r2, f.ledger.Records["op-1"].State)
				}
			}
		})
	}
}

func TestPair(t *testing.T) {
	pair := &contract.Pair{DeclaredPhone: "+10000000000", Notify: "tg-chat-ref", Method: "code"}
	cases := []struct {
		name      string
		setup     func(*fx, *contract.Envelope, *contract.Pair)
		code      string
		delivered bool
		version   int
		logouts   int
	}{
		{name: "happy path", delivered: true, version: 1},
		{name: "wrong jid logs out", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) { f.c.JID = "19999999999@s.whatsapp.net" },
			code: contract.InvalidInput, delivered: true, logouts: 1},
		{name: "wrong jid and logout fails", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) {
			f.c.JID = "19999999999@s.whatsapp.net"
			f.c.Err = map[string]error{"logout": ErrTimeout}
		}, code: contract.Internal, delivered: true, logouts: 1},
		{name: "qr method unsupported", setup: func(_ *fx, _ *contract.Envelope, p *contract.Pair) { p.Method = "qr" },
			code: contract.Unsupported},
		{name: "code not scanned before deadline", setup: func(f *fx, e *contract.Envelope, _ *contract.Pair) {
			f.c.BlockWait = true
			e.Deadline = time.Now().Add(50 * time.Millisecond).UTC()
		}, code: contract.Timeout, delivered: true},
		{name: "notifier failure", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) { f.notes.Err = ErrTimeout },
			code: contract.Timeout},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			f.sessions.Versions = map[string]int{}
			env, p := envelope(contract.KindPair, 0), *pair
			if c.setup != nil {
				c.setup(f, &env, &p)
			}
			r := f.worker("worker-a").Handle(context.Background(), env, &p)
			if code(r) != c.code || r.Pairing == nil || r.Pairing.CodeDelivered != c.delivered || r.SessionVersion != c.version || f.c.Calls["logout"] != c.logouts {
				t.Fatalf("result %+v error %+v calls %v", r, r.Error, f.c.Calls)
			}
			if f.sessions.Versions[ref] != c.version {
				t.Fatal("session saved on failure")
			}
			if c.delivered && (len(f.notes.Codes) != 1 || f.notes.Codes[0] != "FAKE1234") {
				t.Fatal("code not delivered exactly once")
			}
			if !f.c.WaitDeadline.IsZero() && f.c.WaitDeadline.After(time.Now().Add(PairWindow)) {
				t.Fatal("pairing wait exceeds 150 s window")
			}
			if out, _ := json.Marshal(r); strings.Contains(string(out), "FAKE1234") || strings.Contains(string(out), "10000000000") {
				t.Fatal("code or phone leaked into result")
			}
		})
	}
}

func TestSync(t *testing.T) {
	now := time.Now().UTC()
	msgs := []contract.Message{
		{ChatRef: "chat-1", Text: "fixture reply", ObservedAt: now},
		{ChatRef: "chat-private", Text: "SYNTHETIC-PRIVATE-NEVER-KEEP", ObservedAt: now},
	}
	cases := []struct {
		name    string
		setup   func(*fx)
		code    string
		kept    int
		version int
	}{
		{name: "keeps only enabled chats", kept: 1, version: 2},
		{name: "logged out", setup: func(f *fx) { f.c.Err = map[string]error{"sync": ErrLoggedOut} }, code: contract.NeedsReauth, version: 1},
		{name: "stream replaced", setup: func(f *fx) { f.c.Err = map[string]error{"connect": ErrStreamReplaced} }, code: contract.SessionConflict, version: 1},
		{name: "offline sync incomplete", setup: func(f *fx) { f.c.SyncIncomplete = true }, code: contract.Timeout, version: 1},
		{name: "session version conflict", setup: func(f *fx) { f.sessions.Versions[ref] = 5 }, code: contract.SessionConflict, kept: 1, version: 5},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			f.c.Messages = msgs
			if c.setup != nil {
				c.setup(f)
			}
			r := f.worker("worker-a").Handle(context.Background(), envelope(contract.KindSync, 1), &contract.Sync{EnabledChatRefs: []string{"chat-1"}})
			if code(r) != c.code || len(r.Messages) != c.kept || f.sessions.Versions[ref] != c.version {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			if out, _ := json.Marshal(r); strings.Contains(string(out), "SYNTHETIC-PRIVATE") || strings.Contains(string(out), "chat-private") {
				t.Fatal("non-enabled chat reached result")
			}
			if f.leases.Held(ref) || f.c.Calls["close"] != 1 {
				t.Fatalf("lease held or client not closed: %v", f.c.Calls)
			}
		})
	}
}
