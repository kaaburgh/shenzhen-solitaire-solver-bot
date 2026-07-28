"""Bot entry point.

Configuration is entirely through the environment, so the container needs no
files mounted beyond the template bank:

===========================  ==================================================
``TELEGRAM_BOT_TOKEN``       required
``SHENZHEN_TEMPLATES``       card template bank (default ``templates/default``)
``SHENZHEN_MAX_NODES``       search budget in positions (default 400000)
``SHENZHEN_TIME_LIMIT``      search budget in seconds (default 20)
``SHENZHEN_WORKERS``         solver processes (default 2)
``SHENZHEN_COVERAGE``        measure the running bot into this directory
``LOG_LEVEL``                default ``INFO``
===========================  ==================================================

See ``docs/coverage.md`` for what ``SHENZHEN_COVERAGE`` is for.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor

from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ..solver import DEFAULT_MAX_NODES, DEFAULT_TIME_LIMIT
from ..vision import load_bank
from . import handlers, runtime_coverage
from .handlers import BotConfig
from .storage import Sessions

log = logging.getLogger("shenzhen.bot")

#: /help asks for the screenshot as a file, so every file has to reach the
#: handler.  ``filters.Document.IMAGE`` is not enough: it goes by mime type,
#: and a phone sending a screenshot as a file frequently labels it
#: ``application/octet-stream`` or ships no type at all, so those updates
#: matched no handler and the bot answered nothing.  Sorting out what is
#: actually a picture is `handlers.looks_like_image`'s job.
PICTURES = filters.PHOTO | filters.Document.ALL


def build_application(token: str) -> Application:
    bank_path = os.environ.get("SHENZHEN_TEMPLATES", "templates/default")
    bank = load_bank(bank_path)
    if bank is None:
        log.warning(
            "no card templates at %s -- screenshots will be refused and only "
            "typed positions will work.  See docs/calibration.md.",
            bank_path,
        )
    else:
        missing = bank.missing
        log.info("loaded %d card templates from %s", len(bank), bank_path)
        if missing:
            from ..cards import card_code

            log.warning(
                "template bank is incomplete, missing: %s",
                ", ".join(card_code(c) for c in missing),
            )

    config = BotConfig(
        max_nodes=int(os.environ.get("SHENZHEN_MAX_NODES", DEFAULT_MAX_NODES)),
        time_limit=float(os.environ.get("SHENZHEN_TIME_LIMIT", DEFAULT_TIME_LIMIT)),
        bank=bank,
    )
    workers = int(os.environ.get("SHENZHEN_WORKERS", "2"))

    builder = ApplicationBuilder().token(token).post_init(_startup).post_shutdown(_shutdown)
    try:
        # Keeps the bot inside Telegram's send limits under load.  It needs the
        # [rate-limiter] extra, which requirements.txt pins -- but a bare
        # install of python-telegram-bot should still start.
        builder = builder.rate_limiter(AIORateLimiter())
    except RuntimeError:
        log.warning(
            "AIORateLimiter unavailable; install python-telegram-bot[rate-limiter] "
            "to have outgoing messages throttled"
        )
    application = builder.build()
    application.bot_data["config"] = config
    application.bot_data["sessions"] = Sessions()
    # The search is CPU-bound, so it runs in separate processes rather than
    # blocking the event loop for everyone else in the meantime.
    application.bot_data["executor"] = ProcessPoolExecutor(max_workers=workers)

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("lang", handlers.lang_command))
    application.add_handler(CallbackQueryHandler(handlers.on_callback))
    application.add_handler(MessageHandler(PICTURES, handlers.handle_image))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text)
    )
    application.add_error_handler(handlers.on_error)
    return application


async def _startup(application: Application) -> None:
    application.bot_data["coverage_task"] = runtime_coverage.start_periodic_save()


async def _shutdown(application: Application) -> None:
    coverage_task = application.bot_data.get("coverage_task")
    # Ours goes to disk first: waiting on the workers below can outlast
    # Docker's patience, and a process that gets killed mid-wait should still
    # have written what it collected.
    await runtime_coverage.stop_periodic_save(coverage_task)

    executor: ProcessPoolExecutor | None = application.bot_data.get("executor")
    if executor is not None:
        # Under coverage the workers have data of their own to write, and they
        # only get to write it if they are allowed to finish shutting down.
        executor.shutdown(wait=coverage_task is not None, cancel_futures=True)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # May replace this process with one running under coverage.  Logging is up
    # by now so the swap says so in the log, and nothing else has happened yet
    # that would be lost by it.
    runtime_coverage.reexec_if_requested()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    application = build_application(token)
    log.info("starting polling")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
