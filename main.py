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
    
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    ]

    session = requests.Session()
    
    for attempt in range(1, 4):
        try:
            current_ua = random.choice(user_agents)
            session.headers.update({
                'User-Agent': current_ua,
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': BASE_URL + '/'
            })

            # 1. Инит
            session.get(BASE_URL, timeout=10)
            
            # 2. Запрос
            kyiv_now = get_kyiv_time()
            payload = {
                'method': 'getHomeNum',
                'data[0][name]': 'city', 'data[0][value]': REQ_CITY,
                'data[1][name]': 'street', 'data[1][value]': REQ_STREET,
                'data[2][name]': 'updateFact', 'data[2][value]': kyiv_now.strftime("%d.%m.%Y %H:%M")
            }
            
            time.sleep(random.uniform(1, 3))
            resp = session.post(AJAX_URL, data=payload, timeout=15)
            
            try:
                return resp.json()
            except:
                print("      ⚠️ Блокировка. Ждем...")
                time.sleep(5)
        except Exception as e:
            print(f"      ⚠️ Ошибка: {e}")
            time.sleep(5)
    
    return None

def process_data(json_resp):
    print("   ⚙️ Обработка данных...")
    
    # 1. Базовый статус (из шапки ответа)
    house_data = json_resp.get('data', {}).get(REQ_HOUSE_KEY)
    status_text = "❓ Невідомо"
    
    # Проверяем на АВАРИИ (экстренные)
    if house_data:
        raw_status = house_data.get('sub_type', '')
        if raw_status:
            status_text = f"⚠️ {raw_status}" # Авария приоритетнее всего
        else:
            status_text = "✅ Світло є (за графіком)"

    # 2. Слияние графиков (План + Факт)
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

    # 3. УТОЧНЕНИЕ СТАТУСА ПО ГРАФИКУ (НОВАЯ ЛОГИКА)
    # Если аварии нет, но по графику сейчас "черная зона", меняем статус на "Плановое отключение"
    try:
        if "⚠️" not in status_text: # Если нет экстренной аварии
            dt_now = get_kyiv_time()
            current_day = str(dt_now.isoweekday())
            # У DTEK ключи часов сдвинуты: 14:00-15:00 это ключ "15"
            current_hour_key = str(dt_now.hour + 1)
            
            # Смотрим в ИТОГОВЫЙ (слитый) график
            current_val = final_schedule.get(current_day, {}).get(current_hour_key, 'yes')
            
            print(f"   🕒 Проверка статуса на {dt_now.hour}:00. Значение в JSON: '{current_val}'")
            
            if current_val == 'no':
                status_text = "🔴 Планове відключення"
            elif current_val in ['maybe', 'mfirst', 'msecond']:
                status_text = "🔘 Сіра зона (можливе відключення)"
            elif current_val == 'yes':
                status_text = "✅ Світло є (за графіком)"
                
    except Exception as e:
        print(f"⚠️ Ошибка уточнения статуса: {e}")

    schedule_json_str = json.dumps(final_schedule, ensure_ascii=False)
    
    # Хеш
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
    if not raw_json: return 

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
            print(f"✅ УСПЕХ! Статус обновлен на: {clean_data['status']}")
        except Exception as e:
            print(f"   ❌ Ошибка записи: {e}")

if __name__ == "__main__":
    main()
