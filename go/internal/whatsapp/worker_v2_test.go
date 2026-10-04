package whatsapp

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/schematest"
	"radar.local/radar/internal/vault"
)

func envelopeV2(kind string, version int) contract.Envelope {
	e := envelope(kind, version)
	e.SchemaVersion = contract.SchemaV2
	return e
}

// openBlob decrypts a result's private_ref with the fixture identity.
func (f *fx) openBlob(t *testing.T, ref *contract.PrivateRef) []byte {
	t.Helper()
	ct, err := f.blobs.Get(context.Background(), ref.BlobKey)
	if err != nil {
		t.Fatal(err)
	}
	if len(ct) == 0 || ref.RecipientScope != scope {
		t.Fatalf("blob scope %s", ref.RecipientScope)
	}
	plain, err := vault.OpenPrivate(ct, ref.SHA256, f.id)
	if err != nil {
		t.Fatal(err)
	}
	if len(plain) > contract.MaxEnvelope {
		t.Fatalf("private payload %d bytes", len(plain))
	}
	return plain
}

// drain pages through whatsapp.sync v2 the way a consumer does: next_cursor becomes
// since_cursor and expected_version follows session_version. It returns every text received.
func drain(t *testing.T, f *fx, pageSize int) (texts []string, pages int) {
	t.Helper()
	cursor, version := "", 1
	for pages = 1; pages < 100; pages++ {
		p := &contract.Sync{SchemaVersion: 2, EnabledChatRefs: []string{"wachat:seller-a"}, SinceCursor: cursor, PageSize: pageSize}
		r := f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindSync, version), p)
		if r.Error != nil || r.SchemaVersion != 2 || r.SessionVersion != version+1 {
			t.Fatalf("page %d: %+v %+v", pages, r, r.Error)
		}
		checkResult(t, r, "SYNTHETIC", "seller-a")
		if r.MessageCount > 0 {
			got, err := contract.DecodeMessagesPrivate(f.openBlob(t, r.PrivateRef))
			if err != nil || len(got.Messages) != r.MessageCount || r.MessageCount > pageSize {
				t.Fatalf("page %d private %v count %d", pages, err, r.MessageCount)
			}
			schematest.Validate(t, "whatsapp.messages.private.v1", got)
			for _, m := range got.Messages {
				texts = append(texts, m.Text)
			}
		}
		cursor, version = r.NextCursor, r.SessionVersion
		if !r.HasMore {
			return texts, pages
		}
	}
	t.Fatal("paging did not terminate")
	return nil, 0
}

func synthetic(n, size int) []contract.Message {
	now := contract.Time{Time: time.Now().UTC()}
	out := make([]contract.Message, 0, 2*n)
	for i := range n {
		text := fmt.Sprintf("SYNTHETIC-%04d-", i)
		out = append(out, contract.Message{ChatRef: "wachat:seller-a", Text: text + strings.Repeat("x", max(0, size-len(text))), ObservedAt: now},
			contract.Message{ChatRef: "wachat:not-enabled", Text: "SYNTHETIC-PRIVATE-NEVER-KEEP", ObservedAt: now})
	}
	return out
}

// Gap (a): more than a page arrives at once; every message reaches the consumer exactly
// once, in order, across pages, and nothing from a non-enabled chat is returned or kept.
func TestSyncV2PagingLosesNothing(t *testing.T) {
	f := newFx()
	f.c.Messages = synthetic(250, 0)
	texts, pages := drain(t, f, 100)
	if len(texts) != 250 || pages != 3 {
		t.Fatalf("%d messages over %d pages", len(texts), pages)
	}
	for i, txt := range texts {
		if !strings.HasPrefix(txt, fmt.Sprintf("SYNTHETIC-%04d-", i)) {
			t.Fatalf("message %d out of order or duplicated: %q", i, txt[:15])
		}
	}
	// Only the last page is still held: the consumer's next poll acknowledges it with since_cursor.
	if len(f.c.Pending()) != 50 {
		t.Fatalf("%d messages left pending after the last page", len(f.c.Pending()))
	}
}

// The 32 KiB private budget splits a page before page_size; the rest stays pending.
func TestSyncV2BudgetSplit(t *testing.T) {
	f := newFx()
	f.c.Messages = synthetic(30, contract.MaxText) // ~4 KiB each: about 7 fit one private payload
	r := f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindSync, 1),
		&contract.Sync{SchemaVersion: 2, EnabledChatRefs: []string{"wachat:seller-a"}, PageSize: 100})
	if r.Error != nil || r.MessageCount == 0 || r.MessageCount >= 30 || !r.HasMore || r.NextCursor == "" {
		t.Fatalf("first page %+v %+v", r, r.Error)
	}
	if len(f.c.Pending()) != 30 {
		t.Fatalf("pending %d: a page is acknowledged only by the next since_cursor", len(f.c.Pending()))
	}
	f2 := newFx()
	f2.c.Messages = synthetic(30, contract.MaxText)
	if texts, pages := drain(t, f2, 100); len(texts) != 30 || pages < 4 {
		t.Fatalf("%d messages over %d pages", len(texts), pages)
	}
}

