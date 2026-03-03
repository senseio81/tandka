# ==== ПРИНУДИТЕЛЬНАЯ УСТАНОВКА ====
# ==== ЖЕСТКАЯ ПРИНУДИТЕЛЬНАЯ УСТАНОВКА ====
import subprocess
import sys
import os

print("🚀 Устанавливаю пакеты...")

# 1. Сносим всё нахуй
subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", "setuptools", "python-telegram-bot", "apscheduler"])

# 2. Ставим setuptools СТАРОЙ версии (именно она дает pkg_resources)
subprocess.check_call([sys.executable, "-m", "pip", "install", "setuptools==59.5.0"])

# 3. Теперь ставим всё остальное
subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot==13.15"])
subprocess.check_call([sys.executable, "-m", "pip", "install", "APScheduler==3.6.3"])
subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary==2.9.9"])
subprocess.check_call([sys.executable, "-m", "pip", "install", "requests==2.31.0"])
subprocess.check_call([sys.executable, "-m", "pip", "install", "Flask==2.3.3"])

print("✅ Пакеты установлены")
# ===========================================
# ===================================

# Теперь импортируем
import telegram
print(f"✅ Версия telegram-bot: {telegram.__version__}")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
import psycopg2
import psycopg2.extras
import requests
import random
import string
import time
from datetime import datetime
import logging

# ========== НАСТРОЙКА ==========
BOT_TOKEN = os.environ['BOT_TOKEN']
CRYPTOBOT_TOKEN = os.environ['CRYPTOBOT_TOKEN']
BOT_USERNAME = "Galaxy_MoneyBot"
MIN_WITHDRAW = 8
ADMIN_IDS = [8503054217]
ADMIN_USERNAMES = ["siberia_1488"]

# ========== ПОДКЛЮЧЕНИЕ К POSTGRESQL ==========
DATABASE_URL = os.environ['DATABASE_URL']

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# ========== ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            paid_status INTEGER DEFAULT 0,
            balance DECIMAL DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referrer_id BIGINT,
            referral_code TEXT UNIQUE,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount DECIMAL,
            type TEXT,
            status TEXT,
            invoice_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных PostgreSQL готова")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

transaction_logger = logging.getLogger('transactions')
transaction_handler = logging.FileHandler('transactions.log')
transaction_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
transaction_logger.addHandler(transaction_handler)
transaction_logger.setLevel(logging.INFO)
transaction_logger.propagate = False

logger = logging.getLogger(__name__)

# ========== КУРС ДОЛЛАРА ==========
def get_usd_rate():
    try:
        url = "https://www.cbr-xml-daily.ru/latest.js"
        response = requests.get(url, timeout=5)
        data = response.json()
        
        if 'rates' in data and 'RUB' in data['rates']:
            usd_rate = 1 / data['rates']['RUB']
            return round(usd_rate, 2)
    except:
        pass
    
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=5)
        data = response.json()
        if 'rates' in data and 'RUB' in data['rates']:
            return round(data['rates']['RUB'], 2)
    except:
        pass
    
    return 90.0

# ========== ФУНКЦИИ БД ==========
def get_user_balance(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    conn.close()
    return float(result[0]) if result else 0

def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0] in ADMIN_USERNAMES:
        return True
    return False

def get_stats():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM users WHERE paid_status = 1")
    paid_users = c.fetchone()[0]
    
    c.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
    total_balance = float(c.fetchone()[0])
    
    c.execute("SELECT COALESCE(SUM(referral_count), 0) FROM users")
    total_refs = int(c.fetchone()[0])
    
    c.execute("SELECT COUNT(*) FROM transactions")
    total_transactions = c.fetchone()[0]
    
    conn.close()
    
    return {
        'total_users': total_users,
        'paid_users': paid_users,
        'total_balance': total_balance,
        'total_refs': total_refs,
        'total_transactions': total_transactions
    }

