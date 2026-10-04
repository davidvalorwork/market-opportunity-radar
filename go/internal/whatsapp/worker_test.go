package whatsapp

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"filippo.io/age"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/schematest"
	"radar.local/radar/internal/vault"
)

const (
	ref   = "whatsapp:fixture"
	scope = "worker:whatsapp"
)

var text = "fixture text"

type fx struct {
	c        *FakeClient
	leases   *MemLeases
	ledger   *MemLedger
	sessions *MemSessions
	notes    *FakeNotifier
	blobs    *MemBlobs
	id       *age.X25519Identity
}

func newFx() *fx {
	id, _ := age.GenerateX25519Identity()
	return &fx{
		c:      &FakeClient{JID: "10000000000:7@s.whatsapp.net", Code: "FAKE1234", Chats: map[string]contract.Chat{"wachat:seller-a": {ChatRef: "wachat:seller-a"}}},
		leases: &MemLeases{},
		ledger: &MemLedger{Records: map[string]Record{"op-1": {
			State: Approved, OwnerRef: "owner:radar-pilot", RecipientRef: "wachat:seller-a", ApprovalRef: "approval:ap-1", ContentSHA256: contract.SHA256Hex(text),
		}}},
		sessions: &MemSessions{Versions: map[string]int{ref: 1}},
		notes:    &FakeNotifier{},
		blobs:    &MemBlobs{},
		id:       id,
	}
}

func (f *fx) worker(owner string) *Worker {
	return &Worker{Client: f.c, Leases: f.leases, Ledger: f.ledger, Sessions: f.sessions, Notifier: f.notes, Owner: owner, LeaseTTL: time.Minute,
		Blobs: f.blobs, Identity: f.id, Scope: scope, ResultRecipients: []*age.X25519Recipient{f.id.Recipient()}, ResultScope: scope}
}

// seal stores a private payload blob encrypted to to and returns its private_ref.
func (f *fx) seal(key, plain string, to *age.X25519Identity) contract.PrivateRef {
	ct, sum, err := vault.SealPrivate([]byte(plain), to.Recipient())
	if err != nil {
		panic(err)
	}
	f.blobs.Put(context.Background(), key, ct)
	return contract.PrivateRef{BlobKey: key, SHA256: sum, RecipientScope: scope}
}

const sendKey, pairKey = "private/whatsapp/fixture/send.age", "private/whatsapp/fixture/pair.age"

func (f *fx) sendPayload() *contract.Send {
	return &contract.Send{SchemaVersion: 1, RecipientRef: "wachat:seller-a", ApprovalRef: "approval:ap-1", ContentSHA256: contract.SHA256Hex(text),
		PrivateRef: f.seal(sendKey, `{"schema_version":1,"text":"`+text+`"}`, f.id)}
}

