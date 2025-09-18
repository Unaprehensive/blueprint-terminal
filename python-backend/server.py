# ---------------------------------------------------------------------------
# Начало файла: server.py (ЗАМЕНИТЕ ВЕСЬ ФАЙЛ ЭТИМ КОДОМ)
# ---------------------------------------------------------------------------
import MetaTrader5 as mt5
import asyncio
import websockets
import platform
import json
from datetime import datetime
import logging
import hashlib
import time
from collections import defaultdict
import os
import subprocess
import threading
from flask import Flask, request, jsonify, send_file
from datetime import datetime, timedelta


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app for web interface
flask_app = Flask(__name__)

# Global variables
mt5_connected = False
mt5_initialized = False
connected_clients = set()
position_monitors = {}
symbol_subscriptions = {}
pending_order_automation = {}
last_positions_hash = {}
positions_cache = {}
last_tick_time = {}
positions_update_queue = defaultdict(list)
is_processing_updates = defaultdict(bool)

def find_terminal_by_login(login, server):
    """Finds MT5 terminal for both Windows and macOS"""
    
    # --- ЛОГИКА ДЛЯ WINDOWS ---
    if platform.system() == "Windows":
        terminal_base = os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal")
        if not os.path.exists(terminal_base):
            return None, "MetaQuotes folder not found on Windows"
            
        for folder in os.listdir(terminal_base):
            folder_path = os.path.join(terminal_base, folder)
            if not os.path.isdir(folder_path):
                continue
            
            trades_path = os.path.join(folder_path, "bases", server, "trades", str(login))
            if os.path.exists(trades_path):
                origin_file = os.path.join(folder_path, "origin.txt")
                if os.path.exists(origin_file):
                    for encoding in ['utf-16', 'utf-16-le', 'utf-8', 'cp1251']:
                        try:
                            with open(origin_file, 'r', encoding=encoding) as f:
                                terminal_path = f.read().strip()
                            terminal_exe = os.path.join(terminal_path, "terminal64.exe")
                            if os.path.exists(terminal_exe):
                                return terminal_exe, "Windows Terminal found"
                            break
                        except UnicodeDecodeError:
                            continue
        return None, f"Windows Terminal for login {login} on server {server} not found"

    # --- ЛОГИКА ДЛЯ MACOS ---
    elif platform.system() == "Darwin":
        # На macOS путь к приложению стандартный
        mac_app_path = "/Applications/MetaTrader 5.app"
        if os.path.exists(mac_app_path):
            # Для запуска мы будем использовать саму программу, а не внутренний .exe
            return mac_app_path, "macOS Terminal found"
        else:
            return None, "MetaTrader 5.app not found in /Applications"
            
    else:
        return None, f"Unsupported OS: {platform.system()}"

def start_mt5_with_credentials(login, password, server):
    """Launches MT5 with credentials on Windows and macOS"""
    global mt5_connected, mt5_initialized
    
    logger.info(f"Starting MT5 connection for login {login} on server {server}")
    
    terminal_path, message = find_terminal_by_login(login, server)
    if not terminal_path:
        logger.error(f"Terminal search failed: {message}")
        return False, message
    
    logger.info(f"Found terminal: {terminal_path}")

    # --- ЛОГИКА ЗАПУСКА В ЗАВИСИМОСТИ ОТ СИСТЕМЫ ---
    cmd = []
    if platform.system() == "Windows":
        try:
            subprocess.run(['taskkill', '/f', '/im', 'terminal64.exe'], capture_output=True, timeout=10)
            time.sleep(3)
        except:
            pass
        cmd = [terminal_path, f"/login:{login}", f"/server:{server}", f"/password:{password}"]
    
    elif platform.system() == "Darwin": # Darwin - это внутреннее имя macOS
        # На macOS для запуска используется команда 'open'
        # Прямая передача пароля не поддерживается, терминал использует сохраненные данные.
        cmd = ['open', '-a', terminal_path]

    if not cmd:
        return False, "Unsupported OS for launching terminal."
        
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        logger.info("Waiting for MT5 API connection...")
        for attempt in range(25):
            time.sleep(1)
            logger.info(f"Connection attempt {attempt + 1}/25")
            
            # mt5.initialize() работает одинаково на обеих системах
            if mt5.initialize():
                account_info = mt5.account_info()
                if account_info and account_info.login == int(login):
                    mt5_connected = True
                    mt5_initialized = True
                    
                    logger.info("=" * 50)
                    logger.info(f"MT5 Connected Successfully on {platform.system()}!")
                    logger.info(f"   Account: {account_info.login}")
                    logger.info(f"   Server: {account_info.server}")
                    logger.info(f"   Balance: ${account_info.balance:.2f}")
                    logger.info(f"   Equity: ${account_info.equity:.2f}")
                    logger.info("=" * 50)
                    
                    return True, "MT5 connection successful"
        
        return False, "Failed to establish MT5 API connection. Please ensure the terminal is running and you are logged in."
        
    except Exception as e:
        logger.error(f"Error starting MT5: {e}")
        return False, f"Error starting MT5: {str(e)}"

# Flask Routes
@flask_app.route('/connect-mt5', methods=['POST'])
def connect_mt5_endpoint():
    """Handle MT5 connection request"""
    try:
        data = request.json
        login = data.get('login', '').strip()
        password = data.get('password', '').strip()
        server = data.get('server', '').strip()
        
        if not all([login, password, server]):
            return jsonify({"success": False, "error": "Please fill all fields"})
        
        try:
            int(login)
        except ValueError:
            return jsonify({"success": False, "error": "Login must be a number"})
        
        success, message = start_mt5_with_credentials(login, password, server)
        
        if success:
            # Initialize symbols after successful connection
            global SYMBOL_MAP
            SYMBOL_MAP = auto_detect_symbols()
            return jsonify({"success": True, "message": message})
        else:
            return jsonify({"success": False, "error": message})
            
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return jsonify({"success": False, "error": f"Connection failed: {str(e)}"})