# ========== ЛОГИРОВАНИЕ ТРАНЗАКЦИЙ ==========
def log_transaction(user_id, amount, type_, details=""):
    username = f"user_{user_id}"
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        if result and result[0]:
            username = f"@{result[0]}"
        conn.close()
    except:
        pass
    
    log_msg = f"{type_.upper()} | User: {user_id} ({username}) | Amount: {amount} USDT | {details}"
    transaction_logger.info(log_msg)

# ========== ВЫВОД ЧЕРЕЗ CRYPTOBOT ==========
def withdraw_to_user(user_id, amount):
    url = "https://pay.crypt.bot/api/transfer"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    
    data = {
        "user_id": user_id,
        "asset": "USDT",
        "amount": str(amount),
        "spend_id": f"withdraw_{user_id}_{int(time.time())}",
        "description": f"Вывод из бота @{BOT_USERNAME}"
    }
    
    try:
        logger.info(f"💰 Вывод {amount} USDT пользователю {user_id}")
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        
        if result.get('ok'):
            log_transaction(user_id, amount, "WITHDRAW", "Успешно")
            return {'success': True, 'message': f"✅ {amount} USDT отправлено!"}
        else:
            error_msg = result.get('error', 'Неизвестная ошибка')
            log_transaction(user_id, amount, "WITHDRAW_FAILED", f"Ошибка: {error_msg}")
            return {'success': False, 'message': f"❌ Ошибка: {error_msg}"}
            
    except Exception as e:
        log_transaction(user_id, amount, "WITHDRAW_ERROR", str(e))
        return {'success': False, 'message': f"❌ Ошибка: {str(e)}"}

# ========== СОЗДАНИЕ СЧЕТА ==========
def create_crypto_invoice(user_id, amount):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    
    data = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Оплата доступа для user_{user_id}",
        "payload": str(user_id),
        "paid_btn_name": "openBot",
        "paid_btn_url": f"https://t.me/{BOT_USERNAME}"
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        result = response.json()
        
        if result.get('ok'):
            invoice_id = result['result']['invoice_id']
            log_transaction(user_id, amount, "INVOICE_CREATED", f"Invoice: {invoice_id}")
            return result['result']
        else:
            log_transaction(user_id, amount, "INVOICE_ERROR", str(result))
            return None
    except Exception as e:
        log_transaction(user_id, amount, "INVOICE_ERROR", str(e))
        return None

# ========== ПРОВЕРКА ПЛАТЕЖА ==========
def check_payment_status(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    
    try:
        response = requests.get(url, headers=headers, params={"invoice_ids": invoice_id})
        result = response.json()
        if result.get('ok') and result['result']['items']:
            return result['result']['items'][0]['status']
    except:
        pass
    return 'active'

# ========== ОБРАБОТКА ПЛАТЕЖА ==========
def process_payment(user_id, invoice_id, context):
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT referrer_id FROM users WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    referrer_id = result[0] if result else None
    
    c.execute("UPDATE users SET paid_status = 1 WHERE user_id = %s", (user_id,))
    
    log_transaction(user_id, 5, "PAYMENT", f"Оплата доступа, Invoice: {invoice_id}")
    
    if referrer_id:
        # 1 УРОВЕНЬ - 3$
        c.execute("SELECT balance, referral_count FROM users WHERE user_id = %s", (referrer_id,))
        ref_data = c.fetchone()
        
        if ref_data:
            new_balance = float(ref_data[0]) + 3
            new_count = ref_data[1] + 1
            c.execute("UPDATE users SET balance = %s, referral_count = %s WHERE user_id = %s", 
                     (new_balance, new_count, referrer_id))
            
            log_transaction(referrer_id, 3, "REFERRAL_LEVEL1", f"За пользователя {user_id}")
            
            try:
                context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"🎉 Вам начислено 3 USDT за нового реферала!\n🏦 Баланс: {new_balance} USDT"
                )
            except:
                pass
            
            # 2 УРОВЕНЬ - 1$
            c.execute("SELECT referrer_id FROM users WHERE user_id = %s", (referrer_id,))
            result2 = c.fetchone()
            referrer_level2 = result2[0] if result2 else None
            
            if referrer_level2:
                c.execute("SELECT balance FROM users WHERE user_id = %s", (referrer_level2,))
                ref2_data = c.fetchone()
                
                if ref2_data:
                    new_balance2 = float(ref2_data[0]) + 1
                    c.execute("UPDATE users SET balance = %s WHERE user_id = %s", 
                             (new_balance2, referrer_level2))
                    
                    log_transaction(referrer_level2, 1, "REFERRAL_LEVEL2", f"За пользователя {user_id} (через {referrer_id})")
                    
                    try:
                        context.bot.send_message(
                            chat_id=referrer_level2,
                            text=f"✨ Вам начислен 1 USDT за реферала второго уровня!\n🏦 Баланс: {new_balance2} USDT"
                        )
                    except:
                        pass
    
    conn.commit()
    conn.close()
    return True