func envelope(kind string, version int) contract.Envelope {
	return contract.Envelope{SchemaVersion: 1, MessageID: "m-1", OperationID: "op-1", CorrelationID: "c-1", OwnerRef: "owner:radar-pilot",
		Kind: kind, Deadline: contract.Time{Time: time.Now().Add(time.Hour).UTC()}, Attempt: 1, SessionRef: ref, ExpectedVersion: version}
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

// checkResult validates a handler result against whatsapp.result.v<schema_version> and checks no private data leaked.
func checkResult(t *testing.T, r contract.Result, secrets ...string) {
	t.Helper()
	schematest.Validate(t, "whatsapp.result.v"+strconv.Itoa(r.SchemaVersion), r)
	if err := r.Check(); err != nil {
		t.Errorf("Result.Check: %v", err)
	}
	out, _ := json.Marshal(r)
	for _, s := range secrets {
		if strings.Contains(string(out), s) {
			t.Errorf("private data %q leaked into result", s)
		}
	}
}

func TestSend(t *testing.T) {
	setPriv := func(plain string) func(*fx, *contract.Envelope, *contract.Send) {
		return func(f *fx, _ *contract.Envelope, p *contract.Send) { p.PrivateRef = f.seal(sendKey, plain, f.id) }
	}
	cases := []struct {
		name      string
		setup     func(*fx, *contract.Envelope, *contract.Send)
		crash     bool
		code      string
		state     State
		sends     int
		connected bool
	}{
		{name: "happy path", state: ProviderConfirmed, sends: 1, connected: true},
		{name: "lease lost before send", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.leases.FailCheck = true },
			code: contract.SendUncertain, state: SendUncertain, connected: true},
		{name: "crash after claim before send", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			f.ledger.AfterTransition = func(_ string, to State) {
				if to == DispatchCommitted {
					panic("fault injection: crash after claim")
				}
			}
		}, crash: true, state: DispatchCommitted, connected: true},
		{name: "crash after send before confirm", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.c.PanicAfterSend = true },
			crash: true, state: DispatchCommitted, sends: 1, connected: true},
		{name: "send timeout", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.c.Err = map[string]error{"send": ErrTimeout} },
			code: contract.SendUncertain, state: SendUncertain, sends: 1, connected: true},
		{name: "logged out during send", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.c.Err = map[string]error{"send": ErrLoggedOut} },
			code: contract.SendUncertain, state: SendUncertain, sends: 1, connected: true},
		{name: "logged out on connect keeps approval", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			f.c.Err = map[string]error{"connect": ErrLoggedOut}
		},
			code: contract.NeedsReauth, state: Approved, connected: true},
		{name: "stream replaced on connect", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			f.c.Err = map[string]error{"connect": ErrStreamReplaced}
		},
			code: contract.SessionConflict, state: Approved, connected: true},
		{name: "content hash mismatch", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			rec := f.ledger.Records["op-1"]
			rec.ContentSHA256 = contract.SHA256Hex("approved other text")
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "recipient mismatch", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			rec := f.ledger.Records["op-1"]
			rec.RecipientRef = "wachat:seller-b"
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "not approved", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			rec := f.ledger.Records["op-1"]
			rec.State = Proposed
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Proposed},
		{name: "expired deadline", setup: func(_ *fx, e *contract.Envelope, _ *contract.Send) {
			e.Deadline.Time = time.Now().Add(-time.Second).UTC()
		},
			code: contract.Timeout, state: Approved},
		{name: "lease held by another worker", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) {
			f.leases.Acquire(context.Background(), ref, "worker-other", time.Minute)
		}, code: contract.SessionConflict, state: Approved},
		// Private payload: every failure happens before lease, connect and claim.
		{name: "tampered blob", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.blobs.Blobs[sendKey][40] ^= 1 },
			code: contract.InvalidInput, state: Approved},
		{name: "blob encrypted to another recipient", setup: func(f *fx, _ *contract.Envelope, p *contract.Send) {
			other, _ := age.GenerateX25519Identity()
			p.PrivateRef = f.seal(sendKey, `{"schema_version":1,"text":"`+text+`"}`, other)
		}, code: contract.InvalidInput, state: Approved},
		{name: "scope not accepted", setup: func(_ *fx, _ *contract.Envelope, p *contract.Send) { p.PrivateRef.RecipientScope = "worker:browser" },
			code: contract.InvalidInput, state: Approved},
		{name: "blob missing", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { delete(f.blobs.Blobs, sendKey) },
			code: contract.InvalidInput, state: Approved},
		{name: "private text differs from approved hash", setup: setPriv(`{"schema_version":1,"text":"other text"}`),
			code: contract.InvalidInput, state: Approved},
		{name: "private text empty", setup: setPriv(`{"schema_version":1,"text":""}`), code: contract.InvalidInput, state: Approved},
		{name: "private extra field", setup: setPriv(`{"schema_version":1,"text":"` + text + `","recipient_ref":"wachat:x"}`), code: contract.InvalidInput, state: Approved},
		{name: "private text too long", setup: setPriv(`{"schema_version":1,"text":"` + strings.Repeat("a", contract.MaxText+1) + `"}`), code: contract.InvalidInput, state: Approved},
		{name: "private schema version 2", setup: setPriv(`{"schema_version":2,"text":"` + text + `"}`), code: contract.Unsupported, state: Approved},
		{name: "private not json", setup: setPriv(`not json`), code: contract.InvalidInput, state: Approved},
		// Gap (d): an unknown recipient is refused before lease, connect and claim; nothing becomes send_uncertain.
		{name: "recipient unknown to the session", setup: func(f *fx, _ *contract.Envelope, _ *contract.Send) { f.c.Chats = nil },
			code: contract.InvalidInput, state: Approved},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			env, p := envelope(contract.KindSend, 1), f.sendPayload()
			if c.setup != nil {
				c.setup(f, &env, p)
			}
			r, crashed := run(f.worker("worker-a"), env, p)
			if crashed != c.crash || (!c.crash && code(r) != c.code) {
				t.Fatalf("crashed=%v result=%+v error=%+v", crashed, r, r.Error)
			}
			if !c.crash {
				checkResult(t, r, text)
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
				r2, crashed2 := run(f.worker("worker-b"), envelope(contract.KindSend, 1), f.sendPayload())
				if crashed2 || f.c.Calls["send"] != c.sends {
					t.Fatalf("retry resent: calls %v", f.c.Calls)
				}
				checkResult(t, r2, text)
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
	const phone = `{"schema_version":1,"declared_phone":"+10000000000"}`
	cases := []struct {
		name      string
		setup     func(*fx, *contract.Envelope, *contract.Pair)
		code      string
		delivered bool
		version   int
		logouts   int
		connected bool
	}{
		{name: "happy path", delivered: true, version: 1, connected: true},
		{name: "wrong jid logs out", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) { f.c.JID = "19999999999@s.whatsapp.net" },
			code: contract.InvalidInput, delivered: true, logouts: 1, connected: true},
		{name: "wrong jid and logout fails", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) {
			f.c.JID = "19999999999@s.whatsapp.net"
			f.c.Err = map[string]error{"logout": ErrTimeout}
		}, code: contract.Internal, delivered: true, logouts: 1, connected: true},
		{name: "qr method unsupported", setup: func(_ *fx, _ *contract.Envelope, p *contract.Pair) { p.Method = "qr" },
			code: contract.Unsupported},
		{name: "code not scanned before deadline", setup: func(f *fx, e *contract.Envelope, _ *contract.Pair) {
			f.c.BlockWait = true
			e.Deadline.Time = time.Now().Add(50 * time.Millisecond).UTC()
		}, code: contract.Timeout, delivered: true, connected: true},
		{name: "notifier failure", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) { f.notes.Err = ErrTimeout },
			code: contract.Timeout, connected: true},
		{name: "tampered blob", setup: func(f *fx, _ *contract.Envelope, _ *contract.Pair) { f.blobs.Blobs[pairKey][40] ^= 1 },
			code: contract.InvalidInput},
		{name: "blob encrypted to another recipient", setup: func(f *fx, _ *contract.Envelope, p *contract.Pair) {
			other, _ := age.GenerateX25519Identity()
			p.PrivateRef = f.seal(pairKey, phone, other)
		}, code: contract.InvalidInput},
		{name: "private phone not e164", setup: func(f *fx, _ *contract.Envelope, p *contract.Pair) {
			p.PrivateRef = f.seal(pairKey, `{"schema_version":1,"declared_phone":"10000000000"}`, f.id)
		}, code: contract.InvalidInput},
		{name: "private extra field", setup: func(f *fx, _ *contract.Envelope, p *contract.Pair) {
			p.PrivateRef = f.seal(pairKey, `{"schema_version":1,"declared_phone":"+10000000000","name":"Pat"}`, f.id)
		}, code: contract.InvalidInput},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			f.sessions.Versions = map[string]int{}
			env := envelope(contract.KindPair, 0)
			p := &contract.Pair{SchemaVersion: 1, Notify: "tgchat:radar-pilot-owner", Method: "code", PrivateRef: f.seal(pairKey, phone, f.id)}
			if c.setup != nil {
				c.setup(f, &env, p)
			}
			r := f.worker("worker-a").Handle(context.Background(), env, p)
			if code(r) != c.code || r.Pairing == nil || r.Pairing.CodeDelivered != c.delivered || r.SessionVersion != c.version || f.c.Calls["logout"] != c.logouts {
				t.Fatalf("result %+v error %+v calls %v", r, r.Error, f.c.Calls)
			}
			checkResult(t, r, "FAKE1234", "10000000000")
			if (f.c.Calls["connect"] > 0) != c.connected || f.leases.Held(ref) {
				t.Fatalf("connect/lease: calls %v", f.c.Calls)
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
		})
	}
}

