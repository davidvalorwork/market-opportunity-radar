// Offline compatibility check. No Connect, account calls, message delivery or logs.
package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
	"radar.local/radar/internal/whatsapp/wameow"
)

const maxInput = 512
const maxSnapshot = 32 << 20

var phonePattern = regexp.MustCompile(`^\+[1-9][0-9]{6,14}$`)

type report struct {
	Paired          bool   `json:"paired"`
	PhoneMatches    bool   `json:"phone_matches"`
	SelfRefKnown    bool   `json:"self_ref_known"`
	KnownChats      int    `json:"known_chat_count"`
	PendingMessages int    `json:"pending_message_count"`
	Code            string `json:"code"`
}

func declaredPhone(input io.Reader) (string, error) {
	data, err := io.ReadAll(io.LimitReader(input, maxInput+1))
	if err != nil || len(data) > maxInput {
		return "", errors.New("invalid_input")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	first, err := d.Token()
	if err != nil || first != json.Delim('{') {
		return "", errors.New("invalid_input")
	}
	key, err := d.Token()
	if err != nil || key != "declared_phone" {
		return "", errors.New("invalid_input")
	}
	var phone string
	if d.Decode(&phone) != nil || !phonePattern.MatchString(phone) {
		return "", errors.New("invalid_input")
	}
	end, err := d.Token()
	if err != nil || end != json.Delim('}') {
		return "", errors.New("invalid_input")
	}
	if _, err = d.Token(); err != io.EOF {
		return "", errors.New("invalid_input")
	}
	return phone, nil
}

func copySnapshot(input, clone string) error {
	info, err := os.Lstat(input)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("snapshot_unavailable")
	}
	source := filepath.Join(input, wameow.DBFile)
	info, err = os.Lstat(source)
	if err != nil || !info.Mode().IsRegular() || info.Size() < 16 || info.Size() > maxSnapshot {
		return errors.New("snapshot_unavailable")
	}
	for _, suffix := range []string{"-wal", "-shm"} {
		if _, err = os.Lstat(source + suffix); !os.IsNotExist(err) {
			return errors.New("snapshot_requires_checkpoint")
		}
	}
	f, err := os.Open(source)
	if err != nil {
		return errors.New("snapshot_unavailable")
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(info, opened) {
		return errors.New("snapshot_unavailable")
	}
	header := make([]byte, 16)
	if _, err = io.ReadFull(f, header); err != nil || string(header) != "SQLite format 3\x00" {
		return errors.New("snapshot_invalid")
	}
	if _, err = f.Seek(0, io.SeekStart); err != nil {
		return errors.New("snapshot_unavailable")
	}
	target, err := os.OpenFile(filepath.Join(clone, wameow.DBFile), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("clone_unavailable")
	}
	count, copyErr := io.Copy(target, io.LimitReader(f, maxSnapshot+1))
	closeErr := target.Close()
	after, statErr := f.Stat()
	if copyErr != nil || closeErr != nil || statErr != nil || count != info.Size() || count > maxSnapshot || after.Size() != info.Size() || after.ModTime() != info.ModTime() {
		return errors.New("snapshot_changed_or_unavailable")
	}
	return nil
}

func check(input, phone string) report {
	result := report{Code: "session_unavailable"}
	parent, err := filepath.Abs(os.TempDir())
	if err != nil {
		return result
	}
	clone, err := os.MkdirTemp(parent, "session-check-")
	if err != nil {
		return result
	}
	relative, err := filepath.Rel(parent, clone)
	if err != nil || filepath.IsAbs(relative) || strings.HasPrefix(relative, "..") || relative == "." {
		return result
	}
	defer os.RemoveAll(clone) // Validated fresh leaf under the configured temporary parent.
	if err = os.Chmod(clone, 0700); err != nil {
		return result
	}
	if err = copySnapshot(input, clone); err != nil {
		result.Code = err.Error()
		return result
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := wameow.New(ctx, clone)
	if err != nil {
		return result
	}
	defer client.Close()
	// Same SDK observes the stored device identity. This is NOT proof of live login.
	db, err := sql.Open("sqlite", filepath.Join(clone, wameow.DBFile)+"?mode=ro")
	if err != nil {
		return result
	}
	defer db.Close()
	container := sqlstore.NewWithDB(db, "sqlite", waLog.Noop)
	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		return result
	}
	if device.ID == nil {
		result.Code = "unpaired"
		return result
	}
	result.Paired = true
	result.PhoneMatches = device.ID.ToNonAD().User == strings.TrimPrefix(phone, "+")
	if !result.PhoneMatches {
		result.Code = "declared_identity_mismatch"
		return result
	}
	own, err := client.SelfChatRef(ctx)
	if err != nil {
		return result
	}
	result.SelfRefKnown, err = client.HasChat(ctx, own)
	if err != nil || !result.SelfRefKnown {
		return result
	}
	if err = db.QueryRowContext(ctx, "SELECT count(*) FROM radar_chat_ref").Scan(&result.KnownChats); err != nil {
		return result
	}
	if err = db.QueryRowContext(ctx, "SELECT count(*) FROM radar_pending").Scan(&result.PendingMessages); err != nil {
		return result
	}
	result.Code = "ok"
	return result
}

func run(args []string, input io.Reader, output io.Writer) int {
	flags := flag.NewFlagSet("session-check", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	directory := flags.String("session-dir", "", "Read-only checkpoint directory")
	result := report{Code: "invalid_input"}
	if flags.Parse(args) == nil && *directory != "" && flags.NArg() == 0 {
		phone, err := declaredPhone(input)
		if err == nil {
			result = check(*directory, phone)
		}
	}
	if json.NewEncoder(output).Encode(result) != nil {
		return 2
	}
	if result.Code != "ok" {
		return 2
	}
	return 0
}

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout)) }
