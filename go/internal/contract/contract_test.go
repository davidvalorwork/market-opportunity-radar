package contract

import (
	"errors"
	"strings"
	"testing"
)

const (
	head = `"schema_version":1,"message_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC0","operation_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC1","correlation_id":"6f1c2a4e-8b7d-4c3a-9e2f-1a2b3c4d5e6f","owner_ref":"owner:radar-pilot","deadline":"2099-01-01T00:00:00Z","attempt":1,"session_ref":"whatsapp:fixture","refs":[]`
	pref = `"private_ref":{"blob_key":"private/whatsapp/fixture/blob.age","sha256":"` + zero + `","recipient_scope":"worker:whatsapp"}`
	zero = "0000000000000000000000000000000000000000000000000000000000000000"
)

func env(kind string, version int, payload string) string {
	v := "1"
	if version == 0 {
		v = "0"
	}
	return `{` + head + `,"kind":"` + kind + `","expected_version":` + v + `,"payload":` + payload + `}`
}

var (
	pairPayload = `{"schema_version":1,"notify":"tgchat:radar-pilot-owner","method":"code",` + pref + `}`
	syncPayload = `{"schema_version":1,"enabled_chat_refs":["wachat:seller-a"]}`
	sendPayload = `{"schema_version":1,"recipient_ref":"wachat:seller-a","approval_ref":"approval:ap-1","content_sha256":"` + SHA256Hex("fixture text") + `",` + pref + `}`
)

func codeOf(err error) string {
	var ce *Error
	if errors.As(err, &ce) {
		return ce.Code
	}
	if err != nil {
		return "untyped"
	}
	return ""
}

