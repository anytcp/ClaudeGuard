"""
Единый мозг: единственное место, решающее «можно ли сейчас запускать Claude».

Verdict: "allowed" (защита off или IP в whitelist) / "blocked" (онлайн, IP не в
whitelist) / "offline" (IP не проверить). Fail closed — пускаем только "allowed".

Источник истины — демон (main.swift), публикующий вердикт в state.json на каждой
проверке. Все потребители (обёртка claude, лаунчер, CLI) читают его, поэтому
компоненты не расходятся. Если демон лёг (файл отсутствует/протух), мозг сам
делает STUN-first проверку, чтобы защита не отвалилась молча.
"""
import json
import os
import time

from src.ip_checker import perform_ip_check

STATE_PATH = os.path.expanduser("~/.config/claudeguard/state.json")
# Демон переписывает state.json минимум раз в 3с; 7с терпит один пропущенный тик.
STATE_MAX_AGE_SECONDS = 7.0


def read_daemon_state(max_age=STATE_MAX_AGE_SECONDS):
    """Свежий словарь состояния демона, или None если файла нет/протух."""
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        if time.time() - float(d.get("updated_at", 0)) <= max_age:
            return d
    except Exception:
        pass
    return None


def daemon_is_up():
    """True, если демон запущен и публикует свежее состояние."""
    return read_daemon_state() is not None


def get_verdict(config, timeout=2.0, allow_fallback=True):
    """
    Возвращает (verdict, ip, err), verdict ∈ "allowed"/"blocked"/"offline".

    Порядок: защита off → allowed; вердикт демона (мгновенно); иначе, если
    allow_fallback — своя STUN-first проверка. allow_fallback=False отдаёт
    "offline" когда демон лёг (для тесной петли, которой лучше пропустить тик,
    чем висеть на сети).
    """
    if not config.protection_enabled:
        return "allowed", None, None

    st = read_daemon_state()
    if st is not None:
        v = st.get("state", "offline")
        if v == "disabled":  # у демона свой словарь; нормализуем к нашему
            return "allowed", st.get("ip"), None
        return v, st.get("ip"), None

    if not allow_fallback:
        return "offline", None, "ClaudeGuard daemon not running"

    ip, is_allowed, err = perform_ip_check(config.allowed_ips, timeout=timeout)
    if ip is None:
        return "offline", None, err
    return ("allowed" if is_allowed else "blocked"), ip, err


def resolve_display_ip(config, timeout=2.0):
    """Текущий публичный IP только для показа (не для решения). Предпочитает
    последний IP демона, иначе живой STUN-first запрос. IP-строка или None."""
    st = read_daemon_state()
    if st is not None:
        ip = st.get("ip")
        if ip and ip not in ("No Internet", "…"):
            return ip
    try:
        from src.ip_checker import fetch_public_ip
        ip, _ = fetch_public_ip(timeout=timeout)
        return ip
    except Exception:
        return None
