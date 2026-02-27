import telebot
import os
import random
import re
import psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, timedelta
from threading import Timer
import pytz
from flask import Flask, request

bot = telebot.TeleBot(os.environ['T'])
admin_id = int(os.environ.get('ADMIN_ID', 0))
DATABASE_URL = os.environ.get('DATABASE_URL')

conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cursor = conn.cursor(cursor_factory=DictCursor)

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()

app = Flask(__name__)

def add_user_to_db(user_id, username, first_name):
    cursor.execute('''
        INSERT INTO users (user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) 
        DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name
    ''', (user_id, username, first_name))
    conn.commit()

def get_user_id_by_username(username):
    cursor.execute('SELECT user_id FROM users WHERE username = %s', (username,))
    result = cursor.fetchone()
    return result[0] if result else None

emojis = ['🐶', '🐱', '🦊', '🐼', '🐨', '🦁']
user_captcha = {}
muted_users = {}
chat_id = -1003534299392
msk_tz = pytz.timezone('Europe/Moscow')

def parse_time_and_reason(text):
    patterns = {
        'minutes': r'(\d+)\s*(м|мин|минута|минуты|минут|m)',
        'hours': r'(\d+)\s*(ч|час|часа|часов|h)',
        'days': r'(\d+)\s*(д|день|дня|дней|d)',
        'weeks': r'(\d+)\s*(н|нед|неделя|недели|недель|w)',
        'months': r'(\d+)\s*(мес|месяц|месяца|месяцев|m)',
        'years': r'(\d+)\s*(г|год|года|лет|y)'
    }
    
    text_lower = text.lower()
    total_seconds = 0
    reason = ''
    
    for unit, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            value = int(match.group(1))
            if unit == 'minutes':
                total_seconds = value * 60
            elif unit == 'hours':
                total_seconds = value * 3600
            elif unit == 'days':
                total_seconds = value * 86400
            elif unit == 'weeks':
                total_seconds = value * 604800
            elif unit == 'months':
                total_seconds = value * 2592000
            elif unit == 'years':
                total_seconds = value * 31536000
            
            reason = text_lower.replace(match.group(0), '').strip()
            break
    
    return total_seconds, reason

def extract_user_id(message):
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        add_user_to_db(user.id, user.username, user.first_name)
        return user.id, message.text.replace('/mute', '').strip()
    
    text = message.text.replace('/mute', '').strip()
    words = text.split()
    
    for word in words:
        if word.startswith('@'):
            username = word[1:]
            user_id = get_user_id_by_username(username)
            if user_id:
                remaining_text = ' '.join([w for w in words if w != word])
                return user_id, remaining_text
            continue
        elif word.isdigit():
            user_id = int(word)
            remaining_text = ' '.join([w for w in words if w != word])
            return user_id, remaining_text
    
    return None, text

