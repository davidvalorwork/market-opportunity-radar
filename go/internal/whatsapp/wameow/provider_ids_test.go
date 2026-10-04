package wameow

import (
	"bytes"
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
	"radar.local/radar/internal/whatsapp"
)

func TestLocalProviderIdentityDedupeAndPrivacyBeforeInsert(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	c, err := New(ctx, dir)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	account := types.NewJID("15550000111", types.DefaultUserServer)
	c.cli.Store.ID = &account
	chat := types.NewJID("15550000222", types.DefaultUserServer)
	ref, err := c.chatRef(ctx, chat)
	if err != nil {
		t.Fatal(err)
	}
	if err = c.SetEnabledChats(ctx, nil); err != nil {
		t.Fatal(err)
	}
	event := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: chat}, ID: "DISABLED_PROVIDER_ID", Timestamp: time.Now().UTC()}, Message: &waE2E.Message{Conversation: proto.String("DISABLED_PRIVATE_SYNTHETIC_MESSAGE")}}
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	var count int
	if err = c.db.QueryRow(`SELECT count(*) FROM radar_pending`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("disabled body persisted: %d %v", count, err)
	}
	if err = c.SetEnabledChats(ctx, []string{ref}); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(dir, "*"))
	for _, file := range files {
		data, _ := os.ReadFile(file)
		if bytes.Contains(data, []byte("DISABLED_PRIVATE_SYNTHETIC_MESSAGE")) || bytes.Contains(data, []byte("DISABLED_PROVIDER_ID")) {
			t.Fatal("disabled private data reached disk")
		}
	}
	event.Info.ID = "SYNTHETIC_PROVIDER_ID"
	event.Message.Conversation = proto.String("PRIVATE_SYNTHETIC_MESSAGE")
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	page, _, err := pendingPage(ctx, c.db, []string{ref}, 10)
	if err != nil || len(page) != 1 {
		t.Fatalf("duplicate: %d %v", len(page), err)
	}
	id, err := c.ProviderMessageID(ctx, page[0].Cursor)
	if err != nil || id != event.Info.ID {
		t.Fatalf("identity: %s %v", id, err)
	}
	if err = c.Ack(ctx, page[0].Cursor); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	page, _, err = pendingPage(ctx, c.db, []string{ref}, 10)
	if err != nil || len(page) != 0 {
		t.Fatal("provider replay after ACK duplicated")
	}
	// A real restart retains the journal; same text with another ID is distinct.
	c.Close()
	c, err = New(ctx, dir)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.cli.Store.ID = &account
	if err = c.SetEnabledChats(ctx, []string{ref}); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	event.Info.ID = "SYNTHETIC_SECOND_ID"
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	page, _, err = pendingPage(ctx, c.db, []string{ref}, 10)
	if err != nil || len(page) != 1 {
		t.Fatal("restart identity lost")
	}
	event.Info.ID = ""
	if err = c.capture(ctx, event); err == nil {
		t.Fatal("missing provider ID accepted")
	}
	if _, err = c.ProviderMessageID(ctx, whatsapp.Cursor(999)); err != sql.ErrNoRows {
		t.Fatal("unknown cursor accepted")
	}
	self, err := c.SelfChatRef(ctx)
	if err != nil || self == ref {
		t.Fatal("self inferred from recipient", err)
	}
	actual, err := c.jidFor(ctx, self)
	if err != nil || actual != account {
		t.Fatal("self is not authenticated device", err)
	}
	// Same provider ID from a different chat/account cannot collide.
	event.Info.ID = "SYNTHETIC_SECOND_ID"
	event.Info.Chat = types.NewJID("15550000333", types.DefaultUserServer)
	otherRef, err := c.chatRef(ctx, event.Info.Chat)
	if err != nil {
		t.Fatal(err)
	}
	if err = c.SetEnabledChats(ctx, []string{ref, otherRef}); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	otherAccount := types.NewJID("15550000444", types.DefaultUserServer)
	c.cli.Store.ID = &otherAccount
	if err = c.capture(ctx, event); err != nil {
		t.Fatal(err)
	}
	if err = c.db.QueryRow(`SELECT count(*) FROM radar_provider_journal WHERE provider_id='SYNTHETIC_SECOND_ID'`).Scan(&count); err != nil || count != 3 {
		t.Fatal("provider scope collision", count, err)
	}
}

func TestLocalOwnEchoRequiresAuthenticatedSelectedChat(t *testing.T) {
	ctx := context.Background()
	c, err := New(ctx, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	account := types.NewJID("15550000111", types.DefaultUserServer)
	c.cli.Store.ID = &account
	own, err := c.SelfChatRef(ctx)
	if err != nil {
		t.Fatal(err)
	}
	other := types.NewJID("15550000222", types.DefaultUserServer)
	otherRef, err := c.chatRef(ctx, other)
	if err != nil {
		t.Fatal(err)
	}
	echo := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: account, IsFromMe: true}, ID: "SYNTHETIC_REAL_ECHO", Timestamp: time.Now().UTC()}, Message: &waE2E.Message{Conversation: proto.String("SYNTHETIC_OWN_TEXT")}}
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	if err = c.SetEnabledChats(ctx, []string{otherRef}); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	var count int
	if err = c.db.QueryRow(`SELECT count(*) FROM radar_pending`).Scan(&count); err != nil || count != 0 {
		t.Fatal("unselected/legacy own captured", count, err)
	}
	if err = c.SetEnabledChats(ctx, []string{own, otherRef}); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	page, _, err := pendingPage(ctx, c.db, []string{own, otherRef}, 10)
	if err != nil || len(page) != 1 || page[0].ChatRef != own {
		t.Fatal("authenticated own echo missing or duplicated", err)
	}
	if id, err := c.ProviderMessageID(ctx, page[0].Cursor); err != nil || id != "SYNTHETIC_REAL_ECHO" {
		t.Fatal("own identity invented", err)
	}
	if !echo.Info.IsFromMe {
		t.Fatal("provider event mutated")
	}
	echo.Info.Chat = other
	echo.Info.ID = "SYNTHETIC_OTHER_SENT"
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	if err = c.db.QueryRow(`SELECT count(*) FROM radar_pending`).Scan(&count); err != nil || count != 1 {
		t.Fatal("other from-me captured", count, err)
	}
	if err = c.Ack(ctx, page[0].Cursor); err != nil {
		t.Fatal(err)
	}
	echo.Info.Chat = account
	echo.Info.ID = "SYNTHETIC_REAL_ECHO"
	if err = c.capture(ctx, echo); err != nil {
		t.Fatal(err)
	}
	if err = c.db.QueryRow(`SELECT count(*) FROM radar_pending`).Scan(&count); err != nil || count != 0 {
		t.Fatal("own echo after ACK duplicated", count, err)
	}
}
