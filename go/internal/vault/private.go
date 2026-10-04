package vault

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"

	"filippo.io/age"
)

// MaxPrivate bounds the plaintext of a private payload blob (*.private.v1 JSON is itself capped at 32 KiB).
const MaxPrivate = 64 << 10

// SealPrivate encrypts a private payload to X25519 recipients. It returns the ciphertext
// and its lowercase hex sha256, the value a PrivateRef carries. Errors are constant codes.
func SealPrivate(plain []byte, rs ...*age.X25519Recipient) ([]byte, string, error) {
	if len(plain) == 0 || len(plain) > MaxPrivate {
		return nil, "", fail("plaintext_size_rejected")
	}
	if len(rs) == 0 || len(rs) > 16 {
		return nil, "", fail("recipient_count_rejected")
	}
	list := make([]age.Recipient, len(rs))
	for i, r := range rs {
		list[i] = r
	}
	var out bytes.Buffer
	w, e := age.Encrypt(&out, list...)
	if e != nil {
		return nil, "", fail("encryption_failed")
	}
	if _, e = w.Write(plain); e != nil || w.Close() != nil {
		return nil, "", fail("encryption_failed")
	}
	sum := sha256.Sum256(out.Bytes())
	return out.Bytes(), hex.EncodeToString(sum[:]), nil
}

// OpenPrivate checks the ciphertext sha256 against want before decrypting with id, so a
// tampered or swapped blob is rejected without touching age. The plaintext stays in memory.
func OpenPrivate(ciphertext []byte, want string, id *age.X25519Identity) ([]byte, error) {
	// ponytail: loose bound; age adds a small header plus 16 bytes per 64 KiB chunk.
	if len(ciphertext) > 2*MaxPrivate {
		return nil, fail("ciphertext_size_rejected")
	}
	sum := sha256.Sum256(ciphertext)
	if hex.EncodeToString(sum[:]) != want {
		return nil, fail("blob_hash_mismatch")
	}
	r, e := age.Decrypt(bytes.NewReader(ciphertext), id)
	if e != nil {
		return nil, fail("decryption_rejected")
	}
	plain, e := io.ReadAll(io.LimitReader(r, MaxPrivate+1))
	if e != nil {
		return nil, fail("decryption_rejected")
	}
	if len(plain) > MaxPrivate {
		return nil, fail("plaintext_size_rejected")
	}
	return plain, nil
}
