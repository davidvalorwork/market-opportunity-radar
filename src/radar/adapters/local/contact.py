"""Private general contact preparation. No network, sends or fake wire events."""
from base64 import b64decode
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from radar.application.contact.model import (CampaignView, CandidateEvidence,
    ContactError, ConversationPreparation, PreparedContact, PublishedContact,
    ResolvedContact, ResolutionBinding, ResponseObservation, choose_contacts,
    declared_quote, facts_valid, limit, private_text, render)
from radar.application.tasks.models import Authority
from radar.adapters.sources.generic.extraction import contact_candidates
from radar.adapters.sources.generic.model import Record, Report, reference
from radar.domain.core import utc
from radar.ports.types import BlobPointer, LedgerState
from .sqlite import parse, stamp


DDL = """
CREATE TABLE IF NOT EXISTS contact_campaigns(owner TEXT,ref TEXT,actor TEXT,task TEXT,task_version INTEGER,task_hash TEXT,purpose TEXT,private TEXT,hash TEXT,state TEXT,version INTEGER,dedupe_seconds INTEGER,source_refs TEXT,silence_seconds INTEGER,PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS contact_events(owner TEXT,event TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,event));
CREATE TABLE IF NOT EXISTS contact_operations(owner TEXT,ref TEXT,campaign TEXT,candidate TEXT,phase TEXT,private TEXT,hash TEXT,state TEXT,version INTEGER,resolution TEXT,resolution_proof TEXT,outbound TEXT,observation TEXT,purpose TEXT,PRIMARY KEY(owner,ref),UNIQUE(owner,campaign,candidate,phase));
CREATE INDEX IF NOT EXISTS contact_campaign_ops ON contact_operations(owner,campaign,ref);
CREATE TABLE IF NOT EXISTS contact_purpose_reservations(owner TEXT,recipient TEXT,purpose TEXT,phase TEXT,op TEXT,expires TEXT,PRIMARY KEY(owner,recipient,purpose,phase));
"""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def opaque(value):
    if not reference(value):
        raise ContactError('opaque_reference_required')
    return value


def pointer_doc(pointer):
    return asdict(pointer)


def resolution_document(proof):
    result = asdict(proof)
    result['expires_at'] = stamp(proof.expires_at)
    return canonical(result)


