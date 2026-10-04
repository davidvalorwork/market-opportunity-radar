package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.mau.fi/whatsmeow/proto/waAdv"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
	"radar.local/radar/internal/whatsapp/wameow"
)

func syntheticSnapshot(t *testing.T, paired bool) string {
	t.Helper()
	dir := t.TempDir()
	ctx := context.Background()
	client, err := wameow.New(ctx, dir)
	if err != nil {
		t.Fatal(err)
	}
	client.Close()
	db, err := sql.Open("sqlite", filepath.Join(dir, wameow.DBFile))
	if err != nil {
		t.Fatal(err)
	}
	container := sqlstore.NewWithDB(db, "sqlite", waLog.Noop)
	t.Cleanup(func() { container.Close() })
	if paired {
		device, err := container.GetFirstDevice(ctx)
		if err != nil {
			t.Fatal(err)
		}
		identity := types.NewJID("15550000111", types.DefaultUserServer)
		device.ID = &identity
		device.Account = &waAdv.ADVSignedDeviceIdentity{Details: []byte{1}, AccountSignature: make([]byte, 64), AccountSignatureKey: make([]byte, 32), DeviceSignature: make([]byte, 64)}
		if err = device.Save(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Exec("PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
		t.Fatal(err)
	}
	if err = container.Close(); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestSameClientOfflineCloneIdentityNeverMutatesInput(t *testing.T) {
	dir := syntheticSnapshot(t, true)
	source := filepath.Join(dir, wameow.DBFile)
	before, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if code := run([]string{"--session-dir", dir}, strings.NewReader(`{"declared_phone":"+15550000111"}`), &out); code != 0 {
		t.Fatal("offline compatibility failed", out.String())
	}
	var result report
	if err = json.Unmarshal(out.Bytes(), &result); err != nil || result.Code != "ok" || !result.Paired || !result.PhoneMatches || !result.SelfRefKnown || result.KnownChats != 1 || result.PendingMessages != 0 {
		t.Fatal("incomplete compatibility", out.String())
	}
	if strings.Contains(out.String(), "15550000111") || strings.Contains(out.String(), "@s.whatsapp.net") {
		t.Fatal("private identity printed")
	}
	after, err := os.ReadFile(source)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("input mutated")
	}
	for _, suffix := range []string{"-wal", "-shm"} {
		if _, err = os.Stat(source + suffix); !os.IsNotExist(err) {
			t.Fatal("source acquired sidecar")
		}
	}
}

func TestOfflineMismatchAndUnpairedAreNotLiveProof(t *testing.T) {
	for _, paired := range []bool{false, true} {
		dir := syntheticSnapshot(t, paired)
		result := check(dir, "+15550000222")
		if result.Code == "ok" || result.PhoneMatches || result.SelfRefKnown {
			t.Fatal("false identity proof")
		}
		if paired && result.Code != "declared_identity_mismatch" {
			t.Fatal("mismatch lost")
		}
		if !paired && result.Code != "unpaired" {
			t.Fatal("unpaired lost")
		}
	}
}

func TestStrictBoundedPrivateStdinStaticErrors(t *testing.T) {
	for _, data := range []string{`{}`, `null`, `{"declared_phone":"+15550000111","declared_phone":"+15550000111"}`, `{"declared_phone":"+15550000111","extra":true}`, `{"declared_phone":"+15550000111"} {}`, `{"declared_phone":"+05550000111"}`, strings.Repeat("SYNTHETIC_PRIVATE", 100)} {
		var out bytes.Buffer
		if run([]string{"--session-dir", t.TempDir()}, strings.NewReader(data), &out) == 0 || strings.Contains(out.String(), "15550000111") || strings.Contains(out.String(), "SYNTHETIC_PRIVATE") {
			t.Fatal("invalid input leaked or accepted")
		}
	}
}

func TestRefuseUncheckpointedOrInvalidSnapshot(t *testing.T) {
	dir := syntheticSnapshot(t, false)
	if err := os.WriteFile(filepath.Join(dir, wameow.DBFile)+"-wal", nil, 0600); err != nil {
		t.Fatal(err)
	}
	if check(dir, "+15550000111").Code != "snapshot_requires_checkpoint" {
		t.Fatal("live WAL accepted")
	}
	other := t.TempDir()
	if err := os.WriteFile(filepath.Join(other, wameow.DBFile), []byte("SYNTHETIC_INVALID_CONTENT"), 0600); err != nil {
		t.Fatal(err)
	}
	if check(other, "+15550000111").Code != "snapshot_invalid" {
		t.Fatal("invalid SQLite accepted")
	}
}
