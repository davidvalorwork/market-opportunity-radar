package whatsapp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"filippo.io/age"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/vault"
)

// PairWindow stays under the ~160 s login websocket lifetime (B-F021).
const PairWindow = 150 * time.Second

// DefaultSaveBudget bounds a sync's session save when Worker.SaveBudget is zero.
const DefaultSaveBudget = 5 * time.Second

type Worker struct {
	Client   Client
	Leases   LeaseStore
	Ledger   Ledger
	Sessions SessionStore
	Notifier Notifier
	Owner    string // unique per worker invocation
	LeaseTTL time.Duration

	Blobs    BlobStore
	Identity *age.X25519Identity // decrypts private payloads whose recipient_scope is Scope
	Scope    string              // e.g. "worker:whatsapp"
	// Synced messages are sealed to ResultRecipients and referenced with ResultScope.
	ResultRecipients []*age.X25519Recipient
	ResultScope      string
	// SaveBudget bounds the session save that ends every sync (DefaultSaveBudget if zero). It
	// runs on the caller's context, not the job deadline, so a sync that timed out can still
	// save what it received; the caller's own deadline (the Lambda's) still caps it.
	SaveBudget time.Duration
}

// Handle runs one decoded job. It never logs: private payloads hold phones, codes and message text.
func (w *Worker) Handle(ctx context.Context, env contract.Envelope, payload any) contract.Result {
	var r contract.Result
	var err error
	if !time.Now().Before(env.Deadline.Time) {
		err = contract.Fail(contract.Timeout, "deadline expired before start")
	} else {
		outer := ctx
		var cancel context.CancelFunc
		ctx, cancel = context.WithDeadline(ctx, env.Deadline.Time)
		defer cancel()
		switch p := payload.(type) {
		case *contract.Pair:
			r, err = w.pair(ctx, env, p)
		case *contract.Sync:
			r, err = w.sync(ctx, outer, env, p)
		case *contract.Send:
			r, err = w.send(ctx, env, p)
		case *contract.ListChats:
			r, err = w.listChats(ctx, env, p)
		case *contract.ResolveContact:
			r, err = w.resolve(ctx, env, p)
		default:
			err = contract.Fail(contract.Unsupported, "unknown kind")
		}
	}
	// whatsapp.result v1 answers envelope.v1, v2 answers envelope.v2.
	r.SchemaVersion, r.Status = max(env.SchemaVersion, contract.SchemaVersion), contract.StatusSucceeded
	if err != nil {
		r.Status, r.Error = contract.StatusFailed, toError(err)
	}
	return r
}

func toError(err error) *contract.Error {
	var ce *contract.Error
	switch {
	case errors.As(err, &ce):
		return ce
	case errors.Is(err, ErrLoggedOut):
		return contract.Fail(contract.NeedsReauth, "session logged out")
	case errors.Is(err, ErrStreamReplaced):
		return contract.Fail(contract.SessionConflict, "another client replaced the stream")
	case errors.Is(err, ErrConflict), errors.Is(err, ErrLeaseLost):
		return contract.Fail(contract.SessionConflict, "session busy or version conflict")
	case errors.Is(err, ErrTimeout), errors.Is(err, context.DeadlineExceeded):
		return contract.Fail(contract.Timeout, "deadline exceeded")
	case errors.Is(err, ErrBadCursor):
		return contract.Fail(contract.InvalidInput, "since_cursor not issued by this session")
	case errors.Is(err, ErrNotOnWhatsApp):
		return contract.Fail(contract.NotOnWhatsApp, "number has no WhatsApp account")
	}
	return contract.Fail(contract.Internal, "internal error")
}

func (w *Worker) lease(ctx context.Context, ref string) (int64, func(), error) {
	tok, err := w.Leases.Acquire(ctx, ref, w.Owner, w.LeaseTTL)
	if err != nil {
		return 0, nil, err
	}
	return tok, func() { w.Leases.Release(context.WithoutCancel(ctx), ref, w.Owner, tok) }, nil
}

