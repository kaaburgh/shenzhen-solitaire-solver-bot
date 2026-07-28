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
            "Обычного фото достаточно — сжатие Telegram я переживу. Если "
            "какая-то карта всё же не прочитается, спрошу про неё; а скриншот "
            "<b>файлом</b> доходит без сжатия и вопросов обычно не вызывает.\n\n"
            "<b>Обозначения карт</b>\n"
            "<code>G1</code>…<code>G9</code> — зелёная масть (бамбук)\n"
            "<code>R1</code>…<code>R9</code> — красная масть (монеты)\n"
            "<code>B1</code>…<code>B9</code> — чёрная масть (иероглифы)\n"
            "<code>DG</code> <code>DR</code> <code>DB</code> — драконы, "
            "<code>F</code> — цветок\n\n"
            "Когда я показываю карты сам, масть — это цвет, а не буква: "
            "🟢9 · 🔴9 · ⚫9, драконы — 🟩 🟥 ⬜, цветок — 🌸.\n\n"
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
        "check_head": (
            "Расклад прочитан. Сверять его целиком не надо — хватит этих карт: "
            "если ошибка есть, она почти наверняка в одной из них."
        ),
        "check_line": "• {slot} — {card}",
        "check_confirm": "Сходится?",
        "legend": (
            "🟢 бамбук · 🔴 монеты · ⚫ иероглифы · "
            "🟩 🟥 ⬜ драконы · 🌸 цветок · 🔒 схлопнутые драконы"
        ),
        "uncertain": "Не уверен в этих картах:",
        "warnings": "Замечания: {items}",
        "btn_correct": "✅ Верно, решай",
        "btn_fix": "✏️ Исправить",
        "btn_board": "🔍 Весь расклад",
        "btn_more": "Ещё 5 ходов",
        "ask_intro": (
            "❓ Несколько карт прочитались нечётко. Большую часть я досчитал "
            "по колоде — в ней каждая карта ровно одна, так что почти всё "
            "определяется однозначно. Осталось уточнить остальное:"
        ),
        "ask_slot": "<b>{slot}</b> — что там?",
        "ask_left": "После этого останется вопросов: {n}",
        "ask_deduced": (
            "Ещё {n} карт(ы) прочитались нечётко, но по колоде подходил ровно "
            "один вариант — подставил их сам."
        ),
        "ask_other": "Всё, что подходит сюда по колоде:",
        "ask_no_options": (
            "По колоде сюда не подходит ничего — видимо, ошибка в другой "
            "карте. Проще прислать расклад текстом, формат — /help"
        ),
        "ask_contradiction": (
            "С этим ответом расклад не сходится по колоде — значит, "
            "ошибка где-то ещё. Выбери другой вариант или введи расклад текстом."
        ),
        "ask_expired": "Этот вопрос уже неактуален — пришли скриншот заново.",
        "board_narrow": (
            "Картинка мелковата — карты {card_w} px в ширину. Расклад сошёлся "
            "по колоде, так что он почти наверняка верный, но глазом проверь."
        ),
        "btn_other": "Другое…",
        "btn_type": "✏️ Введу текстом",
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
        "bad_reading": (
            "Скриншот я разобрал, но расклад не сходится по колоде: {reason}\n\n"
            "Значит, часть карт я прочитал неверно. Вот всё, что я увидел — "
            "поправь неверные карты и пришли этот текст обратно:"
        ),
        "bad_reading_narrow": (
            "Картинка мелковата — карты {card_w} px в ширину, значки номиналов "
            "размыты. Если проще, пришли тот же скриншот <b>файлом</b> "
            "(скрепка → Файл, на телефоне может называться «Документ») — "
            "тогда он дойдёт без сжатия."
        ),
        "not_an_image": (
            "Это не похоже на картинку. Пришли скриншот доски — фотографией "
            "или файлом (png, jpg), — либо опиши расклад текстом: /help"
        ),
        "file_too_big": (
            "Файл слишком большой: Telegram отдаёт ботам не больше {limit} МБ. "
            "Пришли скриншот фотографией или опиши расклад текстом — /help"
        ),
        "download_failed": (
            "Не получилось забрать файл из Telegram: {reason}\n\n"
            "Попробуй прислать ещё раз или опиши расклад текстом — /help"
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
            "An ordinary photo is fine — I can live with Telegram's "
            "compression. If a card still will not come out I will ask you "
            "about it; sending the screenshot <b>as a file</b> avoids the "
            "compression altogether and usually avoids the questions.\n\n"
            "<b>Card notation</b>\n"
            "<code>G1</code>…<code>G9</code> — green suit (bamboo)\n"
            "<code>R1</code>…<code>R9</code> — red suit (coins)\n"
            "<code>B1</code>…<code>B9</code> — black suit (characters)\n"
            "<code>DG</code> <code>DR</code> <code>DB</code> — dragons, "
            "<code>F</code> — flower\n\n"
            "When I show you cards, the suit is a colour rather than a letter: "
            "🟢9 · 🔴9 · ⚫9, dragons are 🟩 🟥 ⬜ and the flower is 🌸.\n\n"
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
        "check_head": (
            "I have read the board. No need to check all of it — these cards "
            "are enough: if anything is wrong, it is almost certainly one of "
            "them."
        ),
        "check_line": "• {slot} — {card}",
        "check_confirm": "Do those match?",
        "legend": (
            "🟢 bamboo · 🔴 coins · ⚫ characters · "
            "🟩 🟥 ⬜ dragons · 🌸 flower · 🔒 collapsed dragons"
        ),
        "uncertain": "Not sure about these:",
        "warnings": "Notes: {items}",
        "btn_correct": "✅ Right, solve it",
        "btn_fix": "✏️ Fix it",
        "btn_board": "🔍 The whole board",
        "btn_more": "Next 5 moves",
        "ask_intro": (
            "❓ A few cards did not come out clearly. Most of them the deck "
            "settled for me — every card in it exists exactly once, so nearly "
            "everything follows by elimination. What is left to ask:"
        ),
        "ask_slot": "<b>{slot}</b> — what is it?",
        "ask_left": "Questions left after this one: {n}",
        "ask_deduced": (
            "Another {n} card(s) read unclearly, but only one reading fitted "
            "the deck, so I filled those in myself."
        ),
        "ask_other": "Everything the deck still allows here:",
        "ask_no_options": (
            "Nothing fits here at all, so the mistake is in some other card. "
            "Typing the position out will be quicker — format in /help"
        ),
        "ask_contradiction": (
            "That answer leaves the deck not adding up, so something else is "
            "misread. Pick another one, or type the position out."
        ),
        "ask_expired": "That question is out of date — send the screenshot again.",
        "board_narrow": (
            "The picture came in small — cards {card_w}px wide. The position "
            "does add up against the deck, so it is very probably right, but "
            "give it a look."
        ),
        "btn_other": "Other…",
        "btn_type": "✏️ I will type it",
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
        "bad_reading": (
            "I read the screenshot, but the position does not add up against "
            "the deck: {reason}\n\n"
            "So some of the cards came out wrong. Here is everything I saw — "
            "fix the wrong ones and send this text back:"
        ),
        "bad_reading_narrow": (
            "The picture came in small — cards {card_w}px wide, and the rank "
            "glyphs are blurred. If it is easier, send the same screenshot "
            "<b>as a file</b> (paperclip → File, your phone may call it "
            "Document) and it arrives uncompressed."
        ),
        "not_an_image": (
            "That does not look like a picture. Send a screenshot of the board "
            "— as a photo or as a file (png, jpg) — or type the position out: "
            "/help"
        ),
        "file_too_big": (
            "That file is too big: Telegram only hands bots files up to "
            "{limit} MB. Send the screenshot as a photo instead, or type the "
            "position out — /help"
        ),
        "download_failed": (
            "I could not fetch the file from Telegram: {reason}\n\n"
            "Try sending it again, or type the position out — /help"
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