def unmute_user(chat_id, user_id):
    try:
        bot.restrict_chat_member(
            chat_id,
            user_id,
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        if user_id in muted_users:
            del muted_users[user_id]
    except:
        pass

def contains_link(text):
    link_patterns = [
        r'https?://[^\s]+',
        r't\.me/[^\s]+',
        r'telegram\.me/[^\s]+',
        r'www\.[^\s]+',
        r'[a-zA-Z0-9\-]+\.(com|ru|org|net|io|app|xyz|info|site)[^\s]*'
    ]
    for pattern in link_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def contains_forbidden_username(text):
    forbidden = ['chat', 'bot']
    words = text.split()
    for word in words:
        if word.startswith('@'):
            username = word[1:].lower()
            for f in forbidden:
                if f in username:
                    return True
    return False

@bot.message_handler(commands=['start'])
def start(message):
    if message.chat.type != 'private':
        return
    bot.delete_message(message.chat.id, message.message_id)
    emoji = random.choice(emojis)
    user_captcha[message.chat.id] = emoji
    
    keyboard = telebot.types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for e in emojis:
        buttons.append(telebot.types.InlineKeyboardButton(e, callback_data=e))
    keyboard.add(*buttons)
    
    bot.send_message(
        message.chat.id,
        f'🔐 Для последующих действий в боте пройдите небольшую капчу.\nВыберите: {emoji}',
        reply_markup=keyboard
    )

@bot.callback_query_handler(func=lambda call: call.data in emojis)
def captcha_callback(call):
    if call.message.chat.id not in user_captcha:
        return
    
    if call.data == user_captcha[call.message.chat.id]:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
        link = bot.create_chat_invite_link(chat_id, member_limit=1)
        
        keyboard = telebot.types.InlineKeyboardMarkup()
        button = telebot.types.InlineKeyboardButton('Jess Chat', url=link.invite_link)
        keyboard.add(button)
        
        bot.send_message(
            call.message.chat.id,
            '*📲 Проверка пройдена!*\n👇 Нажмите на кнопки ниже для входа.\n🔗 Ссылки станут недействительными через 5 минут.',
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        
        Timer(300, bot.revoke_chat_invite_link, args=[chat_id, link.invite_link]).start()
        del user_captcha[call.message.chat.id]
    else:
        emoji = random.choice(emojis)
        user_captcha[call.message.chat.id] = emoji
        
        keyboard = telebot.types.InlineKeyboardMarkup(row_width=3)
        buttons = []
        for e in emojis:
            buttons.append(telebot.types.InlineKeyboardButton(e, callback_data=e))
        keyboard.add(*buttons)
        
        bot.edit_message_text(
            f'🔐 Капча была решена неверно,повторите попытку.\nВыберите: {emoji}',
            call.message.chat.id,
            call.message.message_id,
            reply_markup=keyboard
        )

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if message.chat.type == 'private':
        return
    
    if message.from_user.id != admin_id:
        bot.reply_to(message, '🔐 У вас недостаточно прав')
        return
    
    user_id, _ = extract_user_id(message)
    if not user_id:
        bot.reply_to(message, '🔐 Укажите данные о пользователе')
        return
    
    bot.ban_chat_member(message.chat.id, user_id)
    
    keyboard = telebot.types.InlineKeyboardMarkup()
    button = telebot.types.InlineKeyboardButton('Разблокировать', callback_data=f'unban_{user_id}')
    keyboard.add(button)
    
    bot.reply_to(message, '🔐 Пользователь заблокирован', reply_markup=keyboard)

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if message.chat.type == 'private':
        return
    
    if message.from_user.id != admin_id:
        bot.reply_to(message, '🔐 У вас недостаточно прав')
        return
    
    user_id, _ = extract_user_id(message)
    if not user_id:
        bot.reply_to(message, '🔐 Укажите данные о пользователе')
        return
    
    try:
        member = bot.get_chat_member(message.chat.id, user_id)
        if member.status == 'kicked':
            bot.unban_chat_member(message.chat.id, user_id)
            bot.reply_to(message, '🔐 Пользователь разблокирован')
        else:
            bot.reply_to(message, '🔐 Пользователь не был заблокирован в данном чате')
    except:
        bot.reply_to(message, '🔐 Пользователь не был заблокирован в данном чате')

@bot.message_handler(commands=['mute'])
def mute_user(message):
    if message.chat.type == 'private':
        return
    
    if message.from_user.id != admin_id:
        bot.reply_to(message, '🔐 У вас недостаточно прав')
        return
    
    user_id, remaining_text = extract_user_id(message)
    if not user_id:
        bot.reply_to(message, '🔐 Укажите данные о пользователе')
        return
    
    if not remaining_text:
        bot.restrict_chat_member(
            message.chat.id,
            user_id,
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        )
        muted_users[user_id] = True
        
        keyboard = telebot.types.InlineKeyboardMarkup()
        button = telebot.types.InlineKeyboardButton('Снять ограничение', callback_data=f'unmute_{user_id}')
        keyboard.add(button)
        
        bot.reply_to(message, '🔐 Пользователь замучен навсегда', reply_markup=keyboard)
        return
    
    total_seconds, reason = parse_time_and_reason(remaining_text)
    if total_seconds == 0:
        bot.reply_to(message, '🔐 Неверный формат времени')
        return
    
    until_date = datetime.now(msk_tz) + timedelta(seconds=total_seconds)
    
    bot.restrict_chat_member(
        message.chat.id,
        user_id,
        until_date=until_date,
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False
    )
    
    muted_users[user_id] = True
    Timer(total_seconds, unmute_user, args=[message.chat.id, user_id]).start()
    
    time_str = until_date.strftime('%H:%M %d.%m.%Y')
    response = f'🔐 Пользователь замучен до {time_str} (МСК)'
    if reason:
        response += f' по причине: {reason}'
    
    keyboard = telebot.types.InlineKeyboardMarkup()
    button = telebot.types.InlineKeyboardButton('Снять ограничение', callback_data=f'unmute_{user_id}')
    keyboard.add(button)
    
    bot.reply_to(message, response, reply_markup=keyboard)

@bot.message_handler(commands=['unmute'])
def unmute_command(message):
    if message.chat.type == 'private':
        return
    
    if message.from_user.id != admin_id:
        bot.reply_to(message, '🔐 У вас недостаточно прав')
        return
    
    user_id, _ = extract_user_id(message)
    if not user_id:
        bot.reply_to(message, '🔐 Укажите данные о пользователе')
        return
    
    try:
        member = bot.get_chat_member(message.chat.id, user_id)
        if not member.can_send_messages:
            unmute_user(message.chat.id, user_id)
            bot.reply_to(message, '🔐 Пользователь размучен')
        else:
            bot.reply_to(message, '🔐 У пользователя отсутствуют ограничения')
    except:
        bot.reply_to(message, '🔐 У пользователя отсутствуют ограничения')

@bot.callback_query_handler(func=lambda call: call.data.startswith('unmute_'))
def unmute_callback(call):
    if call.message.chat.type == 'private':
        return
    
    if call.from_user.id != admin_id:
        bot.answer_callback_query(call.id, 'У вас нет прав')
        return
    
    user_id = int(call.data.split('_')[1])
    unmute_user(call.message.chat.id, user_id)
    
    keyboard = telebot.types.InlineKeyboardMarkup()
    button = telebot.types.InlineKeyboardButton('Ограничения сняты', callback_data='disabled')
    keyboard.add(button)
    
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('unban_'))
def unban_callback(call):
    if call.message.chat.type == 'private':
        return
    
    if call.from_user.id != admin_id:
        bot.answer_callback_query(call.id, 'У вас нет прав')
        return
    
    user_id = int(call.data.split('_')[1])
    bot.unban_chat_member(call.message.chat.id, user_id)
    
    keyboard = telebot.types.InlineKeyboardMarkup()
    button = telebot.types.InlineKeyboardButton('Пользователь разблокирован', callback_data='disabled')
    keyboard.add(button)
    
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=keyboard)
    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['help'])
