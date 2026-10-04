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
}

// Handle runs one decoded job. It never logs: private payloads hold phones, codes and message text.
func (w *Worker) Handle(ctx context.Context, env contract.Envelope, payload any) contract.Result {
	var r contract.Result
	var err error
	if !time.Now().Before(env.Deadline.Time) {
		err = contract.Fail(contract.Timeout, "deadline expired before start")
	} else {
		var cancel context.CancelFunc
		ctx, cancel = context.WithDeadline(ctx, env.Deadline.Time)
		defer cancel()
		switch p := payload.(type) {
		case *contract.Pair:
			r, err = w.pair(ctx, env, p)
		case *contract.Sync:
			r, err = w.sync(ctx, env, p)
		case *contract.Send:
			r, err = w.send(ctx, env, p)
		default:
			err = contract.Fail(contract.Unsupported, "unknown kind")
		}
	}
	r.SchemaVersion, r.Status = contract.SchemaVersion, contract.StatusSucceeded
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

// seal stores the kept messages as a whatsapp.messages.private.v1 blob. The key is
// content-addressed, so a redelivered job never overwrites a blob another result points to.
func (w *Worker) seal(ctx context.Context, env contract.Envelope, msgs []contract.Message) (*contract.PrivateRef, error) {
	plain, err := json.Marshal(contract.MessagesPrivate{SchemaVersion: contract.SchemaVersion, Messages: msgs})
	if err != nil {
		return nil, err
	}
	// Re-check with the consumer's decoder: what we write is exactly what the reader accepts.
	if _, err := contract.DecodeMessagesPrivate(plain); err != nil {
		// ponytail: no paging in contracts v1 (no next cursor); add it before real chats exceed 100 messages or 32 KiB.
		return nil, contract.Fail(contract.BudgetExhausted, "synced messages exceed private payload limits")
	}
	ct, sum, err := vault.SealPrivate(plain, w.ResultRecipients...)
	if err != nil {
		return nil, contract.Fail(contract.Internal, "private blob encryption failed")
	}
	_, alias, _ := strings.Cut(env.SessionRef, ":")
	ref := &contract.PrivateRef{BlobKey: "private/whatsapp/" + alias + "/sync-" + sum + ".age", SHA256: sum, RecipientScope: w.ResultScope}
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

// jidUser extracts the phone digits from "user[.agent][:device]@server".
// ponytail: string parsing; the whatsmeow adapter should compare types.JID.User directly.
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

func (w *Worker) sync(ctx context.Context, env contract.Envelope, p *contract.Sync) (contract.Result, error) {
	var r contract.Result
	_, release, err := w.lease(ctx, env.SessionRef)
	if err != nil {
		return r, err
	}
	defer release()
	defer w.Client.Close()
	if err := w.Client.Connect(ctx); err != nil {
		return r, err
	}
	msgs, completed, err := w.Client.Sync(ctx, p.SinceCursor)
	if err != nil {
		return r, err
	}
	if !completed {
		return r, contract.Fail(contract.Timeout, "offline sync not completed before deadline")
	}
	enabled := map[string]bool{}
	for _, c := range p.EnabledChatRefs {
		enabled[c] = true
	}
	var kept []contract.Message
	for _, m := range msgs {
		if enabled[m.ChatRef] { // others are dropped in memory, never logged
			kept = append(kept, m)
		}
	}
	if len(kept) > 0 {
		// Sealed before the snapshot: if storing fails the session is not advanced past them.
		if r.PrivateRef, err = w.seal(ctx, env, kept); err != nil {
			return r, err
		}
		r.MessageCount = len(kept)
	}
	// The private_ref stays in the result even if the snapshot save fails, so messages are not lost.
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