// open fetches a private_ref blob, checks scope and ciphertext sha256, and decrypts it in memory.
func (w *Worker) open(ctx context.Context, ref contract.PrivateRef) ([]byte, error) {
	if ref.RecipientScope != w.Scope {
		return nil, contract.Fail(contract.InvalidInput, "private_ref scope not accepted by this worker")
	}
	ct, err := w.Blobs.Get(ctx, ref.BlobKey)
	if errors.Is(err, ErrNotFound) {
		return nil, contract.Fail(contract.InvalidInput, "private blob not found")
	}
	if err != nil {
		return nil, err
	}
	plain, err := vault.OpenPrivate(ct, ref.SHA256, w.Identity)
	if err != nil {
		return nil, contract.Fail(contract.InvalidInput, "private blob rejected: "+err.Error()) // constant vault code
	}
	return plain, nil
}

// fit returns how many of the first n items fit one private payload (doc(k) wraps the
// first k) within contract.MaxEnvelope bytes, and that payload's JSON. Go escapes HTML,
// so this is stricter than the validators' compact measure.
// ponytail: re-marshals each prefix (<= 100 x 32 KiB); binary search if pages grow.
func fit(n int, doc func(k int) any) (int, []byte, error) {
	var best []byte
	for k := 1; k <= n; k++ {
		b, err := json.Marshal(doc(k))
		if err != nil {
			return 0, nil, err
		}
		if len(b) > contract.MaxEnvelope {
			return k - 1, best, nil
		}
		best = b
	}
	return n, best, nil
}

// seal stores a private payload (plain, already checked with the consumer's decoder) as
// an age blob. The key is content-addressed, so a redelivered job never overwrites a blob
// another result points to.
func (w *Worker) seal(ctx context.Context, env contract.Envelope, label string, plain []byte) (*contract.PrivateRef, error) {
	ct, sum, err := vault.SealPrivate(plain, w.ResultRecipients...)
	if err != nil {
		return nil, contract.Fail(contract.Internal, "private blob encryption failed")
	}
	_, alias, _ := strings.Cut(env.SessionRef, ":")
	ref := &contract.PrivateRef{BlobKey: "private/whatsapp/" + alias + "/" + label + "-" + sum + ".age", SHA256: sum, RecipientScope: w.ResultScope}
	if err := w.Blobs.Put(ctx, ref.BlobKey, ct); err != nil {
		return nil, err
	}
	return ref, nil
}

func (w *Worker) save(ctx context.Context, env contract.Envelope) (int, error) {
	var buf bytes.Buffer
	if err := w.Client.Snapshot(&buf); err != nil {
		return 0, err
	}
	return w.Sessions.Save(ctx, env.SessionRef, env.ExpectedVersion, buf.Bytes())
}

// saveFailed reports a failed session save after a sync: orig's code (the save error's when
// orig is nil), session_conflict when the version CAS was lost, and a message saying that
// pending messages received in this run live only in this invocation's local copy.
func saveFailed(orig, serr error) error {
	if orig == nil {
		orig = serr
	}
	e := toError(orig)
	code := e.Code
	if errors.Is(serr, ErrConflict) {
		code = contract.SessionConflict
	}
	return contract.Fail(code, e.Message+"; session save failed, pending messages received in this run were not persisted")
}

// jidUser extracts the phone digits from "user[.agent][:device]@server".
// wameow already returns types.JID.User (no "@"), which passes through unchanged; parsing covers fakes.
func jidUser(jid string) string {
	u, _, _ := strings.Cut(jid, "@")
	u, _, _ = strings.Cut(u, ":")
	u, _, _ = strings.Cut(u, ".")
	return u
}

func (w *Worker) pair(ctx context.Context, env contract.Envelope, p *contract.Pair) (contract.Result, error) {
	r := contract.Result{Pairing: &contract.Pairing{}}
	if p.Method != "code" {
		return r, contract.Fail(contract.Unsupported, "only the pairing code method is supported")
	}
	plain, err := w.open(ctx, p.PrivateRef)
	if err != nil {
		return r, err
	}
	priv, err := contract.DecodePairPrivate(plain)
	if err != nil {
		return r, err
	}
	phone := priv.DeclaredPhone
	_, release, err := w.lease(ctx, env.SessionRef)
	if err != nil {
		return r, err
	}
	defer release()
	ctx, cancel := context.WithTimeout(ctx, PairWindow)
	defer cancel()
	defer w.Client.Close()
	if err := w.Client.Connect(ctx); err != nil {
		return r, err
	}
	code, err := w.Client.PairPhone(ctx, phone)
	if err != nil {
		return r, err
	}
	if err := w.Notifier.DeliverCode(ctx, p.Notify, code); err != nil {
		return r, err
	}
	r.Pairing.CodeDelivered = true
	jid, err := w.Client.WaitPaired(ctx)
	if err != nil {
		return r, err
	}
	if jidUser(jid) != strings.TrimPrefix(phone, "+") {
		lctx, lcancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
		defer lcancel()
		if w.Client.Logout(lctx) != nil {
			return r, contract.Fail(contract.Internal, "paired account mismatch and logout failed; revoke the device manually")
		}
		return r, contract.Fail(contract.InvalidInput, "paired account does not match declared phone")
	}
	r.SessionVersion, err = w.save(ctx, env)
	return r, err
}

