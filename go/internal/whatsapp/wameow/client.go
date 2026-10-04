// Local bridge identity journal. Public worker contracts remain unchanged.
package wameow

import (
	"context"
	"database/sql"
	"errors"
	"regexp"

	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"radar.local/radar/internal/whatsapp"
)

var providerIdentity = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

// SelfChatRef binds only the authenticated device identity, never a display name.
// The private JID remains in the protocol database; callers receive an opaque ref.
func (c *Client) SelfChatRef(ctx context.Context) (string, error) {
	if c.cli.Store.ID == nil {
		return "", errors.New("session_identity_unavailable")
	}
	return c.chatRef(ctx, c.cli.Store.ID.ToNonAD())
}

// SetEnabledChats must be called by the trusted local host before Connect.
// An empty allowlist captures no bodies; pair/list use this safe explicit mode.
// An unconfigured legacy Worker retains its existing behavior, not a live gate.
func (c *Client) SetEnabledChats(ctx context.Context, refs []string) error {
	if len(refs) > 100 {
		return errors.New("capture_allowlist_limit")
	}
	enabled := make(map[string]bool, len(refs))
	for _, ref := range refs {
		if _, err := c.jidFor(ctx, ref); err != nil {
			return errors.New("capture_chat_unknown")
		}
		enabled[ref] = true
	}
	c.mu.Lock()
	c.captureFilter = true
	c.enabledChats = enabled
	c.mu.Unlock()
	return nil
}

func (c *Client) identityJournal(ctx context.Context) error {
	_, err := c.db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS radar_provider_journal(account TEXT,chat_ref TEXT,provider_id TEXT,seq INTEGER NOT NULL,PRIMARY KEY(account,chat_ref,provider_id));
CREATE UNIQUE INDEX IF NOT EXISTS radar_provider_cursor ON radar_provider_journal(seq);`)
	return err
}

func (c *Client) capture(ctx context.Context, e *events.Message) error {
	c.mu.Lock()
	configured := c.captureFilter
	c.mu.Unlock()
	if e != nil && e.Info.IsFromMe && configured {
		// Explicit selected-own capture is based on an independently observed
		// provider event, never Send's acknowledgement. Other sent chats retain
		// the legacy ignore policy. Do not mutate the SDK's event object.
		if c.cli.Store.ID == nil {
			return nil
		}
		chat := e.Info.Chat.ToNonAD()
		if chat.Server == types.HiddenUserServer && c.cli.Store.LIDs != nil {
			mapped, err := c.cli.Store.LIDs.GetPNForLID(ctx, chat)
			if err != nil || mapped.IsEmpty() {
				return nil
			}
			chat = mapped.ToNonAD()
		}
		if chat != c.cli.Store.ID.ToNonAD() {
			return nil
		}
		copy := *e
		copy.Info.IsFromMe = false
		e = &copy
	}
	chat, text, at, ok := extract(e)
	if !ok {
		return nil
	}
	if !configured {
		// Preserve the legacy Worker protocol; only the new local bridge opts into
		// strict provider identities and pre-insert private capture filtering.
		return c.store(ctx, chat, text, at)
	}
	ref, err := c.chatRef(ctx, chat)
	if err != nil {
		return err
	}
	c.mu.Lock()
	capture := !c.captureFilter || c.enabledChats[ref]
	c.mu.Unlock()
	if !capture {
		return nil
	} // No body/provider ID from disabled chats reaches SQL.
	if !providerIdentity.MatchString(string(e.Info.ID)) || c.cli.Store.ID == nil {
		return errors.New("provider_identity_unavailable")
	}
	account := c.cli.Store.ID.ToNonAD().String()
	if err = c.identityJournal(ctx); err != nil {
		return err
	}
	tx, err := c.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var previous int64
	err = tx.QueryRowContext(ctx, `SELECT seq FROM radar_provider_journal WHERE account=? AND chat_ref=? AND provider_id=?`, account, ref, string(e.Info.ID)).Scan(&previous)
	if err == nil {
		return nil
	}
	if err != sql.ErrNoRows {
		return err
	}
	insert, err := tx.ExecContext(ctx, `INSERT INTO radar_pending(chat_ref,text,observed_at) VALUES(?,?,?)`, ref, text, at.UTC().Format(tsLayout))
	if err != nil {
		return err
	}
	seq, err := insert.LastInsertId()
	if err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO radar_provider_journal VALUES(?,?,?,?)`, account, ref, string(e.Info.ID), seq); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO radar_chat_seen(ref,last_message_at) VALUES(?,?) ON CONFLICT(ref) DO UPDATE SET last_message_at=max(last_message_at,excluded.last_message_at)`, ref, at.UTC().Format(tsLayout)); err != nil {
		return err
	}
	return tx.Commit()
}

// ProviderMessageID returns the actual private protocol ID for a captured cursor.
// Legacy pending rows without journal identity fail closed, never hash the text.
func (c *Client) ProviderMessageID(ctx context.Context, cursor string) (string, error) {
	seq, ok := whatsapp.CursorSeq(cursor)
	if !ok {
		return "", whatsapp.ErrBadCursor
	}
	if c.cli.Store.ID == nil {
		return "", errors.New("provider_identity_unavailable")
	}
	if err := c.identityJournal(ctx); err != nil {
		return "", err
	}
	var id string
	err := c.db.QueryRowContext(ctx, `SELECT provider_id FROM radar_provider_journal WHERE account=? AND seq=?`, c.cli.Store.ID.ToNonAD().String(), seq).Scan(&id)
	return id, err
}
