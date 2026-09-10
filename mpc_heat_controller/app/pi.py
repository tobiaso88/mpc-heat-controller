"""PI shadow controller. Positive heat demand lowers simulated outdoor temperature."""
import math
import time

def clamp(v, low, high):
    return min(high, max(low, v))

class PI:
    def __init__(self):
        self.reset()

    def reset(self):
        self.integral = 0.0
        self.previous = None
        self.timestamp = None
        self.signature = None

    def step(self, c, items, clock=None):
        clock = time.monotonic() if clock is None else clock
        signature = (c['mode'], c['target'], tuple(c['indoor']), c['outdoor'], c['applied_signal'],
                     c['pi_kp'], c['pi_ki'], c['pi_limit'], c['pi_rate'], c['signal_min'], c['signal_max'])
        required = set(c['indoor'] + [c['outdoor']])
        values = {r['entity']:r['value'] for r in items if r['quality']=='OK' and r['value'] is not None and math.isfinite(r['value'])}
        if c['mode'] != 'shadow' or not c['indoor'] or not required.issubset(values):
            self.reset()
            return {'signal':None,'message':'PI väntar på skuggläge och giltiga inne- och utetemperaturer.'}
        restarting = self.signature != signature or self.timestamp is None or not 0 <= clock-self.timestamp <= 900
        if restarting:
            self.reset(); self.signature = signature
        dt = 0 if self.timestamp is None else (clock-self.timestamp)/3600
        inside = sum(values[e] for e in c['indoor'])/len(c['indoor'])
        outside = values[c['outdoor']]
        error = c['target']-inside
        p = c['pi_kp']*error
        delta = c['pi_ki']*error*dt
        candidate = clamp(self.integral+delta, -c['pi_limit'], c['pi_limit'])
        if self.previous is None:
            self.previous = clamp(values.get(c['applied_signal'],outside),c['signal_min'],c['signal_max'])

        def output(integral):
            raw = outside-p-integral
            limited = outside-clamp(p+integral,-c['pi_limit'],c['pi_limit'])
            limited = clamp(limited,c['signal_min'],c['signal_max'])
            limited = clamp(limited,self.previous-c['pi_rate']*dt,self.previous+c['pi_rate']*dt)
            return raw, clamp(limited,c['signal_min'],c['signal_max'])

        raw, signal = output(candidate)
        # Freeze integration if it would push farther into any output/rate limit.
        frozen = (raw-signal)*(-(candidate-self.integral)) > 1e-12
        if not frozen: self.integral=candidate
        raw, signal=output(self.integral)
        self.previous, self.timestamp = signal, clock
        return {'signal':round(signal,3),'error':round(error,3),'p':round(p,3),'i':round(self.integral,3),
                'compensation':round(signal-outside,3),'limited':abs(raw-signal)>1e-6,'integrator_frozen':frozen,
                'message':'PI-skuggläge. Inget kommando skickas.' + (' Omstart: I-delen nollställd; start från Ohmigos giltiga inställning eller utegivaren.' if restarting else '')}