def help_command(message):
    if message.chat.type == 'private':
        bot.reply_to(message, 'Доступные команды:\n/start - запуск бота\n/help - помощь')
    else:
        if message.from_user.id == admin_id:
            bot.reply_to(message, 'Команды админа:\n/ban\n/unban\n/mute\n/unmute')
        else:
            bot.reply_to(message, 'У вас нет доступа к командам')

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if message.chat.type == 'private':
        return
    
    add_user_to_db(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    if message.from_user.id != admin_id and message.text:
        if contains_link(message.text) or contains_forbidden_username(message.text):
            try:
                bot.delete_message(message.chat.id, message.message_id)
                
                until_date = datetime.now(msk_tz) + timedelta(hours=1)
                bot.restrict_chat_member(
                    message.chat.id,
                    message.from_user.id,
                    until_date=until_date,
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                )
                
                muted_users[message.from_user.id] = True
                Timer(3600, unmute_user, args=[message.chat.id, message.from_user.id]).start()
                
                username = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.first_name}"
                
                keyboard = telebot.types.InlineKeyboardMarkup()
                button = telebot.types.InlineKeyboardButton('Снять ограничения', callback_data=f'unmute_{message.from_user.id}')
                keyboard.add(button)
                
                if contains_link(message.text):
                    text = f'🔐 {username} Отправлять ссылки запрещено. Вы заглушены на: 1 час'
                else:
                    text = f'🔐 {username} Запрещены ссылки на сторонние группы и боты, ознакомьтесь с правилами чата. Ограничение: 1 час'
                
                bot.send_message(
                    message.chat.id,
                    text,
                    reply_markup=keyboard
                )
            except Exception as e:
                print(f"Error in mute: {e}")

@bot.chat_member_handler()
def handle_new_member(update):
    if update.new_chat_member:
        user = update.new_chat_member.user
        add_user_to_db(user.id, user.username, user.first_name)

@app.route('/webhook', methods=['POST'])
def webhook():
    json_str = request.get_data().decode('UTF-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return 'ok', 200

if __name__ == '__main__':
    bot.remove_webhook()
    bot.set_webhook(url=f"https://{os.environ['RAILWAY_STATIC_URL']}/webhook")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
