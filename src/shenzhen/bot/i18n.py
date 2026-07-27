"""Bot copy in Russian and English."""

from __future__ import annotations

from ..notation import DEFAULT_LANG, LANGS

STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "start": (
            "Привет! Я решаю пасьянс Shenzhen Solitaire из SHENZHEN I/O.\n\n"
            "Пришли скриншот расклада — отвечу, есть ли ещё решение, и покажу "
            "ближайшие 5 ходов.\n\n"
            "Расклад можно прислать и текстом: /help расскажет формат.\n"
            "Язык ответов: /lang"
        ),
        "help": (
            "<b>Как пользоваться</b>\n"
            "Пришли скриншот доски или опиши расклад текстом.\n"
            "Скриншот лучше отправлять <b>файлом</b>: обычное фото Telegram "
            "пережимает, и мелкие значки карт плывут.\n\n"
            "<b>Обозначения карт</b>\n"
            "<code>G1</code>…<code>G9</code> — зелёная масть (бамбук)\n"
            "<code>R1</code>…<code>R9</code> — красная масть (монеты)\n"
            "<code>B1</code>…<code>B9</code> — чёрная масть (иероглифы)\n"
            "<code>DG</code> <code>DR</code> <code>DB</code> — драконы, "
            "<code>F</code> — цветок\n\n"
            "<b>Формат текста</b>\n"
            "<code>free: DG . .\n"
            "foundations: 3 0 1\n"
            "1: G9 R8 B7 DG DR\n"
            "2: …\n"
            "8: …</code>\n\n"
            "В колонке карты идут сверху вниз, как на экране: последняя — та, "
            "которую можно взять. Пустая ячейка — <code>.</code>, ячейка, "
            "закрытая схлопнутыми драконами — <code>XG</code>/<code>XR</code>/"
            "<code>XB</code>. Строки <code>free</code> и <code>foundations</code> "
            "можно опустить, если ячейки пусты и фундамент чист — тогда достаточно "
            "восьми строк с картами.\n\n"
            "<b>Правила</b>\n"
            "Считаю как в игре: цветок уходит сам, карты автоматически "
            "собираются в фундамент, когда это уже ничего не стоит. В список "
            "ходов попадает только то, что делаешь ты сам."
        ),
        "lang_prompt": "Выбери язык ответов:",
        "lang_set": "Готово, отвечаю по-русски.",
        "send_something": (
            "Пришли скриншот доски или расклад текстом. Формат — /help"
        ),
        "reading": "Читаю скриншот…",
        "solving": "Считаю…",
        "board_read": "Вот что я вижу:",
        "board_confirm": "Всё верно?",
        "uncertain": "Не уверен в этих картах: {cards}",
        "warnings": "Замечания: {items}",
        "btn_correct": "✅ Верно, решай",
        "btn_fix": "✏️ Исправить",
        "btn_more": "Ещё 5 ходов",
        "fix_hint": (
            "Пришли расклад текстом — я возьму его вместо распознанного.\n"
            "Вот распознанное, поправь и отправь обратно:"
        ),
        "solved": "✅ Решение есть — {total} ходов до победы.",
        "solved_head": "Ближайшие ходы:",
        "unsolvable": (
            "❌ Решения нет. Я перебрал все достижимые позиции — из этого "
            "расклада победить уже нельзя."
        ),
        "unknown": (
            "🤔 Не смог найти решение за отведённое время ({nodes} позиций, "
            "{elapsed:.0f} с). Это не доказательство, что решения нет, — "
            "расклад просто оказался тяжёлым."
        ),
        "already_won": "Тут уже всё собрано — партия выиграна.",
        "no_more_moves": "Ходы закончились: это все {total} ходов до победы.",
        "bad_board": "Так не бывает: {reason}",
        "bad_image": (
            "Не получилось разобрать картинку: {reason}\n\n"
            "Пришли скриншот целиком, без обрезки и без масштабирования, "
            "либо опиши расклад текстом — /help"
        ),
        "image_rescaled": (
            "Картинка уменьшена — карты всего {card_w} px в ширину, "
            "и значки номиналов размылись настолько, что «3» уже не отличить "
            "от «8».\n\n"
            "Это Telegram сжимает то, что отправлено <b>фотографией</b>. "
            "Пришли тот же скриншот <b>файлом</b>: скрепка → Файл (на телефоне "
            "может называться «Документ»), и выбери его из галереи. "
            "Тогда он дойдёт как есть и прочитается.\n\n"
            "Либо опиши расклад текстом — /help"
        ),
        "no_templates": (
            "Распознавание скриншотов на этом сервере ещё не настроено "
            "(нет банка шаблонов карт). Пришли расклад текстом — /help"
        ),
        "no_board": "Сначала пришли расклад.",
        "busy": "Уже считаю предыдущий расклад, подожди немного.",
        "error": "Что-то пошло не так: {reason}",
    },
    "en": {
        "start": (
            "Hi! I solve the Shenzhen Solitaire minigame from SHENZHEN I/O.\n\n"
            "Send me a screenshot of the board and I will tell you whether it "
            "is still winnable and show the next 5 moves.\n\n"
            "You can also type the position out: see /help.\n"
            "Language: /lang"
        ),
        "help": (
            "<b>How to use</b>\n"
            "Send a screenshot of the board, or type the position out.\n"
            "Send the screenshot <b>as a file</b> if you can: Telegram "
            "recompresses ordinary photos and the small card glyphs smear.\n\n"
            "<b>Card notation</b>\n"
            "<code>G1</code>…<code>G9</code> — green suit (bamboo)\n"
            "<code>R1</code>…<code>R9</code> — red suit (coins)\n"
            "<code>B1</code>…<code>B9</code> — black suit (characters)\n"
            "<code>DG</code> <code>DR</code> <code>DB</code> — dragons, "
            "<code>F</code> — flower\n\n"
            "<b>Text format</b>\n"
            "<code>free: DG . .\n"
            "foundations: 3 0 1\n"
            "1: G9 R8 B7 DG DR\n"
            "2: …\n"
            "8: …</code>\n\n"
            "Within a column, cards run top to bottom as they do on screen: the "
            "last one is the card you can pick up. An empty cell is "
            "<code>.</code>; a cell locked by collapsed dragons is "
            "<code>XG</code>/<code>XR</code>/<code>XB</code>. Drop the "
            "<code>free</code> and <code>foundations</code> lines if the cells "
            "are empty and nothing has been collected — then eight lines of "
            "cards are enough.\n\n"
            "<b>Rules</b>\n"
            "Same as the game: the flower leaves on its own and cards are "
            "collected automatically once it costs nothing to do so. Only your "
            "own moves show up in the list."
        ),
        "lang_prompt": "Pick a language:",
        "lang_set": "Done, answering in English.",
        "send_something": "Send a screenshot or type the position out. Format: /help",
        "reading": "Reading the screenshot…",
        "solving": "Thinking…",
        "board_read": "Here is what I see:",
        "board_confirm": "Is that right?",
        "uncertain": "Not sure about these: {cards}",
        "warnings": "Notes: {items}",
        "btn_correct": "✅ Right, solve it",
        "btn_fix": "✏️ Fix it",
        "btn_more": "Next 5 moves",
        "fix_hint": (
            "Type the position out and I will use that instead.\n"
            "Here is what I read — edit it and send it back:"
        ),
        "solved": "✅ Winnable — {total} moves to go.",
        "solved_head": "Next moves:",
        "unsolvable": (
            "❌ Not winnable. I searched every position reachable from here and "
            "none of them wins."
        ),
        "unknown": (
            "🤔 No solution found within the budget ({nodes} positions, "
            "{elapsed:.0f}s). That is not a proof that none exists — this deal "
            "is just a hard one."
        ),
        "already_won": "Everything is collected already — this game is won.",
        "no_more_moves": "That was all of them: {total} moves to the win.",
        "bad_board": "That position cannot occur: {reason}",
        "bad_image": (
            "I could not read the picture: {reason}\n\n"
            "Send the full screenshot, uncropped and unscaled, or type the "
            "position out — /help"
        ),
        "image_rescaled": (
            "The picture has been scaled down — the cards are only {card_w}px "
            "wide, and the rank glyphs are blurred past the point where a 3 "
            "can be told from an 8.\n\n"
            "That is Telegram compressing anything sent as a <b>photo</b>. "
            "Send the same screenshot <b>as a file</b> instead: the paperclip "
            "→ File (your phone may call it Document), then pick it from the "
            "gallery. It arrives untouched and reads fine.\n\n"
            "Or type the position out — /help"
        ),
        "no_templates": (
            "Screenshot reading is not set up on this server (no card template "
            "bank installed). Type the position out instead — /help"
        ),
        "no_board": "Send me a position first.",
        "busy": "Still working on the previous position, hold on.",
        "error": "Something went wrong: {reason}",
    },
}


def normalise_lang(code: str | None) -> str:
    """Map a Telegram ``language_code`` onto a language we speak."""
    if not code:
        return DEFAULT_LANG
    prefix = code.split("-")[0].lower()
    if prefix in LANGS:
        return prefix
    # Everyone else gets English rather than Russian.
    return "en"


def t(lang: str, key: str, **kwargs: object) -> str:
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    text = table.get(key) or STRINGS[DEFAULT_LANG][key]
    return text.format(**kwargs) if kwargs else text
