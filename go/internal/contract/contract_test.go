package contract

import (
	"errors"
	"strings"
	"testing"
)

const head = `"schema_version":1,"message_id":"m-1","operation_id":"op-1","correlation_id":"c-1","causation_id":"","owner_ref":"owner-1","deadline":"2099-01-01T00:00:00Z","attempt":1,"session_ref":"whatsapp:fixture","refs":[]`

func env(kind string, version int, payload string) string {
	v := "1"
	if version == 0 {
		v = "0"
	}
	return `{` + head + `,"kind":"` + kind + `","expected_version":` + v + `,"payload":` + payload + `}`
}

var text = "fixture text"
var sendPayload = `{"recipient_ref":"chat-1","approval_ref":"ap-1","content_sha256":"` + SHA256Hex(text) + `","text":"` + text + `"}`

func TestDecode(t *testing.T) {
	cases := []struct {
		name, in, code string
	}{
		{"pair ok", env(KindPair, 0, `{"declared_phone":"+10000000000","notify":"tg-chat-ref","method":"code"}`), ""},
		{"sync ok", env(KindSync, 1, `{"enabled_chat_refs":["chat-1"],"since_cursor":""}`), ""},
		{"send ok", env(KindSend, 1, sendPayload), ""},
		{"unknown envelope field", strings.Replace(env(KindSend, 1, sendPayload), `"attempt":1`, `"attempt":1,"lease":{}`, 1), InvalidInput},
		{"unknown payload field", env(KindSync, 1, `{"enabled_chat_refs":["chat-1"],"since_cursor":"","x":1}`), InvalidInput},
		{"unknown schema version", strings.Replace(env(KindSend, 1, sendPayload), `"schema_version":1`, `"schema_version":2`, 1), Unsupported},
		{"unknown kind", env("whatsapp.call", 1, `{}`), Unsupported},
		{"trailing data", env(KindSend, 1, sendPayload) + `{}`, InvalidInput},
		{"oversized", env(KindSend, 1, `{"recipient_ref":"chat-1","approval_ref":"ap-1","content_sha256":"`+SHA256Hex(text)+`","text":"`+strings.Repeat("x", MaxEnvelope)+`"}`), InvalidInput},
		{"non utc deadline", strings.Replace(env(KindSend, 1, sendPayload), `T00:00:00Z`, `T00:00:00-05:00`, 1), InvalidInput},
		{"missing message id", strings.Replace(env(KindSend, 1, sendPayload), `"message_id":"m-1"`, `"message_id":""`, 1), InvalidInput},
		{"attempt zero", strings.Replace(env(KindSend, 1, sendPayload), `"attempt":1`, `"attempt":0`, 1), InvalidInput},
		{"bad ref hash", strings.Replace(env(KindSend, 1, sendPayload), `"refs":[]`, `"refs":[{"blob_key":"k","sha256":"abc"}]`, 1), InvalidInput},
		{"content hash mismatch", env(KindSend, 1, strings.Replace(sendPayload, `"text":"fixture text"`, `"text":"other text"`, 1)), InvalidInput},
		{"bad phone", env(KindPair, 0, `{"declared_phone":"10000000000","notify":"tg","method":"code"}`), InvalidInput},
		{"pair needs version zero", env(KindPair, 1, `{"declared_phone":"+10000000000","notify":"tg","method":"code"}`), InvalidInput},
		{"sync needs chats", env(KindSync, 1, `{"enabled_chat_refs":[],"since_cursor":""}`), InvalidInput},
		{"send needs session", env(KindSend, 0, sendPayload), InvalidInput},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, p, err := Decode([]byte(c.in))
			if c.code == "" {
				if err != nil || p == nil {
					t.Fatalf("want ok, got %v", err)
				}
				return
			}
			var ce *Error
			if !errors.As(err, &ce) || ce.Code != c.code {
				t.Fatalf("want %s, got %v", c.code, err)
			}
			if strings.Contains(ce.Message, "+1000") || strings.Contains(ce.Message, "fixture text") {
				t.Fatal("error message echoes input")
			}
		})
	}
}
