package main

import (
	"strings"
	"testing"

	"radar.local/radar/internal/contract"
)

func envelope(kind, version, payload string) string {
	return `{"schema_version":1,"message_id":"m-1","operation_id":"op-1","correlation_id":"c-1","causation_id":"","owner_ref":"owner-1","kind":"` + kind +
		`","deadline":"2099-01-01T00:00:00Z","attempt":1,"session_ref":"whatsapp:fixture","expected_version":` + version + `,"refs":[],"payload":` + payload + `}`
}

func TestRunner(t *testing.T) {
	send := envelope(contract.KindSend, "1", `{"recipient_ref":"chat-1","approval_ref":"ap-1","content_sha256":"`+contract.SHA256Hex("hola")+`","text":"hola"}`)
	cases := []struct {
		name, in, code string
		fake           bool
	}{
		{"real adapter absent", send, contract.Unsupported, false},
		{"fake send", send, "", true},
		{"fake send with utf-8 bom", "\xef\xbb\xbf" + send, "", true},
		{"fake pair", envelope(contract.KindPair, "0", `{"declared_phone":"+10000000000","notify":"tg-chat-ref","method":"code"}`), "", true},
		{"fake sync", envelope(contract.KindSync, "1", `{"enabled_chat_refs":["chat-1"],"since_cursor":""}`), "", true},
		{"invalid envelope", `{}`, contract.InvalidInput, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := run(c.fake, strings.NewReader(c.in))
			got := ""
			if r.Error != nil {
				got = r.Error.Code
			}
			if got != c.code || (c.code == "") != (r.Status == contract.StatusSucceeded) {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
		})
	}
}
