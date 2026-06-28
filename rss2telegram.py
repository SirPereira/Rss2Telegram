from bs4 import BeautifulSoup
from telebot import types
from time import gmtime
from dotenv import load_dotenv
import feedparser
import os
import re
import telebot
import telegraph
import time
import random
import requests
import sqlite3

def get_variable(variable):
    if not os.environ.get(f'{variable}'):
        try:
            var_file = open(f'{variable}.txt', 'r')
            return var_file.read()
        except FileNotFoundError:
            return None
    return os.environ.get(f'{variable}')

#load_dotenv()
URL = get_variable('URL')
DESTINATION = get_variable('DESTINATION')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
EMOJIS = os.environ.get('EMOJIS', '🗞,📰,🗒,🗓,📋,🔗,📝,🗃')
PARAMETERS = os.environ.get('PARAMETERS', False)
HIDE_BUTTON = os.environ.get('HIDE_BUTTON', False)
DRYRUN = os.environ.get('DRYRUN')
TOPIC = os.environ.get('TOPIC', False)
TELEGRAPH_TOKEN = os.environ.get('TELEGRAPH_TOKEN', False)

bot = telebot.TeleBot(BOT_TOKEN)

# Define o caminho do banco de dados no diretório atual do script
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rss2telegram.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS history (link TEXT UNIQUE)')
    conn.commit()
    conn.close()

def add_to_history(link):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO history (link) VALUES (?)', (link,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def check_history(link):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * from history WHERE link=?', (link,))
    data = cursor.fetchone()
    conn.close()
    return data

def firewall(text):
    try:
        rules = open(f'RULES.txt', 'r')
    except FileNotFoundError:
        return True
    result = None
    for rule in rules.readlines():
        opt, arg = rule.split(':')
        arg = arg.strip()
        if arg == 'ALL' and opt == 'DROP':
            result = False
        elif arg == 'ALL' and opt == 'ACCEPT':
            result = True
        elif arg.lower() in text.lower() and opt == 'DROP':
            result = False
        elif arg.lower() in text.lower() and opt == 'ACCEPT':
            result = True
    return result

def create_telegraph_post(topic):
    telegraph_auth = telegraph.Telegraph(
        access_token=f'{get_variable("TELEGRAPH_TOKEN")}'
    )
    response = telegraph_auth.create_page(
        f'{topic["title"]}',
        html_content=(
            f'{topic["summary"]}<br><br>'
            + f'<a href="{topic["link"]}">Ver original ({topic["site_name"]})</a>'
        ),
        author_name=f'{topic["site_name"]}'
    )
    return response["url"]

def send_message(topic, button):
    if DRYRUN == 'failure':
        return

    MESSAGE_TEMPLATE = os.environ.get(f'MESSAGE_TEMPLATE', False)

    if MESSAGE_TEMPLATE:
        MESSAGE_TEMPLATE = set_text_vars(MESSAGE_TEMPLATE, topic)
    else:
        MESSAGE_TEMPLATE = f'<b>{topic["title"]}</b>'

    if TELEGRAPH_TOKEN:
        iv_link = create_telegraph_post(topic)
        MESSAGE_TEMPLATE = f'<a href="{iv_link}">󠀠</a>{MESSAGE_TEMPLATE}'

    if not firewall(str(topic)):
        print(f'xxx {topic["title"]}')
        return

    btn_link = button
    if button:
        btn_link = types.InlineKeyboardMarkup()
        btn = types.InlineKeyboardButton(f'{button}', url=topic['link'])
        btn_link.row(btn)

    if HIDE_BUTTON or TELEGRAPH_TOKEN:
        for dest in DESTINATION.split(','):
            bot.send_message(dest, MESSAGE_TEMPLATE, parse_mode='HTML', reply_to_message_id=TOPIC)
    else:
        if topic['photo'] and not TELEGRAPH_TOKEN:
            response = requests.get(topic['photo'], headers = {'User-agent': 'Mozilla/5.1'})
            
            ext = '.jpg'
            if '.png' in topic['photo'].lower(): ext = '.png'
            elif '.webp' in topic['photo'].lower(): ext = '.webp'
            elif '.gif' in topic['photo'].lower(): ext = '.gif'
                
            filename = f'img{ext}'
            open(filename, 'wb').write(response.content)
            
            for dest in DESTINATION.split(','):
                photo_file = open(filename, 'rb')
                try:
                    bot.send_photo(dest, photo_file, caption=MESSAGE_TEMPLATE, parse_mode='HTML', reply_markup=btn_link, reply_to_message_id=TOPIC)
                except telebot.apihelper.ApiTelegramException:
                    topic['photo'] = False
                    send_message(topic, button)
        else:
            for dest in DESTINATION.split(','):
                bot.send_message(dest, MESSAGE_TEMPLATE, parse_mode='HTML', reply_markup=btn_link, disable_web_page_preview=True, reply_to_message_id=TOPIC)
    print(f'... {topic["title"]}')
    time.sleep(0.2)

