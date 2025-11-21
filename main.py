import requests
import json
import hashlib
import gspread
import pytz
import time
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

# --- ФУНКЦИИ ---

def get_kyiv_time():
    return datetime.now(pytz.timezone('Europe/Kiev'))

def connect_to_sheet():
    print("   📊 Подключение к Google Таблице...")
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open(SPREADSHEET_NAME)
        return sh.worksheet(WORKSHEET_NAME)
    except Exception as e:
        print(f"   ❌ Ошибка подключения к Google: {e}")
        return None

def get_dtek_data_safe():
    print("   🌍 Запрос к сайту DTEK...")
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE_URL + '/'
    })

    try:
        # 1. Токен
        resp_init = session.get(BASE_URL, timeout=15)
        soup = BeautifulSoup(resp_init.text, 'html.parser')
        csrf_token = None
        csrf_inp = soup.find('input', {'name': '_csrf-dtek-dnem'})
        if csrf_inp: csrf_token = csrf_inp.get('value')
        
        # 2. Основной запрос
        kyiv_now = get_kyiv_time()
        payload = {
            'method': 'getHomeNum',
            'data[0][name]': 'city', 'data[0][value]': REQ_CITY,
            'data[1][name]': 'street', 'data[1][value]': REQ_STREET,
            'data[2][name]': 'updateFact', 'data[2][value]': kyiv_now.strftime("%d.%m.%Y %H:%M"),
            '_csrf-dtek-dnem': csrf_token
        }
        
        resp = session.post(AJAX_URL, data=payload, timeout=15)
        
        # Проверка статуса HTTP
        if resp.status_code != 200:
            print(f"   ❌ Ошибка сервера DTEK: HTTP {resp.status_code}")
            return None

        # Попытка разобрать JSON
        try:
            json_resp = resp.json()
        except json.JSONDecodeError:
            print("   ❌ DTEK вернул не JSON (возможно, сайт перегружен или заблокирован).")
            return None

        return json_resp

    except Exception as e:
        print(f"   ❌ Ошибка соединения: {e}")
        return None

def process_data(json_resp):
    print("   ⚙️ Обработка данных (План + Факт)...")
    
    # 1. Текстовый статус
    house_data = json_resp.get('data', {}).get(REQ_HOUSE_KEY)
    status_text = "❓ Невідомо"
    if house_data:
        raw_status = house_data.get('sub_type', '')
        if raw_status:
            status_text = f"⚠️ {raw_status}"
        else:
            status_text = "✅ Світло є (за графіком)"

    # 2. График (Слияние)
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
                except:
                    pass

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
    
    # ШАГ 1: Скачиваем
    raw_json = get_dtek_data_safe()
    if not raw_json:
        print("⚠️ Данные не получены. Пропускаем запись в таблицу.")
        return # Выходим, чтобы не затереть таблицу ошибкой

    # ШАГ 2: Обрабатываем
    clean_data = process_data(raw_json)
    
    # ШАГ 3: Пишем
    sheet = connect_to_sheet()
    if sheet:
        try:
            print("   💾 Запись в Таблицу (строка 2)...")
            row_values = [
                clean_data['hash'],
                clean_data['timestamp'],
                clean_data['status'],
                clean_data['group'],
                clean_data['schedule']
            ]
            # Обновляем диапазон A2:E2
            sheet.update(range_name='A2:E2', values=[row_values])
            print(f"✅ УСПЕХ! Таблица обновлена. Статус: {clean_data['status']}")
        except Exception as e:
            print(f"   ❌ Ошибка при записи в таблицу: {e}")

if __name__ == "__main__":
    main()
