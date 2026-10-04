// Package contract holds the job envelope and WhatsApp payload/result shapes of
// contracts v1 and v2 (envelope, sync, result, list_chats, resolve_contact). The JSON Schemas in src/radar/schemas are the source of truth;
// go/README.md lists the few schema rules this package does not enforce.
package contract

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"regexp"
	"slices"
	"time"
	"unicode/utf8"
)

const (
	SchemaVersion = 1
	SchemaV2      = 2
	MaxEnvelope   = 32768 // bytes of any contract document, private payloads included; larger data travels as refs
	MaxText       = 4096  // characters of a sent or synced message
	MaxMessages   = 100
	MaxChats      = 50
	MaxPage       = 100 // v2 page_size and chat_count

	KindPair   = "whatsapp.pair"
	KindSync   = "whatsapp.sync"
	KindSend   = "whatsapp.send"
	KindResult = "whatsapp.result"
	// v2 only
	KindListChats      = "whatsapp.list_chats"
	KindResolveContact = "whatsapp.resolve_contact"

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
	NotOnWhatsApp   = "not_on_whatsapp" // whatsapp.result.v2 only
)

var codes = map[string]bool{NeedsReauth: true, Unsupported: true, RateLimited: true, Blocked: true, EmptyVerified: true,
	Timeout: true, SendUncertain: true, SessionConflict: true, InvalidInput: true, BudgetExhausted: true, Internal: true}

// kinds is the envelope.v1 kind enum. Only pair/sync/send run here.
var kinds = map[string]bool{"browser.read": true, "browser.result": true, KindPair: true, KindSync: true,
	KindSend: true, KindResult: true, "telegram.command": true}

// kindsV2 is the envelope.v2 kind enum.
var kindsV2 = map[string]bool{"browser.read": true, "browser.result": true, KindPair: true, KindSync: true,
	KindSend: true, KindResult: true, "telegram.command": true, KindListChats: true, KindResolveContact: true}

type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e *Error) Error() string { return e.Code }

func Fail(code, message string) *Error { return &Error{code, message} }

// Failed is a whatsapp.result.v1 failure.
func Failed(e *Error) Result {
	return Result{SchemaVersion: SchemaVersion, Status: StatusFailed, Error: e}
}

// Time is an RFC 3339 instant in UTC with a Z suffix (common.v1 utc_datetime).
type Time struct{ time.Time }

func (t *Time) UnmarshalJSON(b []byte) error {
	var s string
	if json.Unmarshal(b, &s) != nil || !utcRe.MatchString(s) {
		return bad("timestamp must be UTC RFC3339 with Z")
	}
	v, err := time.Parse(time.RFC3339Nano, s) // rejects impossible dates such as 30 February
	if err != nil {
		return bad("timestamp must be UTC RFC3339 with Z")
	}
	t.Time = v
	return nil
}

func (t Time) MarshalJSON() ([]byte, error) { return json.Marshal(t.UTC().Format(time.RFC3339Nano)) }

// Envelope carries no lease: the worker acquires it from the lease store.
type Envelope struct {
	SchemaVersion   int             `json:"schema_version"`
	MessageID       string          `json:"message_id"`
	OperationID     string          `json:"operation_id"`
	CorrelationID   string          `json:"correlation_id"`
	CausationID     string          `json:"causation_id,omitempty"` // omitted on the first message, never "" or null
	OwnerRef        string          `json:"owner_ref"`
	Kind            string          `json:"kind"`
	Deadline        Time            `json:"deadline"`
	Attempt         int             `json:"attempt"`
	SessionRef      string          `json:"session_ref,omitempty"`
	ExpectedVersion int             `json:"expected_version"`
	Refs            []Ref           `json:"refs,omitempty"`
	Payload         json.RawMessage `json:"payload"`
}

type Ref struct {
	BlobKey string `json:"blob_key"`
	SHA256  string `json:"sha256"`
}

// PrivateRef points to an age-encrypted private payload blob. SHA256 is the
// ciphertext hash; RecipientScope names who may decrypt it, never a key.
type PrivateRef struct {
	BlobKey        string `json:"blob_key"`
	SHA256         string `json:"sha256"`
	RecipientScope string `json:"recipient_scope"`
}

func (r PrivateRef) valid() bool {
	return blobKey(r.BlobKey) && hashRe.MatchString(r.SHA256) && scopeRe.MatchString(r.RecipientScope)
}

