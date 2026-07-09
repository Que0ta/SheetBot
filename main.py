import telebot
import gspread, os
import json
import re
from collections import defaultdict
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from telebot import *
from flask import Flask, request
import traceback
import threading

sheet_lock = threading.Lock()  # global lock for sheet updates

def checkHour(hour):
    if hour == 21:
        return 0
    elif hour == 22:
        return 1
    elif hour == 23:
        return 2
    elif hour == 0:
        return 3
    else:
        return hour

def safe_handler(func):
    def wrapper(message_or_call):
        try:
            return func(message_or_call)
        except Exception as e:
            chat_id = None
            # якщо це message handler
            if hasattr(message_or_call, "chat"):
                chat_id = message_or_call.chat.id
            # якщо це callback handler
            elif hasattr(message_or_call, "message"):
                chat_id = message_or_call.message.chat.id

            if chat_id:
                bot.send_message(
                    chat_id,
                    f"❌ Помилка: {e}\n\n<pre>{traceback.format_exc()}</pre>",
                    parse_mode="HTML"
                )
            else:
                print("❌ Unhandled error (no chat_id):", e)
                print(traceback.format_exc())
    return wrapper

# ==== LOAD ENV ====
load_dotenv()
TELEGRAM_TOKEN = os.getenv('Tg_K')
SECRETO = os.getenv('boomba')

# ==== TELEGRAM BOT ====
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True, num_threads=4)

table1 = os.getenv('table1')
table2 = os.getenv('table2')

table1_id = os.getenv('table1_KEY')
table2_id = os.getenv('table2_KEY')

app = Flask(__name__)

@app.route('/' + TELEGRAM_TOKEN, methods=['POST'])
def get_message():
    bot.process_new_updates([telebot.types.Update.de_json(request.stream.read().decode("utf-8"))])
    return "!", 200

@app.route('/')
def webhook():
    bot.remove_webhook()
    bot.set_webhook(url='https://sheetbot-mxhm.onrender.com/' + TELEGRAM_TOKEN)  # Replace with your Render app name!
    return "Webhook set!", 200


# ==== GOOGLE SHEETS AUTH ====
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(SECRETO, scopes=scope)
client = gspread.authorize(creds)

# ==== TWO TABLES (DIFFERENT FILES + FORMATS) ====
TABLES = {
    "Проведені відпрацювання": {
        "sheet_id": table1_id,  # ID першого Google Sheets
        "gid": table1,
        "title": "Відпрацювання 2026",   # ← fallback назва
        "type": "table1"
    },
    "Основна таблиця": {
        "sheet_id": table2_id,  # ID другого Google Sheets
        "gid": table2,
        "title": "Годинні відпрацювання",   # ← fallback назва
        "type": "table2"
    }
}

# Зберігаємо вибір користувача (user_id -> table_name)
user_table_choice = {}

def get_user_sheet(user_id):
    # якщо користувач ще не вибрав — за замовчуванням Проведені відпрацювання
    table_name = user_table_choice.get(user_id, "Проведені відпрацювання")
    table_info = TABLES[table_name]

    # відкриваємо потрібний файл
    spreadsheet = client.open_by_key(table_info["sheet_id"])

    # ==== 1. Шукаємо по gid ====
    sheet = None
    for ws in spreadsheet.worksheets():
        if ws.id == table_info["gid"]:
            sheet = ws
            break

    # ==== 2. Якщо не знайшли — шукаємо по статичній назві (title) ====
    if sheet is None and "title" in table_info:
        try:
            sheet = spreadsheet.worksheet(table_info["title"])
        except Exception:
            pass

    # ==== 3. Якщо не знайдено ні по gid, ні по title ====
    if sheet is None:
        raise Exception(
            f"Аркуш gid={table_info['gid']} "
            f"(title={table_info.get('title')}) у {table_name} не знайдено!"
        )

    return sheet, table_info["type"], table_name


# ==== TABLE 2 HELPERS: MONTH NAME + TEACHER LOOKUP ====

UKRAINIAN_MONTHS = {
    1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
    5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
    9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень"
}

def get_month_name(date_str):
    """
    Дістає номер місяця з рядка дати (наприклад '9.07.2026', '09/07', '9.07')
    та повертає назву місяця українською.
    """
    match = re.search(r'\d{1,2}[./]\s*(\d{1,2})', date_str)
    if match:
        month_num = int(match.group(1))
        return UKRAINIAN_MONTHS.get(month_num, "")
    return ""

# Словник telegram username -> ім'я викладача, зберігається в .env:
# TEACHERS_MAP={"ivan_teacher":"Іван Петренко","olena_t":"Олена Ковальчук"}
TEACHERS_MAP = json.loads(os.getenv('TEACHERS_MAP', '{}'))

def get_teacher_name(message):
    username = message.from_user.username
    if username and username in TEACHERS_MAP:
        return TEACHERS_MAP[username]
    # fallback, якщо викладача немає в мапі
    return username or f"ID:{message.from_user.id}"


