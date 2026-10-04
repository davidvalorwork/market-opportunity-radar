package main

import (
	"encoding/json"
	"strings"
	"testing"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/schematest"
)

const pref = `"private_ref":{"blob_key":"private/whatsapp/fixture/blob.age","sha256":"0000000000000000000000000000000000000000000000000000000000000000","recipient_scope":"worker:whatsapp"}`

func envelope(kind, version, payload string) string {
	return `{"schema_version":1,"message_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC0","operation_id":"01J9ZX3Q4R5S6T7V8W9XAYBZC1","correlation_id":"6f1c2a4e-8b7d-4c3a-9e2f-1a2b3c4d5e6f","owner_ref":"owner:radar-pilot","kind":"` + kind +
		`","deadline":"2099-01-01T00:00:00Z","attempt":1,"session_ref":"whatsapp:fixture","expected_version":` + version + `,"payload":` + payload + `}`
}

func TestRunner(t *testing.T) {
	send := envelope(contract.KindSend, "1", `{"schema_version":1,"recipient_ref":"wachat:seller-a","approval_ref":"approval:ap-1","content_sha256":"`+contract.SHA256Hex("hola")+`",`+pref+`}`)
	pair := envelope(contract.KindPair, "0", `{"schema_version":1,"notify":"tgchat:radar-pilot-owner","method":"code",`+pref+`}`)
	hola := `{"schema_version":1,"text":"hola"}`
	phone := `{"schema_version":1,"declared_phone":"+10000000000"}`
	cases := []struct {
		name, in, private, code string
		fake                    bool
	}{
		{"real adapter absent", send, hola, contract.Unsupported, false},
		{"fake send", send, hola, "", true},
		{"fake send with utf-8 bom", "\xef\xbb\xbf" + send, hola, "", true},
		{"fake send without private", send, "", contract.InvalidInput, true},
		{"fake send private hash mismatch", send, `{"schema_version":1,"text":"adios"}`, contract.InvalidInput, true},
		{"fake pair", pair, phone, "", true},
		{"fake pair bad private", pair, `{"schema_version":1,"declared_phone":"10000000000"}`, contract.InvalidInput, true},
		{"fake sync", envelope(contract.KindSync, "1", `{"schema_version":1,"enabled_chat_refs":["wachat:seller-a"]}`), "", "", true},
		{"foreign kind", envelope("telegram.command", "0", `{"schema_version":1}`), "", contract.Unsupported, true},
		{"invalid envelope", `{}`, "", contract.InvalidInput, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := run(c.fake, c.private, strings.NewReader(c.in))
			got := ""
			if r.Error != nil {
				got = r.Error.Code
			}
			if got != c.code || (c.code == "") != (r.Status == contract.StatusSucceeded) {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			schematest.Validate(t, "whatsapp.result.v1", r)
			if out, _ := json.Marshal(r); strings.Contains(string(out), "synthetic") || strings.Contains(string(out), "hola") || strings.Contains(string(out), "FAKE0000") {
				t.Fatalf("private data in result: %s", out)
			}
		})
	}
}