type Pair struct {
	SchemaVersion int        `json:"schema_version"`
	Notify        string     `json:"notify"`
	Method        string     `json:"method"`
	PrivateRef    PrivateRef `json:"private_ref"` // whatsapp.pair.private.v1
}

type PairPrivate struct {
	SchemaVersion int    `json:"schema_version"`
	DeclaredPhone string `json:"declared_phone"`
}

// Sync is whatsapp.sync v1 or v2; only v2 has page_size (v1 rejects it).
type Sync struct {
	SchemaVersion   int      `json:"schema_version"`
	EnabledChatRefs []string `json:"enabled_chat_refs"`
	SinceCursor     string   `json:"since_cursor,omitempty"`
	PageSize        int      `json:"page_size,omitempty"`
}

// ListChats is whatsapp.list_chats.v2.
type ListChats struct {
	SchemaVersion int    `json:"schema_version"`
	PageSize      int    `json:"page_size"`
	SinceCursor   string `json:"since_cursor,omitempty"`
}

// ResolveContact is whatsapp.resolve_contact.v2; the phone travels only in ContactPrivate.
type ResolveContact struct {
	SchemaVersion int        `json:"schema_version"`
	PrivateRef    PrivateRef `json:"private_ref"` // whatsapp.contact.private.v1
}

// ContactPrivate is whatsapp.contact.private.v1: a number the seller published for that sale.
type ContactPrivate struct {
	SchemaVersion int    `json:"schema_version"`
	Phone         string `json:"phone"`
	SourceRef     string `json:"source_ref"`
	ApprovalRef   string `json:"approval_ref"`
}

// ChatsPrivate is whatsapp.chats.private.v1 (display names are personal data).
type ChatsPrivate struct {
	SchemaVersion int    `json:"schema_version"`
	Chats         []Chat `json:"chats"`
}

type Chat struct {
	ChatRef       string `json:"chat_ref"`
	DisplayName   string `json:"display_name,omitempty"`
	LastMessageAt *Time  `json:"last_message_at,omitempty"` // absent for a resolved chat that never wrote
}

type Send struct {
	SchemaVersion int        `json:"schema_version"`
	RecipientRef  string     `json:"recipient_ref"`
	ApprovalRef   string     `json:"approval_ref"`
	ContentSHA256 string     `json:"content_sha256"`
	PrivateRef    PrivateRef `json:"private_ref"` // whatsapp.send.private.v1
}

type SendPrivate struct {
	SchemaVersion int    `json:"schema_version"`
	Text          string `json:"text"`
}

// Result is whatsapp.result v1 or v2 (SchemaVersion decides; v2 fields stay empty in v1).
// Synced messages and chat lists never appear here: only their count and the
// private_ref of a whatsapp.messages.private.v1 / whatsapp.chats.private.v1 blob.
type Result struct {
	SchemaVersion     int         `json:"schema_version"`
	Status            string      `json:"status"`
	Error             *Error      `json:"error,omitempty"`
	Pairing           *Pairing    `json:"pairing,omitempty"`
	MessageCount      int         `json:"message_count,omitempty"`
	ChatCount         int         `json:"chat_count,omitempty"` // v2
	PrivateRef        *PrivateRef `json:"private_ref,omitempty"`
	ChatRef           string      `json:"chat_ref,omitempty"`    // v2 resolve_contact
	NextCursor        string      `json:"next_cursor,omitempty"` // v2
	HasMore           bool        `json:"has_more,omitempty"`    // v2; absent = false
	ProviderMessageID string      `json:"provider_message_id,omitempty"`
	SessionVersion    int         `json:"session_version,omitempty"`
}

type Pairing struct {
	CodeDelivered bool `json:"code_delivered"`
}

type MessagesPrivate struct {
	SchemaVersion int       `json:"schema_version"`
	Messages      []Message `json:"messages"`
}

type Message struct {
	ChatRef    string `json:"chat_ref"`
	Text       string `json:"text"`
	ObservedAt Time   `json:"observed_at"`
}

