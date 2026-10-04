"""Owner-requested drafts, never autonomous replies from incoming text or an LLM."""
from datetime import timedelta

from radar.domain.core import utc
from .models import ConversationError, active, fingerprint, validate_drafts


class Conversations:
    def __init__(self, repository):
        self.repository = repository

    def compose(self, *, authority, event_ref, account_ref, drafts, now, expires_at=None):
        active(authority, now)
        validate_drafts(drafts)
        expires_at = expires_at or min(utc(authority.expires_at), utc(now) + timedelta(minutes=15))
        return self.repository.compose(authority=authority, event_ref=event_ref, account_ref=account_ref,
                                       drafts=drafts, now=now, expires_at=expires_at)

    def screen(self, *, authority, batch_ref, now):
        active(authority, now)
        return self.repository.screen(authority=authority, batch_ref=batch_ref, now=now)

    def approve(self, *, authority, event_ref, screen, now):
        active(authority, now)
        return self.repository.approve(authority=authority, event_ref=event_ref, callback_ref=screen.callback_ref,
                                       content_hash=screen.content_hash,
                                       operation_ids=tuple(r['operation_id'] for r in screen.rows),
                                       display_hash=fingerprint(screen.rows), now=now)

    def correct(self, *, authority, batch_ref, expected_version, drafts, now):
        active(authority, now)
        validate_drafts(drafts)
        return self.repository.correct(authority=authority, batch_ref=batch_ref, expected_version=expected_version,
                                       drafts=drafts, now=now)

    def list_chats(self, *, authority, account_ref, now, limit=20, after_ref=None):
        active(authority, now)
        return self.repository.list_chats(authority=authority, account_ref=account_ref, now=now,
                                          limit=limit, after_ref=after_ref)

    def sync(self, *, authority, account_ref, now, worker=None, cursor_ref=None, limit=20):
        active(authority, now)
        if worker is None:
            raise ConversationError('inbox_backend_not_connected')
        return self.repository.sync(authority=authority, account_ref=account_ref, now=now,
                                    worker=worker, cursor_ref=cursor_ref, limit=limit)

    def list_messages(self, *, authority, account_ref, chat_ref, now, limit=20, after_ref=None):
        active(authority, now)
        return self.repository.list_messages(authority=authority, account_ref=account_ref, chat_ref=chat_ref,
                                             now=now, limit=limit, after_ref=after_ref)

    def dispatch_fixture(self, *, authority, operation_id, now, dispatcher=None):
        active(authority, now)
        if dispatcher is None:
            raise ConversationError('real_dispatch_disabled')
        return self.repository.dispatch_fixture(authority=authority, operation_id=operation_id,
                                                now=now, dispatcher=dispatcher)

    def results(self, *, authority, batch_ref, now):
        active(authority, now)
        return self.repository.results(authority=authority, batch_ref=batch_ref, now=now)

    def reconcile(self, *, authority, operation_id, proof_ref, now):
        active(authority, now)
        return self.repository.reconcile(authority=authority, operation_id=operation_id, proof_ref=proof_ref, now=now)
