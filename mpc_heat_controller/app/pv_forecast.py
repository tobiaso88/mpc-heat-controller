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


def hourly(forecasts, entry, diagnostics=None):
    """Map hourly Wh to mean W, restoring only bounded overnight zero gaps."""
    raw = forecasts.get(entry, {}).get('wh_hours', {})
    result = {}
    original_hours = []
    invalid = False
    invalid_points = []
    non_hourly_points = []
    added_zeros = []
    def finish():
        if diagnostics is not None:
            diagnostics.update(source_points=len(raw), hourly_points=len(original_hours),
                               non_hourly_points=len(non_hourly_points),
                               non_hourly_examples=non_hourly_points[:4],
                               invalid_points=len(invalid_points), invalid_examples=invalid_points[:4],
                               added_zero_hours=len(added_zeros),
                               night_fill_blocked_by_invalid_values=invalid)
        return result
    for stamp, value in raw.items():
        try:
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            power = float(value)
            if moment.tzinfo is None or not math.isfinite(power) or power < 0 or isinstance(value, bool):
                invalid = True
                invalid_points.append(str(stamp))
                continue
            # Sunrise/sunset boundary periods (and subhourly subscription data)
            # are valid source data, but are not full-hour model inputs.
            # Skipping them must not disable the independent night-zero rule.
            if moment.minute or moment.second or moment.microsecond:
                non_hourly_points.append(str(stamp))
                continue
            result[moment.astimezone(timezone.utc)] = power
            original_hours.append(moment)
        except (ValueError, TypeError, AttributeError):
            invalid = True
            invalid_points.append(str(stamp))
            continue
    # Forecast.Solar's Energy integration omits a zero point at local midnight.
    for left, right in zip(sorted(original_hours), sorted(original_hours)[1:]):
        middle = left + timedelta(hours=1)
        if right - left == timedelta(hours=2) and middle.hour == 0:
            result[middle.astimezone(timezone.utc)] = 0.0
            added_zeros.append(middle.astimezone(timezone.utc))
    # Some sources contain only daylight periods. Restore only bounded nights
    # between consecutive provider-local dates with contiguous daylight blocks.
    # Never extend the source's horizon or fill an interior daytime hole.
    if invalid: return finish()
    days = {}
    for moment in original_hours:
        days.setdefault(moment.date(), []).append(moment)
    dates = sorted(days)
    for left_date, right_date in zip(dates, dates[1:]):
        left_day, right_day = sorted(days[left_date]), sorted(days[right_date])
        if right_date-left_date != timedelta(days=1): continue
        if len(left_day) < 3 or len(right_day) < 3: continue
        if any(b.astimezone(timezone.utc)-a.astimezone(timezone.utc) != timedelta(hours=1)
               for day in (left_day, right_day) for a,b in zip(day, day[1:])): continue
        left, right = left_day[-1], right_day[0]
        if left.hour < 12 or right.hour > 12: continue
        start, end = left.astimezone(timezone.utc), right.astimezone(timezone.utc)
        if not timedelta(hours=6) <= end-start <= timedelta(hours=18): continue
        current = start+timedelta(hours=1)
        while current < end:
            if current not in result:
                result[current] = 0.0
                added_zeros.append(current)
            current += timedelta(hours=1)
    return finish()


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
