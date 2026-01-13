import logging
import threading
import requests
from datetime import datetime, timedelta
import time
import random
import json
import os
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Tuple, List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor
from kivy.utils import platform

from kivy.clock import Clock
from kivy.lang import Builder
from kivy.properties import StringProperty, NumericProperty, ListProperty, BooleanProperty, ObjectProperty
from kivy.metrics import dp
from kivy.core.text import LabelBase
from kivy.animation import Animation
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.image import AsyncImage
from kivymd.uix.floatlayout import MDFloatLayout
from kivymd.uix.scrollview import MDScrollView

from kivymd.app import MDApp
from kivymd.uix.card import MDCard
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDIconButton, MDRaisedButton, MDFlatButton
from kivymd.uix.snackbar import Snackbar
from kivymd.uix.spinner import MDSpinner
from kivymd.uix.dialog import MDDialog
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.list import OneLineListItem

try:
    from kivymd.uix.icon import MDIcon
except ImportError:
    from kivymd.uix.label import MDIcon

# ==========================================
# [配置區]
# ==========================================

APP_VERSION = "0.52"  # Version bumped for UI Fix

logging.basicConfig(
    level=logging.WARNING, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_TIMEOUT = 10
CACHE_DURATION = 900
MAX_WORKERS = 3
UPDATE_INTERVAL_TIME = 1.0
PINNED_SLOTS = 6
MAX_FORECAST_DAYS = 3

MSG_NO_API_KEY = "Error: API KEY not set in config.json"
MSG_INVALID_KEY = "Error: Invalid API KEY format"
MSG_INVALID_CITY = "Error: Invalid City Name"
MSG_TIMEOUT = "Connection Timeout"
MSG_NETWORK_ERR = "Network Error"
MSG_SERVER_ERR = "Server Error (Invalid JSON)"

if platform == 'android':
    from android.storage import app_storage_path
    STORAGE_DIR = app_storage_path()
else:
    STORAGE_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PINNED_FILE = os.path.join(STORAGE_DIR, "pinned_cities.json")
CITIES_FILE = os.path.join(BASE_DIR, "cities.json")
COUNTRIES_FILE = os.path.join(BASE_DIR, "countries.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# ==========================================
# [資料結構] DTO
# ==========================================

@dataclass
class ProcessedWeather:
    temp: float
    desc: str
    weather_code: int
    is_day: int
    timezone_offset: int
    timezone_str: str
    aqi_text: str
    aqi_color: Tuple[float, float, float, float]
    flag_url: str 
    forecast_list: List[Dict[str, Any]] = field(default_factory=list)
    
    bg_color: List[float] = None
    icon_name: str = None
    icon_color: List[float] = None
    weather_text: str = None

WEATHER_CONFIG = {
    "sunny": { "bg": [1.0, 0.98, 0.8, 1], "icon": "weather-sunny", "icon_color": [1, 0.65, 0, 1] },
    "clear_night": { "bg": [0.8, 0.85, 0.95, 1], "icon": "weather-night", "icon_color": [1, 0.95, 0.1, 1] },
    "cloudy": { "bg": [0.85, 0.9, 0.92, 1], "icon": "weather-cloudy", "icon_color": [0.5, 0.5, 0.6, 1] },
    "overcast": { "bg": [0.75, 0.78, 0.8, 1], "icon": "weather-fog", "icon_color": [0.4, 0.4, 0.4, 1] },
    "rain": { "bg": [0.7, 0.8, 0.9, 1], "icon": "weather-pouring", "icon_color": [0.2, 0.4, 0.8, 1] },
    "thunder": { "bg": [0.6, 0.6, 0.7, 1], "icon": "weather-lightning-rainy", "icon_color": [0.3, 0.2, 0.4, 1] },
    "snow": { "bg": [0.95, 0.98, 1.0, 1], "icon": "weather-snowy-heavy", "icon_color": [0.6, 0.7, 0.8, 1] },
    "sleet": { "bg": [0.85, 0.9, 0.95, 1], "icon": "weather-snowy-rainy", "icon_color": [0.4, 0.5, 0.7, 1] },
    "default": { "bg": [0.96, 0.96, 0.96, 1], "icon": "help-circle-outline", "icon_color": [0.5, 0.5, 0.5, 1] }
}

WEATHER_CODE_MAP = {}
def _register_codes(codes, category):
    for c in codes: WEATHER_CODE_MAP[c] = category

_register_codes([1000], "sunny_or_night")
_register_codes([1003, 1006], "cloudy")
_register_codes([1009, 1030, 1135, 1147], "overcast")
_register_codes([1063, 1150, 1153, 1180, 1183, 1186, 1189, 1192, 1195, 1240, 1243, 1246, 1168, 1171, 1198, 1201], "rain")
_register_codes([1087, 1273, 1276, 1279, 1282], "thunder")
_register_codes([1066, 1114, 1117, 1210, 1213, 1216, 1219, 1222, 1225, 1255, 1258], "snow")
_register_codes([1069, 1072, 1204, 1207, 1237, 1249, 1252, 1261, 1264], "sleet")

def get_weather_config_by_code(code: int, is_day: int) -> Tuple[Dict, str]:
    category = WEATHER_CODE_MAP.get(code, "default")
    if category == "sunny_or_night":
        return (WEATHER_CONFIG["sunny"] if is_day == 1 else WEATHER_CONFIG["clear_night"]), ("sunny" if is_day == 1 else "clear_night")
    return WEATHER_CONFIG.get(category, WEATHER_CONFIG["default"]), category

# ==========================================
# [工具函數]
# ==========================================

def get_api_key() -> str:
    if "WEATHER_API_KEY" in os.environ:
        return os.environ["WEATHER_API_KEY"]
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return str(data.get("api_key", "")).strip()
        except: pass
    return ""

def validate_api_key(key: str) -> bool:
    return bool(key and len(key) >= 30)

def load_json_data(filepath: str, default_value: Any) -> Any:
    if not os.path.exists(filepath): return default_value
    try:
        with open(filepath, "r", encoding="utf-8") as f: return json.load(f)
    except: return default_value

def save_json_data(filepath: str, data: Any):
    try:
        with open(filepath, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
    except: pass

CITY_DATA = load_json_data(CITIES_FILE, {})
COUNTRY_CODE_MAP = load_json_data(COUNTRIES_FILE, {})
CITY_LIST = sorted([city for cities in CITY_DATA.values() for city in cities])
CITY_SET = set(CITY_LIST)

def is_valid_city(city: str) -> bool:
    if not city or not isinstance(city, str): return False
    return city in CITY_SET

try:
    LabelBase.register(name="TC_Font", fn_regular="NotoSansTC-Regular.ttf", fn_bold="NotoSansTC-Bold.ttf")
    LabelBase.register(name="Pacifico", fn_regular="Pacifico-Regular.ttf")
except Exception as e:
    logger.error(f"Font loading failed: {e}")

# ==========================================
# [邏輯類別] Cache & Marquee
# ==========================================

class WeatherCache:
    def __init__(self, duration=CACHE_DURATION, max_size=100):
        self.data = OrderedDict()
        self.duration = duration
        self.max_size = max_size
        self.lock = threading.Lock()
    
    def get(self, city: str) -> Optional[ProcessedWeather]:
        with self.lock:
            if city in self.data:
                content, timestamp = self.data[city]
                if time.time() - timestamp < self.duration:
                    self.data.move_to_end(city)
                    return content
                else:
                    del self.data[city]
        return None
    
    def set(self, city: str, content: ProcessedWeather):
        with self.lock:
            if city in self.data:
                del self.data[city]
            elif len(self.data) >= self.max_size:
                self.data.popitem(last=False)
            self.data[city] = (content, time.time())

    def clean_expired(self):
        with self.lock:
            now = time.time()
            expired_keys = [k for k, v in self.data.items() if now - v[1] >= self.duration]
            for k in expired_keys: del self.data[k]

class MarqueeLabel(MDLabel):
    full_text = StringProperty("")
    window_size = NumericProperty(4)
    limit_threshold = NumericProperty(5)
    _index = 0
    _event = None

    def on_full_text(self, instance, value):
        self.trigger_update()
    
    # [V0.51 Fix] 增加清理機制，防止背景資源浪費
    def on_parent(self, widget, parent):
        if parent is None and self._event:
            self._event.cancel()
            self._event = None

    def trigger_update(self):
        if self._event:
            self._event.cancel()
            self._event = None
        
        self.text = self.full_text
        if len(self.full_text) > self.limit_threshold:
            self._index = 0
            self.text = self.full_text[:self.window_size]
            self._event = Clock.schedule_interval(self.update_text, 0.4)
        else:
            self.text = self.full_text

    def update_text(self, dt):
        if not self.full_text: return
        self._index += 1
        if self._index > len(self.full_text) - self.window_size + 1:
            self._index = 0 
        
        end_idx = self._index + self.window_size
        display = self.full_text[self._index : end_idx]
        if len(display) < self.window_size:
            display += " " * (self.window_size - len(display))
        self.text = display

# ==========================================
# [介面設計] KV
# ==========================================
KV = f'''
<RotatingIcon@MDIcon>:
    angle: 0
    canvas.before:
        PushMatrix
        Rotate:
            angle: root.angle
            origin: self.center
    canvas.after:
        PopMatrix

<LocationCard>:
    orientation: "vertical"
    padding: "0dp"
    size_hint: 1, 1
    elevation: 0
    radius: [25]
    md_bg_color: root.current_bg_color
    ripple_behavior: True
    on_release: app.show_forecast_dialog(self)
    
    MDFloatLayout:
        AsyncImage:
            source: root.flag_url
            size_hint: None, None
            size: "36dp", "24dp"
            pos_hint: {{"x": 0.05, "y": 0.05}}
            opacity: 0.9 if root.flag_url else 0

        MDIconButton:
            icon: "pin" if root.is_pinned else "pin-outline"
            theme_text_color: "Custom"
            text_color: [0.3, 0.3, 0.3, 1] if root.is_pinned else [0.7, 0.7, 0.7, 1]
            pos_hint: {{"top": 0.98, "right": 0.98}}
            on_release: root.toggle_pin()
            user_font_size: "24sp"
            z_index: 1 

        MDSpinner:
            size_hint: None, None
            size: dp(46), dp(46)
            pos_hint: {{'center_x': .5, 'center_y': .5}}
            active: root.is_loading
            color: 0.2, 0.2, 0.2, 1

        MDBoxLayout:
            orientation: "vertical"
            padding: "0dp"
            spacing: "0dp"
            pos_hint: {{"center_x": 0.5, "center_y": 0.5}}
            opacity: 0 if root.is_loading else 1

            AnchorLayout:
                anchor_x: "center"
                anchor_y: "center"
                size_hint_y: 0.35
                padding: [0, 15, 0, 15] 
                
                RotatingIcon:
                    id: weather_icon_widget
                    icon: root.weather_icon
                    theme_text_color: "Custom"
                    text_color: root.weather_icon_color
                    font_size: "80dp"
                    size_hint: None, None
                    size: self.texture_size

            MDBoxLayout:
                orientation: "vertical"
                size_hint_y: 0.65
                padding: "10dp"
                spacing: "2dp"

                MDLabel:
                    text: root.location_name
                    halign: "center"
                    font_name: "Pacifico"
                    font_size: "40sp" if len(root.location_name) <= 6 else ("32sp" if len(root.location_name) <= 10 else "24sp")
                    size_hint_y: 0.35
                    theme_text_color: "Primary"

                MDLabel:
                    text: root.time_text
                    halign: "center"
                    font_name: "Pacifico"
                    font_size: "34sp"
                    size_hint_y: 0.35
                    theme_text_color: "Custom"
                    text_color: 0.2, 0.2, 0.2, 1

                MDBoxLayout:
                    orientation: "vertical"
                    size_hint_y: 0.3
                    
                    MDLabel:
                        text: root.weather_text
                        halign: "center"
                        font_style: "Subtitle1"
                        font_name: "TC_Font"
                        bold: True
                        theme_text_color: "Secondary"
                        
                    MDLabel:
                        text: root.aqi_text
                        halign: "center"
                        font_style: "Caption"
                        theme_text_color: "Custom"
                        text_color: root.aqi_color
                        font_name: "TC_Font"
                        bold: True

<ForecastRowItem>:
    orientation: "horizontal"
    size_hint_y: None
    height: "50dp"
    padding: "5dp"
    
    date_text: ""
    desc_text: ""
    temp_text: ""
    
    MDLabel:
        text: root.date_text
        size_hint_x: 0.2
        halign: "center"
        font_name: "TC_Font"
        theme_text_color: "Primary"
    
    MarqueeLabel:
        full_text: root.desc_text
        window_size: 4
        limit_threshold: 5
        size_hint_x: 0.45
        halign: "center"
        font_name: "TC_Font"
        theme_text_color: "Secondary"
    
    MDLabel:
        text: root.temp_text
        size_hint_x: 0.35
        halign: "right"
        font_name: "TC_Font"
        theme_text_color: "Primary"

<ForecastContent>:
    orientation: "vertical"
    spacing: "12dp"
    size_hint_y: None
    height: "320dp"
    current_temp: ""

    MDBoxLayout:
        orientation: "vertical"
        size_hint_y: None
        height: "100dp"
        padding: "15dp"
        spacing: "10dp"
        canvas.before:
            Color:
                rgba: 0.95, 0.95, 0.95, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [15]
        
        MDLabel:
            text: "目前天氣"
            font_style: "Caption"
            font_name: "TC_Font"
            theme_text_color: "Secondary"
            size_hint_y: None
            height: "15dp"
        
        MDBoxLayout:
            orientation: "horizontal"
            
            MDBoxLayout:
                orientation: "vertical"
                size_hint_x: 0.65
                valign: "center"
                adaptive_height: True
                pos_hint: {{"center_y": 0.5}}

                MarqueeLabel:
                    full_text: root.current_desc
                    window_size: 6
                    font_style: "H6"
                    font_name: "TC_Font"
                    bold: True
                    halign: "left"
                    size_hint_y: None
                    height: "30dp"
                    theme_text_color: "Primary"

                MDLabel:
                    text: root.current_temp
                    font_style: "H4"
                    font_name: "TC_Font"
                    bold: True
                    halign: "left"
                    size_hint_y: None
                    height: "40dp"
                    theme_text_color: "Primary"

            MDIcon:
                icon: root.current_icon
                theme_text_color: "Custom"
                text_color: root.current_icon_color
                font_size: "56dp"
                size_hint_x: 0.35
                halign: "right"
                pos_hint: {{"center_y": 0.5}}

    MDLabel:
        text: "未來 3 日預報"
        font_style: "Subtitle2"
        font_name: "TC_Font"
        size_hint_y: None
        height: "30dp"

    MDBoxLayout:
        id: forecast_list
        orientation: "vertical"
        spacing: "8dp"
'''

# ==========================================
# 輔助類別
# ==========================================

class ForecastRowItem(MDBoxLayout):
    date_text = StringProperty("")
    desc_text = StringProperty("")
    temp_text = StringProperty("")

class ForecastContent(MDBoxLayout):
    current_desc = StringProperty("")
    current_temp = StringProperty("")
    current_icon = StringProperty("help")
    current_icon_color = ListProperty([0,0,0,1])

class LocationCard(MDCard):
    location_name = StringProperty("")
    time_text = StringProperty("--:--:--")
    weather_text = StringProperty("Wait...")
    weather_desc = StringProperty("")
    weather_temp = StringProperty("")
    
    timezone_text = StringProperty("") 
    aqi_text = StringProperty("")
    aqi_color = ListProperty([0, 0, 0, 1])
    time_offset = NumericProperty(0)
    current_bg_color = ListProperty([0.96, 0.96, 0.96, 1])
    
    flag_url = StringProperty("")
    weather_icon = StringProperty("help-circle-outline")
    weather_icon_color = ListProperty([0.5, 0.5, 0.5, 1])
    
    is_pinned = BooleanProperty(False)
    is_loading = BooleanProperty(False) 
    current_anim = None
    forecast_data = ListProperty([])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def on_parent(self, widget, parent):
        if parent is None:
            self.stop_animation()

    def stop_animation(self):
        if self.current_anim:
            try: self.current_anim.cancel(self.ids.weather_icon_widget)
            except: pass
            finally: self.current_anim = None
        
        try:
            self.ids.weather_icon_widget.angle = 0
            self.ids.weather_icon_widget.canvas.ask_update()
        except: pass
    
    def toggle_pin(self):
        self.is_pinned = not self.is_pinned
        MDApp.get_running_app().update_pinned_list()

class DashboardApp(MDApp):
    dialog = None 
    menu = None
    pending_tasks = set()

    def build(self):
        self.theme_cls.primary_palette = "Teal"
        self.icon = "icon.png"
        
        if platform == 'android':
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.INTERNET])

        Builder.load_string(KV)
        
        self.weather_cache = WeatherCache(duration=CACHE_DURATION)
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.api_key = get_api_key()

        screen = Builder.load_string(f'''
MDBoxLayout:
    orientation: "vertical"
    md_bg_color: 0.98, 0.98, 0.98, 1
    
    MDLabel:
        text: "V.{APP_VERSION} | Dev. WKJ"
        halign: "center"
        font_name: "TC_Font"
        font_size: "12sp"
        size_hint_y: None
        height: "30dp"
        theme_text_color: "Secondary"
        
    MDScrollView:
        id: scroll_view
        
        MDGridLayout:
            id: grid
            cols: 2
            spacing: "15dp"
            padding: "15dp"
            adaptive_height: True
            row_default_height: "300dp"
            row_force_default: True
''')
        self.grid = screen.ids.grid
        self.init_cards()
        
        Clock.schedule_interval(self.update_time, UPDATE_INTERVAL_TIME)
        Clock.schedule_once(self.start_weather_updates, 1)
        Clock.schedule_interval(self.start_weather_updates, CACHE_DURATION)
        Clock.schedule_interval(lambda dt: self.weather_cache.clean_expired(), 300)
        
        return screen

    def on_stop(self):
        self.executor.shutdown(wait=False)

    def on_pause(self): return True
    def on_resume(self): pass

    def init_cards(self):
        pinned_cities = load_json_data(PINNED_FILE, [])
        final_list_data = [{"city": c, "pinned": True} for c in pinned_cities]
        slots_needed = PINNED_SLOTS - len(final_list_data)
        if slots_needed > 0:
            candidates = [c for c in CITY_LIST if c not in pinned_cities]
            if not candidates: candidates = ["Taipei", "London", "New York", "Tokyo", "Paris", "Sydney"]
            random_picks = random.sample(candidates, min(len(candidates), slots_needed))
            for city in random_picks:
                final_list_data.append({"city": city, "pinned": False})
        
        self.cards = []
        for item in final_list_data[:PINNED_SLOTS]:
            card = LocationCard(location_name=item["city"])
            card.is_pinned = item["pinned"]
            self.grid.add_widget(card)
            self.cards.append(card)

    def update_pinned_list(self):
        pinned_list = [card.location_name for card in self.cards if card.is_pinned]
        save_json_data(PINNED_FILE, pinned_list)

    def open_city_menu(self, card):
        if self.dialog:
            self.dialog.dismiss()
            self.dialog = None

        menu_items = []
        ordered_continents = ["Asia", "Americas", "Europe", "Oceania", "Africa"]
        for continent in ordered_continents:
            if continent not in CITY_DATA: continue
            menu_items.append({"text": f"--- {continent} ---", "viewclass": "OneLineListItem", "disabled": True, "height": dp(40)})
            for city in sorted(CITY_DATA[continent]):
                menu_items.append({
                    "text": f"    {city}",
                    "viewclass": "OneLineListItem",
                    "font_name": "Pacifico",
                    "disabled": False, 
                    "on_release": lambda x=city: self.update_card_city(card, x),
                })

        self.menu = MDDropdownMenu(caller=card, items=menu_items, width_mult=4, max_height=dp(500))
        self.menu.open()

    def show_forecast_dialog(self, card):
        try:
            if not card.forecast_data:
                Snackbar(text="Loading data...", bg_color=[0.2, 0.2, 0.2, 1]).open()
                return

            content = ForecastContent()
            content.current_desc = card.weather_desc
            
            # [V0.52 Fix] 清洗數據，移除卡片模式下的 " / " 分隔線
            raw_temp = card.weather_temp.replace(" / ", "").strip()
            content.current_temp = raw_temp
            
            content.current_icon = card.weather_icon
            content.current_icon_color = card.weather_icon_color
            
            content.ids.forecast_list.clear_widgets()

            for day in card.forecast_data:
                item = ForecastRowItem()
                item.date_text = day['display_date']
                item.desc_text = day['text']
                item.temp_text = f"{day['min']:.0f}° - {day['max']:.0f}°C"
                content.ids.forecast_list.add_widget(item)

            dialog_title = f"{card.location_name} ({card.timezone_text})"

            self.dialog = MDDialog(
                title=dialog_title,
                type="custom",
                content_cls=content,
                buttons=[
                    MDFlatButton(text="更換城市", theme_text_color="Custom", text_color=self.theme_cls.primary_color, font_name="TC_Font", on_release=lambda x: self.open_city_menu(card)),
                    MDRaisedButton(text="關閉", font_name="TC_Font", on_release=lambda x: self.dialog.dismiss()),
                ],
            )
            self.dialog.open()
        except Exception:
            traceback.print_exc()
            self.show_error("Failed to open details")

    def update_card_city(self, card_item, city_name):
        if self.menu: self.menu.dismiss()
        if self.dialog: self.dialog.dismiss()
        card_item.stop_animation()
        card_item.location_name = city_name.strip()
        self.reset_card_ui(card_item)
        if card_item.is_pinned: self.update_pinned_list()
        
        self.trigger_card_update(card_item)

    def reset_card_ui(self, card):
        card.is_loading = True
        card.time_offset = 0 
        card.weather_text = "..."
        card.weather_desc = ""
        card.weather_temp = ""
        card.aqi_text = ""
        card.timezone_text = ""
        card.time_text = "--:--:--"
        card.flag_url = ""
        card.current_bg_color = WEATHER_CONFIG["default"]["bg"]
        card.weather_icon = "dots-horizontal"
        card.forecast_data = [] 

    def update_time(self, dt):
        now = datetime.now()
        for card in self.cards:
            if card.is_loading: continue
            if card.time_offset is not None:
                city_time = now + timedelta(seconds=card.time_offset)
                card.time_text = city_time.strftime("%H:%M:%S")

    def start_weather_updates(self, dt):
        for card in self.cards:
            self.trigger_card_update(card)

    def trigger_card_update(self, card):
        city_key = card.location_name
        if city_key in self.pending_tasks:
            return 
        
        self.pending_tasks.add(city_key)
        self.executor.submit(self.fetch_single_card_data, card)

    def show_error(self, message):
        Clock.schedule_once(lambda dt: Snackbar(text=message, bg_color=[0.8, 0, 0, 1]).open(), 0)

    def fetch_single_card_data(self, card):
        city = card.location_name
        try:
            Clock.schedule_once(lambda dt: setattr(card, 'is_loading', True))
            
            if not self.api_key:
                Clock.schedule_once(lambda dt: self.show_error(MSG_NO_API_KEY))
                return
            if not validate_api_key(self.api_key):
                Clock.schedule_once(lambda dt: self.show_error(MSG_INVALID_KEY))
                return
            
            if not is_valid_city(city) and city not in [c.location_name for c in self.cards]:
                Clock.schedule_once(lambda dt: self.show_error(MSG_INVALID_CITY))
                return

            cached_dto = self.weather_cache.get(city)
            if cached_dto:
                self.update_ui_from_dto(card, cached_dto)
                return

            url = f"https://api.weatherapi.com/v1/forecast.json?key={self.api_key}&q={city}&days={MAX_FORECAST_DAYS}&aqi=yes&lang=zh_tw"
            
            r = requests.get(url, timeout=API_TIMEOUT)
            r.raise_for_status() 
            res = r.json() 
            
            if "error" in res:
                err_msg = res['error']['message']
                Clock.schedule_once(lambda dt: self.show_error(f"{city}: {err_msg}"))
                return
            
            self.process_raw_data(card, res)

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            Clock.schedule_once(lambda dt: self.show_error(f"{city}: HTTP Error ({e.response.status_code})"))
        except (ValueError, json.JSONDecodeError):
            Clock.schedule_once(lambda dt: self.show_error(f"{city}: {MSG_SERVER_ERR}"))
        except requests.exceptions.Timeout:
            Clock.schedule_once(lambda dt: self.show_error(f"{city}: {MSG_TIMEOUT}"))
        except requests.exceptions.RequestException:
            Clock.schedule_once(lambda dt: self.show_error(f"{city}: {MSG_NETWORK_ERR}"))
        except Exception:
            traceback.print_exc()
        finally:
            Clock.schedule_once(lambda dt: self.pending_tasks.discard(city))
            def ensure_stop_loading(dt):
                if card.is_loading and not self.weather_cache.get(city): 
                    card.is_loading = False
            Clock.schedule_once(ensure_stop_loading)

    def _parse_timezone(self, loc_data):
        local_time_str = loc_data['localtime']
        city_dt = datetime.strptime(local_time_str, "%Y-%m-%d %H:%M")
        utc_now = datetime.utcnow().replace(second=0, microsecond=0) 
        
        diff_seconds = (city_dt - utc_now).total_seconds()
        hours_diff = diff_seconds / 3600
        
        sign = "+" if hours_diff >= 0 else ""
        if abs(hours_diff % 1) < 0.1: 
             tz_str = f"UTC{sign}{int(hours_diff)}"
        else:
             tz_str = f"UTC{sign}{hours_diff:.1f}"

        now = datetime.now().replace(second=0, microsecond=0)
        offset = int((city_dt - now).total_seconds())
        
        return offset, tz_str

    def _parse_aqi(self, current_data):
        if 'air_quality' not in current_data:
            return "No AQI", (0.5, 0.5, 0.5, 1)
            
        pm2_5 = current_data['air_quality'].get('pm2_5', 0)
        idx = current_data['air_quality'].get('us-epa-index', 0)
        aqi_map = {
            1: ("Good", (0, 0.7, 0, 1)), 2: ("Fair", (0.7, 0.7, 0, 1)),
            3: ("Moderate", (1, 0.6, 0, 1)), 4: ("Poor", (1, 0, 0, 1)),
            5: ("Very Poor", (0.5, 0, 0.5, 1)), 6: ("Hazardous", (0.5, 0, 0, 1))
        }
        status, col = aqi_map.get(idx, ("-", (0.5, 0.5, 0.5, 1)))
        
        return f"AQI: {pm2_5:.1f}", col

    def _parse_forecast(self, forecast_data):
        raw_list = forecast_data.get('forecastday', [])
        clean_list = []
        for d in raw_list:
            d_str = d.get('date', '')
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d")
                disp_date = d_obj.strftime("%m/%d")
            except: disp_date = d_str
            
            clean_list.append({
                "display_date": disp_date,
                "text": d['day']['condition']['text'],
                "min": d['day'].get('mintemp_c', 0),
                "max": d['day'].get('maxtemp_c', 0)
            })
        return clean_list

    def process_raw_data(self, card, res):
        try:
            loc = res['location']
            curr = res['current']
            
            offset, timezone_str = self._parse_timezone(loc)
            aqi_text, aqi_color = self._parse_aqi(curr)
            forecast_list = self._parse_forecast(res.get('forecast', {}))

            country_code = COUNTRY_CODE_MAP.get(loc['country'], "un").lower()
            flag_url = f"https://flagcdn.com/w80/{country_code}.png"

            weather_data = ProcessedWeather(
                temp=curr['temp_c'],
                desc=curr['condition']['text'],
                weather_code=curr['condition']['code'],
                is_day=curr['is_day'],
                timezone_offset=offset,
                timezone_str=timezone_str,
                aqi_text=aqi_text,
                aqi_color=aqi_color,
                flag_url=flag_url, 
                forecast_list=forecast_list
            )

            config, category = get_weather_config_by_code(weather_data.weather_code, weather_data.is_day)
            weather_data.bg_color = config["bg"]
            weather_data.icon_name = config["icon"]
            weather_data.icon_color = config["icon_color"]
            # 這裡保持不變，仍然包含 " / "，用於卡片顯示
            weather_data.weather_text = f"{weather_data.desc} / {weather_data.temp}°C"

            self.weather_cache.set(card.location_name, weather_data)
            Clock.schedule_once(lambda dt: self.apply_ui_update(card, weather_data, category))

        except Exception:
            traceback.print_exc()
            Clock.schedule_once(lambda dt: setattr(card, 'is_loading', False))

    def update_ui_from_dto(self, card, data: ProcessedWeather):
        _, category = get_weather_config_by_code(data.weather_code, data.is_day)
        Clock.schedule_once(lambda dt: self.apply_ui_update(card, data, category))

    def apply_ui_update(self, card, data: ProcessedWeather, weather_category: str):
        card.is_loading = False 
        card.stop_animation()
        
        card.weather_text = data.weather_text
        
        card.weather_desc = data.desc
        card.weather_temp = f" / {data.temp}°C" # 卡片上保留這個格式
        
        card.flag_url = data.flag_url 
        card.aqi_text = data.aqi_text
        card.aqi_color = data.aqi_color
        card.timezone_text = data.timezone_str
        card.time_offset = data.timezone_offset
        card.current_bg_color = data.bg_color
        card.weather_icon = data.icon_name
        card.weather_icon_color = data.icon_color
        card.forecast_data = data.forecast_list 
        
        icon_widget = card.ids.weather_icon_widget
        
        if weather_category in ["sunny", "clear_night"]:
            anim = Animation(angle=360, duration=10, t='linear') + Animation(angle=0, duration=0)
            anim.repeat = True
            anim.start(icon_widget)
            card.current_anim = anim
        elif weather_category in ["rain", "sleet"]:
            base_y = icon_widget.y
            if base_y < 10: base_y = dp(150)
            anim = Animation(y=base_y - dp(10), duration=0.8, t='in_out_quad') + Animation(y=base_y, duration=0.8, t='in_out_quad')
            anim.repeat = True
            anim.start(icon_widget)
            card.current_anim = anim

if __name__ == "__main__":
    try:
        DashboardApp().run()
    except Exception:
        import traceback
        traceback.print_exc()