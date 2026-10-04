package main

import (
	"bytes"
	"encoding/binary"
	"filippo.io/age"
	"testing"
)

func frame(key string, body []byte) *bytes.Buffer {
	var b bytes.Buffer
	_ = binary.Write(&b, binary.BigEndian, uint32(len(key)))
	b.WriteString(key)
	b.Write(body)
	return &b
}

func TestRealAge(t *testing.T) {
	id, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal("key generation")
	}
	other, _ := age.GenerateX25519Identity()
	plain := []byte("SYNTHETIC private marker")
	cipher, err := transform("encrypt", frame(id.Recipient().String(), plain))
	if err != nil || bytes.Contains(cipher, plain) {
		t.Fatal("encrypt")
	}
	got, err := transform("decrypt", frame(id.String(), cipher))
	if err != nil || !bytes.Equal(got, plain) {
		t.Fatal("decrypt")
	}
	if _, err = transform("decrypt", frame(other.String(), cipher)); err != rejected {
		t.Fatal("foreign key")
	}
	cipher[len(cipher)-1] ^= 1
	if _, err = transform("decrypt", frame(id.String(), cipher)); err != rejected {
		t.Fatal("tamper")
	}
}

func TestLimits(t *testing.T) {
	id, _ := age.GenerateX25519Identity()
	for _, mode := range []string{"unknown", "encrypt", "decrypt"} {
		if _, err := transform(mode, bytes.NewReader(nil)); err != rejected {
			t.Fatal("empty")
		}
	}
	if _, err := transform("encrypt", frame(id.Recipient().String(), make([]byte, maxPlain+1))); err != rejected {
		t.Fatal("limit")
	}
	if _, err := transform("decrypt", frame("SYNTHETIC_SECRET", []byte("invalid"))); err != rejected {
		t.Fatal("invalid")
	}
}
