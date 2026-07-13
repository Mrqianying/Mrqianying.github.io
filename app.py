import sqlite3
import requests
import threading
from flask import Flask, request, jsonify, render_template, send_from_directory
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import time

app = Flask(__name__)

# ==================== 配置 ====================
DB_NAME = "weather.db"
AMAP_KEY = '02ad14a5f061f3c3e178cf48a0a15c30'

# ==================== 数据库操作 ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            date TEXT NOT NULL,
            temperature_max REAL,
            temperature_min REAL,
            temperature_avg REAL,
            precipitation REAL,
            wind_speed_max REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city, date)
        )
    """)
    conn.commit()
    conn.close()

def save_weather_records(city: str, lat: float, lon: float, daily_data: List[Dict]) -> int:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    count = 0
    for record in daily_data:
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO weather_records
                (city, latitude, longitude, date, temperature_max, temperature_min,
                 temperature_avg, precipitation, wind_speed_max)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                city, lat, lon,
                record["date"],
                record.get("temperature_max"),
                record.get("temperature_min"),
                record.get("temperature_avg"),
                record.get("precipitation"),
                record.get("wind_speed_max")
            ))
            count += 1
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    return count

def query_weather(city: str, start_date: str, end_date: str) -> List[Dict]:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM weather_records
        WHERE city = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
    """, (city, start_date, end_date))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_cities() -> List[str]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT city FROM weather_records ORDER BY city")
    cities = [row[0] for row in cursor.fetchall()]
    conn.close()
    return cities

def get_available_date_range(city: str) -> Optional[Tuple[str, str]]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(date), MAX(date) FROM weather_records WHERE city = ?", (city,))
    result = cursor.fetchone()
    conn.close()
    return result if result and result[0] else None

# ==================== 天气爬取 ====================
def fetch_historical_weather(lat: float, lon: float, start_date: str, end_date: str) -> List[Dict]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ["temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
                  "precipitation_sum", "wind_speed_10m_max"],
        "timezone": "auto"
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "daily" not in data:
            return []
        daily = data["daily"]
        dates = daily.get("time", [])
        max_t = daily.get("temperature_2m_max", [])
        min_t = daily.get("temperature_2m_min", [])
        avg_t = daily.get("temperature_2m_mean", [])
        prec = daily.get("precipitation_sum", [])
        wind = daily.get("wind_speed_10m_max", [])
        records = []
        for i in range(len(dates)):
            records.append({
                "date": dates[i],
                "temperature_max": max_t[i] if i < len(max_t) else None,
                "temperature_min": min_t[i] if i < len(min_t) else None,
                "temperature_avg": avg_t[i] if i < len(avg_t) else None,
                "precipitation": prec[i] if i < len(prec) else None,
                "wind_speed_max": wind[i] if i < len(wind) else None,
            })
        return records
    except Exception as e:
        print(f"天气API错误: {e}")
        return []

# ==================== 地理编码（仅高德地图） ====================
# 内置城市坐标（备用，优先使用高德API）
CITY_COORDS = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644), "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551), "成都": (30.5728, 104.0668),
    "武汉": (30.5928, 114.3055), "南京": (32.0603, 118.7969),
    "西安": (34.3416, 108.9398), "重庆": (29.4316, 106.9123),
    "长沙": (28.2282, 112.9388), "郑州": (34.7466, 113.6254),
    "青岛": (36.0671, 120.3826), "厦门": (24.4798, 118.0894),
    "香港": (22.3193, 114.1694), "台北": (25.0330, 121.5654),
    "天津": (39.0841, 117.2009), "苏州": (31.2990, 120.5853),
    "宁波": (29.8683, 121.5440), "合肥": (31.8206, 117.2272),
    "福州": (26.0745, 119.2965), "南昌": (28.6820, 115.8579),
    "济南": (36.6512, 117.1201), "石家庄": (38.0428, 114.5149),
    "太原": (37.8706, 112.5489), "呼和浩特": (40.8424, 111.7492),
    "沈阳": (41.8057, 123.4315), "长春": (43.8868, 125.3245),
    "哈尔滨": (45.8038, 126.5350), "乌鲁木齐": (43.8256, 87.6168),
    "兰州": (36.0611, 103.8343), "西宁": (36.6171, 101.7782),
    "银川": (38.4872, 106.2309), "拉萨": (29.6500, 91.1000),
    "昆明": (25.0409, 102.7123), "贵阳": (26.6477, 106.6302),
    "南宁": (22.8170, 108.3665), "海口": (20.0440, 110.1984),
    "澳门": (22.1987, 113.5439), "珠海": (22.2707, 113.5767),
    "中山": (22.5264, 113.3927), "佛山": (23.0288, 113.1214),
    "东莞": (23.0205, 113.7518), "惠州": (23.1107, 114.4160),
    "江门": (22.5767, 113.0815), "汕头": (23.3714, 116.7049),
    "湛江": (21.2707, 110.3583), "柳州": (24.3166, 109.4143),
    "桂林": (25.2736, 110.2903), "三亚": (18.2528, 109.5119),
}
GEOCODE_CACHE = {}