func TestSyncV2Cursor(t *testing.T) {
	f := newFx()
	f.c.Messages = synthetic(3, 0)
	run := func(cursor string) contract.Result {
		return f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindSync, f.sessions.Versions[ref]),
			&contract.Sync{SchemaVersion: 2, EnabledChatRefs: []string{"wachat:seller-a"}, SinceCursor: cursor, PageSize: 2})
	}
	first := run("")
	// Redelivered without an ack: the same messages come back (at-least-once), never fewer.
	again := run("")
	if first.MessageCount != 2 || again.MessageCount != 2 || again.NextCursor != first.NextCursor {
		t.Fatalf("first %+v again %+v", first, again)
	}
	last := run(first.NextCursor)
	if last.MessageCount != 1 || last.HasMore {
		t.Fatalf("last %+v", last)
	}
	empty := run(last.NextCursor)
	if empty.Error != nil || empty.MessageCount != 0 || empty.PrivateRef != nil || empty.NextCursor != last.NextCursor || empty.HasMore {
		t.Fatalf("empty %+v", empty)
	}
	checkResult(t, empty)
	connects := f.c.Calls["connect"]
	for _, bad := range []string{"p999", "c:000123"} {
		r := run(bad)
		if code(r) != contract.InvalidInput || f.c.Calls["connect"] != connects || f.leases.Held(ref) {
			t.Fatalf("cursor %q: %+v calls %v", bad, r, f.c.Calls)
		}
		checkResult(t, r)
	}
}

// Display names, refs and dates travel only in the private blob; list_chats never connects,
// takes no lease and saves no session.
func TestListChats(t *testing.T) {
	f := newFx()
	at := contract.Time{Time: time.Date(2026, 10, 3, 12, 0, 0, 0, time.UTC)}
	f.c.Chats = map[string]contract.Chat{}
	for i := range 5 {
		r := fmt.Sprintf("wachat:chat-%c", 'a'+i)
		f.c.Chats[r] = contract.Chat{ChatRef: r, DisplayName: "SYNTHETIC-NAME-" + r[7:], LastMessageAt: &at}
	}
	var seen []contract.Chat
	cursor := ""
	for page := 1; ; page++ {
		r := f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindListChats, 1), &contract.ListChats{SchemaVersion: 2, PageSize: 2, SinceCursor: cursor})
		if r.Error != nil || r.ChatCount == 0 || r.SessionVersion != 0 || r.MessageCount != 0 {
			t.Fatalf("page %d: %+v %+v", page, r, r.Error)
		}
		checkResult(t, r, "SYNTHETIC-NAME")
		got, err := contract.DecodeChatsPrivate(f.openBlob(t, r.PrivateRef))
		if err != nil || len(got.Chats) != r.ChatCount {
			t.Fatalf("private chats %v", err)
		}
		schematest.Validate(t, "whatsapp.chats.private.v1", got)
		seen = append(seen, got.Chats...)
		cursor = r.NextCursor
		if !r.HasMore {
			break
		}
	}
	if len(seen) != 5 || seen[0].DisplayName != "SYNTHETIC-NAME-chat-a" || seen[4].ChatRef != "wachat:chat-e" {
		t.Fatalf("chats %+v", seen)
	}
	if f.c.Calls["connect"] != 0 || f.sessions.Versions[ref] != 1 || f.leases.Held(ref) || f.c.Calls["close"] != 3 {
		t.Fatalf("list_chats touched the session: %v", f.c.Calls)
	}
	// No chats yet: succeeded with chat_count 0 and no blob.
	empty := newFx()
	empty.c.Chats = nil
	r := empty.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindListChats, 1), &contract.ListChats{SchemaVersion: 2, PageSize: 10})
	if r.Error != nil || r.ChatCount != 0 || r.PrivateRef != nil || len(empty.blobs.Blobs) != 0 {
		t.Fatalf("empty list %+v", r)
	}
	checkResult(t, r)
}

const contactKey = "private/whatsapp/fixture/contact.age"

