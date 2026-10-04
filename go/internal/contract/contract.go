// Package contract holds the job envelope and WhatsApp payload/result shapes.
// Field names must stay identical to the JSON Schemas under contracts/.
package contract

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"regexp"
	"time"
)

const (
	SchemaVersion = 1
	MaxEnvelope   = 32768 // bytes; larger data travels as refs with sha256

	KindPair = "whatsapp.pair"
	KindSync = "whatsapp.sync"
	KindSend = "whatsapp.send"

	StatusSucceeded = "succeeded"
	StatusFailed    = "failed"
)

// Error codes. Messages are constant text: never echo input, phone numbers or content.
const (
	NeedsReauth     = "needs_reauth"
	Unsupported     = "unsupported"
	RateLimited     = "rate_limited"
	Blocked         = "blocked"
	EmptyVerified   = "empty_verified"
	Timeout         = "timeout"
	SendUncertain   = "send_uncertain"
	SessionConflict = "session_conflict"
	InvalidInput    = "invalid_input"
	BudgetExhausted = "budget_exhausted"
	Internal        = "internal"
)

type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e *Error) Error() string { return e.Code }

func Fail(code, message string) *Error { return &Error{code, message} }

// Envelope carries no lease: the worker acquires it from the lease store.
type Envelope struct {
	SchemaVersion   int             `json:"schema_version"`
	MessageID       string          `json:"message_id"`
	OperationID     string          `json:"operation_id"`
	CorrelationID   string          `json:"correlation_id"`
	CausationID     string          `json:"causation_id"`
	OwnerRef        string          `json:"owner_ref"`
	Kind            string          `json:"kind"`
	Deadline        time.Time       `json:"deadline"`
	Attempt         int             `json:"attempt"`
	SessionRef      string          `json:"session_ref"`
	ExpectedVersion int             `json:"expected_version"`
	Refs            []Ref           `json:"refs"`
	Payload         json.RawMessage `json:"payload"`
}

type Ref struct {
	BlobKey string `json:"blob_key"`
	SHA256  string `json:"sha256"`
}

type Pair struct {
	DeclaredPhone string `json:"declared_phone"`
	Notify        string `json:"notify"`
	Method        string `json:"method"`
}

type Sync struct {
	EnabledChatRefs []string `json:"enabled_chat_refs"`
	SinceCursor     string   `json:"since_cursor"`
}

type Send struct {
	RecipientRef  string `json:"recipient_ref"`
	ApprovalRef   string `json:"approval_ref"`
	ContentSHA256 string `json:"content_sha256"`
	Text          string `json:"text"`
}

type Result struct {
	Status            string    `json:"status"`
	Error             *Error    `json:"error,omitempty"`
	Pairing           *Pairing  `json:"pairing,omitempty"`
	Messages          []Message `json:"messages,omitempty"`
	ProviderMessageID string    `json:"provider_message_id,omitempty"`
	SessionVersion    int       `json:"session_version,omitempty"`
}

type Pairing struct {
	CodeDelivered bool `json:"code_delivered"`
}

type Message struct {
	ChatRef    string    `json:"chat_ref"`
	Text       string    `json:"text"`
	ObservedAt time.Time `json:"observed_at"`
}

var (
	phone = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)
	hash  = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

func strict(data []byte, v any) bool {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	return d.Decode(v) == nil && d.Decode(new(any)) == io.EOF
}

func id(s string) bool { return s != "" && len(s) <= 128 }

func bad(why string) *Error { return Fail(InvalidInput, why) }

// SHA256Hex is the content hash bound by an approval.
func SHA256Hex(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }

// Decode validates one envelope and returns it with its typed payload (*Pair, *Sync or *Send).
func Decode(data []byte) (Envelope, any, error) {
	var e Envelope
	if len(data) > MaxEnvelope {
		return e, nil, bad("envelope too large")
	}
	if !strict(data, &e) {
		return e, nil, bad("envelope schema rejected")
	}
	if e.SchemaVersion < 1 {
		return e, nil, bad("schema_version missing")
	}
	if e.SchemaVersion != SchemaVersion {
		return e, nil, Fail(Unsupported, "unknown schema_version")
	}
	// ponytail: causation_id may be empty for a root message; reconcile with contracts/.
	if !id(e.MessageID) || !id(e.OperationID) || !id(e.CorrelationID) || len(e.CausationID) > 128 || !id(e.OwnerRef) || !id(e.SessionRef) {
		return e, nil, bad("missing or oversized id")
	}
	if _, off := e.Deadline.Zone(); e.Deadline.IsZero() || off != 0 {
		return e, nil, bad("deadline must be UTC RFC3339")
	}
	if e.Attempt < 1 || e.ExpectedVersion < 0 {
		return e, nil, bad("attempt or expected_version out of range")
	}
	for _, r := range e.Refs {
		if !id(r.BlobKey) || !hash.MatchString(r.SHA256) {
			return e, nil, bad("invalid ref")
		}
	}
	var p any
	switch e.Kind {
	case KindPair:
		v := &Pair{}
		if !strict(e.Payload, v) || !phone.MatchString(v.DeclaredPhone) || !id(v.Notify) || v.Method == "" || e.ExpectedVersion != 0 {
			return e, nil, bad("pair payload rejected")
		}
		p = v
	case KindSync:
		v := &Sync{}
		if !strict(e.Payload, v) || len(v.EnabledChatRefs) == 0 || len(v.EnabledChatRefs) > 256 || len(v.SinceCursor) > 256 || e.ExpectedVersion < 1 {
			return e, nil, bad("sync payload rejected")
		}
		for _, c := range v.EnabledChatRefs {
			if !id(c) {
				return e, nil, bad("sync payload rejected")
			}
		}
		p = v
	case KindSend:
		v := &Send{}
		if !strict(e.Payload, v) || !id(v.RecipientRef) || !id(v.ApprovalRef) || v.Text == "" || !hash.MatchString(v.ContentSHA256) || e.ExpectedVersion < 1 {
			return e, nil, bad("send payload rejected")
		}
		if SHA256Hex(v.Text) != v.ContentSHA256 {
			return e, nil, bad("content hash mismatch")
		}
		p = v
	default:
		return e, nil, Fail(Unsupported, "unknown kind")
	}
	return e, p, nil
}
