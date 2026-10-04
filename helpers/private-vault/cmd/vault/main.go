// Binary private pipe protocol: uint32 key length, key bytes, body to EOF.
// Only X25519 age keys: no plugins, shell, child processes or network.
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"os"
	"strings"

	"filippo.io/age"
)

const maxPlain = 1 << 20
const maxCipher = maxPlain + 4096

var rejected = errors.New("private_vault_rejected")

func transform(mode string, input io.Reader) ([]byte, error) {
	var size uint32
	if binary.Read(input, binary.BigEndian, &size) != nil || size == 0 || size > 4096 {
		return nil, rejected
	}
	key := make([]byte, size)
	if _, err := io.ReadFull(input, key); err != nil {
		return nil, rejected
	}
	defer clear(key)
	limit := maxPlain
	if mode == "decrypt" {
		limit = maxCipher
	} else if mode != "encrypt" {
		return nil, rejected
	}
	body, err := io.ReadAll(io.LimitReader(input, int64(limit+1)))
	if err != nil || len(body) == 0 || len(body) > limit {
		return nil, rejected
	}
	defer clear(body)
	if mode == "encrypt" {
		r, err := age.ParseX25519Recipient(strings.TrimSpace(string(key)))
		if err != nil {
			return nil, rejected
		}
		var out bytes.Buffer
		w, err := age.Encrypt(&out, r)
		if err != nil {
			return nil, rejected
		}
		if _, err = w.Write(body); err != nil {
			return nil, rejected
		}
		if err = w.Close(); err != nil || out.Len() > maxCipher {
			return nil, rejected
		}
		return out.Bytes(), nil
	}
	id, err := age.ParseX25519Identity(strings.TrimSpace(string(key)))
	if err != nil {
		return nil, rejected
	}
	r, err := age.Decrypt(bytes.NewReader(body), id)
	if err != nil {
		return nil, rejected
	}
	out, err := io.ReadAll(io.LimitReader(r, maxPlain+1))
	if err != nil || len(out) == 0 || len(out) > maxPlain {
		clear(out)
		return nil, rejected
	}
	return out, nil
}

func main() {
	if len(os.Args) != 2 {
		fail()
	}
	out, err := transform(os.Args[1], os.Stdin)
	if err != nil {
		fail()
	}
	defer clear(out)
	if _, err = os.Stdout.Write(out); err != nil {
		fail()
	}
}

func fail() { _, _ = os.Stderr.WriteString("private_vault_rejected\n"); os.Exit(1) }
