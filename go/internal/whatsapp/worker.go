package whatsapp

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"time"

	"radar.local/radar/internal/contract"
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
}

// Handle runs one decoded job. It never logs: payloads hold phones, codes and message text.
func (w *Worker) Handle(ctx context.Context, env contract.Envelope, payload any) contract.Result {
	var r contract.Result
	var err error
	if !time.Now().Before(env.Deadline) {
		err = contract.Fail(contract.Timeout, "deadline expired before start")
	} else {
		var cancel context.CancelFunc
		ctx, cancel = context.WithDeadline(ctx, env.Deadline)
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
	r.Status = contract.StatusSucceeded
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
	code, err := w.Client.PairPhone(ctx, p.DeclaredPhone)
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
	if jidUser(jid) != strings.TrimPrefix(p.DeclaredPhone, "+") {
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
	for _, m := range msgs {
		if enabled[m.ChatRef] { // others are dropped in memory, never logged
			r.Messages = append(r.Messages, m)
		}
	}
	// Messages stay in the result even if the snapshot save fails, so they are not lost.
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
	id, err := w.Client.Send(ctx, p.RecipientRef, p.Text)
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