// sync returns one page of messages from enabled chats. v2: since_cursor acknowledges the
// previous page, the page stops at page_size or the 32 KiB private budget, and the rest stays
// pending in the session state (next_cursor/has_more). v1 has no cursor: everything must fit
// one result or it is budget_exhausted, and the session is still saved so nothing received is lost.
//
// Once Connect is called the client may have stored messages it already acked to WhatsApp
// (wameow: radar_pending + SynchronousAck), so a failing sync still saves the session, on
// outer with SaveBudget (the job ctx may be expired), and keeps its own error code; what was
// received stays pending for the next page. Two errors never save:
//   - ErrStreamReplaced: another client owns the device and its keys/ratchets have moved on;
//     our snapshot would roll that state back (or race a writer that skips our CAS).
//   - ErrLoggedOut: whatsmeow deletes the device store concurrently with the event, so the
//     snapshot is nondeterministic, and the session needs a new pairing anyway.
//
// Messages acked before either event in that run are lost (needs a pending-only export).
func (w *Worker) sync(ctx, outer context.Context, env contract.Envelope, p *contract.Sync) (r contract.Result, err error) {
	v2 := env.SchemaVersion == contract.SchemaV2
	_, release, err := w.lease(ctx, env.SessionRef)
	if err != nil {
		return r, err
	}
	defer release()
	defer w.Client.Close()
	limit := contract.MaxMessages
	if v2 {
		limit, r.NextCursor = p.PageSize, p.SinceCursor
		if p.SinceCursor != "" {
			if err := w.Client.Ack(ctx, p.SinceCursor); err != nil {
				return r, err // not connected: nothing received, no save
			}
		}
	}
	saved := false
	save := func() error {
		saved = true
		budget := w.SaveBudget
		if budget <= 0 {
			budget = DefaultSaveBudget
		}
		sctx, cancel := context.WithTimeout(outer, budget)
		defer cancel()
		v, err := w.save(sctx, env)
		if err != nil {
			return err
		}
		r.SessionVersion = v
		return nil
	}
	// Registered after Close, so it runs first (LIFO): the snapshot needs the open client.
	defer func() {
		if err != nil && !saved && !errors.Is(err, ErrStreamReplaced) && !errors.Is(err, ErrLoggedOut) {
			if serr := save(); serr != nil {
				err = saveFailed(err, serr)
			}
		}
	}()
	if err := w.Client.Connect(ctx); err != nil {
		return r, err
	}
	page, more, err := w.Client.Sync(ctx, p.EnabledChatRefs, limit)
	if err != nil {
		return r, err
	}
	n, plain, err := fit(len(page), func(k int) any {
		msgs := make([]contract.Message, k)
		for i := range msgs {
			msgs[i] = page[i].Message
		}
		return contract.MessagesPrivate{SchemaVersion: contract.SchemaVersion, Messages: msgs}
	})
	if err != nil {
		return r, err
	}
	if (!v2 && (more || n < len(page))) || (len(page) > 0 && n == 0) {
		// Unreturned messages stay pending in the session; saving it keeps them for a v2 sync.
		if err := save(); err != nil {
			return r, saveFailed(nil, err)
		}
		return r, contract.Fail(contract.BudgetExhausted, "synced messages exceed one v1 result; use whatsapp.sync v2 paging")
	}
	if n > 0 {
		// Re-check with the consumer's decoder: what we write is exactly what the reader accepts.
		if _, err := contract.DecodeMessagesPrivate(plain); err != nil {
			return r, contract.Fail(contract.Internal, "synced messages rejected by the private schema")
		}
		// Sealed before the snapshot: if storing fails the session is not advanced past them.
		if r.PrivateRef, err = w.seal(ctx, env, "sync", plain); err != nil {
			return r, err
		}
		r.MessageCount = n
		if v2 {
			r.NextCursor = page[n-1].Cursor // acknowledged by the next page's since_cursor, not before
		} else if err := w.Client.Ack(ctx, page[n-1].Cursor); err != nil { // the v1 result is the consumer's only copy
			return r, err
		}
	}
	r.HasMore = v2 && (more || n < len(page))
	// The private_ref stays in the result even if the snapshot save fails, so the returned
	// messages are not lost; the pending rest of this run is, and the error says so.
	if err := save(); err != nil {
		return r, saveFailed(nil, err)
	}
	return r, nil
}