func TestResolveContact(t *testing.T) {
	const phone = "+10000000005"
	contact := `{"schema_version":1,"phone":"` + phone + `","source_ref":"listing:synthetic-1","approval_ref":"approval:ap-9"}`
	setup := func(f *fx, plain string) *contract.ResolveContact {
		p := &contract.ResolveContact{SchemaVersion: 2, PrivateRef: f.seal(contactKey, plain, f.id)}
		f.ledger.Records["op-1"] = Record{State: Approved, OwnerRef: "owner:radar-pilot", RecipientRef: "listing:synthetic-1", ApprovalRef: "approval:ap-9", ContentSHA256: p.PrivateRef.SHA256}
		return p
	}
	cases := []struct {
		name      string
		mutate    func(*fx, *contract.ResolveContact)
		code      string
		state     State
		connected bool
	}{
		{name: "found", state: ProviderConfirmed, connected: true},
		{name: "not on whatsapp", mutate: func(f *fx, _ *contract.ResolveContact) { f.c.NotOnWhatsApp = map[string]bool{phone: true} },
			code: contract.NotOnWhatsApp, state: Approved, connected: true},
		{name: "approval required", mutate: func(f *fx, _ *contract.ResolveContact) { delete(f.ledger.Records, "op-1") }, code: contract.InvalidInput},
		{name: "approval not yet approved", mutate: func(f *fx, _ *contract.ResolveContact) {
			rec := f.ledger.Records["op-1"]
			rec.State = Proposed
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Proposed},
		{name: "approval for another listing", mutate: func(f *fx, _ *contract.ResolveContact) {
			rec := f.ledger.Records["op-1"]
			rec.RecipientRef = "listing:synthetic-2"
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "approval bound to another blob", mutate: func(f *fx, p *contract.ResolveContact) {
			p.PrivateRef = f.seal(contactKey, strings.Replace(contact, "05", "06", 1), f.id) // same approval_ref, different number
		}, code: contract.InvalidInput, state: Approved},
		{name: "another owner", mutate: func(f *fx, _ *contract.ResolveContact) {
			rec := f.ledger.Records["op-1"]
			rec.OwnerRef = "owner:someone-else"
			f.ledger.Records["op-1"] = rec
		}, code: contract.InvalidInput, state: Approved},
		{name: "tampered blob", mutate: func(f *fx, _ *contract.ResolveContact) { f.blobs.Blobs[contactKey][40] ^= 1 }, code: contract.InvalidInput, state: Approved},
		{name: "logged out on connect", mutate: func(f *fx, _ *contract.ResolveContact) { f.c.Err = map[string]error{"connect": ErrLoggedOut} },
			code: contract.NeedsReauth, state: Approved, connected: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFx()
			f.c.Chats = nil
			p := setup(f, contact)
			if c.mutate != nil {
				c.mutate(f, p)
			}
			r := f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindResolveContact, 1), p)
			if code(r) != c.code || r.SchemaVersion != 2 {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			checkResult(t, r, phone, "10000000005")
			if f.ledger.Records["op-1"].State != c.state || (f.c.Calls["connect"] > 0) != c.connected || f.leases.Held(ref) {
				t.Fatalf("state %s calls %v", f.ledger.Records["op-1"].State, f.c.Calls)
			}
			if f.c.Calls["send"] != 0 {
				t.Fatal("resolve_contact sent a message")
			}
			if c.code != "" {
				if r.ChatRef != "" || f.sessions.Versions[ref] != 1 {
					t.Fatalf("failure returned a ref or saved the session: %+v", r)
				}
				return
			}
			if !strings.HasPrefix(r.ChatRef, "wachat:") || r.SessionVersion != 2 || f.ledger.Records["op-1"].ProviderMessageID != r.ChatRef {
				t.Fatalf("found %+v", r)
			}
			// The new ref is a known chat: list_chats shows it and send would accept it.
			if ok, _ := f.c.HasChat(context.Background(), r.ChatRef); !ok {
				t.Fatal("resolved chat unknown to the session")
			}
			// Redelivery answers from the ledger: no second lookup, same ref.
			r2 := f.worker("worker-b").Handle(context.Background(), envelopeV2(contract.KindResolveContact, 2), p)
			if r2.Error != nil || r2.ChatRef != r.ChatRef || f.c.Calls["resolve"] != 1 {
				t.Fatalf("redelivery %+v calls %v", r2, f.c.Calls)
			}
			checkResult(t, r2, phone)
		})
	}
}

// Pair and send keep their v1 payloads inside envelope.v2 and answer with whatsapp.result.v2.
func TestSendInEnvelopeV2(t *testing.T) {
	f := newFx()
	r := f.worker("worker-a").Handle(context.Background(), envelopeV2(contract.KindSend, 1), f.sendPayload())
	if r.Error != nil || r.SchemaVersion != 2 || r.ProviderMessageID == "" {
		t.Fatalf("result %+v %+v", r, r.Error)
	}
	checkResult(t, r, text)
	out, _ := json.Marshal(r)
	if _, err := contract.DecodeResultV2(out); err != nil {
		t.Fatal(err)
	}
	if _, err := contract.DecodeResult(out); err == nil {
		t.Fatal("v2 result accepted as v1")
	}
}