def geocode_city_amap(city_name: str) -> Optional[Tuple[float, float]]:
    """使用高德地图 API 将城市名转换为经纬度"""
    if not AMAP_KEY:
        raise ValueError("高德地图 API Key 未配置，请在 app.py 中设置 AMAP_KEY")
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "address": city_name,
        "key": AMAP_KEY,
        "output": "json"
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data['status'] == '1' and data['geocodes']:
            loc = data['geocodes'][0]['location'].split(',')
            return float(loc[1]), float(loc[0])
        else:
            return None
    except Exception as e:
        print(f"高德地理编码错误: {e}")
        return None

def geocode_city(city_name: str) -> Optional[Tuple[float, float]]:
    city_name = city_name.strip()
    if not city_name:
        return None
    # 缓存
    if city_name in GEOCODE_CACHE:
        return GEOCODE_CACHE[city_name]
    # 内置城市库（优先，避免API调用）
    if city_name in CITY_COORDS:
        coords = CITY_COORDS[city_name]
        GEOCODE_CACHE[city_name] = coords
        return coords

    # 调用高德 API
    coords = geocode_city_amap(city_name)
    if coords:
        GEOCODE_CACHE[city_name] = coords
    return coords

# ==================== Flask 路由 ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('.', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/api/locate', methods=['POST'])
def locate():
    data = request.get_json()
    city = data.get('city', '').strip()
    if not city:
        return jsonify({'success': False, 'error': '城市名不能为空'})
    try:
        coords = geocode_city(city)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)})
    if coords:
        lat, lon = coords
        return jsonify({'success': True, 'city': city, 'lat': lat, 'lon': lon})
    else:
        return jsonify({'success': False, 'error': f'未找到城市 "{city}"，请检查输入或高德Key是否有效'})

@app.route('/api/fetch', methods=['POST'])
def fetch():
    data = request.get_json()
    city = data.get('city')
    lat = data.get('lat')
    lon = data.get('lon')
    start = data.get('start_date')
    end = data.get('end_date')
    if not all([city, lat, lon, start, end]):
        return jsonify({'success': False, 'error': '参数不完整'})

    def do_fetch():
        records = fetch_historical_weather(lat, lon, start, end)
        if records:
            saved = save_weather_records(city, lat, lon, records)
            print(f"爬取完成：{city} 保存 {saved} 条")
        else:
            print(f"爬取失败：{city} 无数据")

    thread = threading.Thread(target=do_fetch)
    thread.daemon = True
    thread.start()
    return jsonify({'success': True, 'message': '爬取任务已启动，请稍后查询'})

@app.route('/api/query', methods=['POST'])
def query():
    data = request.get_json()
    city = data.get('city')
    start = data.get('start_date')
    end = data.get('end_date')
    if not city:
        return jsonify({'success': False, 'error': '城市名不能为空'})

    if not start or not end:
        range_ = get_available_date_range(city)
        if range_:
            start, end = range_
        else:
            return jsonify({'success': False, 'error': f'数据库中无 {city} 的数据'})

    records = query_weather(city, start, end)
    return jsonify({
        'success': True,
        'city': city,
        'start_date': start,
        'end_date': end,
        'records': records,
        'count': len(records)
    })

@app.route('/api/cities', methods=['GET'])
def cities():
    city_list = get_all_cities()
    result = []
    for c in city_list:
        range_ = get_available_date_range(c)
        result.append({'city': c, 'start': range_[0] if range_ else None, 'end': range_[1] if range_ else None})
    return jsonify({'success': True, 'cities': result})

if __name__ == '__main__':
    if not AMAP_KEY:
        print("⚠️  警告：未配置高德地图 AMAP_KEY，定位功能将无法使用。请在 app.py 中设置 AMAP_KEY。")
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)