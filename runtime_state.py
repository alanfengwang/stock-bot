"""
runtime_state.py — bot 运行态快照持久化

把后端进程的 regime / worker beats / 熔断状态写到磁盘，
供 dashboard 读取，避免各自进程里维护一份不一致的内存状态。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any


BASE = os.path.dirname(__file__)
RUNTIME_STATE_FILE = os.path.join(BASE, 'runtime_state.json')


def default_runtime_state() -> dict[str, Any]:
    return {
        'bot_pid': None,
        'bot_start_ts': 0.0,
        'updated_ts': 0.0,
        'regime': 'UNKNOWN',
        'worker_beats': {},
        'circuit_breaker': {
            'active': False,
            'status': '',
            'paused_until': '',
            'portfolio_hwm': 0.0,
        },
    }


def load_runtime_state() -> dict[str, Any]:
    if not os.path.exists(RUNTIME_STATE_FILE):
        return default_runtime_state()
    try:
        with open(RUNTIME_STATE_FILE) as f:
            data = json.load(f)
        state = default_runtime_state()
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items() if k in state})
            cb = data.get('circuit_breaker')
            if isinstance(cb, dict):
                state['circuit_breaker'].update(cb)
        return state
    except Exception:
        return default_runtime_state()


def save_runtime_state(state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(RUNTIME_STATE_FILE), exist_ok=True)
    payload = default_runtime_state()
    payload.update(state)
    payload['updated_ts'] = float(payload.get('updated_ts') or time.time())
    fd, tmp_path = tempfile.mkstemp(prefix='runtime_state_', suffix='.json', dir=BASE)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, RUNTIME_STATE_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