@flask_app.route('/status')
def get_status():
    """Get connection status"""
    return jsonify({
        "mt5_connected": mt5_connected,
        "mt5_initialized": mt5_initialized
    })

@flask_app.route('/terminal')
def serve_terminal():
    """Serve the terminal HTML file"""
    try:
        # ОБНОВЛЕНО: Отдаем файл с новым именем
        return send_file('terminal.html')
    except FileNotFoundError:
        return "Terminal HTML file not found", 404
        
@flask_app.route('/')
def serve_login():
    """Serve the login HTML file"""
    try:
        # ОБНОВЛЕНО: Отдаем файл входа по умолчанию
        return send_file('login.html')
    except FileNotFoundError:
        return "Login HTML file not found", 404

@flask_app.route('/history')  # БЕЗ ОТСТУПА В НАЧАЛЕ СТРОКИ
def serve_history():
    """Serve the history HTML file"""
    try:
        return send_file('history.html')
    except FileNotFoundError:
        return "History HTML file not found", 404

@flask_app.route('/bpfx-indicator.html')
def serve_bpfx_indicator():
    """Serve the BPFX indicator HTML file"""
    try:
        return send_file('bpfx-indicator.html')
    except FileNotFoundError:
        return "BPFX indicator HTML file not found", 404

def start_flask_server():
    """Start Flask server in separate thread"""
    flask_app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

def auto_detect_symbols():
    symbol_map = {}
    base_symbols = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
        "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
        "EURGBP", "EURAUD", "EURCAD", "EURCHF",
        "GBPAUD", "GBPCAD", "GBPCHF",
        "AUDCAD", "AUDCHF", "CADCHF",
        "XAUUSD", "XAGUSD",
        "BTCUSD", "ETHUSD"
    ]
    suffixes = ["", "+", ".", "m", "_raw", "pro", "#", ".a"]
    
    for base_symbol in base_symbols:
        for suffix in suffixes:
            test_symbol = base_symbol + suffix
            if mt5.symbol_info(test_symbol) is not None:
                symbol_map[base_symbol] = test_symbol
                if suffix:
                    logger.info(f"Symbol {base_symbol} found as {test_symbol}")
                break
    return symbol_map

SYMBOL_MAP = {}

def get_real_symbol(web_symbol):
    return SYMBOL_MAP.get(web_symbol, web_symbol)

def get_filling_mode(symbol):
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return mt5.ORDER_FILLING_IOC
    
    filling = symbol_info.filling_mode
    if filling & 1:
        return mt5.ORDER_FILLING_FOK
    elif filling & 2:
        return mt5.ORDER_FILLING_IOC
    elif filling & 4:
        return mt5.ORDER_FILLING_RETURN
    else:
        return mt5.ORDER_FILLING_IOC

def validate_volume(symbol, volume):
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return volume
    
    if volume < symbol_info.volume_min:
        logger.warning(f"Volume {volume} less than minimum {symbol_info.volume_min}")
        volume = symbol_info.volume_min
    
    if volume > symbol_info.volume_max:
        logger.warning(f"Volume {volume} greater than maximum {symbol_info.volume_max}")
        volume = symbol_info.volume_max
    
    volume_step = symbol_info.volume_step
    volume = round(volume / volume_step) * volume_step
    
    return round(volume, 2)

def calculate_profit_universal(position, current_price, symbol_info):
    if not current_price or not symbol_info:
        return 0
    
    if position.type == mt5.POSITION_TYPE_BUY:
        current = current_price.bid
        price_diff = current - position.price_open
    else:
        current = current_price.ask
        price_diff = position.price_open - current
    
    # Для золота и серебра - особый расчет
    if "XAU" in position.symbol or "GOLD" in position.symbol:
        profit = price_diff * position.volume * 100
    elif "XAG" in position.symbol or "SILVER" in position.symbol:
        profit = price_diff * position.volume * 5000
    elif "BTC" in position.symbol or "ETH" in position.symbol or "LTC" in position.symbol:
        profit = price_diff * position.volume
    else:
        # Для форекс пар (EURUSD, GBPUSD): движение на 0.001 = $1 при 0.01 лоте
        profit = price_diff * position.volume * 100000
    
    return profit

def get_positions_hash(positions):
    if not positions:
        return ""
    
    positions_str = ""
    for pos in positions:
        positions_str += f"{pos.ticket}_{pos.sl:.5f}_{pos.tp:.5f}_{pos.profit:.2f}_{pos.volume}_{pos.price_current:.5f}_"
    
    return hashlib.md5(positions_str.encode()).hexdigest()

async def handle_client(websocket):
    client_ip = websocket.remote_address[0]
    logger.info(f"Client connected: {client_ip}")
    connected_clients.add(websocket)
    last_positions_hash[websocket] = ""
    
    try:
        price_task = asyncio.create_task(price_updater(websocket))
        monitor_task = asyncio.create_task(position_monitor(websocket))
        
        async for message in websocket:
            await process_message(websocket, message)
            
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {client_ip}")
    except Exception as e:
        logger.error(f"Error with client {client_ip}: {e}")
    finally:
        connected_clients.discard(websocket)
        price_task.cancel()
        monitor_task.cancel()
        if websocket in symbol_subscriptions:
            del symbol_subscriptions[websocket]
        if websocket in last_positions_hash:
            del last_positions_hash[websocket]

async def process_message(websocket, message):
    try:
        data = json.loads(message)
        msg_type = data.get("type")
        
        logger.info(f"Received: {msg_type}")
        
        handlers = {
            "request": handle_request,
            "subscribe": handle_subscribe,
            "order": process_order,
            "close": close_position,
            "closeAll": close_all_positions,
            "closeMultiple": close_multiple_positions,
            "closePartial": close_partial_position,
            "modify": modify_position,
            "chart": send_chart_data,
            "history": send_history,
            "automation": update_automation,
            "cancelOrder": cancel_pending_order,
            "modifyPending": modify_pending_order,
        }
        
        handler = handlers.get(msg_type)
        if handler:
            await handler(websocket, data)
        else:
            logger.warning(f"Unknown message type: {msg_type}")
            
    except json.JSONDecodeError:
        logger.error(f"JSON parse error: {message}")
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await send_error(websocket, str(e))

