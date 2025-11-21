import requests
import json
import hashlib
import gspread
import pytz  # <--- НОВАЯ БИБЛИОТЕКА
from google.oauth2.service_account import Credentials
from datetime import datetime
from bs4 import BeautifulSoup

# --- НАЛАШТУВАННЯ ---
# Мы берем ключи из переменных окружения (для GitHub) или файла (локально)
# Но для простоты оставляем логику как была, GitHub сам создаст файл service_key.json
SERVICE_ACCOUNT_FILE = 'service_key.json'
SPREADSHEET_NAME = 'ГрафикОтключенийБот'
WORKSHEET_NAME = 'Data'

BASE_URL = "https://www.dtek-dnem.com.ua/ua"
AJAX_URL = "https://www.dtek-dnem.com.ua/ua/ajax"

REQ_CITY = "м. Дніпро"
REQ_STREET = "вул. Полігонна"
REQ_HOUSE_KEY = "10/Д"
TARGET_GROUP = "GPV5.1"

# --- ФУНКЦИЯ ВРЕМЕНИ ---
def get_kyiv_time():
    # Определяем часовой пояс Киева
    tz = pytz.timezone('Europe/Kiev')
    return datetime.now(tz)

def connect_to_sheet():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scope)
        client = gspread.authorize(creds)
        sh = client.open(SPREADSHEET_NAME)
        return sh.worksheet(WORKSHEET_NAME)
    except Exception as e:
        print(f"❌ Ошибка Google Sheets: {e}")
        return None

def get_full_data():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': BASE_URL + '/'
    })

    try:
        # 1. Токен
        resp_init = session.get(BASE_URL, timeout=10)
        soup = BeautifulSoup(resp_init.text, 'html.parser')
        csrf_token = None
        csrf_inp = soup.find('input', {'name': '_csrf-dtek-dnem'})
        if csrf_inp: csrf_token = csrf_inp.get('value')
        
        # 2. Запрос данных
        kyiv_now = get_kyiv_time()
        payload = {
            'method': 'getHomeNum',
            'data[0][name]': 'city', 'data[0][value]': REQ_CITY,
            'data[1][name]': 'street', 'data[1][value]': REQ_STREET,
            'data[2][name]': 'updateFact', 'data[2][value]': kyiv_now.strftime("%d.%m.%Y %H:%M"),
            '_csrf-dtek-dnem': csrf_token
        }
        
        resp = session.post(AJAX_URL, data=payload, timeout=15)
        json_resp = resp.json()
        
        # 3. Текущий Статус (Текстовый, для ячейки C2)
        house_data = json_resp.get('data', {}).get(REQ_HOUSE_KEY)
        status_text = "❓ Невідомо"
        if house_data:
            raw_status = house_data.get('sub_type', '')
            if raw_status:
                status_text = f"⚠️ {raw_status}" # Если есть авария или экстренное
            else:
                status_text = "✅ Світло є (за графіком)"

        # =================================================================
        # 🚀 ЛОГИКА СЛИЯНИЯ: ПЛАН + ФАКТ
        # =================================================================
        
        # Шаг А: Берем "Шаблон" (Preset) - он может быть пустым или зеленым
        full_preset = json_resp.get('preset', {})
        final_schedule = full_preset.get('data', {}).get(TARGET_GROUP, {})
        
        # Шаг Б: Проверяем "Факт" (Fact) - это то, что сейчас на сайте
        fact_section = json_resp.get('fact', {})
        fact_data = fact_section.get('data', {}) 
        
        if fact_data:
            print("🔎 Найдены фактические данные! Применяем к графику...")
            
            # fact_data — это словарь, где ключи — это Timestamp даты (например, '1763589600')
            for unix_ts, groups_data in fact_data.items():
                
                # Проверяем, есть ли наша группа (GPV5.1) в этом дне
                if TARGET_GROUP in groups_data:
                    try:
                        # Вычисляем, какой это день недели (1=Пн, ... 4=Чт, ... 7=Нд)
                        ts = int(unix_ts)
                        dt = datetime.fromtimestamp(ts, pytz.timezone('Europe/Kiev'))
                        day_key = str(dt.isoweekday()) 
                        
                        # Забираем "Черный/Серый" график из факта
                        fact_schedule_for_day = groups_data[TARGET_GROUP]
                        
                        # ПЕРЕЗАПИСЫВАЕМ день в общем графике
                        final_schedule[day_key] = fact_schedule_for_day
                        
                        print(f"⚡️ [FACT] График на день {day_key} ({dt.strftime('%d.%m')}) заменен реальными данными (с сайта).")
                        
                    except Exception as e:
                        print(f"⚠️ Ошибка обработки даты факта: {e}")
        else:
            print("ℹ️ Фактических данных нет (или сайт их не отдал). Остаемся на шаблоне.")

        # Сериализуем готовый гибридный график в строку
        schedule_json_str = json.dumps(final_schedule, ensure_ascii=False)
            
        # =================================================================

        # 5. Генерация Хеша (чтобы понимать, изменилось ли что-то)
        content_to_hash = f"{status_text}{schedule_json_str}{TARGET_GROUP}"
        data_hash = hashlib.md5(content_to_hash.encode('utf-8')).hexdigest()
        
        return {
            'hash': data_hash,
            'timestamp': kyiv_now.strftime("%Y-%m-%d %H:%M:%S"),
            'status': status_text,
            'schedule': schedule_json_str,
            'group': TARGET_GROUP
        }

    except Exception as e:
        print(f"❌ Ошибка API: {e}")
        return None

def main():
    # Выводим время запуска (Киевское)
    print(f"--- Запуск {get_kyiv_time().strftime('%H:%M')} (Kyiv Time) ---")
    
    data = get_full_data()
    
    if data:
        sheet = connect_to_sheet()
        if sheet:
            try:
                # Оновлюємо рядок 2 ПОВНІСТЮ (A2:E2)
                row_values = [
                    data['hash'],
                    data['timestamp'],
                    data['status'],
                    data['group'],
                    data['schedule']
                ]
                
                sheet.update(range_name='A2:E2', values=[row_values])
                
                print(f"✅ Оновлено! Хеш: {data['hash']}, Статус: {data['status']}")
            except Exception as e:
                print(f"❌ Ошибка запису: {e}")

if __name__ == "__main__":
    main()
