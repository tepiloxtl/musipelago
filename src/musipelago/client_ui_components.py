# -*- coding: utf-8 -*-
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.dropdown import DropDown
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.properties import StringProperty, NumericProperty, ObjectProperty
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.animation import Animation

# Use correct import based on availability (Client vs Generator)
try:
    from musipelago.utils_client import KIVY_ICON
except ImportError:
    from musipelago.utils import KIVY_ICON


class ItemMenu(DropDown):
    caller = ObjectProperty(None) 
    def on_option_select(self, option_text):
        if self.caller:
            app = App.get_running_app()
            # Check if the app has a client_host_ui (Client)
            if hasattr(app, 'client_host_ui') and app.client_host_ui:
                app.client_host_ui.on_menu_action(option_text, self.caller)
            # Fallback for Generator/Other (if needed)
            elif hasattr(self.caller, 'menu_action'):
                self.caller.menu_action(option_text)
        self.dismiss()

class GenericPlaybackInfo(BoxLayout):
    track_title = StringProperty("No Track Playing")
    artist_album = StringProperty("")
    current_time = StringProperty("00:00")
    total_time = StringProperty("00:00")
    progress_value = NumericProperty(0)
    art_source = StringProperty(KIVY_ICON)

class ToastMessage(Label):
    def __init__(self, **kwargs):
        super().__init__(**kwargs); self.size_hint = (None, None); self.font_size = '15sp'
        self.padding = (dp(15), dp(10)); self.opacity = 0
        self.bind(texture_size=self.on_texture_size)
        with self.canvas.before:
            Color(0, 0, 0, 0.8); self.rect = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._update_rect, size=self._update_rect)
    def on_texture_size(self, instance, size):
        self.size = size; self.x = (Window.width - self.width) / 2; self.y = dp(50)
    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos; self.rect.size = instance.size