import telebot
import gspread, os, requests
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from flask import Flask, request

load_dotenv()
# ==== CONFIG ====
TELEGRAM_TOKEN = os.getenv('Tg_K')
SHEET_ID = os.getenv('SHEETY')
SECRETO = os.getenv('boomba')

app = Flask(__name__)

@app.route('/' + TOKEN, methods=['POST'])
def get_message():
    bot.process_new_updates([telebot.types.Update.de_json(request.stream.read().decode("utf-8"))])
    return "!", 200

@app.route('/')
def webhook():
    bot.remove_webhook()
    bot.set_webhook(url='https://tele-check.onrender.com/' + TOKEN)  # Replace with your Render app name!
    return "Webhook set!", 200


# ==== GOOGLE SHEETS AUTH ====
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(SECRETO, scopes=scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

# ==== TELEGRAM BOT ====
bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id,
                     "Привіт! 👋\n"
                     "Формат повідомлення:\n"
                     "Викладач, Учень, Група, Дата та час\n\n"
                     "✅ Поставити галочку з причиною: Викладач, Учень, Група, Дата та час, check, причина\n"
                     "❌ Зняти галочку з причиною: Викладач, Учень, Група, Дата та час, uncheck, причина\n"
                     "📌 Останній параметр *необов'язковий* — к-сть дітей відпрацювання (якщо більше 2)."
                     "\n\n"
                     "Приклад: "
                     "Петренко Іван, Коваль Марія, КГ_Сб18, 12.08.2025 15:00, check, допрацювання, Іваненко Ігор; Петренко Оксана; Сидоренко Ліна")
@bot.message_handler(func=lambda m: "," in m.text)
def handle_data(message):
    lines = message.text.strip().splitlines()
    responses = []

    # Зчитуємо всі дані один раз перед циклом
    all_values = sheet.get_all_values()

    # Зберемо індекси порожніх рядків, де перші 4 колонки пусті
    empty_rows = []
    max_row = len(all_values)

    for i, row_values in enumerate(all_values, start=1):
        if len(row_values) < 4 or all(v.strip() == "" for v in row_values[:4]):
            empty_rows.append(i)

    next_row_to_use = 0  # Індекс в empty_rows

    for line_number, line in enumerate(lines, start=1):
        try:
            data = [x.strip() for x in line.split(",")]

            if len(data) < 4:
                responses.append(f"Рядок {line_number}: ⚠ Формат: Викладач, Учень, Група, Дата та час [, check/uncheck, причина [, учні через ;]]")
                continue

            checkbox_action = None
            reason = ""
            students_list = ""

            if len(data) >= 5:
                last = data[4].lower()
                if last in ("check", "uncheck"):
                    checkbox_action = last
                    data_main = data[:4]

                    if len(data) > 5:
                        reason = data[5]

                    if len(data) > 6:
                        students_list = data[6]
                else:
                    data_main = data[:4]
                    reason = data[4]

                    if len(data) > 5:
                        students_list = data[5]
            else:
                data_main = data[:4]

            # Визначаємо, який рядок використати
            if next_row_to_use < len(empty_rows):
                row = empty_rows[next_row_to_use]
                next_row_to_use += 1
            else:
                max_row += 1
                row = max_row

            # Записуємо дані
            sheet.update_cell(row, 1, data_main[0])
            sheet.update_cell(row, 2, data_main[1])
            sheet.update_cell(row, 3, data_main[2])
            sheet.update_cell(row, 4, data_main[3])

            if checkbox_action == "check":
                sheet.update_cell(row, 5, "TRUE")
            elif checkbox_action == "uncheck":
                sheet.update_cell(row, 5, "FALSE")

            if reason:
                sheet.update_cell(row, 6, reason)

            if students_list:
                sheet.update_cell(row, 8, students_list)

            responses.append(f"Рядок {line_number}: ✅ Додано у рядок {row}")

        except Exception as e:
            responses.append(f"Рядок {line_number}: ❌ Помилка: {e}")

    bot.send_message(message.chat.id, "\n".join(responses))


@bot.message_handler(commands=['check'])
def check_checkbox(message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2 or not parts[1].isdigit():
            bot.send_message(message.chat.id, "⚠ Використання: /check <номер_рядка> [причина]")
            return

        row = int(parts[1])
        reason = parts[2] if len(parts) == 3 else ""

        sheet.update_cell(row, 5, "TRUE")  # Галочка
        if reason:
            sheet.update_cell(row, 6, reason)  # Причина

        bot.send_message(message.chat.id, f"✅ Галочка у рядку {row} поставлена!\nПричина: {reason or 'немає'}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Помилка: {e}")


@bot.message_handler(commands=['uncheck'])
def uncheck_checkbox(message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2 or not parts[1].isdigit():
            bot.send_message(message.chat.id, "⚠ Використання: /uncheck <номер_рядка> [причина]")
            return

        row = int(parts[1])
        reason = parts[2] if len(parts) == 3 else ""

        sheet.update_cell(row, 5, "FALSE")  # Зняти галочку
        if reason:
            sheet.update_cell(row, 6, reason)  # Причина

        bot.send_message(message.chat.id, f"❌ Галочка у рядку {row} знята!\nПричина: {reason or 'немає'}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Помилка: {e}")


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)