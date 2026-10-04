// Operator key creation. A private parent is prepared by the Python host;
// existing identities are never replaced and key material never reaches logs.
package main

import (
	"filippo.io/age"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 3 || os.Args[1] != "--new" || !filepath.IsAbs(os.Args[2]) {
		fail()
	}
	id, err := age.GenerateX25519Identity()
	if err != nil {
		fail()
	}
	for _, row := range [][2]string{{"identity.agekey", id.String() + "\n"}, {"recipient.txt", id.Recipient().String() + "\n"}} {
		f, err := os.OpenFile(filepath.Join(os.Args[2], row[0]), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			fail()
		}
		_, err = f.WriteString(row[1])
		syncErr, closeErr := f.Sync(), f.Close()
		if err != nil || syncErr != nil || closeErr != nil {
			fail()
		}
	}
}
func fail() { _, _ = os.Stderr.WriteString("identity_creation_failed\n"); os.Exit(1) }
