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

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/whatsapp"
)

func run(fake bool, in io.Reader) contract.Result {
	if !fake {
		return contract.Result{Status: contract.StatusFailed, Error: contract.Fail(contract.Unsupported, "real WhatsApp adapter not implemented; use --fake")}
	}
	data, err := io.ReadAll(io.LimitReader(in, contract.MaxEnvelope+1))
	if err != nil {
		return contract.Result{Status: contract.StatusFailed, Error: contract.Fail(contract.InvalidInput, "stdin unreadable")}
	}
	// Windows PowerShell 5.1 pipes a UTF-8 BOM; the contract itself stays strict.
	env, payload, err := contract.Decode(bytes.TrimPrefix(data, []byte("\xef\xbb\xbf")))
	var ce *contract.Error
	if errors.As(err, &ce) {
		return contract.Result{Status: contract.StatusFailed, Error: ce}
	}
	// Fake world seeded so a synthetic envelope can complete. The seeded ledger
	// record stands in for an approval made elsewhere; it is not an approval.
	client := &whatsapp.FakeClient{}
	ledger := &whatsapp.MemLedger{Records: map[string]whatsapp.Record{}}
	switch p := payload.(type) {
	case *contract.Pair:
		client.JID, client.Code = strings.TrimPrefix(p.DeclaredPhone, "+")+"@s.whatsapp.net", "FAKE0000"
	case *contract.Sync:
		now := time.Now().UTC()
		client.Messages = []contract.Message{
			{ChatRef: p.EnabledChatRefs[0], Text: "synthetic reply", ObservedAt: now},
			{ChatRef: "fake-not-enabled", Text: "synthetic dropped", ObservedAt: now},
		}
	case *contract.Send:
		ledger.Records[env.OperationID] = whatsapp.Record{State: whatsapp.Approved, OwnerRef: env.OwnerRef,
			RecipientRef: p.RecipientRef, ApprovalRef: p.ApprovalRef, ContentSHA256: p.ContentSHA256}
	}
	w := &whatsapp.Worker{
		Client: client, Leases: &whatsapp.MemLeases{}, Ledger: ledger,
		Sessions: &whatsapp.MemSessions{Versions: map[string]int{env.SessionRef: env.ExpectedVersion}},
		Notifier: &whatsapp.FakeNotifier{}, Owner: "local-fake-runner", LeaseTTL: 5 * time.Minute,
	}
	return w.Handle(context.Background(), env, payload)
}

func main() {
	f := flag.NewFlagSet("whatsapp", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	fake := f.Bool("fake", false, "use the in-memory fake WhatsApp client (required)")
	r := contract.Result{Status: contract.StatusFailed, Error: contract.Fail(contract.InvalidInput, "invalid arguments")}
	if f.Parse(os.Args[1:]) == nil && f.NArg() == 0 {
		r = run(*fake, os.Stdin)
	}
	json.NewEncoder(os.Stdout).Encode(r)
	if r.Status != contract.StatusSucceeded {
		os.Exit(1)
	}
}