class LocalContacts:
    """Host-issued grants + real A6/A8 boundaries, no production defaults.

    resolver/observations are independent trusted repositories; user-provided
    booleans or ResolvedContact objects are never accepted as approval/proof.
    """
    def __init__(self, task_store, *, source_sink, vault, host_authority,
                 resolver, observations, clock, private_scope='worker:contact'):
        if any(value is None for value in (source_sink, vault, resolver, observations)) or not callable(host_authority) or not callable(clock):
            raise ContactError('trusted_contact_wiring_required')
        if not reference(private_scope, 'recipient_scope'):
            raise ContactError('private_scope_invalid')
        self.tasks, self.store = task_store, task_store.store
        self.source_sink, self.vault = source_sink, vault
        self.host_authority, self.resolver, self.observations = host_authority, resolver, observations
        self.clock, self.private_scope = clock, private_scope
        self.store.db.executescript(DDL)

    def _host(self, owner, actor):
        opaque(owner)
        opaque(actor)
        now = utc(self.clock())
        authority = self.host_authority(owner, actor, now)
        if not isinstance(authority, Authority) or (authority.owner_ref, authority.actor_ref) != (owner, actor):
            raise ContactError('contact_authority')
        self.tasks._authority(authority, now)
        capability = dict(authority.capabilities).get('contact')
        if capability is None or not self.store.source_allowed(owner_ref=owner, source_ref=capability):
            raise ContactError('contact_capability')
        return authority

    def _pointer(self, pointer):
        if not isinstance(pointer, BlobPointer) or not reference(pointer.blob_key, 'blob_key') or pointer.recipient_scope != self.private_scope or not isinstance(pointer.sha256, str) or len(pointer.sha256) != 64 or any(char not in '0123456789abcdef' for char in pointer.sha256):
            raise ContactError('private_reference_invalid')
        return pointer

    def _seal(self, owner, value):
        self._host(owner, value['actor_ref'])
        plaintext = canonical(value).encode()
        if len(plaintext) > 4_000_000:
            raise ContactError('private_document_limit')
        try:
            pointer = self.vault.seal(owner_ref=owner, plaintext=plaintext)
        except Exception:
            raise ContactError('private_vault_failed') from None
        pointer = self._pointer(pointer)
        return canonical(pointer_doc(pointer))

    def _open(self, owner, value):
        pointer = self._pointer(BlobPointer(**json.loads(value)))
        try:
            content = self.vault.open(owner_ref=owner, pointer=pointer)
        except Exception:
            raise ContactError('private_vault_failed') from None
        if not isinstance(content, bytes) or len(content) > 4_000_000:
            raise ContactError('private_document_limit')
        return json.loads(content)

    def _campaign(self, owner, actor, campaign_ref):
        authority = self._host(owner, actor)
        row = self.store.db.execute('SELECT actor,task,task_version,task_hash,purpose,private,hash,state,version,dedupe_seconds,source_refs,silence_seconds FROM contact_campaigns WHERE owner=? AND ref=?', (owner, campaign_ref)).fetchone()
        if row is None or row[0] != actor:
            raise ContactError('campaign_unknown')
        if row[7] != 'active':
            raise ContactError('campaign_cancelled')
        proposal = self.tasks._row(owner, row[1])
        if proposal.status != 'confirmed' or proposal.version != row[2] or proposal.confirmation_hash != row[3] or proposal.actor_ref != actor:
            raise ContactError('campaign_intent_changed')
        self.tasks._plan(authority, proposal.snapshot)
        for source in json.loads(row[10]):
            if not self.store.source_allowed(owner_ref=owner, source_ref=source):
                raise ContactError('contact_source_revoked')
        doc = self._open(owner, row[5])
        if digest(doc) != row[6]:
            raise ContactError('private_document_integrity')
        return row, doc

    def _event(self, owner, event, fingerprint):
        opaque(event)
        row = self.store.db.execute('SELECT hash,result FROM contact_events WHERE owner=? AND event=?', (owner, event)).fetchone()
        if row and row[0] != fingerprint:
            raise ContactError('contact_event_conflict')
        return row[1] if row else None

    def _resolution(self, binding, proof_ref):
        try:
            return self.resolver.resolve(binding=binding, proof_ref=proof_ref, now=utc(self.clock()))
        except ContactError:
            raise  # Trusted boundary uses static codes, not provider text.
        except Exception:
            raise ContactError('resolution_lookup_failed') from None

    def _remember(self, owner, event, fingerprint, result):
        self.store.db.execute('INSERT INTO contact_events VALUES(?,?,?,?)', (owner, event, fingerprint, result))

    def _finding(self, owner, record):
        if not isinstance(record, Record) or record.owner_ref != owner or not record.provenance:
            raise ContactError('finding_owner_or_evidence')
        opaque(record.record_ref)
        for proof in record.provenance:
            opaque(proof.source_ref)
            utc(proof.observed_at)
            if proof.observed_at > utc(self.clock()):
                raise ContactError('finding_future_evidence')
            if not self.store.source_allowed(owner_ref=owner, source_ref=proof.source_ref):
                raise ContactError('contact_source_revoked')
        try:
            raw = self.source_sink.read(owner_ref=owner, pointer=record.private_ref, max_bytes=524288)
        except Exception:
            raise ContactError('source_private_read_failed') from None
        if not isinstance(raw, bytes) or len(raw) > 524288:
            raise ContactError('finding_limit')
        packet = json.loads(raw)
        content = b64decode(packet['content_b64'], validate=True)
        candidates = contact_candidates(content)
        # No arbitrary endpoint from the caller or a tampered contact_candidates
        # array: derive it again from actual privately stored evidence bytes.
        facts = {}
        try:
            parsed = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict) and 'facts' in parsed:
            facts = facts_valid(parsed['facts'])
        evidence = tuple(CandidateEvidence(record.record_ref, proof.source_ref, proof.observed_at, record.private_ref.sha256) for proof in record.provenance)
        return tuple(PublishedContact('candidate:'+uuid4().hex, candidate.kind,
                                      candidate.value, tuple(sorted(facts.items())), evidence)
                     for candidate in candidates)

    def start(self, *, owner_ref, actor_ref, event_ref, task_ref, reports,
              purpose_ref, template, request_facts, maximum=10, filters=None,
              dedupe_seconds=0, silence_seconds=86400, followup_purpose_ref=None):
        opaque(purpose_ref)
        if followup_purpose_ref is not None:
            opaque(followup_purpose_ref)
            if followup_purpose_ref == purpose_ref:
                raise ContactError('followup_purpose_must_be_explicit')
        limit(dedupe_seconds, 0, 90*86400)
        limit(silence_seconds, 60, 90*86400)
        limit(maximum, 1, 50)
        facts_valid(request_facts)
        private_text(template)
        if type(reports) is not tuple or len(reports) > 20 or any(not isinstance(report, Report) for report in reports):
            raise ContactError('finding_reports_invalid')
        records = tuple(record for report in reports for record in report.records)
        if len(records) > 200:
            raise ContactError('finding_limit')
        # Datetime is framed explicitly; no private payload goes into control.
        record_keys = [(record.owner_ref, record.record_ref, pointer_doc(record.private_ref),
                        [(proof.source_ref, proof.page, stamp(proof.observed_at)) for proof in record.provenance]) for record in records]
        coverage = tuple((report.source_ref, report.code.value, report.complete) for report in reports)
        fingerprint = digest([task_ref, record_keys, purpose_ref, template, request_facts,
                              maximum, filters or {}, dedupe_seconds, silence_seconds, coverage, followup_purpose_ref])
        with self.store.transaction():
            authority = self._host(owner_ref, actor_ref)
            existing = self._event(owner_ref, event_ref, fingerprint)
            if existing:
                stored, doc = self._campaign(owner_ref, actor_ref, existing)
                return self._view(existing, doc, stored[5])
            proposal = self.tasks._row(owner_ref, task_ref)
            if proposal.actor_ref != actor_ref or proposal.status != 'confirmed' or proposal.confirmation_hash != proposal.content_hash or proposal.expires_at <= utc(self.clock()):
                raise ContactError('confirmed_intent_required')
            self.tasks._plan(authority, proposal.snapshot)
            found = tuple(candidate for record in records for candidate in self._finding(owner_ref, record))
            shortlist = choose_contacts(found, maximum=maximum, filters=filters, coverage=coverage)
            doc = {'actor_ref': actor_ref, 'shortlist': asdict(shortlist),
                   'template': template, 'request_facts': request_facts,
                   'followup_purpose_ref': followup_purpose_ref}
            for candidate in doc['shortlist']['candidates']:
                for proof in candidate['evidence']:
                    proof['observed_at'] = stamp(proof['observed_at'])
            sealed = self._seal(owner_ref, doc)
            campaign = 'campaign:'+uuid4().hex
            sources = sorted({proof.source_ref for record in records for proof in record.provenance})
            self.store.db.execute('INSERT INTO contact_campaigns VALUES(?,?,?,?,?,?,?,?,?,\'active\',1,?,?,?)', (owner_ref, campaign, actor_ref, task_ref, proposal.version, proposal.confirmation_hash, purpose_ref, sealed, digest(doc), dedupe_seconds, canonical(sources), silence_seconds))
            self._remember(owner_ref, event_ref, fingerprint, campaign)
            self.store.failpoint('contact_campaign_created')
            self._host(owner_ref, actor_ref)
            return self._view(campaign, doc, sealed)

    def _view(self, campaign, doc, sealed):
        summary = doc['shortlist']
        return CampaignView(campaign, BlobPointer(**json.loads(sealed)), tuple(candidate['candidate_ref'] for candidate in summary['candidates']), summary['discarded'], summary['duplicates'], summary['truncated'], tuple(tuple(row) for row in summary['coverage']))

    def prepare(self, *, owner_ref, actor_ref, campaign_ref, candidate_ref,
                event_ref, phase='initial', followup_template=None):
        if phase not in ('initial', 'followup'):
            raise ContactError('contact_phase_invalid')
        fingerprint = digest([campaign_ref, candidate_ref, phase, followup_template])
        with self.store.transaction():
            campaign, doc = self._campaign(owner_ref, actor_ref, campaign_ref)
            existing = self._event(owner_ref, event_ref, fingerprint)
            if existing:
                return self._prepared(owner_ref, existing)
            old = self.store.db.execute('SELECT ref FROM contact_operations WHERE owner=? AND campaign=? AND candidate=? AND phase=?', (owner_ref, campaign_ref, candidate_ref, phase)).fetchone()
            if old:
                raise ContactError('contact_already_prepared')
            candidate = next((candidate for candidate in doc['shortlist']['candidates'] if candidate['candidate_ref'] == candidate_ref), None)
            if candidate is None:
                raise ContactError('candidate_unknown')
            if phase == 'followup':
                if doc['followup_purpose_ref'] is None:
                    raise ContactError('followup_opt_in_required')
                self._follow_allowed(owner_ref, campaign_ref, candidate_ref, campaign[11])
                private_text(followup_template)
            facts = dict(candidate['facts'])
            if facts.keys() & doc['request_facts'].keys():
                raise ContactError('fact_collision')
            facts.update(doc['request_facts'])
            text = render(followup_template if phase == 'followup' else doc['template'], facts)
            if phase == 'followup':
                text = private_text('SEGUIMIENTO: '+text)
            operation = 'contact:'+uuid4().hex
            purpose = doc['followup_purpose_ref'] if phase == 'followup' else campaign[4]
            packet = {'actor_ref': actor_ref, 'candidate': candidate, 'text': text,
                      'purpose_ref': purpose, 'base_purpose_ref': campaign[4], 'phase': phase}
            sealed = self._seal(owner_ref, packet)
            content_hash = sha256(text.encode()).hexdigest()
            self.store.db.execute('INSERT INTO contact_operations VALUES(?,?,?,?,?,?,?,\'pending_resolution_approval\',1,NULL,NULL,NULL,NULL,?)', (owner_ref, operation, campaign_ref, candidate_ref, phase, sealed, content_hash, purpose))
            self._remember(owner_ref, event_ref, fingerprint, operation)
            self.store.failpoint('contact_prepared')
            self._campaign(owner_ref, actor_ref, campaign_ref)
            return self._prepared(owner_ref, operation)

    def _prepared(self, owner, operation):
        row = self.store.db.execute('SELECT candidate,private,hash,state,purpose FROM contact_operations WHERE owner=? AND ref=?', (owner, operation)).fetchone()
        return PreparedContact(operation, row[0], BlobPointer(**json.loads(row[1])), row[2], row[4], row[3])

    def _follow_allowed(self, owner, campaign_ref, candidate_ref, silence_seconds):
        initial = self.store.db.execute('SELECT outbound,observation,resolution FROM contact_operations WHERE owner=? AND campaign=? AND candidate=? AND phase=\'initial\'', (owner, campaign_ref, candidate_ref)).fetchone()
        if initial is None or not initial[0] or not initial[1]:
            raise ContactError('followup_silence_proof_required')
        record = self.store.get(owner_ref=owner, operation_id=initial[0])
        observation = json.loads(initial[1])
        if record is None or record.state != LedgerState.PROVIDER_CONFIRMED or observation['state'] != 'silence':
            raise ContactError('followup_uncertain_or_response')
        if parse(observation['observed_until']) < parse(observation['confirmed_at'])+timedelta(seconds=silence_seconds):
            raise ContactError('followup_silence_too_short')
        return json.loads(initial[2])

    def _follow_target(self, owner, campaign_ref, candidate_ref, silence_seconds, proof):
        original = self._follow_allowed(owner, campaign_ref, candidate_ref, silence_seconds)
        if (proof.recipient_ref, proof.account_ref, proof.chat_ref, proof.channel) != tuple(original[key] for key in ('recipient_ref', 'account_ref', 'chat_ref', 'channel')):
            raise ContactError('followup_original_target_required')

    def resolve(self, *, owner_ref, actor_ref, operation_ref, proof_ref, expected_version=1):
        opaque(proof_ref)
        with self.store.transaction():
            row = self.store.db.execute('SELECT campaign,candidate,private,hash,state,version,resolution,resolution_proof,phase FROM contact_operations WHERE owner=? AND ref=?', (owner_ref, operation_ref)).fetchone()
            if row is None:
                self._host(owner_ref, actor_ref)
                raise ContactError('contact_unknown')
            campaign, _ = self._campaign(owner_ref, actor_ref, row[0])
            if row[8] == 'followup':
                self._follow_allowed(owner_ref, row[0], row[1], campaign[11])
            purpose = self._prepared(owner_ref, operation_ref).purpose_ref
            binding = ResolutionBinding(owner_ref, actor_ref, operation_ref, row[1], purpose, BlobPointer(**json.loads(row[2])), row[3])
            proof = self._resolution(binding, proof_ref)
            if not isinstance(proof, ResolvedContact) or proof.binding != binding or utc(proof.expires_at) <= utc(self.clock()):
                raise ContactError('contact_resolution_binding')
            if row[8] == 'followup':
                self._follow_target(owner_ref, row[0], row[1], campaign[11], proof)
            authority = self._host(owner_ref, actor_ref)
            for value in (proof.account_ref, proof.chat_ref, proof.recipient_ref):
                opaque(value)
            session = self.store.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?', (owner_ref, proof.session_ref)).fetchone()
            if (proof.session_ref, proof.session_version) not in authority.sessions or session is None or session[0] != proof.session_version:
                raise ContactError('contact_session_changed')
            if row[4] == 'resolved':
                if expected_version != 1 or row[7] != proof_ref or row[6] != resolution_document(proof):
                    raise ContactError('contact_resolution_replay')
                return self._prepared(owner_ref, operation_ref)
            if row[5] != expected_version or row[4] != 'pending_resolution_approval':
                raise ContactError('contact_version_conflict')
            old = self.store.db.execute('SELECT op,expires FROM contact_purpose_reservations WHERE owner=? AND recipient=? AND purpose=? AND phase=?', (owner_ref, proof.recipient_ref, purpose, row[8])).fetchone()
            if old and old[0] != operation_ref and (old[1] is None or parse(old[1]) > utc(self.clock())):
                raise ContactError('contact_purpose_duplicate')
            expiry = stamp(utc(self.clock())+timedelta(seconds=campaign[9])) if campaign[9] else None
            self.store.db.execute('INSERT OR REPLACE INTO contact_purpose_reservations VALUES(?,?,?,?,?,?)', (owner_ref, proof.recipient_ref, purpose, row[8], operation_ref, expiry))
            self.store.db.execute('UPDATE contact_operations SET state=\'resolved\',version=version+1,resolution=?,resolution_proof=? WHERE owner=? AND ref=?', (resolution_document(proof), proof_ref, owner_ref, operation_ref))
            self.store.failpoint('contact_resolved')
            self._campaign(owner_ref, actor_ref, row[0])
            if utc(proof.expires_at) <= utc(self.clock()):
                raise ContactError('contact_resolution_binding')
            return self._prepared(owner_ref, operation_ref)

    def export(self, *, owner_ref, actor_ref, operation_ref):
        with self.store.transaction():
            row = self.store.db.execute('SELECT campaign,private,state,resolution_proof,resolution,phase,candidate FROM contact_operations WHERE owner=? AND ref=?', (owner_ref, operation_ref)).fetchone()
            if row is None:
                self._host(owner_ref, actor_ref)
                raise ContactError('contact_unknown')
            campaign, _ = self._campaign(owner_ref, actor_ref, row[0])
            if row[5] == 'followup':
                self._follow_allowed(owner_ref, row[0], row[6], campaign[11])
            if row[2] != 'resolved':
                raise ContactError('resolution_approval_required')
            prepared = self._prepared(owner_ref, operation_ref)
            binding = ResolutionBinding(owner_ref, actor_ref, operation_ref, prepared.candidate_ref, prepared.purpose_ref, prepared.private_ref, prepared.content_hash)
            proof = self._resolution(binding, row[3])
            if not isinstance(proof, ResolvedContact) or proof.binding != binding or utc(proof.expires_at) <= utc(self.clock()):
                raise ContactError('contact_resolution_binding')
            if resolution_document(proof) != row[4]:
                raise ContactError('contact_resolution_replay')
            if row[5] == 'followup':
                self._follow_target(owner_ref, row[0], row[6], campaign[11], proof)
            authority = self._host(owner_ref, actor_ref)
            session = self.store.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?', (owner_ref, proof.session_ref)).fetchone()
            if (proof.session_ref, proof.session_version) not in authority.sessions or session is None or session[0] != proof.session_version:
                raise ContactError('contact_session_changed')
            packet = self._open(owner_ref, row[1])
            if sha256(packet['text'].encode()).hexdigest() != prepared.content_hash:
                raise ContactError('private_document_integrity')
            self._host(owner_ref, actor_ref)
            if utc(proof.expires_at) <= utc(self.clock()):
                raise ContactError('contact_resolution_binding')
            return ConversationPreparation(operation_ref, proof, packet['text'], prepared.purpose_ref)

    def note_outbound(self, *, owner_ref, actor_ref, operation_ref, outbound_ref):
        """Link an independently approved A3/A10 operation, never create approval."""
        try:
            if str(UUID(outbound_ref)) != outbound_ref:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            opaque(outbound_ref)
        with self.store.transaction():
            row = self.store.db.execute('SELECT campaign,hash,resolution,outbound FROM contact_operations WHERE owner=? AND ref=?', (owner_ref, operation_ref)).fetchone()
            if row is None:
                self._host(owner_ref, actor_ref)
                raise ContactError('contact_unknown')
            campaign, _ = self._campaign(owner_ref, actor_ref, row[0])
            if row[2] is None:
                raise ContactError('resolution_approval_required')
            target = json.loads(row[2])
            record = self.store.get(owner_ref=owner_ref, operation_id=outbound_ref)
            expected = (row[1], target['recipient_ref'], target['binding']['purpose_ref'], target['session_ref'], target['session_version'])
            actual = (record.content_hash, record.recipient_ref, record.purpose, record.session_ref, record.session_version) if record else None
            if actual != expected or record.approval is None or record.approval.actor_ref != actor_ref:
                raise ContactError('outbound_exact_binding')
            approval = record.approval
            if (approval.content_hash, approval.recipient_ref, approval.purpose, approval.session_ref, approval.session_version) != expected:
                raise ContactError('outbound_exact_binding')
            if record.state == LedgerState.PROPOSED:
                raise ContactError('outbound_approval_required')
            if row[3] is not None and row[3] != outbound_ref:
                raise ContactError('outbound_replay_conflict')
            self.store.db.execute('UPDATE contact_operations SET outbound=? WHERE owner=? AND ref=?', (outbound_ref, owner_ref, operation_ref))
            self.store.failpoint('contact_outbound_linked')

    def observe(self, *, owner_ref, actor_ref, operation_ref, proof_ref):
        """Persist independent complete-read evidence; input text is never commands."""
        opaque(proof_ref)
        with self.store.transaction():
            row = self.store.db.execute('SELECT campaign,resolution,outbound,observation FROM contact_operations WHERE owner=? AND ref=?', (owner_ref, operation_ref)).fetchone()
            if row is None:
                self._host(owner_ref, actor_ref)
                raise ContactError('contact_unknown')
            self._campaign(owner_ref, actor_ref, row[0])
            if not row[1] or not row[2]:
                raise ContactError('confirmed_outbound_required')
            record = self.store.get(owner_ref=owner_ref, operation_id=row[2])
            if record is None or record.state != LedgerState.PROVIDER_CONFIRMED:
                raise ContactError('outbound_uncertain_or_unconfirmed')
            try:
                observation = self.observations.resolve(owner_ref=owner_ref, operation_ref=row[2], proof_ref=proof_ref, now=utc(self.clock()))
            except ContactError:
                raise
            except Exception:
                raise ContactError('observation_lookup_failed') from None
            if not isinstance(observation, ResponseObservation) or (observation.owner_ref, observation.operation_ref, observation.recipient_ref, observation.provider_message_ref) != (owner_ref, row[2], record.recipient_ref, record.provider_message_ref):
                raise ContactError('response_observation_binding')
            if observation.state not in ('silence', 'reply', 'ambiguous', 'uncertain') or not utc(observation.confirmed_at) <= utc(observation.observed_until) <= utc(self.clock()):
                raise ContactError('response_observation_invalid')
            if observation.state in ('reply', 'ambiguous') and observation.private_ref is None:
                raise ContactError('response_evidence_required')
            if observation.private_ref is not None:
                self._pointer(observation.private_ref)
            document = asdict(observation)
            document['confirmed_at'], document['observed_until'] = stamp(observation.confirmed_at), stamp(observation.observed_until)
            document['proof_ref'] = proof_ref
            if row[3]:
                previous = json.loads(row[3])
                if document['observed_until'] < previous['observed_until'] or (previous['state'] in ('reply', 'ambiguous') and observation.state == 'silence'):
                    raise ContactError('response_observation_stale')
                if document['observed_until'] == previous['observed_until'] and document != previous:
                    raise ContactError('response_observation_conflict')
            self.store.db.execute('UPDATE contact_operations SET observation=? WHERE owner=? AND ref=?', (canonical(document), owner_ref, operation_ref))
            self.store.failpoint('contact_observed')
            self._campaign(owner_ref, actor_ref, row[0])
            return observation

    def cancel(self, *, owner_ref, actor_ref, campaign_ref, expected_version=1):
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            row = self.store.db.execute('SELECT version,state,actor FROM contact_campaigns WHERE owner=? AND ref=?', (owner_ref, campaign_ref)).fetchone()
            if row is None or row[2] != actor_ref:
                raise ContactError('campaign_unknown')
            if row[1] == 'cancelled':
                return
            if row[0] != expected_version:
                raise ContactError('contact_version_conflict')
            # Durable cancellation of linked, not-yet-settled local operations.
            # Keep A10's outbox intact: A3 claim observes this tombstone, while
            # uncertainty/confirmed effects retain their actual state.
            linked = self.store.db.execute('SELECT outbound FROM contact_operations WHERE owner=? AND campaign=? AND outbound IS NOT NULL', (owner_ref, campaign_ref)).fetchall()
            for (operation,) in linked:
                record = self.store.get(owner_ref=owner_ref, operation_id=operation)
                if record and record.state in (LedgerState.PROPOSED, LedgerState.APPROVED, LedgerState.DISPATCH_COMMITTED):
                    self.store.db.execute('INSERT OR IGNORE INTO cancelled_actions VALUES(?,?)', (owner_ref, operation))
            self.store.db.execute('UPDATE contact_campaigns SET state=\'cancelled\',version=version+1 WHERE owner=? AND ref=?', (owner_ref, campaign_ref))

    def status(self, *, owner_ref, actor_ref, campaign_ref, after='', maximum=20):
        limit(maximum, 1, 100)
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            row = self.store.db.execute('SELECT 1 FROM contact_campaigns WHERE owner=? AND ref=? AND actor=?', (owner_ref, campaign_ref, actor_ref)).fetchone()
            if row is None:
                raise ContactError('campaign_unknown')
            return self.store.db.execute('SELECT ref,candidate,phase,state,version FROM contact_operations WHERE owner=? AND campaign=? AND ref>? ORDER BY ref LIMIT ?', (owner_ref, campaign_ref, after, maximum)).fetchall()
