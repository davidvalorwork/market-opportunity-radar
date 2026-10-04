"""One technical CLI: explicit offline fixture OR real configured bot polling."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from radar.adapters.local.general_bindings import from_config, private_operator_path
from radar.adapters.local.general_runtime import GeneralError
from radar.adapters.local.polling import PollingRunner
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.adapters.telegram.bot_api import BotApi


def parser():
    value = argparse.ArgumentParser(description='General durable local runtime. Telegram is the product UI.')
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument('--fixture',action='store_true',help='Synthetic offline full flow. No real accounts or encryption claim.')
    mode.add_argument('--config',help='Trusted private JSON .local/config; real age vault required, no fake fallback.')
    value.add_argument('--state-db')
    value.add_argument('--allowlist')
    value.add_argument('--bot-ref',default='bot:general')
    value.add_argument('--authorize-bot-io',action='store_true')
    value.add_argument('--take-over-bot',action='store_true',help='Explicit deleteWebhook; pending updates are retained.')
    value.add_argument('--max-polls',type=int,default=100)
    return value


def main(argv=None,*,environ=None,api_factory=BotApi):
    args = parser().parse_args(argv)
    wiring,runner = None,None
    try:
        if args.fixture:
            if args.authorize_bot_io or args.take_over_bot or args.state_db or args.allowlist:
                raise GeneralError('fixture_real_flags_rejected')
            from radar.adapters.local.general_fixture import run
            with tempfile.TemporaryDirectory(prefix='mor-general-fixture-') as root:
                result = run(Path(root)/'control.sqlite')
            print(json.dumps(result,sort_keys=True))
            return 0
        if not args.authorize_bot_io:
            raise GeneralError('bot_io_not_authorized')
        if not args.state_db or not args.allowlist or not 1<=args.max_polls<=10000:
            raise GeneralError('general_cli_configuration')
        config_path = private_operator_path(args.config)
        state_db = private_operator_path(args.state_db,new_file=True)
        allowlist_path = private_operator_path(args.allowlist)
        env = os.environ if environ is None else environ
        if not env.get('RADAR_TELEGRAM_TOKEN') or not env.get('RADAR_TELEGRAM_SECRET'):
            raise GeneralError('secret_configuration_required')
        api = api_factory(env['RADAR_TELEGRAM_TOKEN'])
        wiring,runtime = from_config(state_db=state_db,phone_allowlist=PhoneAllowlist.from_file(allowlist_path),
            secret=env['RADAR_TELEGRAM_SECRET'],config_path=config_path,api=api)
        runner = PollingRunner(api,wiring,bot_ref=args.bot_ref)
        runner.prepare(take_over_bot=args.take_over_bot)
        runner.run(max_polls=args.max_polls)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('general_local_blocked')  # Never provider exception/token/path/input.
        return 2
    finally:
        if runner is not None:
            runner.close()
        if wiring is not None:
            wiring.store.close()


if __name__=='__main__':
    raise SystemExit(main())
