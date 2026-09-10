"""Configuration and explicitly uncalibrated thermal simulation. No actuation."""
import copy
import math

DEFAULT = {"mode": "demo", "target": 21.5, "comfort_min": 21.0, "comfort_max": 22.0,
           "indoor": [], "outdoor": "", "supply": "", "return": "", "observe": [],
           "weather": "", "applied_signal": "", "signal_min": -15.0, "signal_max": 30.0, "max_step": 1.0,
           "pi_kp": 2.0, "pi_ki": 0.1, "pi_limit": 10.0, "pi_rate": 2.0}

def validate(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT):
        raise ValueError("Okända inställningar")
    c = copy.deepcopy(DEFAULT)
    c.update(raw)
    if c["mode"] not in ("demo", "shadow"):
        raise ValueError("Endast demo och skuggläge stöds")
    for k in ("target", "comfort_min", "comfort_max", "signal_min", "signal_max", "max_step", "pi_kp", "pi_ki", "pi_limit", "pi_rate"):
        if isinstance(c[k], bool) or not isinstance(c[k], (float, int)) or not math.isfinite(c[k]):
            raise ValueError("Ogiltigt numeriskt värde: " + k)
    if not (0 <= c['pi_kp'] <= 20 and 0 <= c['pi_ki'] <= 5 and 0 < c['pi_limit'] <= 30 and 0 < c['pi_rate'] <= 20):
        raise ValueError('PI-parametrar ligger utanför tillåtna gränser')
    if not 7 <= c["comfort_min"] <= c["target"] <= c["comfort_max"] <= 35:
        raise ValueError("Börvärdet måste ligga inom komfortintervallet 7–35 °C")
    if not -40 <= c["signal_min"] < c["signal_max"] <= 50 or not 0 < c["max_step"] <= 5:
        raise ValueError("Kontrollera signalgränser och maximal ändring")
    for k in ("indoor", "observe"):
        if not isinstance(c[k], list) or len(c[k]) > 50:
            raise ValueError("Ogiltigt givarval")
        if any(not isinstance(x, str) or not x.startswith("sensor.") or len(x) > 255 for x in c[k]):
            raise ValueError("Välj sensorer för rumstemperatur")
        if len(set(c[k])) != len(c[k]):
            raise ValueError("Samma givare får bara väljas en gång")
    for k in ("outdoor", "supply", "return", "weather", "applied_signal"):
        if not isinstance(c[k], str) or len(c[k]) > 255:
            raise ValueError("Ogiltig entitet")
    if c['applied_signal'] and not c['applied_signal'].startswith(('sensor.', 'number.')):
        raise ValueError('Välj en sensor eller number-entitet för Ohmigos inställda temperatur')
    if c["mode"] == "shadow" and (not c["indoor"] or not c["outdoor"]):
        raise ValueError("Välj inne- och utegivare först")
    return c

def simulate(c):
    """Beam-search MPC on a synthetic first-order house/pump model, hourly steps.

    Never used with live equipment or presented as an identified house model.
    """
    weather = [5 + 3 * math.sin((h - 6) * math.pi / 12) for h in range(24)]
    beam = [(0.0, 21.1, 0.35, min(c["signal_max"], max(c["signal_min"], 5.0)), [])]
    for outside in weather:
        candidates = []
        for cost, temp, heat, previous, path in beam:
            for delta in (-c["max_step"], 0, c["max_step"]):
                signal = min(c["signal_max"], max(c["signal_min"], previous + delta))
                new_heat = heat + 0.4 * (max(0, (18 - signal) * 0.028) - heat)
                new_temp = temp + 0.025 * (outside - temp) + new_heat
                violation = max(c["comfort_min"] - new_temp, 0, new_temp - c["comfort_max"])
                score = cost + (new_temp - c["target"]) ** 2 + 20 * violation ** 2 + 0.02 * delta ** 2
                candidates.append((score, new_temp, new_heat, signal, path + [{"indoor": round(new_temp, 3), "outdoor": round(outside, 2), "signal": round(signal, 2)}]))
        beam = sorted(candidates, key=lambda x: x[0])[:60]
    return beam[0][4]