# ==== TABLE 2: НОТИФІКАЦІЇ У ГРУПИ ЗА ЛОКАЦІЄЮ ====

# Словник Локація -> chat_id групи, зберігається в .env:
# LOCATION_GROUPS_MAP={"Кабінет 1": -1001234567890, "Кабінет 2": -1009876543210, "Онлайн": -1005555555555}
LOCATION_GROUPS_MAP = json.loads(os.getenv('LOCATION_GROUPS_MAP', '{}'))
# нормалізуємо ключі (без зайвих пробілів, без урахування регістру), щоб не залежати
# від того, як саме користувач написав локацію в повідомленні
LOCATION_GROUPS_MAP_NORMALIZED = {k.strip().lower(): v for k, v in LOCATION_GROUPS_MAP.items()}


def get_group_chat_id(location):
    return LOCATION_GROUPS_MAP_NORMALIZED.get(location.strip().lower())


def notify_location_group(location, teacher, entries):
    """
    entries: список dict {student, date, time, comment, reason}
    Відправляє одне зведене повідомлення у групу, що відповідає локації.
    Якщо для локації немає групи в мапі — нічого не відправляє.
    """
    chat_id = get_group_chat_id(location)
    if not chat_id:
        return

    lines_text = []
    for e in entries:
        line = f"👤 {e['student']} — {e['date']} {e['time']}"
        if e['comment']:
            line += f"\n   💬 {e['comment']}"
        if e['reason']:
            line += f"\n   📝 {e['reason']}"
        lines_text.append(line)

    text = (
        f"📍 {location}\n"
        f"👨‍🏫 Викладач: {teacher}\n\n" +
        "\n\n".join(lines_text)
    )

    try:
        bot.send_message(chat_id, text)
    except Exception as e:
        print(f"⚠ Не вдалось відправити повідомлення в групу '{location}' ({chat_id}): {e}")


@bot.message_handler(commands=['start'], chat_types=['private'])
@safe_handler
def start_message(message):
    import datetime
    date = datetime.datetime.now()
    ukrH = checkHour(int(date.strftime("%H"))+3)
    ukrMin = date.strftime("%M")
    bot.send_message(message.chat.id,
                     "Привіт! 👋\n"
                     "Використай /table щоб обрати таблицю. Приклади повідомлень внизу =>\n"
                     "📌 Проведені відпрацювання → \n"
                     f"Викладач, Прізвище та ім'я учня, {date.day}.{date.month}.{date.year}, {ukrH}:00, check,, індивідуальне заняття, пропуск >= 2 занять\n"
                     "\n"
                     "📌❗️ Тестова таблиця → \n"
                     "формат: Учень, Дата, Час, Локація, Коментар (якщо кілька учнів), Причина годинного відпрацювання\n"
                     "❗️ Викладач та Місяць проставляються автоматично, Відбулось завжди TRUE."
                     "❗️ Виключно для тестування роботи бота!")