async def handle_request(websocket, data):
    request_type = data.get("data")
    
    if request_type == "initial":
        await send_account_data(websocket)
        await send_positions(websocket, force=True)
        await send_pending_orders(websocket, force=True)
    elif request_type == "positions":
        await send_positions(websocket, force=True)
        await send_pending_orders(websocket, force=True)
    elif request_type == "account":
        await send_account_data(websocket)

async def handle_subscribe(websocket, data):
    symbol = get_real_symbol(data.get("symbol", "EURUSD"))
    symbol_subscriptions[websocket] = symbol
    logger.info(f"Client subscribed to {symbol}")
    await send_price(websocket, symbol, data.get("symbol"))

async def send_account_data(websocket):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    account = mt5.account_info()
    if account is None:
        await send_error(websocket, "Failed to get account data")
        return
    
    data = {
        "type": "account",
        "balance": account.balance,
        "equity": account.equity,
        "margin": account.margin,
        "freeMargin": account.margin_free,
        "marginLevel": account.margin_level if account.margin > 0 else 0,
        "server": account.server # <-- ДОБАВЛЕН ЭТОТ СТРОК
    }
    await websocket.send(json.dumps(data))

async def send_price(websocket, mt5_symbol, web_symbol=None):
    current_time = time.time()
    if mt5_symbol in last_tick_time:
        if current_time - last_tick_time[mt5_symbol] < 0.1:
            return
    last_tick_time[mt5_symbol] = current_time
    
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        return
    
    display_symbol = web_symbol if web_symbol else mt5_symbol
    
    rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_D1, 0, 1)
    open_price = rates[0]['open'] if rates and len(rates) > 0 else tick.bid
    
    symbol_info = mt5.symbol_info(mt5_symbol)
    if symbol_info:
        spread = (tick.ask - tick.bid) / symbol_info.point
    else:
        spread = tick.ask - tick.bid
    
    data = {
        "type": "tick",
        "symbol": display_symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "spread": round(spread, 1),
        "open": open_price,
        "time": int(tick.time) * 1000
    }
    await websocket.send(json.dumps(data))

async def send_positions(websocket, force=False):
    if not mt5_connected:
        return
        
    positions = mt5.positions_get()
    current_hash = get_positions_hash(positions)
    
    if not force and websocket in last_positions_hash:
        if current_hash == last_positions_hash[websocket]:
            return
    
    last_positions_hash[websocket] = current_hash
    
    if positions is None:
        positions = []
    
    positions_data = []
    for pos in positions:
        current_price = mt5.symbol_info_tick(pos.symbol)
        symbol_info = mt5.symbol_info(pos.symbol)
        
        profit = calculate_profit_universal(pos, current_price, symbol_info)
        
        if current_price:
            current = current_price.bid if pos.type == mt5.POSITION_TYPE_BUY else current_price.ask
        else:
            current = pos.price_current
        
        web_symbol = pos.symbol
        for suffix in ["+", ".", "m", "_raw", "pro", "#", ".a"]:
            web_symbol = web_symbol.replace(suffix, "")
        
        position_data = {
            "id": pos.ticket,
            "symbol": web_symbol,
            "type": "buy" if pos.type == mt5.POSITION_TYPE_BUY else "sell",
            "volume": pos.volume,
            "openPrice": pos.price_open,
            "currentPrice": current,
            "sl": pos.sl if pos.sl > 0 else None,
            "tp": pos.tp if pos.tp > 0 else None,
            "profit": round(profit, 2),
            "commission": getattr(pos, 'commission', 0),
            "swap": getattr(pos, 'swap', 0),
            "comment": getattr(pos, 'comment', '')
        }
        
        positions_data.append(position_data)
        positions_cache[pos.ticket] = pos
        
        # НОВЫЙ КОД: Проверяем, не из лимитного ли ордера эта позиция
        if pos.ticket not in position_monitors:
            # Пытаемся найти настройки автоматизации по комментарию
            position_comment = getattr(pos, 'comment', '')
            settings_found = False
            
            # Ищем в сохраненных настройках лимитных ордеров
            for order_id, settings in list(pending_order_automation.items()):
                if str(order_id) in position_comment:
                    # Переносим настройки на позицию
                    position_monitors[pos.ticket] = settings
                    logger.info(f"Automation settings transferred from order #{order_id} to position #{pos.ticket}")
                    logger.info(f"Settings: trailing={settings.get('trailing')}, trailing_distance=${settings.get('trailing_distance')}")
                    
                    # Удаляем использованные настройки
                    del pending_order_automation[order_id]
                    settings_found = True
                    break
            
            # Если настроек не нашли, создаем дефолтные
            if not settings_found:
                position_monitors[pos.ticket] = {
                    "trailing": False,
                    "trailing_profit": 10,
                    "trailing_distance": 5,
                    "breakeven": False,
                    "breakeven_profit": 5,
                    "breakeven_activated": False,
                    "partial_close": None,
                    "partial_close_profit": None,
                    "partial_closed": False,
                    "last_modified": 0
                }
    
    current_tickets = [pos.ticket for pos in positions]
    for ticket in list(positions_cache.keys()):
        if ticket not in current_tickets:
            del positions_cache[ticket]
            if ticket in position_monitors:
                del position_monitors[ticket]
    
    data = {
        "type": "positions",
        "positions": positions_data
    }
    await websocket.send(json.dumps(data))

