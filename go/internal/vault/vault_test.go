package vault

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSyntheticTransferAndRegistry(t *testing.T) {
	result := Selftest()
	if result["status"] != "passed" {
		t.Fatalf("synthetic checks failed: %v", result["checks"])
	}
	encoded, e := json.Marshal(result)
	if e != nil {
		t.Fatal("result serialization failed")
	}
	if strings.Contains(string(encoded), "SYNTHETIC-COOKIE") || strings.Contains(string(encoded), "SYNTHETIC-LOCAL") || strings.Contains(string(encoded), "SYNTHETIC-IDB") || strings.Contains(string(encoded), "AGE-SECRET-KEY") {
		t.Fatal("sensitive marker reached telemetry")
	}
}
func TestTamperedManifestFailsClosed(t *testing.T) {
	for _, change := range []func(*bundle){func(b *bundle) { b.SchemaVersion = 2 }, func(b *bundle) { b.Version = 0 }, func(b *bundle) { b.StateType = "cookie_array" }, func(b *bundle) { b.CreatedAt = "bad" }, func(b *bundle) { b.AllowedOrigins = append(b.AllowedOrigins, "https://foreign.example") }, func(b *bundle) { b.ProducerVersion = "unknown" }} {
		b := fixtureBundle()
		change(&b)
		if validateBundle(b, syntheticEvent.Alias, syntheticEvent.AllowedOrigin, false) == nil {
			t.Fatal("invalid manifest accepted")
		}
	}
}
