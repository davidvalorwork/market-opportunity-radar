// Test-only ephemeral key material. No key ever goes to stdout/stderr.
package main

import (
	"filippo.io/age"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 3 || os.Args[1] != "--synthetic-only" || !filepath.IsAbs(os.Args[2]) {
		fail()
	}
	id, err := age.GenerateX25519Identity()
	if err != nil {
		fail()
	}
	for name, data := range map[string]string{"identity.agekey": id.String() + "\n", "recipient.txt": id.Recipient().String() + "\n"} {
		f, err := os.OpenFile(filepath.Join(os.Args[2], name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			fail()
		}
		_, err = f.WriteString(data)
		if err != nil || f.Sync() != nil || f.Close() != nil {
			fail()
		}
	}
}
func fail() { _, _ = os.Stderr.WriteString("synthetic_key_fixture_failed\n"); os.Exit(1) }
