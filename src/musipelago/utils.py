# -*- coding: utf-8 -*-
import os, sys, json
# dotenv is no longer needed here
from unidecode import unidecode

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

# --- Jinja2 Filters ---

def filter_to_ascii(text):
    return unidecode(str(text))

def filter_py_json(value):
    return json.dumps(value)