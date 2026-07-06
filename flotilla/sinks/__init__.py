from __future__ import annotations
from .base import StateSink, ProjectSnapshot
from . import web
from .web import WebSink
from .feishu import FeishuSink

REGISTRY: dict[str, StateSink] = {"web": WebSink(), "feishu": FeishuSink()}

def fan_out(snapshot: ProjectSnapshot) -> None:
    for sink in REGISTRY.values():
        try: sink.render(snapshot)
        except Exception: pass   # one sink failing must not break others