func TestSync(t *testing.T) {
	now := contract.Time{Time: time.Now().UTC()}
	msgs := []contract.Message{
		{ChatRef: "wachat:seller-a", Text: "SYNTHETIC-KEPT-REPLY", ObservedAt: now},
		{ChatRef: "wachat:private", Text: "SYNTHETIC-PRIVATE-NEVER-KEEP", ObservedAt: now},
	}
	many := make([]contract.Message, contract.MaxMessages+1)
	for i := range many {
		many[i] = msgs[0]
	}
	cases := []struct {
		name    string
		setup   func(*fx)
		code    string
		kept    int
		version int
	}{
		{name: "keeps only enabled chats", kept: 1, version: 2},
		{name: "no enabled messages", setup: func(f *fx) { f.c.Messages = msgs[1:] }, version: 2},
		{name: "logged out", setup: func(f *fx) { f.c.Err = map[string]error{"sync": ErrLoggedOut} }, code: contract.NeedsReauth, version: 1},
		{name: "stream replaced", setup: func(f *fx) { f.c.Err = map[string]error{"connect": ErrStreamReplaced} }, code: contract.SessionConflict, version: 1},
		{name: "offline sync incomplete", setup: func(f *fx) { f.c.SyncIncomplete = true }, code: contract.Timeout, version: 1},
		{name: "session version conflict", setup: func(f *fx) { f.sessions.Versions[ref] = 5 }, code: contract.SessionConflict, kept: 1, version: 5},
		// v1 cannot page: budget_exhausted, but the session is saved so the 101 messages stay pending (gap a).
		{name: "more than 100 messages", setup: func(f *fx) { f.c.Messages = many }, code: contract.BudgetExhausted, version: 2},
		{name: "blob store failure", setup: func(f *fx) { f.blobs.PutErr = ErrTimeout }, code: contract.Timeout, version: 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			f.c.Messages = msgs
			if c.setup != nil {
				c.setup(f)
			}
			r := f.worker("worker-a").Handle(context.Background(), envelope(contract.KindSync, 1), &contract.Sync{SchemaVersion: 1, EnabledChatRefs: []string{"wachat:seller-a"}})
			if code(r) != c.code || r.MessageCount != c.kept || (r.PrivateRef != nil) != (c.kept > 0) || f.sessions.Versions[ref] != c.version {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			// Message text and chat refs never reach the transport result, kept or not.
			checkResult(t, r, "SYNTHETIC", "wachat:")
			if f.leases.Held(ref) || f.c.Calls["close"] != 1 {
				t.Fatalf("lease held or client not closed: %v", f.c.Calls)
			}
			if c.name == "more than 100 messages" && len(f.c.Pending()) != len(many) {
				t.Fatalf("%d of %d messages still pending", len(f.c.Pending()), len(many))
			}
			if c.kept == 0 {
				if len(f.blobs.Blobs) != 0 {
					t.Fatal("blob stored without a result pointing to it")
				}
				return
			}
			ct, err := f.blobs.Get(context.Background(), r.PrivateRef.BlobKey)
			if err != nil || r.PrivateRef.RecipientScope != scope {
				t.Fatalf("blob %v scope %s", err, r.PrivateRef.RecipientScope)
			}
			plain, err := vault.OpenPrivate(ct, r.PrivateRef.SHA256, f.id)
			if err != nil {
				t.Fatal(err)
			}
			got, err := contract.DecodeMessagesPrivate(plain)
			if err != nil || len(got.Messages) != 1 || got.Messages[0].Text != "SYNTHETIC-KEPT-REPLY" {
				t.Fatalf("private messages %+v err %v", got, err)
			}
			schematest.Validate(t, "whatsapp.messages.private.v1", got)
		})
	}
}
