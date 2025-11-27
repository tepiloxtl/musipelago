# -*- coding: utf-8 -*-
import os, sys, json, traceback, logging
from unidecode import unidecode
import dotenv

def global_exception_handler(exc_type, exc_value, exc_traceback):
    """Catches and logs *all* unhandled exceptions without quitting."""
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        from kivy.logger import Logger
        Logger.critical(f"--- UNHANDLED GLOBAL EXCEPTION ---:\n{error_msg}")
    except ImportError:
        print(f"[CRITICAL] --- UNHANDLED GLOBAL EXCEPTION ---:\n{error_msg}", file=sys.stderr)
    print("="*80, file=sys.stderr)
    print(f"--- UNHANDLED GLOBAL EXCEPTION (RAW) ---:\n{error_msg}", file=sys.stderr)
    print("="*80, file=sys.stderr)

# Set the hook
sys.excepthook = global_exception_handler

# --- Config & Env ---
# This is no longer needed here, plugins load their own
# dotenv.load_dotenv()
# CLIENT_ID = ...

KIVY_ICON = 'data/logo/kivy-icon-64.png'

# --- Helpers ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev, pipx, and PyInstaller """
    
    # 1. PyInstaller --onefile
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    
    # 2. PyInstaller --onedir
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
        # Check for PyInstaller 6+ _internal folder
        internal_path = os.path.join(base_path, '_internal')
        if os.path.exists(internal_path):
            return os.path.join(internal_path, relative_path)
        return os.path.join(base_path, relative_path)

    # 3. Development / Pip / Pipx
    # Anchors to the location of THIS file (utils.py)
    base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def filter_to_ascii(text):
    return unidecode(str(text))