async def send_pending_orders(websocket, force=False):
    if not mt5_connected:
        return
    
    # Получаем все pending ордера
    orders = mt5.orders_get()
    
    if orders is None:
        orders = []
    
    orders_data = []
    for order in orders:
        # Преобразуем символ для веба
        web_symbol = order.symbol
        for suffix in ["+", ".", "m", "_raw", "pro", "#", ".a"]:
            web_symbol = web_symbol.replace(suffix, "")
        
        order_data = {
            "ticket": order.ticket,
            "symbol": web_symbol,
            "type": "buy_limit" if order.type == mt5.ORDER_TYPE_BUY_LIMIT else "sell_limit",
            "volume": order.volume_current,
            "price": order.price_open,
            "sl": order.sl if order.sl > 0 else None,
            "tp": order.tp if order.tp > 0 else None,
            "time_setup": int(order.time_setup) * 1000,
            "comment": order.comment
        }
        orders_data.append(order_data)
    
    data = {
        "type": "pending_orders",
        "orders": orders_data
    }
    await websocket.send(json.dumps(data))

async def process_order(websocket, data):
    """Processes market and pending orders."""
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return

    web_symbol = data.get("symbol", "EURUSD")
    mt5_symbol = get_real_symbol(web_symbol)
    volume = float(data.get("volume", 0.01))
    action = data.get("action") # 'buy', 'sell'
    order_mode = data.get("order_type") # 'market', 'buy_limit', 'sell_limit'

    logger.info(f"Processing order: {order_mode} {action} {volume} {mt5_symbol}")

    symbol_info = mt5.symbol_info(mt5_symbol)
    if not symbol_info:
        await send_error(websocket, f"Symbol {mt5_symbol} not found")
        return

    volume = validate_volume(mt5_symbol, volume)
    request = {}
    
    # Получаем tick для всех типов ордеров
    tick = mt5.symbol_info_tick(mt5_symbol)
    if not tick:
        await send_error(websocket, f"No quotes for {mt5_symbol}")
        return
    
    # --- ИСПРАВЛЕНИЯ ЛОГИКИ ---
    if order_mode in ["buy_limit", "sell_limit"]:
        # --- ЛОГИКА ДЛЯ ЛИМИТНЫХ ОРДЕРОВ ---
        limit_price = float(data.get("price", 0))
        if limit_price <= 0:
            await send_error(websocket, "Invalid limit price")
            return
        
        order_type_map = {
            "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
            "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT
        }
        
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": mt5_symbol,
            "volume": volume,
            "type": order_type_map[order_mode],
            "price": limit_price,
            "magic": 12345,
            "comment": "Blueprint Limit Order",
            "type_filling": get_filling_mode(mt5_symbol),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        current_price_for_stops = limit_price # SL/TP считаем от цены лимита

    else: # Рыночный ордер
        # --- ЛОГИКА ДЛЯ РЫНОЧНЫХ ОРДЕРОВ ---
        is_buy = action == "buy"
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if is_buy else tick.bid,
            "deviation": 20,
            "magic": 12345,
            "comment": "Blueprint Market Order",
            "type_filling": get_filling_mode(mt5_symbol),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        current_price_for_stops = request["price"]

    # --- ОБЩАЯ ЛОГИКА ДЛЯ РАСЧЕТА SL/TP ---
    sl_value = data.get("sl")
    tp_value = data.get("tp")
    sl_unit = data.get("sl_unit")
    tp_unit = data.get("tp_unit")
    
    # Используем current_price_for_stops для расчета
    current_price = current_price_for_stops

    # Расчет Stop Loss
    if sl_value and sl_unit:
        sl_price = 0
        
        # Специальная логика для золота
        if "XAU" in mt5_symbol or "GOLD" in mt5_symbol:
            if sl_unit == 'dollar':
                # Для золота: $1 профита при 0.01 лоте = $1 движения цены
                price_change = sl_value / (volume * 100)
                if action == "buy":
                    sl_price = current_price - price_change
                else:
                    sl_price = current_price + price_change
            else:  # pips
                # Для золота 1 pip = 0.1$ движения цены
                price_change = sl_value * 0.1
                if action == "buy":
                    sl_price = current_price - price_change
                else:
                    sl_price = current_price + price_change
        else:
            # Логика для форекс пар (EURUSD, GBPUSD и др.)
            if sl_unit == 'dollar':
                # Для форекс: движение на 0.001 = $1 при 0.01 лоте
                price_change = sl_value / (volume * 100000)
                if action == "buy":
                    sl_price = current_price - price_change
                else:
                    sl_price = current_price + price_change
            else:  # points (не pips!)
                # 100 points = 0.001 движения цены
                price_change = sl_value * 0.00001
                if action == "buy":
                    sl_price = current_price - price_change
                else:
                    sl_price = current_price + price_change
        
        request["sl"] = round(sl_price, symbol_info.digits)

    # Расчет Take Profit
    if tp_value and tp_unit:
        tp_price = 0
        
        # Специальная логика для золота
        if "XAU" in mt5_symbol or "GOLD" in mt5_symbol:
            if tp_unit == 'dollar':
                price_change = tp_value / (volume * 100)
                if action == "buy":
                    tp_price = current_price + price_change
                else:
                    tp_price = current_price - price_change
            else:  # pips
                price_change = tp_value * 0.1
                if action == "buy":
                    tp_price = current_price + price_change
                else:
                    tp_price = current_price - price_change
        else:
            # Логика для форекс пар (EURUSD, GBPUSD и др.)
            if tp_unit == 'dollar':
                # Для форекс: движение на 0.001 = $1 при 0.01 лоте
                price_change = tp_value / (volume * 100000)
                if action == "buy":
                    tp_price = current_price + price_change
                else:
                    tp_price = current_price - price_change
            else:  # points (не pips!)
                # 100 points = 0.001 движения цены
                price_change = tp_value * 0.00001
                if action == "buy":
                    tp_price = current_price + price_change
                else:
                    tp_price = current_price - tp_value * 0.00001
        
        request["tp"] = round(tp_price, symbol_info.digits)
        
    # --- КОНЕЦ БЛОКА РАСЧЕТА SL/TP ---
    
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Order executed: #{result.order}")
        
        # ОБНОВЛЕННЫЙ КОД: Сохраняем настройки автоматизации для ВСЕХ типов ордеров
        automation_settings = {
            "trailing": data.get("trailing", False),
            "trailing_profit": data.get("trailing_profit", 10),
            "trailing_distance": data.get("trailing_distance", 5),
            "breakeven": data.get("breakeven", False),
            "breakeven_profit": data.get("breakeven_profit", 5),
            "breakeven_activated": False,
            "partial_close": data.get("partial_close"),
            "partial_close_profit": data.get("partial_close_profit"),
            "partial_closed": False,
            "last_modified": 0
        }
        
        # Проверяем, есть ли настройки автоматизации
        has_automation = data.get("trailing") or data.get("breakeven")
        
        if order_mode in ["buy_limit", "sell_limit"]:
            # Для лимитных ордеров сохраняем с ID ордера
            if has_automation:
                pending_order_automation[result.order] = automation_settings
                logger.info(f"Saved automation settings for limit order #{result.order}")
                logger.info(f"Trailing: {automation_settings['trailing']}, Distance: ${automation_settings['trailing_distance']}")
        else:
            # Для рыночных ордеров применяем к позиции
            if has_automation:
                # Пытаемся получить ID позиции из результата
                position_id = result.deal if hasattr(result, 'deal') else result.order
                position_monitors[position_id] = automation_settings
                logger.info(f"Automation settings applied to market position #{position_id}")
                logger.info(f"Trailing: {automation_settings['trailing']}, Breakeven: {automation_settings['breakeven']}")
        
        response = { 
            "type": "execution", 
            "success": True, 
            "order": result.order, 
            "volume": volume, 
            "price": result.price if hasattr(result, 'price') else limit_price if order_mode in ["buy_limit", "sell_limit"] else 0, 
            "symbol": web_symbol, 
            "type": action 
        }
    else:
        error_msg = f"Error {result.retcode if result else 'None'}: {result.comment if result else 'Unknown error'}"
        logger.error(error_msg)
        response = { 
            "type": "execution", 
            "success": False, 
            "error": error_msg 
        }
    
    await websocket.send(json.dumps(response))
    await send_account_data(websocket)
    await send_positions(websocket, force=True)
    await send_pending_orders(websocket, force=True)
    
