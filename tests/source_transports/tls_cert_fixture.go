// Ephemeral synthetic TLS fixture ONLY; standard library, no third-party modules.
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"path/filepath"
	"time"
)

func main() {
	if len(os.Args) != 3 || os.Args[1] != "--synthetic-only" || !filepath.IsAbs(os.Args[2]) {
		fail()
	}
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		fail()
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		fail()
	}
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "SYNTHETIC A17 CA"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	caBytes, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		fail()
	}
	server := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "SYNTHETIC fixture.invalid"},
		DNSNames: []string{"fixture.invalid"}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(24 * time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true}
	serverBytes, err := x509.CreateCertificate(rand.Reader, server, ca, &key.PublicKey, caKey)
	if err != nil {
		fail()
	}
	keyBytes, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		fail()
	}
	defer clear(keyBytes)
	for name, block := range map[string]*pem.Block{"ca.pem": {Type: "CERTIFICATE", Bytes: caBytes},
		"server.pem": {Type: "CERTIFICATE", Bytes: serverBytes}, "server-key.pem": {Type: "PRIVATE KEY", Bytes: keyBytes}} {
		file, err := os.OpenFile(filepath.Join(os.Args[2], name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			fail()
		}
		if pem.Encode(file, block) != nil || file.Sync() != nil || file.Close() != nil {
			fail()
		}
	}
}
func fail() { _, _ = os.Stderr.WriteString("synthetic_tls_fixture_failed\n"); os.Exit(1) }
