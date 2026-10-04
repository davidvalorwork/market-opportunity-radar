"""Interpretation and proposal flow. It never dispatches any operation."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json

from radar.domain.core import utc
from radar.ports.types import StructuredRequest
from .models import Budget, Plan, TaskError, canonical, content_hash
from .prompt import PROMPT_VERSION, SCHEMA_NAME
from .registry import OperationRegistry


class TaskRouter:
    def __init__(self, repository, *, validate_document, registry=None, parser=None,
                 parser_enabled=False, model_ref='fixture/task-parser',
                 default_timezone='America/Caracas', allowed_timezones=('America/Caracas',),
                 confidence_threshold=0.8, clock=None):
        self.repository = repository
        self.validate_document = validate_document
        self.registry = registry or OperationRegistry()
        self.parser, self.parser_enabled, self.model_ref = parser, parser_enabled, model_ref
        self.clock = clock
        self.allowed_timezones = frozenset(allowed_timezones)
        self.default_timezone = default_timezone
        if default_timezone not in self.allowed_timezones or not 0 <= confidence_threshold <= 1:
            raise TaskError('invalid_router_config')
        self.confidence_threshold = confidence_threshold

    def plan(self, document, authority, now):
        if utc(authority.expires_at) <= utc(now):
            raise TaskError('expired_authority')
        try:
            self.validate_document(SCHEMA_NAME, document)
            request = json.loads(canonical(document))
        except Exception:
            raise TaskError('invalid_request') from None
        grants = dict(authority.capabilities)
        missing = set(request['missing_fields'])
        if request['confidence'] < self.confidence_threshold:
            missing.add('intent')
        seen, refs, approvals = set(), set(), []
        minimum_calls = minimum_messages = 0
        for step in request['steps']:
            name, operation = step['step_id'], self.registry.get(step['operation'])
            if name in seen or any(parent not in seen for parent in step.get('depends_on', ())):
                raise TaskError('invalid_dependencies')
            seen.add(name)
            if operation.name not in grants:
                raise TaskError('capability_not_authorized')
            refs.add(grants[operation.name])
            args = step['arguments']
            for field in operation.required_fields:
                if field not in args:
                    missing.add(f'{name}.{field}')
            if operation.requires_session:
                if 'session_ref' not in args or 'session_version' not in args:
                    missing.add(f'{name}.session')
                elif (args['session_ref'], args['session_version']) not in authority.sessions:
                    raise TaskError('session_not_authorized')
            if operation.requires_effect_approval:
                approvals.append(name)
                minimum_messages += len(args.get('recipient_refs', ()))
            if operation.name == 'schedule' and 'schedule' in args:
                schedule = args['schedule']
                schedule.setdefault('timezone', self.default_timezone)
                if schedule['timezone'] not in self.allowed_timezones:
                    raise TaskError('timezone_not_configured')
                # Schedule acceptance does not grant contact approval to future firings.
            minimum_calls += operation.default_calls
        budget_doc = request.get('budget', Budget(max(8, minimum_calls), minimum_messages).document())
        budget = Budget(budget_doc['calls'], budget_doc['message_limit'], Decimal(budget_doc['max_usd']))
        if not budget.fits(authority.ceiling) or budget.calls < minimum_calls or budget.messages < minimum_messages:
            raise TaskError('budget_exhausted')
        request['budget'] = budget.document()
        request['missing_fields'] = sorted(missing)
        snapshot = {'request': request, 'registry_revision': self.registry.revision,
                    'capability_refs': sorted(refs), 'effect_approval_required': approvals,
                    'actor_ref': authority.actor_ref, 'owner_ref': authority.owner_ref}
        return Plan(canonical(snapshot), content_hash(snapshot), budget, tuple(sorted(refs)),
                    tuple(sorted(missing)), tuple(approvals))

    def propose(self, *, authority, event_ref, document, now):
        plan = self.plan(document, authority, now)
        expiry = min(utc(authority.expires_at), utc(now) + timedelta(minutes=15))
        return self.repository.create(authority=authority, event_ref=event_ref, plan=plan, expires_at=expiry, now=now)

    def interpret(self, *, authority, event_ref, now, public_text='', context_refs=(), max_cost=Decimal('0.02')):
        if self.parser_enabled is not True or self.parser is None:
            raise TaskError('parser_disabled')
        if self.clock is None:
            raise TaskError('parser_clock_required')
        now = max(utc(now), utc(self.clock.now()))
        if not isinstance(public_text, str) or len(public_text.encode('utf-8')) > 4096 or (not public_text and not context_refs):
            raise TaskError('invalid_parser_input')
        if not isinstance(context_refs, tuple) or len(context_refs) > 16:
            raise TaskError('invalid_parser_input')
        if not isinstance(max_cost, Decimal) or not max_cost.is_finite() or max_cost <= 0:
            raise TaskError('invalid_budget')
        # Private content remains encrypted references; no decryption or prompt logs here.
        source = {'public_text': public_text, 'context_refs': list(context_refs)}
        try:
            input_text = canonical(source)
        except Exception:
            raise TaskError('invalid_parser_input') from None
        if len(input_text.encode('utf-8')) > 32 * 1024:
            raise TaskError('invalid_parser_input')
        frozen_refs = tuple(json.loads(input_text)['context_refs'])
        fingerprint = sha256(input_text.encode()).hexdigest()
        cached = self.repository.claim_parse(authority=authority, event_ref=event_ref, input_hash=fingerprint,
                                             context_refs=frozen_refs, max_cost=max_cost, now=now)
        if cached is None:
            request = StructuredRequest(SCHEMA_NAME, 1, self.model_ref, PROMPT_VERSION, fingerprint, 'es',
                                        'personal' if frozen_refs else 'public', input_text, 2048, max_cost)
            try:
                output = self.parser.generate(owner_ref=authority.owner_ref, request=request)
                now = max(utc(now), utc(self.clock.now()))
                if output.model_ref != self.model_ref or type(output.input_tokens) is not int or output.input_tokens < 0 or type(output.output_tokens) is not int or not 0 <= output.output_tokens <= request.max_tokens:
                    raise TaskError('parser_usage_unknown_or_exceeded')
                plan = self.plan(output.document, authority, now)
                document = json.loads(plan.snapshot)['request']
                if document['privacy_scope'] != request.privacy_scope:
                    raise TaskError('privacy_scope_mismatch')
                available = {canonical(ref) for ref in frozen_refs}
                returned = document.get('context_refs', []) + [step['arguments']['private_ref'] for step in document['steps'] if 'private_ref' in step['arguments']]
                if any(canonical(ref) not in available for ref in returned):
                    raise TaskError('private_reference_substitution')
                if output.cost is None or not isinstance(output.cost, Decimal) or not output.cost.is_finite() or not 0 <= output.cost <= max_cost:
                    raise TaskError('parser_usage_unknown_or_exceeded')
                self.repository.finish_parse(authority=authority, event_ref=event_ref, input_hash=fingerprint,
                                             document=document, now=now)
            except TaskError:
                raise
            except Exception:
                raise TaskError('parser_failed') from None
        else:
            document = cached
            now = max(utc(now), utc(self.clock.now()))
        parsed_event = 'parsed:' + sha256(event_ref.encode('utf-8')).hexdigest()
        return self.propose(authority=authority, event_ref=parsed_event, document=document, now=now)

    def callback(self, *, authority, callback_ref, event_ref, now):
        return self.repository.act(authority=authority, callback_ref=callback_ref, event_ref=event_ref, now=now)

    def correct(self, *, authority, task_ref, expected_version, document, now):
        plan = self.plan(document, authority, now)
        return self.repository.correct(authority=authority, task_ref=task_ref, expected_version=expected_version, plan=plan, now=now)
