package contract

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"radar.local/radar/internal/schematest"
)

var decoders = map[string]func([]byte) (any, error){
	"envelope.v1":                  envelopeOf(SchemaVersion),
	"envelope.v2":                  envelopeOf(SchemaV2),
	"whatsapp.sync.v2":             func(b []byte) (any, error) { return DecodePayloadV2(KindSync, b) },
	"whatsapp.list_chats.v2":       func(b []byte) (any, error) { return DecodePayloadV2(KindListChats, b) },
	"whatsapp.resolve_contact.v2":  func(b []byte) (any, error) { return DecodePayloadV2(KindResolveContact, b) },
	"whatsapp.result.v2":           func(b []byte) (any, error) { return DecodeResultV2(b) },
	"whatsapp.contact.private.v1":  contactPriv,
	"whatsapp.chats.private.v1":    chatsPriv,
	"whatsapp.pair.v1":             func(b []byte) (any, error) { return DecodePayload(KindPair, b) },
	"whatsapp.sync.v1":             func(b []byte) (any, error) { return DecodePayload(KindSync, b) },
	"whatsapp.send.v1":             func(b []byte) (any, error) { return DecodePayload(KindSend, b) },
	"whatsapp.result.v1":           func(b []byte) (any, error) { return DecodeResult(b) },
	"whatsapp.pair.private.v1":     pairPriv,
	"whatsapp.send.private.v1":     sendPriv,
	"whatsapp.messages.private.v1": msgsPriv,
}

// envelopeOf decodes with Decode (which accepts v1 and v2) and pins the schema's version.
func envelopeOf(version int) func([]byte) (any, error) {
	return func(b []byte) (any, error) {
		e, _, err := Decode(b)
		if err == nil && e.SchemaVersion != version {
			err = Fail(Unsupported, "unknown schema_version")
		}
		return e, err
	}
}

// notEnforced lists invalid golden examples Go accepts on purpose because the rule is
// schema-only here. Keep in sync with the table in go/README.md.
var notEnforced = map[string]string{
	"envelope.v1/payload-kind-mismatch.json": "browser.* and telegram.* payload schemas are not checked in Go",
}

// TestGoldenExamples decodes contracts/examples with the Go decoders: every valid example
// must decode and re-marshal to schema-valid JSON; every invalid one must be rejected
// unless listed in notEnforced.
func TestGoldenExamples(t *testing.T) {
	root := schematest.Root(t)
	names := make([]string, 0, len(decoders))
	for n := range decoders {
		names = append(names, n)
	}
	sort.Strings(names)
	total, accepted := 0, 0
	for _, name := range names {
		for _, group := range []string{"valid", "invalid"} {
			files, _ := filepath.Glob(filepath.Join(root, "contracts", "examples", group, name, "*.json"))
			if len(files) == 0 {
				t.Errorf("%s/%s: no examples found", group, name)
			}
			for _, f := range files {
				total++
				label := name + "/" + filepath.Base(f)
				data, err := os.ReadFile(f)
				if err != nil {
					t.Fatal(err)
				}
				v, err := decoders[name](data)
				switch {
				case group == "valid" && err != nil:
					t.Errorf("valid %s rejected: %v", label, err)
				case group == "valid":
					schematest.Validate(t, name, v) // Go round trip stays schema-valid
				case err == nil && notEnforced[label] == "":
					t.Errorf("invalid %s accepted", label)
				case err == nil:
					accepted++
				case notEnforced[label] != "":
					t.Errorf("invalid %s now rejected; drop it from notEnforced and go/README.md", label)
				case strings.HasPrefix(filepath.Base(f), "wrong-schema-version") && codeOf(err) != Unsupported:
					t.Errorf("%s: want unsupported, got %v", label, err)
				}
			}
		}
	}
	t.Logf("go/contract: %d golden examples across %d schemas; %d invalid accepted by design", total, len(names), accepted)
}
