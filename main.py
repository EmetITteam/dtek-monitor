import requests
import json
import hashlib
import gspread
import pytz
import time
import random
from google.oauth2.service_account import Credentials
from datetime import datetime
from bs4 import BeautifulSoup

# --- НАСТРОЙКИ ---
SERVICE_ACCOUNT_FILE = 'service_key.json'
SPREADSHEET_NAME = 'ГрафикОтключенийБот'
WORKSHEET_NAME = 'Data'

BASE_URL = "https://www.dtek-dnem.com.ua/ua"
AJAX_URL = "https://www.dtek-dnem.com.ua/ua/ajax"

REQ_CITY = "м. Дніпро"
REQ_STREET = "вул. Полігонна"
REQ_HOUSE_KEY = "10/Д"
TARGET_GROUP = "GPV5.1"

def get_kyiv_time():
    return datetime.now(pytz.timezone('Europe/Kiev'))

def connect_to_sheet():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open(SPREADSHEET_NAME)
        return sh.worksheet(WORKSHEET_NAME)
    except Exception as e:
        print(f"   ❌ Ошибка подключения к Google: {e}")
        return None

def get_dtek_data_stealth():
    print("   🌍 Запрос к сайту DTEK (Режим маскировки)...")
    
    # Список User-Agent, чтобы менять "личность" при каждом запросе
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'
    ]

    session = requests.Session()
    
    # 3 ПОПЫТКИ пробиться
    for attempt in range(1, 4):
        try:
            # Настраиваем заголовки как реальный браузер
            current_ua = random.choice(user_agents)
            session.headers.update({
                'User-Agent': current_ua,
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7', # Говорим, что мы из Украины
                'Referer': BASE_URL + '/',
                'Origin': BASE_URL,
                'X-Requested-With': 'XMLHttpRequest',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin'
            })

            print(f"   🔄 Попытка {attempt}/3...")
            
            # 1. Получаем Cookies и Token
            resp_init = session.get(BASE_URL, timeout=10)
            soup = BeautifulSoup(resp_init.text, 'html.parser')
            csrf_token = None
            csrf_inp = soup.find('input', {'name': '_csrf-dtek-dnem'})
            if csrf_inp: csrf_token = csrf_inp.get('value')
            
            # 2. Делаем запрос
            kyiv_now = get_kyiv_time()
            payload = {
                'method': 'getHomeNum',
                'data[0][name]': 'city', 'data[0][value]': REQ_CITY,
                'data[1][name]': 'street', 'data[1][value]': REQ_STREET,
                'data[2][name]': 'updateFact', 'data[2][value]': kyiv_now.strftime("%d.%m.%Y %H:%M"),
                '_csrf-dtek-dnem': csrf_token
            }
            
            # Случайная пауза перед AJAX запросом (как человек)
            time.sleep(random.uniform(1, 3))
            
            resp = session.post(AJAX_URL, data=payload, timeout=15)
            
            # Проверяем ответ
            try:
                json_resp = resp.json()
                # Если в ответе есть данные — успех!
                if 'data' in json_resp or 'preset' in json_resp:
                    return json_resp
            except json.JSONDecodeError:
                # Если вернулся HTML с ошибкой
                if attempt < 3:
                    print("      ⚠️ DTEK заблокировал запрос. Ждем 5 сек...")
                    time.sleep(5)
                    continue # Пробуем еще раз
                else:
                    print("      ❌ Не удалось получить JSON. Ответ сервера (первые 100 симв.):")
                    print(f"      {resp.text[:100]}")
                    return None

        except Exception as e:
            print(f"      ⚠️ Ошибка соединения: {e}")
            time.sleep(5)
    
    return None

def process_data(json_resp):
    print("   ⚙️ Обработка данных...")
    
    # 1. Статус
    house_data = json_resp.get('data', {}).get(REQ_HOUSE_KEY)
    status_text = "❓ Невідомо"
    if house_data:
        raw_status = house_data.get('sub_type', '')
        status_text = f"⚠️ {raw_status}" if raw_status else "✅ Світло є (за графіком)"

    # 2. Слияние графиков
    full_preset = json_resp.get('preset', {})
    final_schedule = full_preset.get('data', {}).get(TARGET_GROUP, {})
    
    fact_section = json_resp.get('fact', {})
    fact_data = fact_section.get('data', {}) 
    
    if fact_data:
        for unix_ts, groups_data in fact_data.items():
            if TARGET_GROUP in groups_data:
                try:
                    ts = int(unix_ts)
                    dt = datetime.fromtimestamp(ts, pytz.timezone('Europe/Kiev'))
                    day_key = str(dt.isoweekday()) 
                    final_schedule[day_key] = groups_data[TARGET_GROUP]
                except: pass

    schedule_json_str = json.dumps(final_schedule, ensure_ascii=False)
    content_to_hash = f"{status_text}{schedule_json_str}{TARGET_GROUP}"
    data_hash = hashlib.md5(content_to_hash.encode('utf-8')).hexdigest()
    
    return {
        'hash': data_hash,
        'timestamp': get_kyiv_time().strftime("%Y-%m-%d %H:%M:%S"),
        'status': status_text,
        'schedule': schedule_json_str,
        'group': TARGET_GROUP
    }

def main():
    print(f"--- ЗАПУСК {get_kyiv_time().strftime('%H:%M')} (Kyiv Time) ---")
    
    raw_json = get_dtek_data_stealth()
    
    if not raw_json:
        print("❌ Все попытки исчерпаны. Данные не получены.")
        # Не пишем ошибку в таблицу, чтобы не пугать бота, просто ждем следующего запуска по расписанию
        return 

    clean_data = process_data(raw_json)
    
    sheet = connect_to_sheet()
    if sheet:
        try:
            row_values = [
                clean_data['hash'],
                clean_data['timestamp'],
                clean_data['status'],
                clean_data['group'],
                clean_data['schedule']
            ]
            sheet.update(range_name='A2:E2', values=[row_values])
            print(f"✅ УСПЕХ! Таблица обновлена.")
        except Exception as e:
            print(f"   ❌ Ошибка записи: {e}")

if __name__ == "__main__":
    main()
