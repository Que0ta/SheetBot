# 🤖 Telegram Bot — Google Sheets Updater

This project is a **Telegram bot** built using [PyTelegramBotAPI (telebot)](https://github.com/eternnoir/pyTelegramBotAPI) that interacts with **Google Sheets** in real time.  
It allows users to send messages or data through Telegram, which are then automatically added or updated in a connected Google Sheet page to specific columns.
# handle_table functions aren't universal, each column should be specified, in order to add data in right columns!

---

## 🚀 Features

- 📊 Append or update rows in Google Sheets  
- 🔐 Secure connection via Google API credentials  
- 🕒 Real-time synchronization  
- 💻 Easy to set up and deploy (cloud)

---

## 🧱 Tech Stack

| Component | Description |
|------------|-------------|
| **Python 3.11+** | Programming language |
| **telebot** | Telegram Bot API |
| **gspread** | Google Sheets API wrapper |
| **Flask (optional)** | For webhook deployment |

---
