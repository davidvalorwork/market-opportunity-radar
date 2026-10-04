// Package whatsapp runs pair/sync/send jobs over ports. The whatsmeow client lives
// in wameow; DynamoDB/S3 stores are pending; fakes live in fake.go.
package whatsapp

import (
	"context"
	"errors"
	"io"
	"time"

	"radar.local/radar/internal/contract"
)

var (
	ErrLoggedOut      = errors.New("logged_out")      // whatsmeow events.LoggedOut
	ErrStreamReplaced = errors.New("stream_replaced") // events.StreamReplaced: another client owns the device
	ErrTimeout        = errors.New("timeout")
	ErrConflict       = errors.New("conflict") // lease held by another owner, or a CAS/condition lost
	ErrLeaseLost      = errors.New("lease_lost")
	ErrNotFound       = errors.New("not_found")
)

// Client is implemented by wameow.Client (whatsmeow) and FakeClient.
type Client interface {
	Connect(ctx context.Context) error
	// PairPhone must run right after Connect: the login websocket closes after ~160 s.
	PairPhone(ctx context.Context, phone string) (code string, err error)
	WaitPaired(ctx context.Context) (jid string, err error)
	// Sync returns once OfflineSyncCompleted arrives (completed=true) or ctx ends.
	Sync(ctx context.Context, since string) (msgs []contract.Message, completed bool, err error)
	Send(ctx context.Context, chat, text string) (providerMsgID string, err error)
	Logout(ctx context.Context) error
	Close() error
	// Snapshot writes a consistent copy of the protocol SQLite (disconnect + WAL checkpoint, never a live-file copy).
	Snapshot(w io.Writer) error
}

// LeaseStore serializes jobs per session. The DynamoDB version conditions on expires_at.
type LeaseStore interface {
	Acquire(ctx context.Context, sessionRef, owner string, ttl time.Duration) (token int64, err error)
	Check(ctx context.Context, sessionRef, owner string, token int64) error
	Release(ctx context.Context, sessionRef, owner string, token int64) error
}

type State string

const (
	Proposed          State = "proposed"
	Approved          State = "approved"
	DispatchCommitted State = "dispatch_committed"
	ProviderConfirmed State = "provider_confirmed"
	SendUncertain     State = "send_uncertain"
)

// Record is the ledger entry for one operation_id; the approval binds owner, recipient and content hash.
type Record struct {
	State             State
	OwnerRef          string
	RecipientRef      string
	ApprovalRef       string
	ContentSHA256     string
	LeaseToken        int64
	ProviderMessageID string
}

type Meta struct {
	LeaseToken        int64
	ProviderMessageID string
}

// Ledger transitions are conditional on the current state (ErrConflict otherwise).
type Ledger interface {
	Get(ctx context.Context, opID string) (Record, error)
	Transition(ctx context.Context, opID string, from, to State, meta Meta) error
}

// SessionStore saves a snapshot as version expected+1 only if the current version is expected.
type SessionStore interface {
	Save(ctx context.Context, sessionRef string, expected int, snapshot []byte) (version int, err error)
}

// BlobStore holds immutable age-encrypted private payload blobs (S3 later). Get returns ErrNotFound for a missing key.
type BlobStore interface {
	Put(ctx context.Context, key string, ciphertext []byte) error
	Get(ctx context.Context, key string) ([]byte, error)
}

// Notifier delivers the pairing code to the user (Telegram with protect_content later). Never log the code.
type Notifier interface {
	DeliverCode(ctx context.Context, notifyRef, code string) error
}