# ========== КОМАНДА СТАРТ ==========
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    user_id = user.id
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    existing_user = c.fetchone()
    
    args = context.args
    referrer_id = None
    
    if args and args[0].startswith('ref_'):
        ref_code = args[0]
        c.execute("SELECT user_id FROM users WHERE referral_code = %s", (ref_code,))
        result = c.fetchone()
        if result:
            referrer_id = result[0]
    
    if not existing_user:
        new_ref_code = 'ref_' + ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        c.execute("INSERT INTO users (user_id, username, paid_status, referrer_id, referral_code) VALUES (%s, %s, 0, %s, %s)",
                  (user_id, user.username, referrer_id, new_ref_code))
        conn.commit()
        logger.info(f"👤 Новый пользователь {user_id}, реферер: {referrer_id}, код: {new_ref_code}")
    
    c.execute("SELECT paid_status FROM users WHERE user_id = %s", (user_id,))
    paid_status = c.fetchone()[0]
    conn.close()
    
    if paid_status == 0 and not is_admin(user_id):
        keyboard = [[InlineKeyboardButton("💳 Оплатить 5 USDT", callback_data='pay')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            "Для использования оплатите 5 USDT",
            reply_markup=reply_markup
        )
    else:
        keyboard = [
            [InlineKeyboardButton("Профиль", callback_data='profile')],
            [
                InlineKeyboardButton("Реферальная ссылка", callback_data='referral'),
                InlineKeyboardButton("Вывести", callback_data='withdraw')
            ]
        ]
        
        if is_admin(user_id):
            keyboard.append([InlineKeyboardButton("Админ панель", callback_data='admin_panel')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "*🔮 Galaxy - безграничная вселенная возможностей!*\n\n"
            "• В данном сервисе приглашайте рефералов и получайте за это деньги!"
        )
        
        update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ========== АДМИН ПАНЕЛЬ ==========
def show_admin_panel(query, user_id):
    if not is_admin(user_id):
        query.edit_message_text("У вас нет доступа к админ-панели")
        return
    
    keyboard = [
        [
            InlineKeyboardButton("Статистика", callback_data='admin_stats'),
            InlineKeyboardButton("Логи", callback_data='admin_logs')
        ],
        [
            InlineKeyboardButton("Баланс бота", callback_data='admin_balance')
        ],
        [InlineKeyboardButton("Назад", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        "Админ панель:",
        reply_markup=reply_markup
    )

def show_admin_stats(query):
    stats = get_stats()
    
    text = (
        "<blockquote>"
        f"📊 Статистика\n\n"
        f"Всего пользователей: {stats['total_users']}\n"
        f"Оплативших: {stats['paid_users']}\n"
        f"Общий баланс: {stats['total_balance']:.2f} USDT\n"
        f"Всего рефералов: {stats['total_refs']}\n"
        f"Транзакций: {stats['total_transactions']}"
        "</blockquote>"
    )
    
    keyboard = [[InlineKeyboardButton("Назад", callback_data='admin_panel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

def send_logs(query, context, log_type='bot'):
    try:
        filename = 'bot.log' if log_type == 'bot' else 'transactions.log'
        
        if not os.path.exists(filename):
            temp_filename = f'temp_{log_type}.log'
            with open(temp_filename, 'w', encoding='utf-8') as f:
                f.write(f"📭 Файл {log_type}.log пуст. Транзакций пока нет.\n")
                f.write(f"Создано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            with open(temp_filename, 'rb') as f:
                context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=f,
                    filename=f'{log_type}_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt',
                    caption=f"📋 Логи {log_type} (файл был пуст)"
                )
            
            os.remove(temp_filename)
            query.answer()
            return
        
        if os.path.getsize(filename) == 0:
            temp_filename = f'temp_{log_type}.log'
            with open(temp_filename, 'w', encoding='utf-8') as f:
                f.write(f"📭 Файл {log_type}.log пуст. Транзакций пока нет.\n")
                f.write(f"Создано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            with open(temp_filename, 'rb') as f:
                context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=f,
                    filename=f'{log_type}_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt',
                    caption=f"📋 Логи {log_type} (файл был пуст)"
                )
            
            os.remove(temp_filename)
            query.answer()
            return
            
        with open(filename, 'rb') as f:
            context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename=f'{log_type}_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt',
                caption=f"📋 Логи {log_type}"
            )
        query.answer("✅ Логи отправлены")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке логов: {e}")
        query.answer(f"❌ Ошибка: {e}", show_alert=True)

def show_admin_balance(query):
    url = "https://pay.crypt.bot/api/getBalance"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    
    try:
        response = requests.get(url, headers=headers)
        result = response.json()
        
        if result.get('ok'):
            balances = result['result']
            usdt_balance = 0
            
            for asset in balances:
                if asset.get('asset') == 'USDT':
                    usdt_balance = float(asset.get('available', 0))
                    break
            
            text = (
                "<blockquote>"
                f"🏦 Баланс бота в CryptoBot:\n\n"
                f"USDT: {usdt_balance}"
                "</blockquote>"
            )
        else:
            error_msg = result.get('error', 'Неизвестная ошибка')
            text = (
                "<blockquote>"
                f"❌ Ошибка API:\n{error_msg}"
                "</blockquote>"
            )
    except Exception as e:
        text = (
            "<blockquote>"
            f"❌ Ошибка:\n{str(e)}"
            "</blockquote>"
        )
    
    keyboard = [[InlineKeyboardButton("Назад", callback_data='admin_panel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# ========== ПРОФИЛЬ ==========
def show_profile(query, user_id):
    balance = get_user_balance(user_id)
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT referral_count FROM users WHERE user_id = %s", (user_id,))
    referrals = c.fetchone()[0]
    conn.close()
    
    usd_rate = get_usd_rate()
    rub = balance * usd_rate
    ref_income = referrals * 3
    
    text = (
        "<blockquote>"
        f"📲 Профиль\n\n"
        f"🏦 Баланс: {balance} USDT (~{rub:.0f}₽)\n"
        f"👥 Рефералы: {referrals}шт (~{ref_income} USDT)\n"
        f"📉 Минимум вывода: {MIN_WITHDRAW}$\n"
        f"💱 Курс USD: {usd_rate}₽"
        "</blockquote>"
    )
    
    keyboard = [[InlineKeyboardButton("Назад", callback_data='back_to_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# ========== РЕФЕРАЛЬНАЯ ССЫЛКА ==========
def show_referral(query, user_id):
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT referral_code FROM users WHERE user_id = %s", (user_id,))
    result = c.fetchone()
    
    if result and result[0]:
        ref_code = result[0]
    else:
        ref_code = 'ref_' + ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        c.execute("UPDATE users SET referral_code = %s WHERE user_id = %s", (ref_code, user_id))
        conn.commit()
    
    conn.close()
    
    ref_link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
    
    text = (
        "*🏦 Ваша реферальная ссылка:*\n\n"
        f"`{ref_link}`\n\n"
        "• За каждого реферала вы получите 3 USDT!\n"
        "• За рефералов второго уровня +1 USDT!"
    )
    
    keyboard = [
        [InlineKeyboardButton("Назад", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ========== ВЫВОД ==========
def show_withdraw(query, user_id, context):
    balance = get_user_balance(user_id)
    
    if balance < MIN_WITHDRAW:
        need = MIN_WITHDRAW - balance
        referrals_needed = (need + 2) // 3
        
        text = (
            "<blockquote>"
            f"🔮 Ваш баланс: {balance} USDT\n"
            f"📲 Для вывода нужно минимум {MIN_WITHDRAW} USDT\n"
            f"🔗 Пригласите ещё {referrals_needed} рефералов!"
            "</blockquote>"
        )
        
        keyboard = [[InlineKeyboardButton("Назад", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    else:
        text = (
            f"*🏦 Ваш баланс: {balance} USDT*\n\n\n"
            f"• Вывод от {MIN_WITHDRAW} USDT"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("Вывести", callback_data='process_withdraw'),
                InlineKeyboardButton("Назад", callback_data='back_to_menu')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ========== ОБРАБОТЧИК ТЕКСТА ==========
def handle_text(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if context.user_data.get('awaiting_withdraw'):
        try:
            amount = float(update.message.text.replace(',', '.'))
            balance = get_user_balance(user_id)
            
            if amount < MIN_WITHDRAW:
                update.message.reply_text(f"❌ Минимальная сумма вывода {MIN_WITHDRAW} USDT")
                return
            
            if amount > balance:
                update.message.reply_text(f"❌ Недостаточно средств. Баланс: {balance} USDT")
                return
            
            result = withdraw_to_user(user_id, amount)
            
            if result['success']:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (amount, user_id))
                conn.commit()
                conn.close()
                
                update.message.reply_text(
                    f"🔮",
                    reply_to_message_id=update.message.message_id
                )
            else:
                update.message.reply_text(result['message'])
            
            context.user_data['awaiting_withdraw'] = False
            
        except ValueError:
            update.message.reply_text("❌ Введите число")

# ========== ОБРАБОТЧИК КНОПОК ==========
def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == 'pay':
        invoice = create_crypto_invoice(user_id, 5)
        if invoice:
            keyboard = [
                [InlineKeyboardButton("💳 Перейти к оплате", url=invoice['pay_url'])],
                [InlineKeyboardButton("✅ Проверить оплату", callback_data=f'check_{invoice["invoice_id"]}')],
                [InlineKeyboardButton("◀️ Назад", callback_data='back_to_start')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(
                "💰 Счет на 5 USDT создан!\n\n1. Оплати в @CryptoBot\n2. Нажми 'Проверить'",
                reply_markup=reply_markup
            )
        else:
            query.edit_message_text("❌ Ошибка создания счета. Попробуйте позже.")
    
    elif query.data.startswith('check_'):
        invoice_id = query.data.replace('check_', '')
        status = check_payment_status(invoice_id)
        
        if status == 'paid':
            # Проверяем не был ли уже оплачен
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT paid_status FROM users WHERE user_id = %s", (user_id,))
            paid_status = c.fetchone()
            conn.close()
            
            if paid_status and paid_status[0] == 1:
                # Уже оплатил - просто показываем меню
                keyboard = [
                    [InlineKeyboardButton("Профиль", callback_data='profile')],
                    [
                        InlineKeyboardButton("Реферальная ссылка", callback_data='referral'),
                        InlineKeyboardButton("Вывести", callback_data='withdraw')
                    ]
                ]
                if is_admin(user_id):
                    keyboard.append([InlineKeyboardButton("Админ панель", callback_data='admin_panel')])
                reply_markup = InlineKeyboardMarkup(keyboard)
                query.edit_message_text(
                    "✅ Вы уже оплатили!",
                    reply_markup=reply_markup
                )
            else:
                # Начисляем бонусы
                process_payment(user_id, invoice_id, context)
                
                keyboard = [
                    [InlineKeyboardButton("Профиль", callback_data='profile')],
                    [
                        InlineKeyboardButton("Реферальная ссылка", callback_data='referral'),
                        InlineKeyboardButton("Вывести", callback_data='withdraw')
                    ]
                ]
                if is_admin(user_id):
                    keyboard.append([InlineKeyboardButton("Админ панель", callback_data='admin_panel')])
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                text = (
                    "*🔮 Galaxy - безграничная вселенная возможностей!*\n\n"
                    "• В данном сервисе приглашайте рефералов и получайте за это деньги!"
                )
                
                query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        else:
            # Возвращаем к созданию счета, а не в начало
            keyboard = [
                [InlineKeyboardButton("🔄 Проверить снова", callback_data=f'check_{invoice_id}')],
                [InlineKeyboardButton("◀️ Назад к счету", callback_data='back_to_invoice')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(
                "⏳ Платеж еще не найден.\n\nУбедитесь что вы оплатили и нажмите 'Проверить снова'",
                reply_markup=reply_markup
            )
    
    elif query.data == 'back_to_invoice':
        # Возвращаем к созданию счета
        invoice = create_crypto_invoice(user_id, 5)
        if invoice:
            keyboard = [
                [InlineKeyboardButton("💳 Перейти к оплате", url=invoice['pay_url'])],
                [InlineKeyboardButton("✅ Проверить оплату", callback_data=f'check_{invoice["invoice_id"]}')],
                [InlineKeyboardButton("◀️ Назад", callback_data='back_to_start')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(
                "💰 Счет на 5 USDT создан!\n\n1. Оплати в @CryptoBot\n2. Нажми 'Проверить'",
                reply_markup=reply_markup
            )
    
    elif query.data == 'admin_panel':
        show_admin_panel(query, user_id)
    elif query.data == 'admin_stats':
        show_admin_stats(query)
    elif query.data == 'admin_logs':
        keyboard = [
            [
                InlineKeyboardButton("Логи бота", callback_data='admin_logs_bot'),
                InlineKeyboardButton("Транзакции", callback_data='admin_logs_trans')
            ],
            [InlineKeyboardButton("Назад", callback_data='admin_panel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("Выберите тип логов:", reply_markup=reply_markup)
    elif query.data == 'admin_logs_bot':
        send_logs(query, context, 'bot')
    elif query.data == 'admin_logs_trans':
        send_logs(query, context, 'transactions')
    elif query.data == 'admin_balance':
        show_admin_balance(query)
    
    elif query.data == 'profile':
        show_profile(query, user_id)
    elif query.data == 'referral':
        show_referral(query, user_id)
    elif query.data == 'withdraw':
        show_withdraw(query, user_id, context)
    elif query.data == 'process_withdraw':
        query.edit_message_text("💰 Введите сумму вывода:")
        context.user_data['awaiting_withdraw'] = True
    elif query.data == 'back_to_menu':
        keyboard = [
            [InlineKeyboardButton("Профиль", callback_data='profile')],
            [
                InlineKeyboardButton("Реферальная ссылка", callback_data='referral'),
                InlineKeyboardButton("Вывести", callback_data='withdraw')
            ]
        ]
        
        if is_admin(user_id):
            keyboard.append([InlineKeyboardButton("Админ панель", callback_data='admin_panel')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = (
            "*🔮 Galaxy - безграничная вселенная возможностей!*\n\n"
            "• В данном сервисе приглашайте рефералов и получайте за это деньги!"
        )
        
        query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif query.data == 'back_to_start':
        if is_admin(user_id):
            keyboard = [
                [InlineKeyboardButton("Профиль", callback_data='profile')],
                [
                    InlineKeyboardButton("Реферальная ссылка", callback_data='referral'),
                    InlineKeyboardButton("Вывести", callback_data='withdraw')
                ],
                [InlineKeyboardButton("Админ панель", callback_data='admin_panel')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                "*🔮 Galaxy - безграничная вселенная возможностей!*\n\n"
                "• В данном сервисе приглашайте рефералов и получайте за это деньги!"
            )
            query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            keyboard = [[InlineKeyboardButton("💳 Оплатить 5 USDT", callback_data='pay')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(
                "Для использования оплатите 5 USDT",
                reply_markup=reply_markup
            )

# ========== ЗАПУСК ==========
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(button_callback))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    
    updater.start_polling()
    logger.info("🚀 Бот @%s запущен с PostgreSQL! Мин.вывод: %s$", BOT_USERNAME, MIN_WITHDRAW)
    updater.idle()

if __name__ == '__main__':
    init_db()
    main()
