import telebot
import gspread, os
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from telebot import *
from flask import Flask, request
import traceback

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
bot = telebot.TeleBot(TELEGRAM_TOKEN)

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
        "title": "Відпрацювання",   # ← fallback назва
        "type": "table1"
    },
    "Інша таблиця": {
        "sheet_id": table2_id,  # ID другого Google Sheets
        "gid": table2,
        "title": "Test",   # ← fallback назва
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


@bot.message_handler(commands=['start'])
@safe_handler
def start_message(message):
    bot.send_message(message.chat.id,
                     "Привіт! 👋\n"
                     "Використай /table щоб обрати таблицю. Приклади повідомлень внизу =>\n"
                     "📌 Проведені відпрацювання → \n"
                     "Викладач, Прізвище та ім'я учня, 18.08.2025, 18:00 , check,, індивідуальне заняття, пропуск >= 2 занять\n"
                     "\n"
                     "📌❗️ Тестова таблиця → \n"
                     "формат: Викладач, Учень, Група, Дата та час, check/uncheck, причина, учні через ;"
                     "❗️ Виключно для тестування роботи бота!")


# ==== TABLE SELECTION ====
@bot.message_handler(commands=['table'])
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
    all_values = sheet.get_all_values()
    empty_rows = [i + 1 for i, row in enumerate(all_values)
                  if len(row) < 4 or all(v.strip() == "" for v in row[:4])]
    max_row = len(all_values)
    next_empty_index = 0

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

            # Визначаємо рядок для запису: пустий рядок або наступний після max_row
            if next_empty_index < len(empty_rows):
                target_row = empty_rows[next_empty_index]
                next_empty_index += 1
                sheet.update(f"A{target_row}:H{target_row}", [row_data])
            else:
                sheet.append_row(row_data)
                target_row = max_row + 1
                max_row += 1

            responses.append(f"Рядок {line_number}: ✅ Додано у рядок {target_row}")

        except Exception as e:
            responses.append(f"Рядок {line_number}: ❌ Помилка: {e}")

    return responses


def handle_table2(sheet, lines):
    responses = []
    all_values = sheet.get_all_values()
    empty_rows = [i + 1 for i, row in enumerate(all_values)
                  if len(row) < 4 or all(v.strip() == "" for v in row[:4])]
    max_row = len(all_values)
    next_empty_index = 0

    for line_number, line in enumerate(lines, start=1):
        try:
            data = [x.strip() for x in line.split(",")]
            if len(data) < 4:
                responses.append(f"Рядок {line_number}: ⚠ Формат: Викладач, Учень, Група, Дата та час ...")
                continue

            teacher, student, group, datetime_val = data[:4]
            checkbox_value = None
            reason = ""
            students_list = ""

            if len(data) >= 5:
                if data[4].lower() in ("check", "uncheck"):
                    checkbox_value = True if data[4].lower() == "check" else False
                    if len(data) > 5: reason = data[5]
                    if len(data) > 6: students_list = data[6]
                else:
                    reason = data[4]
                    if len(data) > 5: students_list = data[5]

            # Prepare full row: leave empty column 7 to match original format
            row_data = [teacher, student, group, datetime_val, checkbox_value, reason, "", students_list]

            # Determine row to write: first empty row or append at the bottom
            if next_empty_index < len(empty_rows):
                target_row = empty_rows[next_empty_index]
                next_empty_index += 1
                sheet.update(f"A{target_row}:H{target_row}", [row_data])
            else:
                sheet.append_row(row_data)
                target_row = max_row + 1
                max_row += 1

            responses.append(f"Рядок {line_number}: ✅ Додано у рядок {target_row}")

        except Exception as e:
            responses.append(f"Рядок {line_number}: ❌ Помилка: {e}")

    return responses

# ==== UNIVERSAL HANDLER ====
@bot.message_handler(func=lambda m: True)
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
        responses = handle_table2(sheet, lines)

    bot.send_message(message.chat.id, f"📊 ({table_name})\n" + "\n".join(responses))


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)