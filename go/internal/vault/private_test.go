package vault

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"testing"

	"filippo.io/age"
)

func TestPrivateBlob(t *testing.T) {
	id, _ := age.GenerateX25519Identity()
	other, _ := age.GenerateX25519Identity()
	plain := []byte(`{"schema_version":1,"text":"SYNTHETIC-PRIVATE"}`)
	ct, sum, err := SealPrivate(plain, id.Recipient())
	if err != nil || len(sum) != 64 || bytes.Contains(ct, []byte("SYNTHETIC")) {
		t.Fatalf("seal: %v", err)
	}
	if got, err := OpenPrivate(ct, sum, id); err != nil || !bytes.Equal(got, plain) {
		t.Fatalf("open: %v", err)
	}
	tampered := bytes.Clone(ct)
	tampered[len(tampered)-1] ^= 1
	cases := []struct {
		name, code string
		err        error
	}{
		{"tampered", "blob_hash_mismatch", func() error { _, e := OpenPrivate(tampered, sum, id); return e }()},
		// Hash rebound to the tampered bytes: age's own authentication still rejects it.
		{"tampered with matching hash", "decryption_rejected", func() error {
			s := sha256.Sum256(tampered)
			_, e := OpenPrivate(tampered, hex.EncodeToString(s[:]), id)
			return e
		}()},
		{"wrong recipient", "decryption_rejected", func() error { _, e := OpenPrivate(ct, sum, other); return e }()},
		{"empty plaintext", "plaintext_size_rejected", func() error { _, _, e := SealPrivate(nil, id.Recipient()); return e }()},
		{"oversized plaintext", "plaintext_size_rejected", func() error { _, _, e := SealPrivate(make([]byte, MaxPrivate+1), id.Recipient()); return e }()},
		{"no recipients", "recipient_count_rejected", func() error { _, _, e := SealPrivate(plain); return e }()},
		{"oversized ciphertext", "ciphertext_size_rejected", func() error { _, e := OpenPrivate(make([]byte, 2*MaxPrivate+1), sum, id); return e }()},
	}
	for _, c := range cases {
		if c.err == nil || c.err.Error() != c.code {
			t.Errorf("%s: got %v, want %s", c.name, c.err, c.code)
		}
	}
}