func TestDecode(t *testing.T) {
	send := env(KindSend, 1, sendPayload)
	cases := []struct {
		name, in, code string
	}{
		{"pair ok", env(KindPair, 0, pairPayload), ""},
		{"sync ok", env(KindSync, 1, syncPayload), ""},
		{"sync with cursor ok", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a","wachat:b"],"since_cursor":"c:000123"}`), ""},
		{"send ok", send, ""},
		{"causation present ok", strings.Replace(send, `"attempt":1`, `"attempt":1,"causation_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC3"`, 1), ""},
		{"unknown envelope field", strings.Replace(send, `"attempt":1`, `"attempt":1,"lease":{}`, 1), InvalidInput},
		{"unknown payload field", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a"],"x":1}`), InvalidInput},
		{"text in clear", env(KindSend, 1, strings.Replace(sendPayload, `"recipient_ref"`, `"text":"fixture text","recipient_ref"`, 1)), InvalidInput},
		{"declared phone in clear", env(KindPair, 0, strings.Replace(pairPayload, `"notify"`, `"declared_phone":"+10000000000","notify"`, 1)), InvalidInput},
		{"unknown schema version", strings.Replace(send, `"schema_version":1`, `"schema_version":2`, 1), Unsupported},
		{"missing schema version", strings.Replace(send, `"schema_version":1,`, ``, 1), InvalidInput},
		{"unknown payload schema version", env(KindSync, 1, `{"schema_version":2,"enabled_chat_refs":["wachat:a"]}`), Unsupported},
		{"missing payload schema version", env(KindSync, 1, `{"enabled_chat_refs":["wachat:a"]}`), InvalidInput},
		{"kind outside enum", env("whatsapp.call", 1, `{}`), InvalidInput},
		{"trailing data", send + `{}`, InvalidInput},
		{"oversized", env(KindSend, 1, strings.Replace(sendPayload, `"recipient_ref"`, `"pad":"`+strings.Repeat("x", MaxEnvelope)+`","recipient_ref"`, 1)), InvalidInput},
		{"non utc deadline", strings.Replace(send, `T00:00:00Z`, `T00:00:00-05:00`, 1), InvalidInput},
		{"plus zero deadline", strings.Replace(send, `T00:00:00Z`, `T00:00:00+00:00`, 1), InvalidInput},
		{"impossible deadline", strings.Replace(send, `2099-01-01`, `2099-02-30`, 1), InvalidInput},
		{"missing message id", strings.Replace(send, `"message_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC0"`, `"message_id":""`, 1), InvalidInput},
		{"lowercase ulid", strings.Replace(send, `01J9ZX3Q4R5S6T7V8W9XAYBZC0`, `01j9zx3q4r5s6t7v8w9xaybzc0`, 1), InvalidInput},
		{"causation empty", strings.Replace(send, `"attempt":1`, `"attempt":1,"causation_id":""`, 1), InvalidInput},
		{"causation null", strings.Replace(send, `"attempt":1`, `"attempt":1,"causation_id":null`, 1), InvalidInput},
		{"phone as owner ref", strings.Replace(send, `owner:radar-pilot`, `owner:10000000000`, 1), InvalidInput},
		{"attempt zero", strings.Replace(send, `"attempt":1`, `"attempt":0`, 1), InvalidInput},
		{"bad ref hash", strings.Replace(send, `"refs":[]`, `"refs":[{"blob_key":"k","sha256":"abc"}]`, 1), InvalidInput},
		{"ref traversal", strings.Replace(send, `"refs":[]`, `"refs":[{"blob_key":"sessions/../k","sha256":"`+zero+`"}]`, 1), InvalidInput},
		{"private ref leading slash", strings.Replace(send, `private/whatsapp`, `/private/whatsapp`, 1), InvalidInput},
		{"private ref empty segment", strings.Replace(send, `private/whatsapp`, `private//whatsapp`, 1), InvalidInput},
		{"private ref key as scope", strings.Replace(send, `worker:whatsapp`, `AGE-SECRET-KEY-1SYNTHETIC`, 1), InvalidInput},
		{"private ref uppercase hash", strings.Replace(send, zero, strings.Repeat("A", 64), 1), InvalidInput},
		{"pair needs version zero", env(KindPair, 1, pairPayload), InvalidInput},
		{"pair qr not in contract", env(KindPair, 0, strings.Replace(pairPayload, `"code"`, `"qr"`, 1)), InvalidInput},
		{"pair needs private ref", env(KindPair, 0, `{"schema_version":1,"notify":"tgchat:radar-pilot-owner","method":"code"}`), InvalidInput},
		{"sync needs chats", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":[]}`), InvalidInput},
		{"sync duplicate chats", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a","wachat:a"]}`), InvalidInput},
		{"sync 51 chats", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a`+strings.Repeat(`","wachat:a`, 50)+`"]}`), InvalidInput},
		{"sync cursor traversal", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a"],"since_cursor":"../c"}`), InvalidInput},
		{"sync empty cursor", env(KindSync, 1, `{"schema_version":1,"enabled_chat_refs":["wachat:a"],"since_cursor":""}`), InvalidInput},
		{"send needs session", env(KindSend, 0, sendPayload), InvalidInput},
		{"send needs session ref", strings.Replace(send, `"session_ref":"whatsapp:fixture",`, ``, 1), InvalidInput},
		{"send needs expected version", strings.Replace(send, `,"expected_version":1`, ``, 1), InvalidInput},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, p, err := Decode([]byte(c.in))
			if got := codeOf(err); got != c.code || (c.code == "" && p == nil) {
				t.Fatalf("want %q, got %v", c.code, err)
			}
			if c.code != "" {
				var ce *Error
				errors.As(err, &ce)
				if strings.Contains(ce.Message, "+1000") || strings.Contains(ce.Message, "fixture text") || strings.Contains(ce.Message, "10000000000") {
					t.Fatal("error message echoes input")
				}
			}
		})
	}
}

// Kinds outside this module validate at envelope level only; the worker answers unsupported.
func TestDecodeForeignKind(t *testing.T) {
	in := env("telegram.command", 1, `{"anything":true}`)
	if _, p, err := Decode([]byte(in)); err != nil || p != nil {
		t.Fatalf("payload %v err %v", p, err)
	}
}

func TestDecodePrivate(t *testing.T) {
	cases := []struct {
		name string
		dec  func([]byte) (any, error)
		in   string
		code string
	}{
		{"phone ok", pairPriv, `{"schema_version":1,"declared_phone":"+10000000000"}`, ""},
		{"phone not e164", pairPriv, `{"schema_version":1,"declared_phone":"10000000000"}`, InvalidInput},
		{"phone too long", pairPriv, `{"schema_version":1,"declared_phone":"+1000000000000000"}`, InvalidInput},
		{"pair private v2", pairPriv, `{"schema_version":2,"declared_phone":"+10000000000"}`, Unsupported},
		{"text ok", sendPriv, `{"schema_version":1,"text":"hola"}`, ""},
		{"text 4096 multibyte chars ok", sendPriv, `{"schema_version":1,"text":"` + strings.Repeat("ñ", MaxText) + `"}`, ""},
		{"text 4097 chars", sendPriv, `{"schema_version":1,"text":"` + strings.Repeat("a", MaxText+1) + `"}`, InvalidInput},
		{"text empty", sendPriv, `{"schema_version":1,"text":""}`, InvalidInput},
		{"text missing", sendPriv, `{"schema_version":1}`, InvalidInput},
		{"text extra field", sendPriv, `{"schema_version":1,"text":"hola","recipient_ref":"wachat:a"}`, InvalidInput},
		{"messages ok", msgsPriv, `{"schema_version":1,"messages":[{"chat_ref":"wachat:a","text":"","observed_at":"2026-10-03T12:00:00.5Z"}]}`, ""},
		{"messages empty", msgsPriv, `{"schema_version":1,"messages":[]}`, InvalidInput},
		{"message text missing", msgsPriv, `{"schema_version":1,"messages":[{"chat_ref":"wachat:a","observed_at":"2026-10-03T12:00:00Z"}]}`, InvalidInput},
		{"message local time", msgsPriv, `{"schema_version":1,"messages":[{"chat_ref":"wachat:a","text":"x","observed_at":"2026-10-03T14:00:00+02:00"}]}`, InvalidInput},
		{"message numeric chat ref", msgsPriv, `{"schema_version":1,"messages":[{"chat_ref":"wachat:100","text":"x","observed_at":"2026-10-03T12:00:00Z"}]}`, InvalidInput},
		{"101 messages", msgsPriv, `{"schema_version":1,"messages":[` + strings.Repeat(`{"chat_ref":"wachat:a","text":"x","observed_at":"2026-10-03T12:00:00Z"},`, 100) + `{"chat_ref":"wachat:a","text":"x","observed_at":"2026-10-03T12:00:00Z"}]}`, InvalidInput},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, err := c.dec([]byte(c.in)); codeOf(err) != c.code {
				t.Fatalf("want %q, got %v", c.code, err)
			}
		})
	}
}

func pairPriv(b []byte) (any, error) { return DecodePairPrivate(b) }
func sendPriv(b []byte) (any, error) { return DecodeSendPrivate(b) }
func msgsPriv(b []byte) (any, error) { return DecodeMessagesPrivate(b) }