# ==== TABLE SELECTION ====
@bot.message_handler(commands=['table'], chat_types=['private'])
@safe_handler
def choose_table(message):
    keyboard = types.InlineKeyboardMarkup()
    for name in TABLES.keys():
        keyboard.add(types.InlineKeyboardButton(text=name, callback_data=f"choose_{name}"))
    bot.send_message(message.chat.id, "📊 Оберіть таблицю для запису:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith("choose_"))
@safe_handler
def callback_choose_table(call):
    table_name = call.data.replace("choose_", "")
    if table_name not in TABLES:
        bot.answer_callback_query(call.id, "❌ Таблиця не знайдена")
        return

    user_table_choice[call.from_user.id] = table_name
    bot.answer_callback_query(call.id, f"Обрана {table_name}")
    bot.send_message(call.message.chat.id, f"✅ Дані тепер будуть додаватись у: *{table_name}*", parse_mode="Markdown")


# ==== HANDLERS FOR TABLE 1 & TABLE 2 ====

def handle_table1(sheet, lines):
    responses = []

    for line_number, line in enumerate(lines, start=1):
        try:
            data = [x.strip() for x in line.split(",")]
            if len(data) < 4:
                responses.append(f"Рядок {line_number}: ⚠ Формат: Викладач, Учень, Дата, Час ...")
                continue

            teacher, student, date, time = data[:4]
            checkbox_value = None
            reason_not_happened = ""
            comment_students = ""
            reason_hourly = ""

            if len(data) >= 5:
                if data[4].lower() in ("check", "uncheck"):
                    checkbox_value = True if data[4].lower() == "check" else False
                    if len(data) > 5: reason_not_happened = data[5]
                    if len(data) > 6: comment_students = data[6]
                    if len(data) > 7: reason_hourly = data[7]
                else:
                    reason_not_happened = data[4]
                    if len(data) > 5: comment_students = data[5]
                    if len(data) > 6: reason_hourly = data[6]

            row_data = [teacher, student, date, time, checkbox_value, reason_not_happened, comment_students, reason_hourly]

            # --- Entire read + write inside the lock ---
            with sheet_lock:
                all_values = sheet.get_all_values()
                empty_rows = [i + 1 for i, row in enumerate(all_values)
                              if len(row) < 4 or all(v.strip() == "" for v in row[:4])]

                if empty_rows:
                    target_row = empty_rows[0]
                    sheet.update(f"A{target_row}:H{target_row}", [row_data])
                else:
                    sheet.append_row(row_data)
                    target_row = len(sheet.get_all_values())  # recalc bottom row inside lock

            responses.append(f"Рядок {line_number}: ✅ Додано у рядок {target_row}")

        except Exception as e:
            responses.append(f"Рядок {line_number}: ❌ Помилка: {e}")

    return responses


def handle_table2(sheet, lines, message):
    """
    Формат повідомлення (через кому):
    Учень, Дата, Час, Локація, Коментар (якщо кілька учнів), Причина годинного відпрацювання

    Колонки в таблиці:
    A - Місяць        -> автоматично з дати, словом українською
    B - Викладач      -> автоматично за telegram username (TEACHERS_MAP в .env)
    C - Учень         -> з повідомлення
    D - Дата          -> з повідомлення
    E - Час           -> з повідомлення
    F - Локація       -> з повідомлення
    G - Відбулось     -> завжди TRUE
    H - Не відбулось  -> завжди пропускається (порожньо)
    I - Коментар      -> з повідомлення (декілька учнів)
    J - Причина       -> з повідомлення (причина годинного відпрацювання)
    """
    responses = []
    teacher = get_teacher_name(message)
    entries_by_location = defaultdict(list)  # location -> list of entries (для нотифікацій у групи)

    with sheet_lock:
        all_values = sheet.get_all_values()
        empty_rows = [i + 1 for i, row in enumerate(all_values)
                      if len(row) < 4 or all(v.strip() == "" for v in row[:4])]
        max_row = len(all_values)
        next_empty_index = 0

        for line_number, line in enumerate(lines, start=1):
            try:
                data = [x.strip() for x in line.split(",")]
                if len(data) < 3:
                    responses.append(
                        f"Рядок {line_number}: ⚠ Формат: Учень, Дата, Час, Локація, Коментар, Причина"
                    )
                    continue

                student = data[0]
                date_val = data[1] if len(data) > 1 else ""
                time_val = data[2] if len(data) > 2 else ""
                location = data[3] if len(data) > 3 else ""
                comment_multiple = data[4] if len(data) > 4 else ""
                reason_hourly = data[5] if len(data) > 5 else ""

                month_name = get_month_name(date_val)

                row_data = [
                    month_name,        # A - Місяць
                    teacher,           # B - Викладач
                    student,           # C - Прізвище та ім'я учня
                    date_val,          # D - Дата
                    time_val,          # E - Час
                    location,          # F - Локація
                    True,              # G - Відбулось (завжди TRUE)
                    "",                # H - Не відбулось (завжди пропускається)
                    comment_multiple,  # I - Коментар (декілька учнів)
                    reason_hourly      # J - Причина годинного відпрацювання
                ]

                if next_empty_index < len(empty_rows):
                    target_row = empty_rows[next_empty_index]
                    next_empty_index += 1
                    sheet.update(f"A{target_row}:J{target_row}", [row_data])
                else:
                    sheet.append_row(row_data)
                    target_row = max_row + 1
                    max_row += 1

                responses.append(f"Рядок {line_number}: ✅ Додано у рядок {target_row}")

                if location:
                    entries_by_location[location].append({
                        "student": student,
                        "date": date_val,
                        "time": time_val,
                        "comment": comment_multiple,
                        "reason": reason_hourly
                    })

            except Exception as e:
                responses.append(f"Рядок {line_number}: ❌ Помилка: {e}")

    # Сповіщення у групи відправляємо ПІСЛЯ виходу з sheet_lock,
    # щоб мережевий виклик до Telegram не тримав блокування таблиці
    for location, entries in entries_by_location.items():
        notify_location_group(location, teacher, entries)

    return responses

# ==== UNIVERSAL HANDLER ====
@bot.message_handler(func=lambda m: True, chat_types=['private'])
@safe_handler
def handle_data(message):
    if "," not in message.text:
        bot.send_message(message.chat.id, "⚠ Повідомлення повинно містити коми!")
        return

    sheet, table_type, table_name = get_user_sheet(message.from_user.id)
    lines = message.text.strip().splitlines()

    if table_type == "table1":
        responses = handle_table1(sheet, lines)
    else:
        responses = handle_table2(sheet, lines, message)

    bot.send_message(message.chat.id, f"📊 ({table_name})\n" + "\n".join(responses))


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)