async def close_position(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    position_id = data.get("positionId")
    
    position = mt5.positions_get(ticket=position_id)
    if not position:
        await send_error(websocket, "Position not found")
        return
    
    position = position[0]
    tick = mt5.symbol_info_tick(position.symbol)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": position_id,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask,
        "deviation": 20,
        "magic": 12345,
        "comment": "Close from Terminal",
        "type_filling": get_filling_mode(position.symbol),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Position #{position_id} closed")
        
        if position_id in position_monitors:
            del position_monitors[position_id]
        
        await send_positions(websocket, force=True)
        await send_account_data(websocket)
    else:
        await send_error(websocket, f"Close error: {result.comment if result else 'Unknown error'}")

async def close_partial_position(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    position_id = data.get("positionId")
    close_volume = float(data.get("volume", 0))
    
    position = mt5.positions_get(ticket=position_id)
    if not position:
        await send_error(websocket, "Position not found")
        return
    
    position = position[0]
    
    if close_volume > position.volume:
        close_volume = position.volume
    
    close_volume = validate_volume(position.symbol, close_volume)
    
    tick = mt5.symbol_info_tick(position.symbol)
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": position_id,
        "symbol": position.symbol,
        "volume": close_volume,
        "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask,
        "deviation": 20,
        "magic": 12345,
        "comment": "Partial close",
        "type_filling": get_filling_mode(position.symbol),
        "type_time": mt5.ORDER_TIME_GTC,
    }
    
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Partial close #{position_id}: {close_volume} lots")
        
        if position_id in position_monitors:
            position_monitors[position_id]["partial_closed"] = True
        
        notification = {
            "type": "notification",
            "message": f"Closed {close_volume} lots of position #{position_id}",
            "level": "success"
        }
        await websocket.send(json.dumps(notification))
        
        await send_positions(websocket, force=True)
        await send_account_data(websocket)
    else:
        await send_error(websocket, f"Partial close error: {result.comment if result else 'Unknown error'}")

async def close_all_positions(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    positions = mt5.positions_get()
    if not positions:
        await send_error(websocket, "No open positions")
        return
    
    closed = 0
    errors = 0
    
    for position in positions:
        tick = mt5.symbol_info_tick(position.symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask,
            "deviation": 20,
            "magic": 12345,
            "comment": "CloseAll",
            "type_filling": get_filling_mode(position.symbol),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
            if position.ticket in position_monitors:
                del position_monitors[position.ticket]
        else:
            errors += 1
            logger.error(f"Error closing #{position.ticket}: {result.comment if result else 'Unknown'}")
    
    logger.info(f"Closed positions: {closed}, errors: {errors}")
    await send_positions(websocket, force=True)
    await send_account_data(websocket)

async def close_multiple_positions(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    position_ids = data.get("positionIds", [])
    
    closed = 0
    errors = 0
    
    for position_id in position_ids:
        position = mt5.positions_get(ticket=position_id)
        if not position:
            errors += 1
            continue
        
        position = position[0]
        tick = mt5.symbol_info_tick(position.symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position_id,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask,
            "deviation": 20,
            "magic": 12345,
            "comment": "Multiple close",
            "type_filling": get_filling_mode(position.symbol),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result = mt5.order_send(request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
            if position_id in position_monitors:
                del position_monitors[position_id]
        else:
            errors += 1
    
    logger.info(f"Multiple close: {closed} closed, {errors} errors")
    await send_positions(websocket, force=True)
    await send_account_data(websocket)

async def cancel_pending_order(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
    
    ticket = data.get("ticket")
    
    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }
    
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Pending order #{ticket} cancelled")
        
        notification = {
            "type": "notification",
            "message": f"Order #{ticket} cancelled",
            "level": "success"
        }
        await websocket.send(json.dumps(notification))
        
        await send_pending_orders(websocket, force=True)
        await send_account_data(websocket)
    else:
        error_msg = f"Cancel error: {result.comment if result else 'Unknown error'}"
        logger.error(error_msg)
        await send_error(websocket, error_msg)

async def modify_position(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    position_id = data.get("positionId")
    
    position = mt5.positions_get(ticket=position_id)
    if not position:
        await send_error(websocket, "Position not found")
        return
    
    position = position[0]
    symbol_info = mt5.symbol_info(position.symbol)
    
    # Получаем переданные цены напрямую
    new_sl_price = data.get("sl_price")
    new_tp_price = data.get("tp_price")
    
    # Если цены не переданы напрямую, проверяем передачу в долларах (из диалога Settings)
    if new_sl_price is None:
        sl_dollar = data.get("sl")
        if sl_dollar and isinstance(sl_dollar, (int, float)):
            # Специальный расчет для золота
            if "XAU" in position.symbol or "GOLD" in position.symbol:
                price_change = sl_dollar / (position.volume * 100)
            else:
                # Для форекс пар: движение на 0.001 = $1 при 0.01 лоте
                price_change = sl_dollar / (position.volume * 100000)
            
            if position.type == mt5.POSITION_TYPE_BUY:
                new_sl_price = position.price_open - price_change
            else:
                new_sl_price = position.price_open + price_change
    
    if new_tp_price is None:
        tp_dollar = data.get("tp")
        if tp_dollar and isinstance(tp_dollar, (int, float)):
            # Специальный расчет для золота
            if "XAU" in position.symbol or "GOLD" in position.symbol:
                price_change = tp_dollar / (position.volume * 100)
            else:
                # Для форекс пар: движение на 0.001 = $1 при 0.01 лоте
                price_change = tp_dollar / (position.volume * 100000)
            
            if position.type == mt5.POSITION_TYPE_BUY:
                new_tp_price = position.price_open + price_change
            else:
                new_tp_price = position.price_open - price_change
    
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position_id,
        "symbol": position.symbol,
        "sl": float(new_sl_price) if new_sl_price is not None else position.sl,
        "tp": float(new_tp_price) if new_tp_price is not None else position.tp,
        "magic": 12345,
        "comment": "Modify from Terminal"
    }
    
    # Округляем до правильного количества знаков
    if symbol_info:
        request["sl"] = round(request["sl"], symbol_info.digits) if request["sl"] else 0
        request["tp"] = round(request["tp"], symbol_info.digits) if request["tp"] else 0
    
    result = mt5.order_send(request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Position #{position_id} modified")
        await send_positions(websocket, force=True)
    else:
        error_msg = f"Modify error: {result.comment if result else 'Unknown error'}"
        logger.error(error_msg)
        await send_error(websocket, error_msg)

async def update_automation(websocket, data):
    position_id = data.get("positionId")
    settings = data.get("settings", {})
    automation_type = data.get("automationType")
    
    if position_id not in position_monitors:
        position_monitors[position_id] = {
            "trailing": False,
            "trailing_profit": 10,
            "trailing_distance": 5,
            "breakeven": False,
            "breakeven_profit": 5,
            "breakeven_activated": False,
            "partial_close": None,
            "partial_close_profit": None,
            "partial_closed": False,
            "last_modified": 0
        }
    
    if automation_type == "trailing":
        position_monitors[position_id]["trailing"] = settings.get("enabled", False)
        position_monitors[position_id]["trailing_profit"] = settings.get("profitTrigger", 10)
        position_monitors[position_id]["trailing_distance"] = settings.get("distance", 5)
        
    elif automation_type == "breakeven":
        position_monitors[position_id]["breakeven"] = settings.get("enabled", False)
        position_monitors[position_id]["breakeven_profit"] = settings.get("profitTrigger", 5)
        if not settings.get("enabled"):
            position_monitors[position_id]["breakeven_activated"] = False
    
    else:
        for key, value in settings.items():
            if key in position_monitors[position_id]:
                position_monitors[position_id][key] = value
    
    logger.info(f"Automation updated for position #{position_id}")

async def send_chart_data(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    symbol = get_real_symbol(data.get("symbol", "EURUSD"))
    timeframe = data.get("timeframe", "H1")
    count = data.get("count", 500)
    
    logger.info(f"Chart request: {symbol} {timeframe} ({count} candles)")
    
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
        "MN": mt5.TIMEFRAME_MN1
    }
    
    mt5_timeframe = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
    
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
    
    if rates is None or len(rates) == 0:
        logger.warning(f"No data for {symbol}")
        await send_error(websocket, f"No data for {symbol}")
        return
    
    candles = []
    for rate in rates:
        candles.append({
            "time": int(rate['time']) * 1000,
            "open": float(rate['open']),
            "high": float(rate['high']),
            "low": float(rate['low']),
            "close": float(rate['close']),
            "volume": float(rate['tick_volume'])
        })
    
    logger.info(f"Sent {len(candles)} candles")
    
    response = {
        "type": "chart",
        "candles": candles
    }
    await websocket.send(json.dumps(response))

async def send_history(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
        
    from_date = datetime.strptime(data.get("from", "2025-01-01"), "%Y-%m-%d")
    to_date = datetime.strptime(data.get("to", datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d")
    # Добавляем день к to_date чтобы включить сегодняшние сделки
    to_date = to_date + timedelta(days=1)
    
    # Получаем историю сделок
    history = mt5.history_deals_get(from_date, to_date)
    
    trades = []
    positions_map = {}
    
    if history is not None:
        # Группируем сделки по позициям
        for deal in history:
            # Пропускаем балансовые операции
            if deal.type not in [mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL]:
                continue
            
            position_id = deal.position_id
            
            # Проверяем есть ли уже эта позиция в словаре
            if position_id not in positions_map:
                positions_map[position_id] = {
                    "id": position_id,
                    "symbol": deal.symbol,
                    "in_deal": None,
                    "out_deal": None,
                    "profit": 0,
                    "commission": 0,
                    "swap": 0,
                    "exit_type": "manual"  # По умолчанию manual
                }
            
            # DEAL_ENTRY_IN - открытие позиции
            if deal.entry == mt5.DEAL_ENTRY_IN:
                positions_map[position_id]["in_deal"] = deal
                positions_map[position_id]["type"] = "buy" if deal.type == mt5.DEAL_TYPE_BUY else "sell"
                positions_map[position_id]["volume"] = deal.volume
                positions_map[position_id]["openTime"] = int(deal.time) * 1000
                positions_map[position_id]["openPrice"] = deal.price
            
            # DEAL_ENTRY_OUT или DEAL_ENTRY_OUT_BY - закрытие позиции
            elif deal.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY]:
                positions_map[position_id]["out_deal"] = deal
                positions_map[position_id]["closeTime"] = int(deal.time) * 1000
                positions_map[position_id]["closePrice"] = deal.price
            
            # Суммируем profit, commission и swap
            positions_map[position_id]["profit"] += getattr(deal, 'profit', 0)
            positions_map[position_id]["commission"] += getattr(deal, 'commission', 0)
            positions_map[position_id]["swap"] += getattr(deal, 'swap', 0)
            
            # Сохраняем comment от последней сделки и определяем тип закрытия
            if hasattr(deal, 'comment') and deal.comment:
                positions_map[position_id]["comment"] = deal.comment
                
                # Определяем тип закрытия по comment
                comment_lower = deal.comment.lower()
                if '[tp]' in comment_lower or 'take profit' in comment_lower or 'tp' in comment_lower:
                    positions_map[position_id]["exit_type"] = "tp"
                elif '[sl]' in comment_lower or 'stop loss' in comment_lower or 'sl' in comment_lower:
                    positions_map[position_id]["exit_type"] = "sl"
                elif 'so' in comment_lower:  # Stop Out
                    positions_map[position_id]["exit_type"] = "sl"
                else:
                    positions_map[position_id]["exit_type"] = "manual"
    
    # Преобразуем словарь позиций в список для отправки
    for position_id, pos_data in positions_map.items():
        # Пропускаем незакрытые позиции если нет out_deal
        if pos_data["in_deal"] is None:
            continue
            
        trade = {
            "id": position_id,
            "ticket": position_id,
            "symbol": pos_data["symbol"],
            "type": pos_data.get("type", "unknown"),
            "volume": pos_data.get("volume", 0),
            "openTime": pos_data.get("openTime", 0),
            "closeTime": pos_data.get("closeTime", pos_data.get("openTime", 0)),
            "price": pos_data.get("openPrice", 0),
            "openPrice": pos_data.get("openPrice", 0),
            "closePrice": pos_data.get("closePrice", pos_data.get("openPrice", 0)),
            "profit": pos_data["profit"],
            "commission": pos_data["commission"],
            "swap": pos_data["swap"],
            "comment": pos_data.get("comment", ""),
            "exit_type": pos_data.get("exit_type", "manual")  # Добавляем exit_type
        }
        
        trades.append(trade)
    
    # Сортируем по времени закрытия (новые сверху)
    trades.sort(key=lambda x: x.get("closeTime", 0), reverse=True)
    
    response = {
        "type": "history",
        "trades": trades
    }
    await websocket.send(json.dumps(response))

async def send_error(websocket, error_message):
    response = {
        "type": "error",
        "message": error_message
    }
    await websocket.send(json.dumps(response))

async def price_updater(websocket):
    while True:
        try:
            if websocket in symbol_subscriptions:
                symbol = symbol_subscriptions[websocket]
                web_symbol = symbol
                for suffix in ["+", ".", "m", "_raw", "pro", "#", ".a"]:
                    web_symbol = web_symbol.replace(suffix, "")
                await send_price(websocket, symbol, web_symbol)
            
            await send_positions(websocket, force=False)
            await send_pending_orders(websocket, force=False)
            
            await asyncio.sleep(0.5)
            
        except websockets.exceptions.ConnectionClosed:
            break
        except Exception as e:
            logger.error(f"Price updater error: {e}")
            await asyncio.sleep(1)

async def position_monitor(websocket):
    while True:
        try:
            if not mt5_connected:
                await asyncio.sleep(5)
                continue
                
            positions = mt5.positions_get()
            current_time = datetime.now().timestamp()
            
            if positions:
                for pos in positions:
                    if pos.ticket in position_monitors:
                        monitor = position_monitors[pos.ticket]
                        
                        if current_time - monitor.get("last_modified", 0) < 5:
                            continue
                        
                        tick = mt5.symbol_info_tick(pos.symbol)
                        symbol_info = mt5.symbol_info(pos.symbol)
                        if not tick or not symbol_info:
                            continue
                        
                        profit_usd = calculate_profit_universal(pos, tick, symbol_info)
                        
                        # СТУПЕНЧАТЫЙ ТРЕЙЛИНГ
                        if monitor["trailing"] and profit_usd >= monitor["trailing_profit"]:
                            # Рассчитываем количество полных шагов
                            steps_achieved = int(profit_usd / monitor["trailing_profit"])
                            
                            # Рассчитываем общее движение SL в долларах
                            total_sl_movement_usd = steps_achieved * monitor["trailing_distance"]
                            
                            if "XAU" in pos.symbol or "GOLD" in pos.symbol:
                                # Для золота: конвертируем доллары в движение цены
                                sl_movement_price = total_sl_movement_usd / (pos.volume * 100)
                                
                            elif "XAG" in pos.symbol or "SILVER" in pos.symbol:
                                sl_movement_price = total_sl_movement_usd / (pos.volume * 5000)
                                
                            elif "BTC" in pos.symbol or "ETH" in pos.symbol:
                                sl_movement_price = total_sl_movement_usd / pos.volume
                                
                            else:
                                # Для форекс (EURUSD, GBPUSD)
                                sl_movement_price = total_sl_movement_usd / (pos.volume * 100000)
                            
                            # Рассчитываем целевой SL от цены входа
                            if pos.type == mt5.POSITION_TYPE_BUY:
                                target_sl = pos.price_open + sl_movement_price
                            else:  # SELL
                                target_sl = pos.price_open - sl_movement_price
                            
                            target_sl = round(target_sl, symbol_info.digits)
                            
                            # Обновляем только если SL изменился и движется в сторону прибыли
                            need_update = False
                            if pos.sl == 0:
                                need_update = True
                            elif pos.type == mt5.POSITION_TYPE_BUY and target_sl > pos.sl:
                                need_update = True
                            elif pos.type == mt5.POSITION_TYPE_SELL and target_sl < pos.sl:
                                need_update = True
                            
                            if need_update:
                                success = await modify_position_sl(pos.ticket, target_sl, pos.symbol)
                                if success:
                                    monitor["last_modified"] = current_time
                                    direction = "UP" if pos.type == mt5.POSITION_TYPE_BUY else "DOWN"
                                    logger.info(f"Stepped trailing: profit ${profit_usd:.2f}, step {steps_achieved}, SL moved {direction} to {target_sl}")
                        
                        # BREAKEVEN - тоже проверим правильность
                        if monitor["breakeven"] and not monitor["breakeven_activated"] and profit_usd >= monitor["breakeven_profit"]:
                            if pos.type == mt5.POSITION_TYPE_BUY:
                                breakeven_price = pos.price_open + (1 * symbol_info.point)
                            else:
                                breakeven_price = pos.price_open - (1 * symbol_info.point)
                            
                            breakeven_price = round(breakeven_price, symbol_info.digits)
                            
                            success = await modify_position_sl(pos.ticket, breakeven_price, pos.symbol)
                            if success:
                                monitor["breakeven_activated"] = True
                                monitor["last_modified"] = current_time
                                logger.info(f"Position #{pos.ticket} moved to breakeven at {breakeven_price}")
                                
                                notification = {
                                    "type": "notification",
                                    "message": f"Position #{pos.ticket} moved to breakeven",
                                    "level": "success"
                                }
                                await websocket.send(json.dumps(notification))
                        
                        # PARTIAL CLOSE при достижении цели
                        if monitor.get("partial_close") and not monitor.get("partial_closed"):
                            if profit_usd >= monitor.get("partial_close_profit", 0):
                                close_volume = validate_volume(pos.symbol, pos.volume / 2)
                                if close_volume > 0:
                                    await close_partial_position(websocket, {
                                        "positionId": pos.ticket,
                                        "volume": close_volume
                                    })
                                    monitor["partial_closed"] = True
            
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"Position monitor error: {e}")
            await asyncio.sleep(5)

async def modify_position_sl(ticket, new_sl, symbol):
    try:
        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.error(f"Position #{ticket} not found")
            return False
        
        position = position[0]
        
        if abs(position.sl - new_sl) < 0.00001:
            return False
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": symbol,
            "sl": float(new_sl),
            "tp": position.tp if position.tp > 0 else 0.0,
            "magic": 12345,
            "comment": "Auto-SL"
        }
        
        result = mt5.order_send(request)
        
        if result is None:
            logger.error(f"SL modify error: result is None for position #{ticket}")
            return False
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"SL modified for position #{ticket}: {new_sl}")
            return True
        else:
            logger.error(f"SL modify error: {result.retcode} - {result.comment}")
            return False
            
    except Exception as e:
        logger.error(f"Exception modifying SL: {e}")
        return False
async def modify_pending_order(websocket, data):
    if not mt5_connected:
        await send_error(websocket, "MT5 not connected")
        return
    
    ticket = data.get('ticket')
    new_price = data.get('price')
    new_sl = data.get('sl', 0)
    new_tp = data.get('tp', 0)
    
    # Получаем текущий ордер
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        await send_error(websocket, "Order not found")
        return
    
    order = orders[0]
    
    # Отменяем старый ордер
    cancel_request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket
    }
    
    result = mt5.order_send(cancel_request)
    
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        # Создаем новый ордер с новыми параметрами
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if order.type == mt5.ORDER_TYPE_BUY_LIMIT else mt5.ORDER_TYPE_SELL_LIMIT
        
        new_request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": order.symbol,
            "volume": order.volume_current,
            "type": order_type,
            "price": new_price,
            "sl": new_sl if new_sl > 0 else 0,
            "tp": new_tp if new_tp > 0 else 0,
            "magic": 12345,
            "comment": "Modified order",
            "type_filling": get_filling_mode(order.symbol),
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result = mt5.order_send(new_request)
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Order #{ticket} modified -> new order #{result.order}")
            
            notification = {
                "type": "notification",
                "message": f"Order modified successfully",
                "level": "success"
            }
            await websocket.send(json.dumps(notification))
            
            await send_pending_orders(websocket, force=True)
        else:
            await send_error(websocket, f"Failed to place modified order: {result.comment if result else 'Unknown error'}")
    else:
        await send_error(websocket, f"Failed to cancel original order: {result.comment if result else 'Unknown error'}")
async def main():
    global mt5_connected

    # --- НАЧАЛО ФИНАЛЬНОГО ИСПРАВЛЕНИЯ ---
    print("=" * 60)
    print("    BLUEPRINT FX TRADING TERMINAL v2.1")
    print("=" * 60)

    flask_thread = threading.Thread(target=start_flask_server, daemon=True)
    flask_thread.start()

    print("Login Interface running at: http://127.0.0.1:5000")
    print("WebSocket Server running at: ws://127.0.0.1:8080")
    print("Waiting for MT5 connection from login page...")
    print("=" * 60)
    # --- КОНЕЦ ФИНАЛЬНОГО ИСПРАВЛЕНИЯ ---

    server = await websockets.serve(handle_client, "127.0.0.1", 8080)

    await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBlueprint FX Terminal stopped")
        if mt5_initialized:
            mt5.shutdown()
    except Exception as e:
        logger.error(f"Critical error: {e}")
        if mt5_initialized:
            mt5.shutdown()

# ---------------------------------------------------------------------------
# Конец файла: server.py
# ---------------------------------------------------------------------------