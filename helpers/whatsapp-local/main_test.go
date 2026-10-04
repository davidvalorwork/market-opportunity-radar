package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"radar.local/radar/internal/contract"
	"radar.local/radar/internal/whatsapp"
)

// Explicit protocol fixture under the SAME helper run/journal; never a live login.
type protocolFixture struct {
	whatsapp.FakeClient
	filters [][]string
}

func (f *protocolFixture) SelfChatRef(_ context.Context) (string, error) { return "chat:self", nil }

func (f *protocolFixture) SetEnabledChats(_ context.Context, refs []string) error {
	f.filters = append(f.filters, append([]string(nil), refs...))
	return nil
}
func (f *protocolFixture) ProviderMessageID(_ context.Context, _ string) (string, error) {
	return "SYNTHETIC_PROVIDER_ID", nil
}
func gateChannel(id string, count int) (*channel, *bytes.Buffer) {
	var input strings.Builder
	for range count {
		input.WriteString(`{"v":1,"id":"` + id + `","event":"continue","allowed":true}` + "\n")
	}
	output := new(bytes.Buffer)
	return &channel{bufio.NewScanner(strings.NewReader(input.String())), json.NewEncoder(output), id}, output
}
func sendRequest() request {
	sum := sha256.Sum256([]byte("SYNTHETIC_PRIVATE exact café"))
	body, _ := json.Marshal(map[string]string{"chat_ref": "chat:one", "text": "SYNTHETIC_PRIVATE exact café", "content_hash": hex.EncodeToString(sum[:]), "purpose_ref": "purpose:reply"})
	return request{Version: 1, ID: "operation:synthetic", Owner: "owner:alpha", Account: "account:alpha", Session: "session:alpha", SessionVersion: 1, Method: "send", Body: body, Deadline: time.Now().UTC().Add(time.Minute)}
}
func TestSendDurableJournalRestartAndBinding(t *testing.T) {
	ctx, dir, r := context.Background(), t.TempDir(), sendRequest()
	db, hash, err := journal(ctx, dir, r)
	if err != nil {
		t.Fatal(err)
	}
	f := &protocolFixture{FakeClient: whatsapp.FakeClient{Chats: map[string]contract.Chat{"chat:one": {ChatRef: "chat:one"}}}}
	c, _ := gateChannel(r.ID, 2)
	result, err := run(ctx, f, r, c, db, hash)
	if err != nil {
		t.Fatal(err)
	}
	if result.(map[string]string)["state"] != "provider_confirmed" || f.Calls["send"] != 1 {
		t.Fatal("send not confirmed exactly once")
	}
	db.Close()
	db, hash, err = journal(ctx, dir, r)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	c, _ = gateChannel(r.ID, 1)
	if _, err = run(ctx, f, r, c, db, hash); err != nil || f.Calls["send"] != 1 {
		t.Fatal("replay sent again", err)
	}
	other := r
	other.SessionVersion++
	c, _ = gateChannel(r.ID, 1)
	if _, err = run(ctx, f, other, c, db, binding(other)); err == nil || err.Error() != "operation_binding" {
		t.Fatal("changed version disclosed replay")
	}
	other = r
	other.Owner = "owner:foreign"
	if foreign, _, err := journal(ctx, dir, other); err == nil {
		foreign.Close()
		t.Fatal("foreign owner bound session")
	}
}

func TestSyncSelectedCaptureProviderIDAndAck(t *testing.T) {
	ctx, r := context.Background(), sendRequest()
	r.Method = "sync"
	r.Body = json.RawMessage(`{"enabled":["chat:one"],"limit":1,"ack":""}`)
	db, hash, err := journal(ctx, t.TempDir(), r)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	f := &protocolFixture{FakeClient: whatsapp.FakeClient{Messages: []contract.Message{{ChatRef: "chat:one", Text: "SYNTHETIC_TEXT", ObservedAt: contract.Time{Time: time.Now().UTC()}}}}}
	c, _ := gateChannel(r.ID, 1)
	result, err := run(ctx, f, r, c, db, hash)
	if err != nil {
		t.Fatal(err)
	}
	page := result.(map[string]any)["messages"].([]map[string]any)
	if len(f.filters) != 2 || len(f.filters[0]) != 0 || len(f.filters[1]) != 1 || f.filters[1][0] != "chat:one" || page[0]["provider_message_id"] != "SYNTHETIC_PROVIDER_ID" {
		t.Fatal("capture/provider binding")
	}
	r.Body = json.RawMessage(`{"enabled":["chat:one"],"limit":1,"ack":"p1"}`)
	c, _ = gateChannel(r.ID, 1)
	if _, err = run(ctx, f, r, c, db, binding(r)); err != nil || f.Calls["ack"] != 1 {
		t.Fatal("ack after host gate", err)
	}
}
func TestSendCrashNeverRetriesProtocol(t *testing.T) {
	ctx, r := context.Background(), sendRequest()
	db, hash, err := journal(ctx, t.TempDir(), r)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	f := &protocolFixture{FakeClient: whatsapp.FakeClient{Chats: map[string]contract.Chat{"chat:one": {ChatRef: "chat:one"}}, PanicAfterSend: true}}
	c, _ := gateChannel(r.ID, 2)
	func() {
		defer func() {
			if recover() == nil {
				t.Error("fixture crash missing")
			}
		}()
		run(ctx, f, r, c, db, hash)
	}()
	f.PanicAfterSend = false
	c, _ = gateChannel(r.ID, 1)
	result, err := run(ctx, f, r, c, db, hash)
	if err != nil || result.(map[string]string)["state"] != "send_uncertain" || f.Calls["send"] != 1 {
		t.Fatal("uncertain retried", err)
	}
}
func TestPairCodePrivateGateAndEmptyCapture(t *testing.T) {
	ctx, r := context.Background(), sendRequest()
	r.Method = "pair"
	r.Body = json.RawMessage(`{"phone":"+15550000111"}`)
	db, hash, err := journal(ctx, t.TempDir(), r)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	f := &protocolFixture{FakeClient: whatsapp.FakeClient{JID: "15550000111", Code: "SYNTHETIC_CODE"}}
	c, out := gateChannel(r.ID, 2)
	if _, err := run(ctx, f, r, c, db, hash); err != nil {
		t.Fatal(err)
	}
	if len(f.filters) != 1 || len(f.filters[0]) != 0 || !strings.Contains(out.String(), `"event":"pair_code"`) {
		t.Fatal("pair captured bodies or no private code")
	}
}
func TestGateRefusalNeverSends(t *testing.T) {
	ctx, r := context.Background(), sendRequest()
	db, hash, err := journal(ctx, t.TempDir(), r)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	f := &protocolFixture{FakeClient: whatsapp.FakeClient{Chats: map[string]contract.Chat{"chat:one": {ChatRef: "chat:one"}}, Err: map[string]error{"connect": errors.New("SYNTHETIC_ERROR")}}}
	c, _ := gateChannel(r.ID, 0)
	if _, err = run(ctx, f, r, c, db, hash); err == nil || f.Calls["send"] != 0 {
		t.Fatal("refused gate effect")
	}
}