def get_img(url):
    try:
        response = requests.get(url, headers = {'User-agent': 'Mozilla/5.1'}, timeout=3)
        html = BeautifulSoup(response.content, 'html.parser')
        photo = html.find('meta', {'property': 'og:image'})['content']
    except (TypeError, KeyError, IndexError, requests.exceptions.RequestException):
        photo = False
    return photo

def define_link(link, PARAMETERS):
    if PARAMETERS:
        if '?' in link:
            return f'{link}&{PARAMETERS}'
        return f'{link}?{PARAMETERS}'
    return f'{link}'

def set_text_vars(text, topic):
    cases = {
        'SITE_NAME': topic['site_name'],
        'TITLE': topic['title'],
        'SUMMARY': re.sub('<[^<]+?>', '', topic['summary']),
        'LINK': define_link(topic['link'], PARAMETERS),
        'EMOJI': random.choice(EMOJIS.split(","))
    }
    for word in re.split('{|}', text):
        try:
            text = text.replace(word, cases.get(word))
        except TypeError:
            continue
    return text.replace('\\n', '\n').replace('{', '').replace('}', '')

def check_topics(url):
    init_db()
    feed = feedparser.parse(url)
    try:
        source = feed['feed']['title']
    except KeyError:
        print(f'\nERRO: {url} não parece um feed RSS válido.')
        return
    print(f'\nChecando {source}:{url}')
    
    unsent_topics = []
    seen_links_this_run = set()
    
    for tpc in feed['items'][:20]:
        soup = BeautifulSoup(tpc.summary, 'html.parser')
        link_tag = soup.find('a', href=True)
        destination_link = link_tag['href'] if link_tag else tpc.links[0].href
        
        clean_link = destination_link.split('?')[0].rstrip('/')
        
        if clean_link in seen_links_this_run or check_history(clean_link):
            continue
            
        seen_links_this_run.add(clean_link)
        
        topic = {
            'site_name': feed['feed']['title'],
            'title': tpc.title.strip(),
            'summary': tpc.summary,
            'link': destination_link,
            'clean_link': clean_link,
            'date': tpc.get('published_parsed') or tpc.get('updated_parsed') or time.gmtime()
        }
        unsent_topics.append(topic)
        
    if not unsent_topics:
        print("Nenhuma postagem nova.")
        return

    # Garante a ordenação estrita: do mais recente para o mais antigo
    unsent_topics.sort(key=lambda x: x['date'], reverse=True)
    
    MAX_SENDS_PER_RUN = int(os.environ.get('MAX_SENDS_PER_RUN', 3)) 
    
    # Separação estratégica dos lotes
    topics_to_send = unsent_topics[:MAX_SENDS_PER_RUN]   # Os X mais recentes que vão para o Telegram
    topics_to_archive = unsent_topics[MAX_SENDS_PER_RUN:] # Os posts mais antigos que sobraram do corte
    
    # MELHORIA CRÍTICA: Queima os posts antigos descartados mandando-os direto pro histórico sem enviar nada
    for old_topic in topics_to_archive:
        add_to_history(old_topic['clean_link'])
        print(f'--- Ignorado e Arquivado (Antigo): {old_topic["title"]}')
    
    # Envia os posts selecionados na ordem cronológica correta (do mais antigo pro mais novo do lote de 3)
    topics_to_send.reverse() 
    
    for topic in topics_to_send:
        add_to_history(topic['clean_link'])
        topic['photo'] = get_img(topic['link'])
        
        BUTTON_TEXT = os.environ.get('BUTTON_TEXT', False)
        if BUTTON_TEXT:
            BUTTON_TEXT = set_text_vars(BUTTON_TEXT, topic)
        try:
            send_message(topic, BUTTON_TEXT)
        except telebot.apihelper.ApiTelegramException as e:
            print(e)
            pass

if __name__ == "__main__":
    for url in URL.split():
        check_topics(url)