// listChats pages through the chats the session knows. Read-only and offline: no lease,
// no Connect, no snapshot. Refs, names and dates go only into whatsapp.chats.private.v1.
func (w *Worker) listChats(ctx context.Context, env contract.Envelope, p *contract.ListChats) (contract.Result, error) {
	r := contract.Result{NextCursor: p.SinceCursor}
	defer w.Client.Close()
	chats, more, err := w.Client.ListChats(ctx, p.SinceCursor, p.PageSize)
	if err != nil {
		return r, err
	}
	n, plain, err := fit(len(chats), func(k int) any {
		return contract.ChatsPrivate{SchemaVersion: contract.SchemaVersion, Chats: chats[:k]}
	})
	if err != nil {
		return r, err
	}
	if len(chats) > 0 && n == 0 {
		return r, contract.Fail(contract.BudgetExhausted, "chat entry exceeds private payload limits")
	}
	if n > 0 {
		if _, err := contract.DecodeChatsPrivate(plain); err != nil {
			return r, contract.Fail(contract.Internal, "chat list rejected by the private schema")
		}
		if r.PrivateRef, err = w.seal(ctx, env, "chats", plain); err != nil {
			return r, err
		}
		r.ChatCount, r.NextCursor = n, chats[n-1].ChatRef
	}
	r.HasMore = more || n < len(chats)
	return r, nil
}

// resolve turns a seller's published number into a chat ref. It needs an approved ledger
// record for the operation bound to owner, approval_ref, source_ref (RecipientRef) and the
// contact blob's ciphertext sha256 (ContentSHA256), so the phone itself is never hashed or
// stored outside the encrypted blob. One approval = one lookup; it never sends anything.
// The ref is kept in the record (ProviderMessageID) so a redelivery answers without a new lookup.
func (w *Worker) resolve(ctx context.Context, env contract.Envelope, p *contract.ResolveContact) (contract.Result, error) {
	var r contract.Result
	op := env.OperationID
	rec, err := w.Ledger.Get(ctx, op)
	if errors.Is(err, ErrNotFound) {
		return r, contract.Fail(contract.InvalidInput, "contact lookup not approved")
	}
	if err != nil {
		return r, err
	}
	plain, err := w.open(ctx, p.PrivateRef)
	if err != nil {
		return r, err
	}
	priv, err := contract.DecodeContactPrivate(plain)
	if err != nil {
		return r, err
	}
	if rec.OwnerRef != env.OwnerRef || rec.ApprovalRef != priv.ApprovalRef || rec.RecipientRef != priv.SourceRef || rec.ContentSHA256 != p.PrivateRef.SHA256 {
		return r, contract.Fail(contract.InvalidInput, "approval does not match contact lookup")
	}
	switch {
	case rec.State == ProviderConfirmed && rec.ProviderMessageID != "":
		r.ChatRef = rec.ProviderMessageID
		return r, nil
	case rec.State != Approved:
		return r, contract.Fail(contract.InvalidInput, "contact lookup not approved")
	}
	_, release, err := w.lease(ctx, env.SessionRef)
	if err != nil {
		return r, err
	}
	defer release()
	defer w.Client.Close()
	if err := w.Client.Connect(ctx); err != nil {
		return r, err
	}
	ref, err := w.Client.ResolveChat(ctx, priv.Phone)
	if err != nil {
		return r, err // not_on_whatsapp or transport error: the approval stays approved, nothing was sent
	}
	if err := w.Ledger.Transition(ctx, op, Approved, ProviderConfirmed, Meta{ProviderMessageID: ref}); err != nil {
		return r, err
	}
	r.ChatRef = ref
	r.SessionVersion, err = w.save(ctx, env)
	return r, err
}

