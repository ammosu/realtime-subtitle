# ui/__init__.py
"""UI 子套件：GTK3 可用性偵測、共用匯出。"""
import sys

_GTK3_AVAILABLE = False
if sys.platform != "win32":
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
        import cairo
        _GTK3_AVAILABLE = True
    except Exception:
        pass
