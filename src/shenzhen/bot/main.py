"""Bot entry point.

Configuration is entirely through the environment, so the container needs no
files mounted beyond the template bank:

===========================  ==================================================
``TELEGRAM_BOT_TOKEN``       required
``SHENZHEN_TEMPLATES``       card template bank (default ``templates/default``)
``SHENZHEN_MAX_NODES``       search budget in positions (default 400000)
``SHENZHEN_TIME_LIMIT``      search budget in seconds (default 20)
``SHENZHEN_WORKERS``         solver processes (default 2)
``LOG_LEVEL``                default ``INFO``
===========================  ==================================================
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
from . import handlers
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

    builder = ApplicationBuilder().token(token).post_shutdown(_shutdown)
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


async def _shutdown(application: Application) -> None:
    executor: ProcessPoolExecutor | None = application.bot_data.get("executor")
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    application = build_application(token)
    log.info("starting polling")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
