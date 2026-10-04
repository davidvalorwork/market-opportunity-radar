package main

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strconv"
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
	contact := `{"schema_version":1,"phone":"+10000000005","source_ref":"listing:synthetic-1","approval_ref":"approval:ap-9"}`
	v2 := func(e string) string {
		return strings.Replace(e, `{"schema_version":1,"message_id"`, `{"schema_version":2,"message_id"`, 1)
	}
	cases := []struct {
		name, in, private, code string
		fake                    bool
	}{
		{"no mode selected", send, hola, contract.Unsupported, false},
		{"fake send", send, hola, "", true},
		{"fake send with utf-8 bom", "\xef\xbb\xbf" + send, hola, "", true},
		{"fake send without private", send, "", contract.InvalidInput, true},
		{"fake send private hash mismatch", send, `{"schema_version":1,"text":"adios"}`, contract.InvalidInput, true},
		{"fake pair", pair, phone, "", true},
		{"fake pair bad private", pair, `{"schema_version":1,"declared_phone":"10000000000"}`, contract.InvalidInput, true},
		{"fake sync", envelope(contract.KindSync, "1", `{"schema_version":1,"enabled_chat_refs":["wachat:seller-a"]}`), "", "", true},
		{"foreign kind", envelope("telegram.command", "0", `{"schema_version":1}`), "", contract.Unsupported, true},
		{"fake v2 send", v2(send), hola, "", true},
		{"fake v2 sync page", v2(envelope(contract.KindSync, "1", `{"schema_version":2,"enabled_chat_refs":["wachat:seller-a"],"page_size":10}`)), "", "", true},
		{"fake v2 list chats", v2(envelope(contract.KindListChats, "1", `{"schema_version":2,"page_size":10}`)), "", "", true},
		{"fake v2 resolve contact", v2(envelope(contract.KindResolveContact, "1", `{"schema_version":2,`+pref+`}`)), contact, "", true},
		{"fake v2 resolve without private", v2(envelope(contract.KindResolveContact, "1", `{"schema_version":2,`+pref+`}`)), "", contract.InvalidInput, true},
		{"fake v2 resolve bad private", v2(envelope(contract.KindResolveContact, "1", `{"schema_version":2,`+pref+`}`)), strings.Replace(contact, `"+1`, `"1`, 1), contract.InvalidInput, true},
		{"invalid envelope", `{}`, "", contract.InvalidInput, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := run(opts{fake: c.fake, private: c.private}, strings.NewReader(c.in))
			got := ""
			if r.Error != nil {
				got = r.Error.Code
			}
			if got != c.code || (c.code == "") != (r.Status == contract.StatusSucceeded) {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			schematest.Validate(t, "whatsapp.result.v"+strconv.Itoa(r.SchemaVersion), r)
			if out, _ := json.Marshal(r); strings.Contains(string(out), "synthetic") || strings.Contains(string(out), "hola") || strings.Contains(string(out), "FAKE0000") || strings.Contains(string(out), "10000000005") {
				t.Fatalf("private data in result: %s", out)
			}
		})
	}
}

// failReader fails the test if the gate lets a refused run read stdin.
type failReader struct{ t *testing.T }

func (f failReader) Read([]byte) (int, error) {
	f.t.Fatal("stdin read by a refused run")
	return 0, io.EOF
}

// The --real gate refuses before reading stdin or touching disk. No case here can reach
// wameow.Client.Connect, so no test dials WhatsApp.
func TestRealGate(t *testing.T) {
	dir := t.TempDir()
	send := envelope(contract.KindSend, "1", `{"schema_version":1,"recipient_ref":"wachat:seller-a","approval_ref":"approval:ap-1","content_sha256":"`+contract.SHA256Hex("hola")+`",`+pref+`}`)
	cases := []struct {
		name string
		o    opts
		in   io.Reader
	}{
		{"real without authorization", opts{real: true, sessionDir: dir}, failReader{t}},
		{"real without session dir", opts{real: true, authorized: true}, failReader{t}},
		{"real and fake together", opts{real: true, fake: true, authorized: true, sessionDir: dir}, failReader{t}},
		// Authorized but the directory does not exist: wameow.New refuses before any client exists.
		{"real with missing session dir", opts{real: true, authorized: true, sessionDir: filepath.Join(dir, "missing"), private: `{"schema_version":1,"text":"hola"}`}, strings.NewReader(send)},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := run(c.o, c.in)
			if r.Status == contract.StatusSucceeded || r.Error == nil || r.Error.Code != contract.InvalidInput {
				t.Fatalf("result %+v error %+v", r, r.Error)
			}
			schematest.Validate(t, "whatsapp.result.v1", r)
			if left, _ := os.ReadDir(dir); len(left) != 0 {
				t.Fatalf("refused run created files: %v", left)
			}
		})
	}
}