// send follows architecture-final-review §4: approved -> dispatch_committed -> provider_confirmed | send_uncertain.
// A claimed operation is never dispatched again; timeouts never reset the ledger.
func (w *Worker) send(ctx context.Context, env contract.Envelope, p *contract.Send) (contract.Result, error) {
	var r contract.Result
	op := env.OperationID
	rec, err := w.Ledger.Get(ctx, op)
	if errors.Is(err, ErrNotFound) {
		return r, contract.Fail(contract.InvalidInput, "operation not approved")
	}
	if err != nil {
		return r, err
	}
	if rec.State != Approved {
		return w.settled(ctx, op, rec)
	}
	if rec.OwnerRef != env.OwnerRef || rec.RecipientRef != p.RecipientRef || rec.ApprovalRef != p.ApprovalRef || rec.ContentSHA256 != p.ContentSHA256 {
		return r, contract.Fail(contract.InvalidInput, "approval does not match operation")
	}
	plain, err := w.open(ctx, p.PrivateRef)
	if err != nil {
		return r, err
	}
	priv, err := contract.DecodeSendPrivate(plain)
	if err != nil {
		return r, err
	}
	if contract.SHA256Hex(priv.Text) != p.ContentSHA256 {
		return r, contract.Fail(contract.InvalidInput, "content hash mismatch")
	}
	// Unknown recipient: refuse before lease, connect and claim, so nothing is recorded as uncertain.
	if known, err := w.Client.HasChat(ctx, p.RecipientRef); err != nil {
		return r, err
	} else if !known {
		return r, contract.Fail(contract.InvalidInput, "recipient_ref not known to this session")
	}
	tok, release, err := w.lease(ctx, env.SessionRef)
	if err != nil {
		return r, err
	}
	defer release()
	defer w.Client.Close()
	if err := w.Client.Connect(ctx); err != nil {
		return r, err // not claimed yet: ledger stays approved
	}
	if err := w.Ledger.Transition(ctx, op, Approved, DispatchCommitted, Meta{LeaseToken: tok}); err != nil {
		if errors.Is(err, ErrConflict) {
			if rec, gerr := w.Ledger.Get(ctx, op); gerr == nil && rec.State != Approved {
				return w.settled(ctx, op, rec)
			}
		}
		return r, err
	}
	// Not provider fencing: the lease may still expire after this check (§4).
	if w.Leases.Check(ctx, env.SessionRef, w.Owner, tok) != nil {
		return r, w.uncertain(ctx, op, "lease lost after claim; not dispatched by this attempt")
	}
	id, err := w.Client.Send(ctx, p.RecipientRef, priv.Text)
	if err != nil {
		return r, w.uncertain(ctx, op, "send outcome unknown")
	}
	r.ProviderMessageID = id
	if w.Ledger.Transition(context.WithoutCancel(ctx), op, DispatchCommitted, ProviderConfirmed, Meta{LeaseToken: tok, ProviderMessageID: id}) != nil {
		return r, contract.Fail(contract.SendUncertain, "sent but confirmation not recorded")
	}
	r.SessionVersion, err = w.save(ctx, env)
	return r, err
}

// uncertain records send_uncertain; if that write fails the record stays dispatch_committed,
// which later attempts also resolve to send_uncertain without sending.
func (w *Worker) uncertain(ctx context.Context, op, msg string) error {
	w.Ledger.Transition(context.WithoutCancel(ctx), op, DispatchCommitted, SendUncertain, Meta{})
	return contract.Fail(contract.SendUncertain, msg)
}

func (w *Worker) settled(ctx context.Context, op string, rec Record) (contract.Result, error) {
	switch rec.State {
	case ProviderConfirmed:
		return contract.Result{ProviderMessageID: rec.ProviderMessageID}, nil
	case DispatchCommitted:
		return contract.Result{}, w.uncertain(ctx, op, "claimed by an earlier attempt; not resent")
	case SendUncertain:
		return contract.Result{}, contract.Fail(contract.SendUncertain, "earlier attempt outcome unknown; not resent")
	}
	return contract.Result{}, contract.Fail(contract.InvalidInput, "operation not approved")
}
