"""Read Home Assistant Energy's hourly Forecast.Solar data, without actuator access."""
import json
import math
import os
from datetime import datetime, timedelta, timezone


def _call(socket, command, message_id, **fields):
    socket.send(json.dumps({'id': message_id, 'type': command, **fields}))
    reply = json.loads(socket.recv())
    if reply.get('id') != message_id or not reply.get('success'):
        raise ValueError('Home Assistant kunde inte lämna ' + command + '.')
    return reply['result']


def energy_data():
    """Return Energy dashboard preferences and hourly Wh forecasts."""
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        raise ValueError('Home Assistant är inte ansluten.')
    # The Energy dashboard exposes wh_hours over HA's authenticated WebSocket API.
    from websocket import create_connection
    socket = create_connection('ws://supervisor/core/websocket', timeout=15)
    try:
        if json.loads(socket.recv()).get('type') != 'auth_required':
            raise ValueError('Home Assistants WebSocket svarade oväntat.')
        socket.send(json.dumps({'type': 'auth', 'access_token': token}))
        if json.loads(socket.recv()).get('type') != 'auth_ok':
            raise ValueError('Home Assistant nekade åtkomst till solprognosen.')
        try:
            prefs = _call(socket, 'energy/get_prefs', 1) or {}
        except ValueError:
            # An installed Forecast.Solar instance remains selectable before
            # the user has configured an Energy dashboard.
            prefs = {}
        try:
            forecasts = _call(socket, 'energy/solar_forecast', 2) or {}
        except ValueError:
            forecasts = {}
        return prefs, forecasts
    finally:
        socket.close()


def sources(entries, prefs, forecasts):
    """Installed sources stay visible even when Energy is not configured."""
    linked = {
        entry: source.get('stat_energy_from')
        for source in prefs.get('energy_sources', [])
        if source.get('type') == 'solar'
        for entry in source.get('config_entry_solar_forecast') or []
    }
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('domain') != 'forecast_solar' or entry.get('disabled_by'):
            continue
        entry_id = entry.get('entry_id')
        if not isinstance(entry_id, str) or not entry_id:
            continue
        name = entry.get('title') or 'Forecast.Solar'
        is_linked = entry_id in linked
        has_forecast = bool(forecasts.get(entry_id, {}).get('wh_hours'))
        suffix = (' · ' + str(linked[entry_id]) if linked.get(entry_id) else '') if is_linked else ' · koppla i Energipanelen'
        if is_linked and not has_forecast:
            suffix += ' · timprognos saknas'
        result.append({'id': entry_id, 'label': name + suffix, 'linked': is_linked,
                       'has_forecast': has_forecast})
    return result


def hourly(forecasts, entry):
    """Map hourly Wh to mean W over the hour; reject sparse or invalid data."""
    raw = forecasts.get(entry, {}).get('wh_hours', {})
    result = {}
    original_hours = []
    for stamp, value in raw.items():
        try:
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            power = float(value)
            if moment.tzinfo is None or moment.minute or moment.second or not math.isfinite(power) or power < 0:
                continue
            result[moment.astimezone(timezone.utc)] = power
            original_hours.append(moment)
        except (ValueError, TypeError, AttributeError):
            continue
    # Forecast.Solar's Energy integration omits a zero point at local midnight.
    for left, right in zip(sorted(original_hours), sorted(original_hours)[1:]):
        middle = left + timedelta(hours=1)
        if right - left == timedelta(hours=2) and middle.hour == 0:
            result[middle.astimezone(timezone.utc)] = 0.0
    return result


def combine(weather, hourly_power):
    """Attach only exact matching UTC hours, retaining absence as absence."""
    points = []
    for row in weather:
        point = dict(row)
        try:
            stamp = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00')).astimezone(timezone.utc)
            if stamp in hourly_power:
                point['pv_power'] = hourly_power[stamp]
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        points.append(point)
    return points