var (
	idRe       = regexp.MustCompile(`^([0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$`)
	opaqueRe   = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*$`)
	sessionRe  = regexp.MustCompile(`^[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9-]{0,62}$`)
	blobRe     = regexp.MustCompile(`^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*(/[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)*$`)
	scopeRe    = regexp.MustCompile(`^[a-z][a-z0-9_]{0,31}:[a-z][a-z0-9_-]{0,63}$`)
	hashRe     = regexp.MustCompile(`^[0-9a-f]{64}$`)
	cursorRe   = regexp.MustCompile(`^[A-Za-z0-9_.:=-]{1,256}$`)
	providerRe = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)
	phoneRe    = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)
	utcRe      = regexp.MustCompile(`^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,9})?Z$`)
)

func opaque(s string) bool  { return len(s) <= 129 && opaqueRe.MatchString(s) }
func blobKey(s string) bool { return len(s) <= 256 && blobRe.MatchString(s) }
func chars(s string) int    { return utf8.RuneCountInString(s) } // JSON Schema lengths count code points

func bad(why string) *Error { return Fail(InvalidInput, why) }

// SHA256Hex is the content hash bound by an approval.
func SHA256Hex(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }

func hasNull(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case map[string]any:
		for _, x := range t {
			if hasNull(x) {
				return true
			}
		}
	case []any:
		for _, x := range t {
			if hasNull(x) {
				return true
			}
		}
	}
	return false
}

func has(obj any, key string) bool { m, _ := obj.(map[string]any); _, ok := m[key]; return ok }

// parse applies the rules every contract document shares: at most MaxEnvelope bytes, a single
// JSON object, no null anywhere (no schema allows it), schema_version in versions (default
// "1"; missing = invalid_input, other = unsupported), unknown fields rejected. It returns the
// generic tree for presence checks that Go zero values cannot express.
func parse(data []byte, v any, versions ...json.Number) (map[string]any, error) {
	if len(versions) == 0 {
		versions = []json.Number{"1"}
	}
	if len(data) > MaxEnvelope {
		return nil, bad("document too large")
	}
	var tree any
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if d.Decode(&tree) != nil || d.Decode(new(any)) != io.EOF || hasNull(tree) {
		return nil, bad("schema rejected")
	}
	m, ok := tree.(map[string]any)
	if !ok {
		return nil, bad("schema rejected")
	}
	switch n, ok := m["schema_version"].(json.Number); {
	case !has(m, "schema_version"):
		return nil, bad("schema_version missing")
	case !ok:
		return nil, bad("schema rejected")
	case !slices.Contains(versions, n):
		return nil, Fail(Unsupported, "unknown schema_version")
	}
	s := json.NewDecoder(bytes.NewReader(data))
	s.DisallowUnknownFields()
	if s.Decode(v) != nil {
		return nil, bad("schema rejected")
	}
	return m, nil
}

// Decode validates one envelope.v1 or envelope.v2 (consumers accept both during the
// migration). The payload is *Pair, *Sync, *Send, *ListChats or *ResolveContact for jobs
// this module runs, *Result for whatsapp.result, and nil for browser.* and telegram.*
// kinds, whose payload schemas are not checked here. In a v2 envelope sync, result,
// list_chats and resolve_contact carry v2 payloads; pair and send keep their v1 payloads.
func Decode(data []byte) (Envelope, any, error) {
	var e Envelope
	m, err := parse(data, &e, "1", "2")
	if err != nil {
		return e, nil, err
	}
	known := kinds
	if e.SchemaVersion == SchemaV2 {
		known = kindsV2
	}
	switch {
	case !idRe.MatchString(e.MessageID) || !idRe.MatchString(e.OperationID) || !idRe.MatchString(e.CorrelationID) ||
		(has(m, "causation_id") && !idRe.MatchString(e.CausationID)):
		return e, nil, bad("invalid id")
	case !opaque(e.OwnerRef):
		return e, nil, bad("invalid owner_ref")
	case !known[e.Kind]:
		return e, nil, bad("unknown kind")
	case !has(m, "deadline"):
		return e, nil, bad("deadline must be UTC RFC3339 with Z")
	case e.Attempt < 1 || e.ExpectedVersion < 0:
		return e, nil, bad("attempt or expected_version out of range")
	case has(m, "session_ref") && !sessionRe.MatchString(e.SessionRef):
		return e, nil, bad("invalid session_ref")
	case len(e.Refs) > 16:
		return e, nil, bad("too many refs")
	}
	if _, ok := m["payload"].(map[string]any); !ok {
		return e, nil, bad("payload must be an object")
	}
	for _, r := range e.Refs {
		if !blobKey(r.BlobKey) || !hashRe.MatchString(r.SHA256) {
			return e, nil, bad("invalid ref")
		}
	}
	var p any
	switch e.Kind {
	case KindPair, KindSync, KindSend, KindListChats, KindResolveContact:
		if !has(m, "session_ref") || !has(m, "expected_version") || (e.Kind == KindPair) != (e.ExpectedVersion == 0) {
			return e, nil, bad("session_ref or expected_version invalid for kind")
		}
		if e.SchemaVersion == SchemaV2 {
			p, err = DecodePayloadV2(e.Kind, e.Payload)
		} else {
			p, err = DecodePayload(e.Kind, e.Payload)
		}
	case KindResult:
		if e.SchemaVersion == SchemaV2 {
			p, err = DecodeResultV2(e.Payload)
		} else {
			p, err = DecodeResult(e.Payload)
		}
	}
	if err != nil {
		return e, nil, err
	}
	return e, p, nil
}

// DecodePayload validates a whatsapp.pair/sync/send v1 transport payload.
func DecodePayload(kind string, data []byte) (any, error) {
	switch kind {
	case KindPair:
		v := &Pair{}
		if _, err := parse(data, v); err != nil {
			return nil, err
		}
		if !opaque(v.Notify) || v.Method != "code" || !v.PrivateRef.valid() {
			return nil, bad("pair payload rejected")
		}
		return v, nil
	case KindSync:
		v := &Sync{}
		m, err := parse(data, v)
		if err != nil {
			return nil, err
		}
		if has(m, "page_size") || !v.chatsValid(m) {
			return nil, bad("sync payload rejected")
		}
		return v, nil
	case KindSend:
		v := &Send{}
		if _, err := parse(data, v); err != nil {
			return nil, err
		}
		if !opaque(v.RecipientRef) || !opaque(v.ApprovalRef) || !hashRe.MatchString(v.ContentSHA256) || !v.PrivateRef.valid() {
			return nil, bad("send payload rejected")
		}
		return v, nil
	}
	return nil, Fail(Unsupported, "unknown kind")
}

// DecodePayloadV2 validates the payload of an envelope.v2 job: whatsapp.sync/list_chats/
// resolve_contact v2, or the v1 pair/send payloads that envelope.v2 reuses.
func DecodePayloadV2(kind string, data []byte) (any, error) {
	switch kind {
	case KindPair, KindSend:
		return DecodePayload(kind, data)
	case KindSync:
		v := &Sync{}
		m, err := parse(data, v, "2")
		if err != nil {
			return nil, err
		}
		if v.PageSize < 1 || v.PageSize > MaxPage || !v.chatsValid(m) {
			return nil, bad("sync payload rejected")
		}
		return v, nil
	case KindListChats:
		v := &ListChats{}
		m, err := parse(data, v, "2")
		if err != nil {
			return nil, err
		}
		if v.PageSize < 1 || v.PageSize > MaxPage || (has(m, "since_cursor") && !cursorRe.MatchString(v.SinceCursor)) {
			return nil, bad("list_chats payload rejected")
		}
		return v, nil
	case KindResolveContact:
		v := &ResolveContact{}
		if _, err := parse(data, v, "2"); err != nil {
			return nil, err
		}
		if !v.PrivateRef.valid() {
			return nil, bad("resolve_contact payload rejected")
		}
		return v, nil
	}
	return nil, Fail(Unsupported, "unknown kind")
}

func (v *Sync) chatsValid(m map[string]any) bool {
	if len(v.EnabledChatRefs) == 0 || len(v.EnabledChatRefs) > MaxChats || (has(m, "since_cursor") && !cursorRe.MatchString(v.SinceCursor)) {
		return false
	}
	seen := map[string]bool{}
	for _, c := range v.EnabledChatRefs {
		if !opaque(c) || seen[c] {
			return false
		}
		seen[c] = true
	}
	return true
}

// DecodePairPrivate validates a decrypted whatsapp.pair.private.v1 payload.
func DecodePairPrivate(data []byte) (*PairPrivate, error) {
	v := &PairPrivate{}
	if _, err := parse(data, v); err != nil {
		return nil, err
	}
	if !phoneRe.MatchString(v.DeclaredPhone) {
		return nil, bad("private pair payload rejected")
	}
	return v, nil
}

// DecodeContactPrivate validates a decrypted whatsapp.contact.private.v1 payload.
func DecodeContactPrivate(data []byte) (*ContactPrivate, error) {
	v := &ContactPrivate{}
	if _, err := parse(data, v); err != nil {
		return nil, err
	}
	if !phoneRe.MatchString(v.Phone) || !opaque(v.SourceRef) || !opaque(v.ApprovalRef) {
		return nil, bad("private contact payload rejected")
	}
	return v, nil
}

// DecodeSendPrivate validates a decrypted whatsapp.send.private.v1 payload. The caller
// still checks sha256(text) against the transport content_sha256.
func DecodeSendPrivate(data []byte) (*SendPrivate, error) {
	v := &SendPrivate{}
	if _, err := parse(data, v); err != nil {
		return nil, err
	}
	if n := chars(v.Text); n < 1 || n > MaxText {
		return nil, bad("private send payload rejected")
	}
	return v, nil
}

// DecodeMessagesPrivate validates a decrypted whatsapp.messages.private.v1 payload.
func DecodeMessagesPrivate(data []byte) (*MessagesPrivate, error) {
	v := &MessagesPrivate{}
	m, err := parse(data, v)
	if err != nil {
		return nil, err
	}
	items, _ := m["messages"].([]any)
	if len(v.Messages) == 0 || len(v.Messages) > MaxMessages {
		return nil, bad("private messages payload rejected")
	}
	for i, msg := range v.Messages {
		if !opaque(msg.ChatRef) || !has(items[i], "text") || chars(msg.Text) > MaxText || msg.ObservedAt.IsZero() {
			return nil, bad("private messages payload rejected")
		}
	}
	return v, nil
}

// DecodeChatsPrivate validates a decrypted whatsapp.chats.private.v1 payload.
func DecodeChatsPrivate(data []byte) (*ChatsPrivate, error) {
	v := &ChatsPrivate{}
	m, err := parse(data, v)
	if err != nil {
		return nil, err
	}
	items, _ := m["chats"].([]any)
	if len(v.Chats) == 0 || len(v.Chats) > MaxPage {
		return nil, bad("private chats payload rejected")
	}
	for i, c := range v.Chats {
		named := has(items[i], "display_name")
		if !opaque(c.ChatRef) || (named && (chars(c.DisplayName) < 1 || chars(c.DisplayName) > 128)) ||
			(has(items[i], "last_message_at") && c.LastMessageAt.IsZero()) {
			return nil, bad("private chats payload rejected")
		}
	}
	return v, nil
}

// DecodeResult validates a whatsapp.result.v1 payload.
func DecodeResult(data []byte) (*Result, error) {
	return decodeResult(data, "1")
}

// DecodeResultV2 validates a whatsapp.result.v2 payload.
func DecodeResultV2(data []byte) (*Result, error) {
	return decodeResult(data, "2")
}

func decodeResult(data []byte, version json.Number) (*Result, error) {
	r := &Result{}
	m, err := parse(data, r, version)
	if err != nil {
		return nil, err
	}
	v2only := has(m, "chat_count") || has(m, "chat_ref") || has(m, "next_cursor") || has(m, "has_more")
	if (version == "1" && v2only) || (has(m, "provider_message_id") && r.ProviderMessageID == "") ||
		(has(m, "chat_ref") && r.ChatRef == "") || (has(m, "next_cursor") && r.NextCursor == "") ||
		(r.Pairing != nil && !has(m["pairing"], "code_delivered")) || (has(m, "message_count") && has(m, "chat_count") && r.MessageCount > 0) {
		return nil, bad("result rejected")
	}
	if err := r.Check(); err != nil {
		return nil, err
	}
	return r, nil
}

// Check enforces the whatsapp.result v1/v2 rules a Go value can break.
func (r *Result) Check() error {
	v2 := r.SchemaVersion == SchemaV2
	switch {
	case r.SchemaVersion != SchemaVersion && !v2,
		r.Status != StatusSucceeded && r.Status != StatusFailed,
		(r.Error != nil) != (r.Status == StatusFailed),
		r.Error != nil && (!(codes[r.Error.Code] || (v2 && r.Error.Code == NotOnWhatsApp)) || chars(r.Error.Message) > 512),
		r.MessageCount < 0 || r.MessageCount > MaxMessages || (r.MessageCount > 0 && r.PrivateRef == nil),
		!v2 && (r.ChatCount != 0 || r.ChatRef != "" || r.NextCursor != "" || r.HasMore),
		r.ChatCount < 0 || r.ChatCount > MaxPage || (r.ChatCount > 0 && r.PrivateRef == nil) || (r.ChatCount > 0 && r.MessageCount > 0),
		r.ChatRef != "" && !opaque(r.ChatRef),
		r.NextCursor != "" && !cursorRe.MatchString(r.NextCursor),
		r.PrivateRef != nil && !r.PrivateRef.valid(),
		r.ProviderMessageID != "" && !providerRe.MatchString(r.ProviderMessageID),
		r.SessionVersion < 0:
		return bad("result rejected")
	}
	return nil
}
