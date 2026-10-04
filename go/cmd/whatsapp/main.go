// Command whatsapp is a local runner: one envelope JSON on stdin, one result JSON on stdout.
// Only --fake exists; the whatsmeow adapter and DynamoDB/S3 stores are pending.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"strings"
	"time"

	"filippo.io/age"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/vault"
	"radar.local/radar/internal/whatsapp"
)

const scope = "worker:whatsapp"

// run handles one envelope. private is the synthetic *.private.v1 JSON the fake world
// seeds as the blob behind the payload's private_ref (pair and send only).
func run(fake bool, private string, in io.Reader) contract.Result {
	if !fake {
		return contract.Failed(contract.Fail(contract.Unsupported, "real WhatsApp adapter not implemented; use --fake"))
	}
	data, err := io.ReadAll(io.LimitReader(in, contract.MaxEnvelope+1))
	if err != nil {
		return contract.Failed(contract.Fail(contract.InvalidInput, "stdin unreadable"))
	}
	// Windows PowerShell 5.1 pipes a UTF-8 BOM; the contract itself stays strict.
	env, payload, err := contract.Decode(bytes.TrimPrefix(data, []byte("\xef\xbb\xbf")))
	var ce *contract.Error
	if errors.As(err, &ce) {
		return contract.Failed(ce)
	}
	// Fake world seeded so a synthetic envelope can complete. The seeded ledger record
	// stands in for an approval made elsewhere; it is not an approval. The identity is
	// ephemeral, so the seeded blob gets a fresh ciphertext and the fake world rebinds
	// private_ref.sha256 to it; a real worker never rewrites a private_ref.
	id, err := age.GenerateX25519Identity()
	if err != nil {
		return contract.Failed(contract.Fail(contract.Internal, "key generation failed"))
	}
	blobs := &whatsapp.MemBlobs{}
	seed := func(ref *contract.PrivateRef) *contract.Error {
		if private == "" {
			return contract.Fail(contract.InvalidInput, "--fake-private is required for pair and send")
		}
		ct, sum, err := vault.SealPrivate([]byte(private), id.Recipient())
		if err != nil {
			return contract.Fail(contract.InvalidInput, "fake private payload rejected")
		}
		blobs.Put(context.Background(), ref.BlobKey, ct)
		ref.SHA256 = sum
		return nil
	}
	client := &whatsapp.FakeClient{}
	ledger := &whatsapp.MemLedger{Records: map[string]whatsapp.Record{}}
	switch p := payload.(type) {
	case *contract.Pair:
		if ce := seed(&p.PrivateRef); ce != nil {
			return contract.Failed(ce)
		}
		priv, err := contract.DecodePairPrivate([]byte(private))
		if errors.As(err, &ce) {
			return contract.Failed(ce)
		}
		client.JID, client.Code = strings.TrimPrefix(priv.DeclaredPhone, "+")+"@s.whatsapp.net", "FAKE0000"
	case *contract.Sync:
		now := contract.Time{Time: time.Now().UTC()}
		client.Messages = []contract.Message{
			{ChatRef: p.EnabledChatRefs[0], Text: "synthetic reply", ObservedAt: now},
			{ChatRef: "fake:not-enabled", Text: "synthetic dropped", ObservedAt: now},
		}
	case *contract.Send:
		if ce := seed(&p.PrivateRef); ce != nil {
			return contract.Failed(ce)
		}
		ledger.Records[env.OperationID] = whatsapp.Record{State: whatsapp.Approved, OwnerRef: env.OwnerRef,
			RecipientRef: p.RecipientRef, ApprovalRef: p.ApprovalRef, ContentSHA256: p.ContentSHA256}
	}
	w := &whatsapp.Worker{
		Client: client, Leases: &whatsapp.MemLeases{}, Ledger: ledger,
		Sessions: &whatsapp.MemSessions{Versions: map[string]int{env.SessionRef: env.ExpectedVersion}},
		Notifier: &whatsapp.FakeNotifier{}, Owner: "local-fake-runner", LeaseTTL: 5 * time.Minute,
		Blobs: blobs, Identity: id, Scope: scope, ResultRecipients: []*age.X25519Recipient{id.Recipient()}, ResultScope: scope,
	}
	return w.Handle(context.Background(), env, payload)
}

func main() {
	f := flag.NewFlagSet("whatsapp", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	fake := f.Bool("fake", false, "use the in-memory fake WhatsApp client (required)")
	private := f.String("fake-private", "", "synthetic *.private.v1 JSON seeded behind private_ref (pair/send)")
	r := contract.Failed(contract.Fail(contract.InvalidInput, "invalid arguments"))
	if f.Parse(os.Args[1:]) == nil && f.NArg() == 0 {
		r = run(*fake, *private, os.Stdin)
	}
	json.NewEncoder(os.Stdout).Encode(r)
	if r.Status != contract.StatusSucceeded {
		os.Exit(1)
	}
}
