// Command whatsapp is a local runner: one envelope JSON on stdin, one result JSON on stdout.
// --fake uses the in-memory client. --real wires the whatsmeow adapter and is gated: it
// refuses unless RADAR_WA_REAL_AUTHORIZED=yes and --session-dir are both given. Leases,
// ledger, sessions and blobs stay in memory in both modes (DynamoDB/S3 are pending).
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"filippo.io/age"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/vault"
	"radar.local/radar/internal/whatsapp"
	"radar.local/radar/internal/whatsapp/wameow"
)

const scope = "worker:whatsapp"

type opts struct {
	fake, real bool
	// private is the *.private.v1 JSON seeded as the blob behind the payload's private_ref (pair and send only).
	private    string
	sessionDir string    // --real: existing directory holding whatsmeow.db
	authorized bool      // RADAR_WA_REAL_AUTHORIZED=yes
	notify     io.Writer // --real: where the operator reads the pairing code
}

// termNotifier shows the pairing code to the operator of a local --real run. A deployed
// worker uses the Telegram notifier; this writer must never be a log sink.
type termNotifier struct{ w io.Writer }

func (n termNotifier) DeliverCode(_ context.Context, _, code string) error {
	_, err := fmt.Fprintf(n.w, "pairing code (type it on the phone within ~2 min): %s\n", code)
	return err
}

// run handles one envelope. The real-mode gate is checked before stdin is read or any
// file is created, so a refused run touches neither disk nor network.
func run(o opts, in io.Reader) contract.Result {
	switch {
	case o.fake && o.real:
		return contract.Failed(contract.Fail(contract.InvalidInput, "choose --fake or --real"))
	case o.real && !o.authorized:
		return contract.Failed(contract.Fail(contract.InvalidInput, "real WhatsApp mode refused: RADAR_WA_REAL_AUTHORIZED=yes not set"))
	case o.real && o.sessionDir == "":
		return contract.Failed(contract.Fail(contract.InvalidInput, "real WhatsApp mode refused: --session-dir required"))
	case !o.fake && !o.real:
		return contract.Failed(contract.Fail(contract.Unsupported, "use --fake, or the gated --real"))
	}
	private := o.private
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
	// Local world seeded so an envelope can complete. The seeded ledger record stands in
	// for an approval made elsewhere; it is not an approval (in --real the operator's
	// gated invocation is the only authorization). The identity is ephemeral, so the
	// seeded blob gets a fresh ciphertext and the runner rebinds private_ref.sha256 to
	// it; a deployed worker never rewrites a private_ref.
	id, err := age.GenerateX25519Identity()
	if err != nil {
		return contract.Failed(contract.Fail(contract.Internal, "key generation failed"))
	}
	blobs := &whatsapp.MemBlobs{}
	seed := func(ref *contract.PrivateRef) *contract.Error {
		if private == "" {
			return contract.Fail(contract.InvalidInput, "--fake-private (or --private) is required for pair and send")
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
	var wc whatsapp.Client = client
	var notifier whatsapp.Notifier = &whatsapp.FakeNotifier{}
	owner := "local-fake-runner"
	if o.real {
		real, err := wameow.New(context.Background(), o.sessionDir)
		if err != nil {
			return contract.Failed(contract.Fail(contract.InvalidInput, "session directory unusable"))
		}
		// ponytail: MemSessions drops the snapshot; locally the live whatsmeow.db in
		// --session-dir is the state. Lambda will seal the snapshot to S3 instead.
		wc, notifier, owner = real, termNotifier{o.notify}, "local-real-runner"
	}
	w := &whatsapp.Worker{
		Client: wc, Leases: &whatsapp.MemLeases{}, Ledger: ledger,
		Sessions: &whatsapp.MemSessions{Versions: map[string]int{env.SessionRef: env.ExpectedVersion}},
		Notifier: notifier, Owner: owner, LeaseTTL: 5 * time.Minute,
		Blobs: blobs, Identity: id, Scope: scope, ResultRecipients: []*age.X25519Recipient{id.Recipient()}, ResultScope: scope,
	}
	return w.Handle(context.Background(), env, payload)
}

func main() {
	f := flag.NewFlagSet("whatsapp", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	o := opts{notify: os.Stderr, authorized: os.Getenv("RADAR_WA_REAL_AUTHORIZED") == "yes"}
	f.BoolVar(&o.fake, "fake", false, "use the in-memory fake WhatsApp client")
	f.BoolVar(&o.real, "real", false, "use the whatsmeow adapter (needs RADAR_WA_REAL_AUTHORIZED=yes and --session-dir)")
	f.StringVar(&o.sessionDir, "session-dir", "", "existing directory holding whatsmeow.db (--real)")
	f.StringVar(&o.private, "fake-private", "", "synthetic *.private.v1 JSON seeded behind private_ref (pair/send)")
	f.StringVar(&o.private, "private", "", "alias of --fake-private for --real")
	r := contract.Failed(contract.Fail(contract.InvalidInput, "invalid arguments"))
	if f.Parse(os.Args[1:]) == nil && f.NArg() == 0 {
		r = run(o, os.Stdin)
	}
	json.NewEncoder(os.Stdout).Encode(r)
	if r.Status != contract.StatusSucceeded {
		os.Exit(1)
	}
}
