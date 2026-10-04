"""Technical local polling entrypoint; no default real or synthetic wiring."""
import argparse
import importlib
import os
from pathlib import Path
import signal
import sys
from threading import Event

from radar.adapters.local.polling import PollingError, PollingRunner, PollingWiring
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.adapters.telegram.bot_api import BotApi


def load_factory(spec):
    module, separator, name = spec.partition(':')
    if not separator or not module or not name or ':' in name:
        raise PollingError('invalid_factory')
    return getattr(importlib.import_module(module),name)


def parser():
    value = argparse.ArgumentParser(description='Local Telegram polling; encrypted trusted wiring required.')
    value.add_argument('--factory',required=True,help='Trusted operator configuration: module:function (no default).')
    value.add_argument('--state-db',required=True,help='Private SQLite path under .local/.')
    value.add_argument('--allowlist',required=True,help='Private PhoneAllowlist JSON under .local/.')
    value.add_argument('--bot-ref',required=True,help='Stable opaque bot account reference; never the token.')
    value.add_argument('--token-env',default='RADAR_TELEGRAM_TOKEN')
    value.add_argument('--secret-env',default='RADAR_TELEGRAM_SECRET')
    value.add_argument('--authorize-bot-io',action='store_true',help='Explicit authorization for bot API I/O, not third-party effects.')
    value.add_argument('--take-over-bot',action='store_true',help='Permit deleteWebhook; never drops pending updates.')
    value.add_argument('--poll-timeout',type=int,default=20,help='Long polling seconds, 1-30.')
    value.add_argument('--batch-size',type=int,default=20,help='Updates per poll, 1-100.')
    value.add_argument('--max-polls',type=int,default=100,help='Finite poll budget, 1-10000.')
    return value


def _private_path(value):
    path = Path(value).resolve()
    if '.local' not in path.parts:
        raise PollingError('private_path_required')
    return path


def main(argv=None, *, environ=None, factory_loader=load_factory, api_factory=BotApi):
    args = parser().parse_args(argv)
    wiring, runner, old_handlers = None, None, {}
    try:
        if not args.authorize_bot_io:
            raise PollingError('bot_io_not_authorized')
        if not 1<=args.max_polls<=10000:
            raise PollingError('invalid_limits')
        env = os.environ if environ is None else environ
        token,secret = env.get(args.token_env,''),env.get(args.secret_env,'')
        if not token or not secret:
            raise PollingError('secret_configuration_required')
        state_db,allowlist_path = _private_path(args.state_db),_private_path(args.allowlist)
        phones = PhoneAllowlist.from_file(allowlist_path)
        factory = factory_loader(args.factory)
        wiring = factory(state_db=state_db,phone_allowlist=phones,secret=secret)
        if not isinstance(wiring,PollingWiring) or wiring.secret != secret or wiring.store.path.resolve()!=state_db:
            raise PollingError('factory_binding')
        api = api_factory(token,timeout=args.poll_timeout+5)
        runner = PollingRunner(api,wiring,bot_ref=args.bot_ref,timeout=args.poll_timeout,batch_size=args.batch_size,
                               diagnostic=lambda code: print(code,file=sys.stderr))
        runner.prepare(take_over_bot=args.take_over_bot)
        stop = Event()
        for signum in (signal.SIGINT,signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum,lambda signum,frame: stop.set())
        runner.run(stop=stop,max_polls=args.max_polls)
        return 0
    except PollingError as error:
        print(error.code,file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Factory/transport/storage exceptions can embed paths, tokens or PII.
        print('local_polling_failed',file=sys.stderr)
        return 2
    finally:
        for signum,handler in old_handlers.items():
            signal.signal(signum,handler)
        if isinstance(wiring,PollingWiring):
            try:
                if runner is not None:
                    runner.close()
                wiring.store.close()
            except Exception:
                print('local_store_close_failed',file=sys.stderr)
                return 2


if __name__=='__main__':
    raise SystemExit(main())
