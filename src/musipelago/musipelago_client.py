# -*- coding: utf-8 -*-
import os, sys, json, traceback, logging, uuid, ctypes
import requests, threading, hashlib, shutil

# --- KIVY IMPORTS ---
from musipelago.utils import resource_path
from kivy.logger import Logger
from kivy.config import Config
try:
    # Query Windows for screen size
    user32 = ctypes.windll.user32
    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)
    
    target_w = 1024
    target_h = 768
    
    # Add a buffer for taskbar/decorations (e.g. 50px)
    if screen_width < (target_w + 50) or screen_height < (target_h + 50):
        # Screen is too small: Configure Kivy to start MAXIMIZED
        Config.set('graphics', 'window_state', 'maximized')
        # We still set a minimum size just in case
        Config.set('graphics', 'width', '800')
        Config.set('graphics', 'height', '600')
        print("Window: Screen too small. Configured to start maximized.")
    else:
        # Screen is large enough: Configure EXACT SIZE
        Config.set('graphics', 'width', str(target_w))
        Config.set('graphics', 'height', str(target_h))
        
        # Optional: Force centering (Kivy usually centers by default if size is set here)
        # Config.set('graphics', 'position', 'auto')
        print(f"Window: Configured to {target_w}x{target_h}.")

except Exception as e:
    print(f"Window Config Error: {e}. Using default 1024x768.")
    Config.set('graphics', 'width', '1024')
    Config.set('graphics', 'height', '768')
Config.set('input', 'mouse', 'mouse,disable_multitouch')

from kivy.core.text import LabelBase, DEFAULT_FONT
try:
    font_path = resource_path(os.path.join('resources', 'NotoSansJP-Regular.ttf'))
    LabelBase.register(DEFAULT_FONT, font_path)
    Logger.info(f"Font: Registered default font: {font_path}")
except Exception as e:
    Logger.error(f"Font: Failed to register custom font: {e}")

from kivy.core.window import Window
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.uix.dropdown import DropDown
from kivy.uix.image import Image
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.gridlayout import GridLayout
from kivy.animation import Animation
from kivy.graphics import Color, RoundedRectangle
from kivy.storage.jsonstore import JsonStore
from kivy.uix.image import Image as KivyImage
from kivy.uix.relativelayout import RelativeLayout # Used for positioning the image within the button
from kivy.metrics import dp
from kivy.properties import StringProperty, ListProperty, ObjectProperty, BooleanProperty, NumericProperty
from plyer import filechooser
from kivy.resources import resource_add_path
from kivy.uix.spinner import Spinner

# --- ASYNCIO / WEBSOCKETS ---
import asyncio
import websockets
import ssl

# --- LOCAL IMPORTS ---
from musipelago.client_ui_components import GenericPlaybackInfo, ItemMenu, ToastMessage
from musipelago.utils_client import (
    filter_to_ascii, KIVY_ICON, global_exception_handler
)
from musipelago.plugin_loader import PluginManager
from musipelago.ff_audio_player import GenericAudioPlayer
from musipelago.backends import (
    GenericAlbum, GenericArtist, GenericPlaylist, GenericTrack
)

# --- Set global exception hook ---
sys.excepthook = global_exception_handler

# --- Suppress noisy logs ---
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.INFO)

# --- AsyncImageWithHeaders ---
class AsyncImageWithHeaders(Image):
    web_source = StringProperty(None); _cache_dir = ''; _cache_path = ''; _headers = {}
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not AsyncImageWithHeaders._cache_dir:
            app_dir = App.get_running_app().user_data_dir
            AsyncImageWithHeaders._cache_dir = os.path.join(app_dir, 'image_cache')
            if not os.path.exists(AsyncImageWithHeaders._cache_dir): os.makedirs(AsyncImageWithHeaders._cache_dir)
        if not AsyncImageWithHeaders._headers:
             AsyncImageWithHeaders._headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    def on_web_source(self, instance, url):
        if not url: self.source = KIVY_ICON; return
        if url.startswith('http://') or url.startswith('https://'):
            filename = hashlib.md5(url.encode('utf-8')).hexdigest() + '.jpg'
            self._cache_path = os.path.join(AsyncImageWithHeaders._cache_dir, filename)
            if os.path.exists(self._cache_path): self.source = self._cache_path
            else:
                self.source = KIVY_ICON
                threading.Thread(target=self._download_image, args=(url, self._cache_path)).start()
        else: self.source = url
    def _download_image(self, url, cache_path):
        try:
            response = requests.get(url, headers=self._headers, stream=True)
            if response.status_code == 200:
                with open(cache_path, 'wb') as f:
                    response.raw.decode_content = True
                    shutil.copyfileobj(response.raw, f)
                Clock.schedule_once(lambda dt: self._set_source(cache_path))
            else: Logger.error(f"ImageDownloader: Failed {url}, status {response.status_code}")
        except Exception as e: Logger.error(f"ImageDownloader: Exception for {url}: {e}")
    def _set_source(self, cache_path):
        if self._cache_path == cache_path: self.source = cache_path
    @classmethod
    def set_http_headers(cls, headers: dict): cls._headers = headers

class IconButton(ButtonBehavior, RelativeLayout):
    """
    A custom button that displays an image, ensuring the image maintains
    its aspect ratio and is not distorted, regardless of button size.
    """
    icon_source = StringProperty('') 
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # We define the image widget once and only update its properties
        # The internal image will take up 90% of the parent button space.
        self.image_widget = KivyImage(
            source=self.icon_source,
            size_hint=(0.9, 0.9), # CRITICAL: Make the image take up 90% of the button size
            pos_hint={'center_x': 0.5, 'center_y': 0.5}, # CRITICAL: Center the image within the button
            allow_stretch=True,
            keep_ratio=True
        )
        self.add_widget(self.image_widget)
        self.bind(icon_source=self.image_widget.setter('source'))

# --- CustomLoginPopup ---
class CustomLoginPopup(Popup):
    def __init__(self, login_widget: object, backend: object, **kwargs):
        super().__init__(**kwargs)
        self.title = f"Login to {backend.service_name.capitalize()}"
        self.size_hint = (0.9, None); self.auto_dismiss = False
        self.backend = backend; self.login_widget = login_widget
        main_layout = BoxLayout(orientation='vertical', spacing='10dp', padding='10dp')
        main_layout.add_widget(self.login_widget)
        button_layout = BoxLayout(size_hint_y=None, height=dp(44), spacing='10dp')
        cancel_btn = Button(text='Cancel', on_release=self.dismiss)
        login_btn = Button(text='Login', on_release=self.on_login_press)
        button_layout.add_widget(cancel_btn); button_layout.add_widget(login_btn)
        main_layout.add_widget(button_layout)
        self.content = main_layout
        self.height = getattr(login_widget, 'desired_popup_height', dp(400))
    def on_login_press(self, instance):
        Logger.info(f"CustomLoginPopup: Calling {self.backend.service_name}.login()")
        self.backend.login(login_widget=self.login_widget)
        self.dismiss()


# --- LoginPopup (No longer used on start) ---
class LoginPopup(Popup):
    # ... (This class is unchanged, but is no longer the first popup) ...
    def __init__(self, app_instance, **kwargs):
        super(LoginPopup, self).__init__(**kwargs)
        self.friendly_to_module_map = {}
        self.title = 'Select Service'
        self.size_hint = (0.8, 0.6); self.auto_dismiss = False
        self.app = app_instance
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text="Musipelago", font_size='24sp'))
        layout.add_widget(Label(text="Please select your music service."))
        layout.add_widget(Label(text="Service:"))
        self.backend_spinner = Spinner(text='No plugins found', values=[], size_hint_y=None, height='44dp')
        layout.add_widget(self.backend_spinner)
        self.status_label = Label(text="Status: Not Logged In", size_hint_y=0.4)
        layout.add_widget(self.status_label)
        self.login_button = Button(text="Connect", on_press=self.authenticate, size_hint_y=None, height='48dp')
        layout.add_widget(self.login_button)
        self.content = layout

    def authenticate(self, instance):
        selected_friendly_name = self.backend_spinner.text
        selected_backend_name = self.friendly_to_module_map.get(selected_friendly_name)
        if not selected_backend_name:
             self.app.on_login_failure(f"Could not find plugin for: {selected_friendly_name}"); return
        
        BackendClass = self.app.plugin_manager.get_plugin_component_class(
            selected_backend_name, "client_backend"
        )
        if not BackendClass:
            self.app.on_login_failure(f"Plugin '{selected_backend_name}' does not support client mode."); return
            
        self.app.backend = BackendClass(
            service_name_key=selected_backend_name,
            on_login_success=self.app.on_login_success,
            on_login_failure=self.app.on_login_failure
        )
        
        self.login_button.disabled = True; self.status_label.text = "Initializing login..."
        
        try:
            login_ui_signal = self.app.backend.get_login_ui()
        except Exception as e:
            self.app.on_login_failure(f"Plugin error: {e}"); return
            
        if login_ui_signal is None:
            Logger.info("LoginPopup: Backend provided no UI. Assuming external login.")
            Window.minimize(); self.app.backend.login(login_widget=None)
        elif isinstance(login_ui_signal, str) and login_ui_signal == "DIRECTORY_DIALOG":
            Logger.info("LoginPopup: Backend requested a directory dialog.")
            self.app.backend.login(login_widget=None)
        else:
            Logger.info("LoginPopup: Backend provided a custom UI. Showing CustomLoginPopup.")
            custom_popup = CustomLoginPopup(login_widget=login_ui_signal, backend=self.app.backend)
            custom_popup.open()
            self.dismiss()


# --- ArchipelagoLoginPopup (Modified) ---
class ArchipelagoLoginPopup(Popup):
    # ... (__init__, load_cached_settings, open_file_dialog, etc. are unchanged) ...
    def __init__(self, app_instance, **kwargs):
        super(ArchipelagoLoginPopup, self).__init__(**kwargs)
        self.title = 'Connect to Archipelago Server'
        self.size_hint = (0.8, 0.7)
        self.auto_dismiss = False
        self.app = app_instance
        self.json_file_path = None

        layout = BoxLayout(orientation='vertical', spacing=10, padding=20)
        form_grid = GridLayout(cols=2, spacing=10, size_hint_y=None, height='150dp')
        
        form_grid.add_widget(Label(text="Address:", halign='right', text_size=(self.width, None)))
        self.address_input = TextInput(hint_text="archipelago.gg:12345", multiline=False)
        form_grid.add_widget(self.address_input)
        
        form_grid.add_widget(Label(text="Name:", halign='right', text_size=(self.width, None)))
        self.name_input = TextInput(hint_text="Your Slot Name", multiline=False)
        form_grid.add_widget(self.name_input)
        
        form_grid.add_widget(Label(text="Password:", halign='right', text_size=(self.width, None)))
        self.password_input = TextInput(hint_text="(Optional)", multiline=False, password=True)
        form_grid.add_widget(self.password_input)
        
        layout.add_widget(form_grid)
        file_box = BoxLayout(orientation='horizontal', size_hint_y=None, height='48dp')
        
        self.json_button = Button(text="Select Game File...")
        self.json_button.bind(on_press=self.open_file_dialog)
        file_box.add_widget(self.json_button)
        
        self.file_status_label = Label(text="No file selected.")
        file_box.add_widget(self.file_status_label)
        
        layout.add_widget(file_box)
        layout.add_widget(BoxLayout(size_hint_y=1.0)) # Spacer

        self.status_label = Label(text="", size_hint_y=None, height='30dp')
        layout.add_widget(self.status_label)

        self.connect_button = Button(text="Connect", size_hint_y=None, height='48dp')
        self.connect_button.bind(on_press=self.on_connect_click)
        layout.add_widget(self.connect_button)

        self.content = layout
        self.load_cached_settings()

    def load_cached_settings(self):
        try:
            if self.app.store.exists('connection_info'):
                data = self.app.store.get('connection_info')
                self.address_input.text = data.get('address', '')
                self.name_input.text = data.get('name', '')
                self.password_input.text = data.get('password', '')
                json_path = data.get('json_path', '')
                if json_path and os.path.exists(json_path):
                    self.json_file_path = json_path
                    self.file_status_label.text = os.path.basename(json_path)
        except Exception as e:
            Logger.error(f"Cache: Failed to load settings: {e}")

    def open_file_dialog(self, instance):
        instance.disabled = True
        self.status_label.text = "Opening file dialog..."
        threading.Thread(target=self._run_file_chooser, args=(instance,)).start()

    def _run_file_chooser(self, button_instance):
        try:
            selection = filechooser.open_file(
                title="Select your Musipelago JSON game file",
                filters=[("JSON files", "*.json")]
            )
            Clock.schedule_once(lambda dt: self._handle_selection(selection, button_instance))
        except Exception as e:
            Logger.error(f"Plyer FileChooser: {e}")
            Clock.schedule_once(lambda dt: self._handle_selection(None, button_instance))

    def _handle_selection(self, selection, button_instance):
        button_instance.disabled = False
        if not selection or not selection[0]:
            if not self.json_file_path:
                 self.status_label.text = "No file selected."
            return
        file_path = selection[0]
        if not file_path.lower().endswith('.json'):
            self.status_label.text = "Error: Selected file must be a .json file."
            self.json_file_path = None
            return
        self.json_file_path = file_path
        self.file_status_label.text = f"{os.path.basename(file_path)}"
        self.status_label.text = "File selected. Ready to connect."
        
    def on_connect_click(self, instance):
        """
        Validates input and tells the main app to START THE LOGIN FLOW.
        It no longer connects to AP directly.
        """
        address = self.address_input.text.strip()
        name = self.name_input.text.strip()
        password = self.password_input.text
        
        if not address:
            self.status_label.text = "Error: Address field is required."; return
        if not name:
            self.status_label.text = "Error: Name field is required."; return
        if not self.json_file_path:
            self.status_label.text = "Error: You must select a game .json file."; return
        
        self.status_label.text = "Validating game file..."
        self.connect_button.disabled = True
        self.json_button.disabled = True
        
        # --- NEW FLOW ---
        # Pass data to the main app to start the full login process
        self.app.on_ap_form_submitted(
            address, 
            name, 
            password, 
            self.json_file_path
        )
        
    def on_connection_success(self):
        # This is now only called when AP connection is successful
        self.status_label.text = "Success! Loading game..."
        Logger.info("AP: Connection successful, dismissing login popup.")
        Clock.schedule_once(lambda dt: self.dismiss(), 0.5)

    def on_connection_failed(self, error_message):
        # This is called for AP connection failures OR plugin login failures
        self.status_label.text = f"Error: {error_message}"
        self.connect_button.disabled = False
        self.json_button.disabled = False


# --- ToastMessage, ItemMenu, CustomListItem, ListContainer (Unchanged) ---
class CustomListItem(ButtonBehavior, BoxLayout):
    text_line_1 = StringProperty('Line 1'); text_line_2 = StringProperty('Line 2')
    text_line_3 = StringProperty('Line 3'); text_line_4 = StringProperty('Line 4')
    image_source = StringProperty(KIVY_ICON); list_id = StringProperty('')
    raw_item_type = StringProperty(''); is_finished = BooleanProperty(False)
    is_owned = BooleanProperty(True); has_hint = BooleanProperty(False)
    all_tracks_finished = BooleanProperty(False); raw_title = StringProperty()
    raw_artist = StringProperty(); raw_album_type = StringProperty()
    raw_total_tracks = StringProperty(); raw_image_url = StringProperty()
    raw_uri = StringProperty(); menu = ObjectProperty(None)

    def handle_menu_click(self, button_instance):
        """
        This is the old 'open_menu' logic.
        It builds and shows the menu.
        """
        # If menu exists and is open, just dismiss it.
        if self.menu and self.menu.parent:
             self.menu.dismiss()
             return True # Consume the click

        # If it doesn't exist, create it
        self.menu = ItemMenu(caller=self, auto_width=False, width=dp(200))
        self.menu.clear_widgets()
        
        app = App.get_running_app()
        cheat_color = "ff8888"
        button_added = False

        if self.raw_item_type == 'album' or self.raw_item_type == 'playlist':
            btn = Button(text="Play", size_hint_y=None, height=dp(44))
            btn.bind(on_release=lambda x: self.menu.on_option_select("Play Album"))
            self.menu.add_widget(btn)

            btn_hint = Button(text="Hint", size_hint_y=None, height=dp(44))
            btn_hint.bind(on_release=lambda x: self.menu.on_option_select("Hint"))
            self.menu.add_widget(btn_hint)
            button_added = True
        
        elif self.raw_item_type == 'track':
            if app.allow_playing_any_track or app.cheat_mode:
                btn_text = f"[color={cheat_color}]Play Track[/color]" if (not app.allow_playing_any_track and app.cheat_mode) else "Play Track"
                btn = Button(text=btn_text, markup=True, size_hint_y=None, height=dp(44))
                btn.bind(on_release=lambda x: self.menu.on_option_select("Play Track"))
                self.menu.add_widget(btn)
                button_added = True

            if app.cheat_mode:
                cheat_text = f"[color={cheat_color}]Send Location[/color]"
                btn_send_loc = Button(text=cheat_text, markup=True, size_hint_y=None, height=dp(44))
                btn_send_loc.bind(on_release=lambda x: self.menu.on_option_select("Send Location"))
                self.menu.add_widget(btn_send_loc)
                button_added = True

        if not button_added:
            btn = Button(text="No actions", size_hint_y=None, height=dp(44))
            btn.bind(on_release=lambda x: self.menu.on_option_select("No actions"))
            self.menu.add_widget(btn)

        self.menu.open(button_instance)
        return True # Consume the click

    def menu_action(self, option_text):
        """
        This is the NEW method. It's called by ItemMenu and
        delegates the action to the plugin.
        """
        if option_text == "No actions":
            return
            
        app = App.get_running_app()
        if app.client_host_ui:
            app.client_host_ui.on_menu_action(option_text, self)
        else:
            Logger.warning("CustomListItem: menu_action called but no client_host_ui is active.")

class ListContainer(BoxLayout):
    list_one_data = ListProperty(); list_two_data = ListProperty(); apworld_data = ListProperty()


# --- RootLayout (Refactored) ---
class RootLayout(BoxLayout):
    _status_text_internal = StringProperty("App started. Ready.")
    status_text = StringProperty()
    ap_status_text = StringProperty("Not connected to Archipelago.")
    is_muted = BooleanProperty(False)
    previous_volume = NumericProperty(50)
    is_playing = BooleanProperty(False)
    playback_info_widget = ObjectProperty(None)

    def __init__(self, **kwargs):
        super(RootLayout, self).__init__(**kwargs)
        # Bind the RootLayout's is_playing property to the method that updates the button text
        self.bind(is_playing=self.on_is_playing_changed)
        self._last_known_volume = 50 
        self.set_status(self._status_text_internal)

    def set_status(self, text):
        self._status_text_internal = text
        if App.get_running_app().cheat_mode:
            self.status_text = f"{text} [color=ff8888](Cheat mode activated)[/color]"
        else:
            self.status_text = text

    def on_is_playing_changed(self, instance, value):
        """Method to update button text (This is the method the KV was trying to access)"""
        if value:
            self.ids.play_pause_button.text = "Pause"
        else:
            self.ids.play_pause_button.text = "Play"

    def format_duration(self, ms):
        if not isinstance(ms, (int, float)) or ms < 0: return "00:00"
        seconds = int((ms / 1000) % 60); minutes = int((ms / (1000 * 60)) % 60)
        return f"{minutes:02}:{seconds:02}"

    def on_stop_click(self):
        """Delegates Stop to the active plugin and the audio player."""
        app = App.get_running_app()
        if app.audio_player:
            app.audio_player.stop()

        if app.client_host_ui:
             app.client_host_ui.on_stop_click() # Plugin handles its own status updates/cleanup
        
        # Reset UI status
        self.is_playing = False
        if self.playback_info_widget:
            self.playback_info_widget.track_title = "Stopped"
            self.playback_info_widget.progress_value = 0
            self.playback_info_widget.current_time = "00:00"
    
    def on_mute_click(self):
        """Toggles mute state and delegates to plugin."""
        app = App.get_running_app()
        self.is_muted = not self.is_muted
        slider_value = self.ids.volume_slider.value
        
        if self.is_muted:
            self.previous_volume = slider_value
            self.ids.volume_slider.value = 0
            self.on_volume_change(0)
            
            if app.client_host_ui:
                app.client_host_ui.on_mute_toggle(True)
        else:
            self.ids.volume_slider.value = self.previous_volume
            self.on_volume_change(self.previous_volume)
            
            if app.client_host_ui:
                app.client_host_ui.on_mute_toggle(False)

    def on_list_item_click(self, list_item):
        app = App.get_running_app()
        if app.client_host_ui:
            app.client_host_ui.on_list_item_click(list_item)

    def update_playback_state(self):
        app = App.get_running_app()
        if app.client_host_ui:
            app.client_host_ui.update_playback_state()

    def on_play_pause_click(self):
        app = App.get_running_app()
        if app.client_host_ui:
            app.client_host_ui.on_play_pause_click()
            
    def on_device_select(self, spinner_text):
        app = App.get_running_app()
        if app.client_host_ui:
            app.client_host_ui.on_device_select(spinner_text)

    def on_volume_change(self, value):
        """Called by Slider."""
        if value > 0 and self.is_muted:
            self.is_muted = False # Unmute if user drags slider
        
        # Pass 0-1 volume to the audio player
        app = App.get_running_app()
        if app.audio_player:
            app.audio_player.set_volume(value / 100.0)
            
        # Delegate to plugin
        if app.client_host_ui:
             app.client_host_ui.on_volume_change(int(value))

    def on_settings_icon_click(self):
        """Opens the plugin-specific settings popup."""
        app = App.get_running_app()
        if not app.client_host_ui:
            app.show_toast("Not connected to a music service.")
            return

        settings_ui = app.client_host_ui.get_settings_ui()
        
        if settings_ui:
            popup = Popup(
                title=f"{app.backend.service_name.capitalize()} Settings",
                content=settings_ui,
                size_hint=(0.7, 0.7),
                auto_dismiss=True
            )
            popup.open()
        else:
            app.show_toast("No specific settings available for this plugin.")

    # --- UI-Only Methods (Remain in RootLayout) ---
    def populate_track_list(self, container_uri, local_image_path=None):
        """
        Populates the right-hand RecycleView with tracks.
        NOW READS GENERIC DATA.
        """
        Logger.info(f"--- populate_track_list started for {container_uri} ---")
        try:
            app = App.get_running_app()
            
            # container_data is now a GenericAlbum object
            container_data = app.album_data_cache.get(container_uri)
            if not container_data:
                Logger.error(f"Could not find album data for URI: {container_uri}")
                self.set_status("Error: Album data not found."); return

            # raw_items is now a list of GenericTrack objects
            raw_items = container_data.tracks
            
            track_list_for_rv = []
            for track in raw_items: # 'track' is a GenericTrack
                if not track: continue
                
                track_uri = track.uri
                if not track_uri:
                    Logger.warning(f"Skipping track with no URI: {track.title}")
                    continue
                
                apworld_name = app.apworld_map.get(track_uri, "Unknown Track")
                
                # Get data directly from the GenericTrack
                title = track.title
                duration_ms = track.duration_ms
                artists = track.artist

                track_progress_data = app.track_progress.get(track_uri, {})
                is_finished = track_progress_data.get('is_finished', False)
                text_line_3 = track_progress_data.get('hint_text')
                has_hint_bool = text_line_3 is not None
                if not text_line_3:
                    text_line_3 = app.apworld_map.get(track_uri, "Unknown Track")

                item_data = {
                    'text_line_1': title,
                    'text_line_2': self.format_duration(duration_ms),
                    'text_line_3': text_line_3,
                    'text_line_4': artists,
                    # Get the single, processed image URL
                    'image_source': local_image_path or container_data.display_image_url or container_data.image_url or KIVY_ICON,
                    'list_id': track_uri,
                    'raw_item_type': 'track',
                    'raw_uri': track_uri,
                    'raw_title': title,
                    'raw_artist': artists,
                    'is_finished': is_finished,
                    'has_hint': has_hint_bool
                }
                track_list_for_rv.append(item_data)
                
            self.ids.list_container.ids.track_rv.data = track_list_for_rv
            self.set_status(f"Showing {len(track_list_for_rv)} tracks.")
            Logger.info("--- populate_track_list finished successfully ---")

        except Exception as e:
            Logger.error(f"FATAL ERROR in populate_track_list: {e}", exc_info=True)
            self.set_status(f"Error: Failed to build track list. See console.")
        
    def update_album_ui(self, album_uri):
        album_rv = self.ids.list_container.ids.album_rv
        for i, album_data in enumerate(album_rv.data):
            if album_data['raw_uri'] == album_uri:
                album_data['is_owned'] = True; album_rv.refresh_from_data()
                Logger.info(f"UI updated for album: {album_data['raw_title']}"); break

    def update_album_hint_text(self, album_uri, hint_text):
        album_rv = self.ids.list_container.ids.album_rv 
        for i, album_data in enumerate(album_rv.data):
            if album_data['raw_uri'] == album_uri:
                album_data['text_line_4'] = hint_text; album_rv.refresh_from_data()
                Logger.info(f"UI updated hint for album: {album_data['raw_title']}"); break

    def update_album_hint_status(self, album_uri, has_hint_status):
        album_rv = self.ids.list_container.ids.album_rv  
        for i, album_data in enumerate(album_rv.data):
            if album_data['raw_uri'] == album_uri:
                if album_data.get('has_hint') == has_hint_status: break 
                album_data['has_hint'] = has_hint_status; album_rv.refresh_from_data()
                Logger.info(f"UI updated hint status for album: {album_data['raw_title']}"); break

    def update_album_all_tracks_finished_status(self, album_uri, all_finished_status):
        # Logger.info(f"DEBUG: Requesting UI update for {album_uri} -> Finished={all_finished_status}")
        album_rv = self.ids.list_container.ids.album_rv  
        for i, album_data in enumerate(album_rv.data):
            if album_data['raw_uri'] == album_uri:
                # Logger.info(f"DEBUG: > Found matching UI Item: {album_data['text_line_1']}")
                if album_data.get('all_tracks_finished') == all_finished_status: break
                album_data['all_tracks_finished'] = all_finished_status; album_rv.refresh_from_data()
                Logger.info(f"UI updated 'all_tracks_finished' for album: {album_data['raw_title']}"); break

    def check_and_update_album_completion(self, container_uri):
        """
        Checks if all tracks for a given album are finished.
        NOW READS GENERIC DATA.
        """
        app = App.get_running_app()
        if not container_uri: return
        
        # container_data is now a GenericAlbum
        container_data = app.album_data_cache.get(container_uri)
        if not container_data:
            Logger.warning(f"UI: Cannot check completion for {container_uri}, not in cache.")
            return
        
        # Logger.info(f"DEBUG: Checking completion for container: {container_data.title} ({container_uri})")

        all_tracks_complete = True
        # raw_items is now a list of GenericTrack
        raw_items = container_data.tracks
        
        if not raw_items: all_tracks_complete = False
        
        for track in raw_items: # 'track' is a GenericTrack
            if not track: continue
            track_uri = track.uri
            if not track_uri: continue
            
            track_data = app.track_progress.get(track_uri)
            if not track_data or not track_data.get('is_finished', False):
                # Logger.info(f"DEBUG: > Incomplete track found: {track.title} ({track_uri})")
                all_tracks_complete = False
                break 
        # if all_tracks_complete:
        #     Logger.info(f"DEBUG: > ALL TRACKS COMPLETE for {container_data.title}")
        self.update_album_all_tracks_finished_status(container_uri, all_tracks_complete)

    def update_track_hint_text(self, track_uri, hint_text):
        track_rv = self.ids.list_container.ids.track_rv
        for i, track_data in enumerate(track_rv.data):
            if track_data['raw_uri'] == track_uri:
                track_data['text_line_3'] = hint_text; track_rv.refresh_from_data()
                Logger.info(f"UI updated hint for track: {track_data['raw_title']}"); break

    def update_track_ui(self, track_uri):
        track_rv = self.ids.list_container.ids.track_rv
        track_updated = False
        for i, track_data in enumerate(track_rv.data):
            if track_data['raw_uri'] == track_uri:
                if not track_data['is_finished']:
                    track_data['is_finished'] = True; track_updated = True
                track_rv.refresh_from_data()
                Logger.info(f"UI updated for track: {track_uri}"); break
        if track_updated:
            app = App.get_running_app()
            try:
                track_prog = app.track_progress.get(track_uri)
                if track_prog:
                    parent_uri = track_prog.get('parent_uri')
                    # Logger.info(f"DEBUG: Track {track_uri} finished.")
                    # Logger.info(f"DEBUG: > Mapped to Parent Album: {parent_uri}")
                    if parent_uri:
                        self.check_and_update_album_completion(parent_uri)
                    else:
                        Logger.warning(f"DEBUG: Track {track_uri} has NO parent_uri!")
            except Exception as e:
                Logger.error(f"UI: Failed to check parent album completion: {e}")


# --- ArchipelagoClient ---
class ArchipelagoClient:
    def __init__(self, app, uri, name, password, game_name, cache_dir):
        self.app = app; self.uri = uri; self.name = name; self.password = password
        self.game_name = game_name; self.cache_dir = cache_dir
        self.ws = None; self.handshake_complete = False; self.error_reported = False
        self.received_connected = False; self.received_datapackage = False
        self.loop = None; self.slot_id = None; self.slot_info = {}
        self.server_checksums = {}; self.game_data_packages = {}
        self.missing_locations = set(); self.checked_locations = set()
        self.received_items = []; self.owned_item_ids = set()
        self.owned_item_names = set(); self.id_to_item_name = {}
        self.id_to_location_name = {}
        self.app_is_ready = False
        self.victory_reported = False
    def start_client_loop(self):
        loop = None
        try:
            self.loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.run())
        except Exception as e:
            Logger.error(f"AP: Async loop failed: {e}")
            if not self.error_reported: self.report_error(f"Connection loop error: {e}")
        finally:
            if self.loop: self.loop.close()
            Logger.info("AP: Websocket thread finished.")
    async def run(self):
        Logger.info(f"AP: Async task started. Connecting to {self.uri}...")
        ssl_context = None
        if self.uri.startswith("wss://"): ssl_context = ssl.create_default_context()
        try:
            async with websockets.connect(self.uri, ssl=ssl_context, max_size=None) as ws:
                self.ws = ws
                Logger.info("AP: Connection open. Waiting for initial packet...")
                Clock.schedule_once(lambda dt: self.app.root.set_status("Connected! Waiting for server..."))
                async for message in ws:
                    await self.handle_message_list(message)
        except Exception as e:
            Logger.error(f"AP: Websocket loop error: {e}")
            self.report_error(f"{e}")
    def _sync_owned_items(self):
        """
        Resolves item IDs into names. This is the core
        fix for the ReceivedItems/DataPackage race condition.
        It's safe to call multiple times.
        """
        if not self.app_is_ready:
            Logger.info("AP: _sync_owned_items: Skipping, app is not ready (name_to_uri_map is not built).")
            return # Not ready
        # We can't do anything if either of these is missing
        if not self.owned_item_ids or not self.id_to_item_name:
            Logger.info("AP: _sync_owned_items: Not ready (missing IDs or name map).")
            return # Not ready, will be called again by the other handler

        # Logger.info("AP: _sync_owned_items: Syncing item IDs to names.")
        is_at_login = not self.received_connected # Check if 'Connected' has arrived
        
        for item_id in self.owned_item_ids:
            item_name = self.id_to_item_name.get(item_id)
            
            # If we have a name, and it's not already in our set of names...
            if item_name and item_name not in self.owned_item_names:
                self.owned_item_names.add(item_name)
                
                if is_at_login:
                    Logger.info(f"AP: Registered starting item: {item_name}")
                else:
                    # This is a new item found during gameplay
                    Logger.info(f"AP: Received new item: {item_name}")
                    self.app.show_toast(f"Received: {item_name}")
                    
                    # We also need to update the app's *live* state
                    uri = self.app.name_to_uri_map.get(item_name)
                    
                    if uri and uri in self.app.album_data_cache:
                        Logger.info(f"AP: Unlocked new album: {item_name} (URI: {uri})")
                        self.app.owned_albums.add(uri)
                        # Schedule a UI update
                        Clock.schedule_once(lambda dt, u=uri: self.app.root.update_album_ui(u))
                    else:
                        Logger.warning(f"AP: Received item '{item_name}' but its URI '{uri}' is not in the album cache.")
                    
        victory_item_name = "Album finished!"
        victory_item_id = None
        
        # Find the ID from the map
        # (Optimization: In a real app, cache this ID so we don't loop every time)
        for i_id, i_name in self.id_to_item_name.items():
            if i_name == victory_item_name:
                victory_item_id = i_id
                break
        
        if victory_item_id:
            # Count how many times we have received this specific item ID
            # We look at the raw received_items list, which contains duplicates
            victory_count = sum(1 for item in self.received_items if item.get('item') == victory_item_id)
            
            total_albums = len(self.app.ordered_album_uris)
            
            # Logger.debug(f"AP: Victory Check: {victory_count}/{total_albums} albums finished.")

            if total_albums > 0 and victory_count >= total_albums:
                if not self.victory_reported:
                    Logger.info(f"AP: VICTORY! All {total_albums} albums finished.")
                    self.app.show_toast("VICTORY! All albums finished!")
                    self.send_status_update(30) # ClientStatus.GOAL
                    self.victory_reported = True
    
    def send_status_update(self, status_code):
        if not self.ws or not self.loop: return
        asyncio.run_coroutine_threadsafe(self._async_send_status_update(status_code), self.loop)

    async def _async_send_status_update(self, status_code):
        try:
            packet = {"cmd": "StatusUpdate", "status": status_code}
            message_json = json.dumps([packet])
            print(f">>> SENDING: {message_json}"); await self.ws.send(message_json)
            Logger.info(f"AP: Sent StatusUpdate: {status_code}")
        except Exception as e: Logger.error(f"AP: Could not send StatusUpdate: {e}")

    def check_game_ready(self):
        if self.received_connected and self.received_datapackage:
            Logger.info("AP: Received 'Connected' and 'DataPackage'. Applying data.")
            Clock.schedule_once(self.app.on_connection_success)
            Clock.schedule_once(self.app.apply_archipelago_data)
    def on_datapackage_received(self):
        self.received_datapackage = True; self.check_game_ready()
    async def handle_message_list(self, message):
        print(f"<<< RECEIVED: {message}")
        try: packets = json.loads(message)
        except json.JSONDecodeError: Logger.error(f"AP: Could not decode server message: {message}"); return
        for packet in packets:
            if not isinstance(packet, dict): continue
            cmd = packet.get('cmd')
            if not self.handshake_complete:
                if cmd == 'RoomInfo':
                    try:
                        games_list = packet.get("games", [])
                        if self.game_name in games_list:
                            self.handshake_complete = True; await self.send_connect_packet()
                            self.server_checksums = packet.get('datapackage_checksums', {})
                            await self._check_and_request_datapackages(self.server_checksums)
                        else:
                            error_msg = f"Game '{self.game_name}' not found in this room."
                            self.report_error(error_msg); await self.ws.close()
                    except Exception as e: self.report_error(f"Handshake error: {e}"); await self.ws.close()
                else: self.report_error("Unexpected server response."); await self.ws.close()
            else:
                try:
                    if cmd == 'ConnectionRefused':
                        errors = packet.get('errors', ['Unknown connection error.'])
                        self.report_error(", ".join(errors)); await self.ws.close()
                    elif cmd == 'Connected':
                        self.slot_id = packet.get('slot'); self.slot_info = packet.get('slot_info', {})
                        slot_data = packet.get('slot_data', {}); options = slot_data.get('options', {})
                        self.app.allow_playing_any_track = bool(options.get('AllowPlayingAnyTrack', 0))
                        self.missing_locations = set(packet.get('missing_locations', []))
                        self.checked_locations = set(packet.get('checked_locations', []))
                        self.received_connected = True; self.check_game_ready()
                    elif cmd == 'ReceivedItems':
                        index = packet.get('index', 0)
                        items = packet.get('items', [])
                        
                        # Only append items that are new.
                        # AP sends items starting from 'index'.
                        if index == len(self.received_items):
                            self.received_items.extend(items)
                            
                            # Update the unique set for fast lookup
                            for item in items:
                                if item_id := item.get('item'): 
                                    self.owned_item_ids.add(item_id)
                            
                            self._sync_owned_items()
                        else:
                            # If indices don't match, we might be desynced or receiving a redelivery.
                            # For simple clients, ignoring is safer than duplicating.
                            Logger.warning(f"AP: Received item packet index {index} but have {len(self.received_items)}. Ignoring mismatch.")
                    elif cmd == 'DataPackage':
                        data = packet.get('data', {})
                        for game_name, game_data in data.get('games', {}).items():
                            try:
                                with open(os.path.join(self.cache_dir, f"{game_name}.json"), 'w', encoding='utf-8') as f:
                                    json.dump(game_data, f, indent=4)
                            except Exception as e: Logger.error(f"AP: Failed to write cache for '{game_name}': {e}")
                            item_map = game_data.get('item_name_to_id', {}); location_map = game_data.get('location_name_to_id', {})
                            self.game_data_packages[game_name] = {
                                'id_to_item_name': {v: k for k, v in item_map.items()},
                                'id_to_location_name': {v: k for k, v in location_map.items()}
                            }
                            if game_name == self.game_name:
                                self.id_to_item_name = self.game_data_packages[game_name]['id_to_item_name']
                                self.id_to_location_name = self.game_data_packages[game_name]['id_to_location_name']
                                self._sync_owned_items(); self.on_datapackage_received()
                    elif cmd == 'RoomUpdate':
                        if newly_checked := packet.get('checked_locations', []):
                            self.checked_locations.update(newly_checked)
                    elif cmd == 'PrintJSON':
                        data_parts = packet.get('data', []); message_text = ""
                        for part in data_parts:
                            if not isinstance(part, dict): continue
                            text = part.get('text', ''); part_type = part.get('type')
                            try:
                                if part_type == 'player_id': message_text += self.get_ap_info(player_id=text, entity_id=0)[0]
                                elif part_type == 'item_id': message_text += self.get_ap_info(part.get('player'), int(text), is_location=False)[2]
                                elif part_type == 'location_id': message_text += self.get_ap_info(part.get('player'), int(text), is_location=True)[2]
                                else: message_text += text
                            except Exception: message_text += text
                        if message_text:
                            Clock.schedule_once(lambda dt, m=message_text: setattr(self.app.root, 'ap_status_text', m))
                        if packet.get('type') == 'Hint':
                            if item_data := packet.get('item'):
                                location_world_id = item_data.get('player')
                                if packet.get('receiving') == self.slot_id:
                                    hinted_item_name = self.get_ap_info(self.slot_id, item_data.get('item'), is_location=False)[2]
                                    loc_player_name, _, loc_name = self.get_ap_info(location_world_id, item_data.get('location'), is_location=True)
                                    hint_text = f"At: {loc_player_name}'s {loc_name}"
                                    if album_uri := self.app.name_to_uri_map.get(hinted_item_name):
                                        Clock.schedule_once(lambda dt, u=album_uri, t=hint_text: self.app.root.update_album_hint_text(u, t))
                                elif location_world_id == self.slot_id:
                                    hinter_name, _, item_name = self.get_ap_info(packet.get('receiving'), item_data.get('item'), is_location=False)
                                    location_name = self.get_ap_info(self.slot_id, item_data.get('location'), is_location=True)[2]
                                    if track_uri := self.app.name_to_uri_map.get(location_name):
                                        hint_text = f"Hinted for {hinter_name}: {item_name}"
                                        Clock.schedule_once(lambda dt, u=track_uri, t=hint_text: self.app.store_track_hint(u, t))
                except Exception as e: Logger.error(f"AP: Error parsing game packet (cmd: {cmd}): {e}")
    async def _check_and_request_datapackages(self, server_checksums):
        if not server_checksums: await self.send_data_package_request([self.game_name]); return
        games_to_request = []; all_cached = True
        for game_name, server_checksum in server_checksums.items():
            cache_file_path = os.path.join(self.cache_dir, f"{game_name}.json")
            if not os.path.exists(cache_file_path):
                games_to_request.append(game_name)
                if game_name == self.game_name: all_cached = False
            else:
                try:
                    with open(cache_file_path, 'r', encoding='utf-8') as f: cached_data = json.load(f)
                    if cached_data.get('checksum') != server_checksum:
                        games_to_request.append(game_name)
                        if game_name == self.game_name: all_cached = False
                except Exception:
                    games_to_request.append(game_name)
                    if game_name == self.game_name: all_cached = False
        if games_to_request: await self.send_data_package_request(games_to_request)
        if all_cached:
            Logger.info("AP: Our game's datapackage is already cached.")
            Clock.schedule_once(self.app.load_datapackage_from_cache)
    async def send_connect_packet(self):
        try:
            connect_packet = {"cmd": "Connect", "game": self.game_name, "items_handling": 7, "name": self.name, "password": self.password, "slot_data": True, "tags": [], "version": {"class": "Version", "build": 4, "major": 0, "minor": 6}, "uuid": self.app.client_uuid}
            message_json = json.dumps([connect_packet])
            print(f">>> SENDING: {message_json}"); await self.ws.send(message_json)
        except Exception as e: self.report_error(f"Failed to send connect packet: {e}")
    async def send_data_package_request(self, games_list):
        try:
            package_request = {"cmd": "GetDataPackage", "games": games_list}
            message_json = json.dumps([package_request])
            print(f">>> SENDING: {message_json}"); await self.ws.send(message_json)
        except Exception as e: self.report_error(f"Failed to send data request: {e}")
    def send_location_check(self, location_id):
        if self.ws and self.loop: asyncio.run_coroutine_threadsafe(self._async_send_location_check(location_id), self.loop)
    async def _async_send_location_check(self, location_id):
        try:
            packet = {"cmd": "LocationChecks", "locations": [location_id]}
            message_json = json.dumps([packet])
            print(f">>> SENDING: {message_json}"); await self.ws.send(message_json)
        except Exception as e: Logger.error(f"AP: Could not send LocationChecks: {e}")
    def send_chat_message(self, text):
        if self.ws and self.loop: asyncio.run_coroutine_threadsafe(self._async_send_say(text), self.loop)
    async def _async_send_say(self, text):
        try:
            packet = {"cmd": "Say", "text": text}
            message_json = json.dumps([packet])
            print(f">>> SENDING: {message_json}"); await self.ws.send(message_json)
        except Exception as e: Logger.error(f"AP: Could not send Say packet: {e}")
    def report_error(self, error_message):
        if self.error_reported: return
        self.error_reported = True; Logger.warning(f"AP: Reporting error: {error_message}")
        Clock.schedule_once(lambda dt: self.app.on_connection_failed(error_message))
    def get_ap_info(self, player_id, entity_id, is_location=False):
        player_id_str = str(player_id)
        player_name = "Unknown Player"; game_name = "Unknown Game"; apworld_name = f"Unknown Entity ({entity_id})"
        try:
            if player_id_str in self.slot_info:
                slot = self.slot_info[player_id_str]
                player_name = slot.get('name', player_name); game_name = slot.get('game', game_name)
            elif player_id == 0:
                player_name = "Archipelago"; game_name = "Archipelago"
            if game_name in self.game_data_packages:
                maps = self.game_data_packages[game_name]
                id_map = maps.get('id_to_location_name' if is_location else 'id_to_item_name', {})
                apworld_name = id_map.get(entity_id, apworld_name)
        except Exception as e: Logger.error(f"AP: Error in get_ap_info: {e}")
        return player_name, game_name, apworld_name


# --- MusipelagoClientApp (Refactored) ---
class MusipelagoClientApp(App):
    
    plugin_manager = ObjectProperty(None)
    backend = ObjectProperty(None)
    client_host_ui = ObjectProperty(None)
    AsyncImageWithHeaders = AsyncImageWithHeaders
    audio_player = ObjectProperty(None)

    # --- Stored data for login flow ---
    ap_address = StringProperty("")
    ap_name = StringProperty("")
    ap_password = StringProperty("")
    game_data = ObjectProperty(None)
    backend_data = ObjectProperty(None)
    
    def build(self):
        self.apworld_map = {}; self.album_data_cache = {}
        self.ordered_album_uris = []; self.track_progress = {}; self.owned_albums = set()
        self.name_to_uri_map = {}; self.store = JsonStore('musipelago.json')
        self.cache_dir = None; self.allow_playing_any_track = False
        self.cheat_mode = False; self.ap_client = None; self.client_uuid = None
        self.json_path = None
        
        resource_add_path(resource_path(''))
        self.plugin_manager = PluginManager(plugin_dir=resource_path('plugins'))
        self.plugin_manager.discover_plugins()

        if getattr(sys, 'frozen', False): base_path = os.path.dirname(sys.executable)
        else: base_path = os.path.abspath(os.path.dirname(__file__))
        store_path = os.path.join(base_path, 'musipelago.json')
        Logger.info(f"Cache: Using JsonStore at {store_path}"); self.store = JsonStore(store_path)
        self.cache_dir = os.path.join(base_path, 'datapackage_cache')
        if not os.path.exists(self.cache_dir):
            try: os.makedirs(self.cache_dir)
            except Exception as e: Logger.error(f"Cache: Could not create cache directory: {e}")
        
        try:
            if self.store.exists('client_uuid'): self.client_uuid = self.store.get('client_uuid')['uuid']
            else:
                self.client_uuid = str(uuid.uuid4()); self.store.put('client_uuid', uuid=self.client_uuid)
        except Exception as e:
            if not self.client_uuid: self.client_uuid = str(uuid.uuid4())
            Logger.warning(f"Cache: Using ephemeral UUID (storage failed): {self.client_uuid}")

        self.audio_player = GenericAudioPlayer(
            on_finish_callback=self.on_playback_finished_callback
        )
        
        return RootLayout()

    def on_start(self):
        if 'idoasiplease' in sys.argv:
            self.cheat_mode = True
            Logger.info("--- CHEAT MODE ACTIVATED ---")
            
        # --- NEW FLOW: Open AP Login first ---
        self.archipelago_login_popup = ArchipelagoLoginPopup(app_instance=self)
        self.archipelago_login_popup.open()

    def on_stop(self):
        if self.client_host_ui:
            self.client_host_ui.stop_polling()
        Logger.info("AP: App closing.")

    def on_ap_form_submitted(self, address, name, password, json_path):
        """
        Called by ArchipelagoLoginPopup. This is step 1.
        It now *only* starts the plugin login.
        """
        self.ap_address = address; self.ap_name = name;
        self.ap_password = password; self.json_path = json_path
        
        game_data, backend_name, backend_data = self.parse_game_file(json_path)
        
        if not backend_name:
            self.archipelago_login_popup.on_connection_failed("Invalid .json: Could not find 'backend' info.")
            return
            
        self.game_data = game_data; self.backend_data = backend_data
        
        BackendClass = self.plugin_manager.get_plugin_component_class(backend_name, "client_backend")
        if not BackendClass:
            self.archipelago_login_popup.on_connection_failed(f"Plugin '{backend_name}' not found or doesn't support client.")
            return

        self.backend = BackendClass(
            service_name_key=backend_name,
            on_login_success=self.on_login_success,
            on_login_failure=self.on_login_failure
        )
        
        self.archipelago_login_popup.dismiss()
        self.archipelago_login_popup = None
        
        # --- SIMPLIFIED FLOW ---
        # We NO LONGER call initialize_client here.
        # We just start the login process.
        
        try:
            login_ui_signal = self.backend.get_login_ui()
        except Exception as e:
            self.on_login_failure(f"Plugin error: {e}"); return
            
        if login_ui_signal is None:
            Logger.info("LoginFlow: Backend provided no UI. Assuming external login.")
            Window.minimize(); self.backend.login(login_widget=None)
        elif isinstance(login_ui_signal, str) and login_ui_signal == "DIRECTORY_DIALOG":
            Logger.info("LoginFlow: Backend requested a directory dialog.")
            self.backend.login(login_widget=None)
        else:
            Logger.info("LoginFlow: Backend provided a custom UI. Showing CustomLoginPopup.")
            custom_popup = CustomLoginPopup(login_widget=login_ui_signal, backend=self.backend)
            custom_popup.open()

    def on_login_success(self, user_data):
        """
        Called by the PLUGIN'S backend on successful auth. Step 2.
        This now initializes the backend AND connects to AP.
        """
        Window.restore()
        
        manifest = self.plugin_manager.get_plugin_manifest(self.backend.service_name)
        friendly_name = manifest.get("name", self.backend.service_name.capitalize())
        self.root.set_status(f"Logged into {friendly_name}. Connecting to Archipelago...")
        
        # 1. Initialize the backend
        try:
            self.backend.initialize_client(self.backend_data, self)
        except Exception as e:
            self.on_login_failure(f"Plugin init failed: {e}")
            return
        
        # 2. Connect to AP
        self.connect_to_archipelago(
            self.ap_address, self.ap_name, self.ap_password, self.json_path
        )

    def on_login_failure(self, error_message):
        """Called by the PLUGIN'S backend on failed auth."""
        Window.restore()
        Logger.error(f"Plugin Login Failed: {error_message}")
        
        # Re-open the main AP login popup
        self.archipelago_login_popup = ArchipelagoLoginPopup(app_instance=self)
        self.archipelago_login_popup.open()
        
        def update_status(dt):
            if self.archipelago_login_popup:
                self.archipelago_login_popup.on_connection_failed(f"Plugin Login Failed: {error_message}")
        Clock.schedule_once(update_status, 0.1)

    def on_playback_finished_callback(self):
        """
        Called by audio_player.py when a track ends.
        We forward this to the active plugin host.
        """
        # Must run on main thread to interact with Kivy properties
        Clock.schedule_once(self._dispatch_finish_event)
        
    def _dispatch_finish_event(self, dt):
        if self.client_host_ui:
            self.client_host_ui.on_playback_finished()

    def show_toast(self, text, duration=2.5):
        Clock.schedule_once(lambda dt: self._create_toast(text, duration))
    def _create_toast(self, text, duration):
        toast = ToastMessage(text=text); Window.add_widget(toast)
        anim = Animation(opacity=1, duration=0.3) + Animation(duration=duration) + Animation(opacity=0, duration=0.5)
        anim.bind(on_complete=self._remove_toast); anim.start(toast)
    def _remove_toast(self, animation, widget):
        Window.remove_widget(widget)

    def _populate_initial_lists(self, dt=None):
        """
        Populates the left-hand (Album) list.
        This is the FINAL step, called by the plugin host.
        """
        try:
            Logger.info("UI: Applying AP data to internal state...")
            self.apply_archipelago_data() # This builds name_to_uri_map
            self.root.set_status("Populating lists...")
            album_list_data = []

            for uri in self.ordered_album_uris:
                # container is now a GenericAlbum
                container = self.album_data_cache.get(uri)
                if not container:
                    Logger.warning(f"UI: Could not find container data for {uri} in cache.")
                    continue

                # raw_tracks is now a list of GenericTrack
                raw_tracks = container.tracks
                
                album_has_hint = False
                all_tracks_complete = True
                if not raw_tracks: all_tracks_complete = False

                for track in raw_tracks: # 'track' is a GenericTrack
                    if not track: continue
                    track_uri = track.uri
                    if not track_uri: continue
                    track_data = self.track_progress.get(track_uri)
                    if track_data and track_data.get('hint_text') is not None:
                        album_has_hint = True
                    if not track_data or not track_data.get('is_finished', False):
                        all_tracks_complete = False

                apworld_name = self.apworld_map.get(uri, "Unknown Item")
                # Get processed data directly from the generic object
                image_url = container.display_image_url or container.image_url or KIVY_ICON
                name = container.title
                is_owned = (uri in self.owned_albums)
                artists = container.artist
                type_str = container.album_type
                total_tracks = container.total_tracks

                album_list_data.append({
                    'text_line_1': name,
                    'text_line_2': artists,
                    'text_line_3': f"{type_str} • Tracks: {total_tracks}",
                    'text_line_4': apworld_name,
                    'image_source': image_url,
                    'is_owned': is_owned,
                    'has_hint': album_has_hint,
                    'all_tracks_finished': all_tracks_complete,
                    'is_finished': False,
                    'list_id': uri,
                    'raw_item_type': 'album', # UI still uses 'album'
                    'raw_uri': uri,
                    'raw_title': name,
                    'raw_artist': artists,
                    'raw_album_type': type_str,
                    'raw_total_tracks': str(total_tracks),
                    'raw_image_url': image_url
                })
                
            self.root.ids.list_container.list_one_data = album_list_data
            self.root.set_status(f"Loaded {len(album_list_data)} albums.")
            self.root.ids.list_container.list_two_data = []

            if self.ap_client:
                Logger.info("AP: Data loading complete.")
                self.ap_client.app_is_ready = True
                self.ap_client._sync_owned_items()
                self.ap_client.send_chat_message("!hint")
        except Exception as e:
            Logger.error(f"FATAL ERROR in _populate_initial_lists: {e}", exc_info=True)

    def parse_game_file(self, file_path):
        """
        Loads and parses the game JSON file.
        Returns (game_data, backend_name, backend_data)
        """
        Logger.info(f"Game: Attempting to parse JSON from: {file_path}")
        self.root.set_status(f"Loading game file: {os.path.basename(file_path)}")

        self.apworld_map.clear(); self.album_data_cache.clear()
        self.ordered_album_uris.clear(); self.track_progress.clear(); self.owned_albums.clear()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                game_data = json.load(f)
            
            backend_info = game_data.get("backend", {})
            backend_name = backend_info.get("name")
            backend_data = backend_info.get("data", {})
            
            if not backend_name:
                Logger.error("Game: JSON file has no 'backend' key.")
                return None, None, None
            
            # --- We still parse the 'apworld' key for the AP mapping ---
            for album_item in game_data.get("apworld", []):
                album_ap_name = album_item.get('name'); album_uri = album_item.get('uri')
                if not album_ap_name or not album_uri: continue
                self.apworld_map[album_uri] = album_ap_name
                
                for track_item in album_item.get('tracks', []):
                    track_ap_name = track_item.get('title'); track_uri = track_item.get('uri')
                    if not track_ap_name or not track_uri: continue
                    if track_uri not in self.track_progress:
                        self.track_progress[track_uri] = {
                            'is_finished': False, 'last_seen_progress_ms': 0,
                            'hint_text': None, 'parent_uri': album_uri
                        }
                    self.apworld_map[track_uri] = track_ap_name
            
            Logger.info(f"Game: JSON parsed. {len(self.track_progress)} tracks to be tracked.")
            # Return the full game_data, which contains the new 'display_data' key
            return game_data, backend_name, backend_data

        except Exception as e:
            Logger.error(f"Game: Failed to load or parse JSON file: {e}")
            self.root.set_status(f"Error loading file: {e}")
            return None, None, None

    def store_track_hint(self, track_uri, hint_text):
        Logger.info(f"UI: Storing persistent hint for {track_uri}")
        if track_uri in self.track_progress:
            self.track_progress[track_uri]['hint_text'] = hint_text
        else:
            Logger.warning(f"UI: Tried to store hint for unknown track: {track_uri}")
        
        self.root.update_track_hint_text(track_uri, hint_text)
        try:
            track_prog_data = self.track_progress.get(track_uri)
            if track_prog_data:
                parent_uri = track_prog_data.get('parent_uri')
                if parent_uri:
                    self.root.update_album_hint_status(parent_uri, True)
        except Exception as e:
            Logger.error(f"UI: Failed to update parent album hint status: {e}")

    def connect_to_archipelago(self, address, name, password, json_path):
        """
        Saves connection info and initiates the websocket connection.
        This is now Step 3 of the login flow.
        """
        try:
            self.store.put('connection_info',
                address=address, name=name, password=password, json_path=json_path
            )
        except Exception as e:
            Logger.error(f"Cache: Failed to save settings: {e}")
        
        if not address.startswith(("ws://", "wss://")):
            address = f"wss://{address}"
        base_name = os.path.basename(json_path)
        game_name, _ = os.path.splitext(base_name)
        Logger.info(f"Connecting to AP server at {address} as {name} for game {game_name}...")
        self.ap_client = ArchipelagoClient(self, address, name, password, game_name, self.cache_dir)
        threading.Thread(target=self.ap_client.start_client_loop, daemon=True).start()

    def on_connection_success(self, *args):
        """
        Called by APClient. This is the main setup flow.
        """
        if not self.json_path or not self.game_data:
            Logger.error("AP: Connection successful but no game data is loaded."); return
            
        # 1. Instantiate the Client UI Host
        UIHostClass = self.plugin_manager.get_plugin_component_class(
            self.backend.service_name, 'client_ui'
        )
        if not UIHostClass:
            self.on_connection_failed(f"Plugin {self.backend.service_name} has no client_ui.")
            return
        
        self.client_host_ui = UIHostClass()
        self.client_host_ui.initialize(self.root, self.backend)
        
        # --- THIS IS THE MODIFIED PART ---
        # 2. Tell the plugin host to start fetching data
        #    We pass the *entire* game_data dict.
        self.client_host_ui.fetch_game_data_threaded(self.game_data)
        # --- END MODIFIED PART ---
        
        # 3. Start the plugin's progress polling
        self.client_host_ui.start_polling()

    def on_connection_failed(self, error_message, *args):
        """Called by APClient when connection fails."""
        Logger.warning(f"AP: Connection failed: {error_message}")
        
        # Re-open the main AP login popup
        self.archipelago_login_popup = ArchipelagoLoginPopup(app_instance=self)
        self.archipelago_login_popup.open()

        def update_status(dt):
            if self.archipelago_login_popup:
                self.archipelago_login_popup.on_connection_failed(f"AP Error: {error_message}")
        Clock.schedule_once(update_status, 0.1)
        
        self.ap_client = None
        self.json_path = None

    def apply_archipelago_data(self, *args):
        """
        Uses data from APClient to update the app's state.
        Called by _populate_initial_lists.
        """
        Logger.info("AP: Applying new data package...")
        if not self.ap_client: return
        try:
            # 1. This map is created
            self.name_to_uri_map = {v: k for k, v in self.apworld_map.items()}
        except Exception as e:
            Logger.error(f"AP: Failed to build name_to_uri map: {e}"); return
        
        # # 2. Set the "ready" flag on the client
        # self.ap_client.app_is_ready = True
        
        # # 3. Manually call _sync_owned_items to process the backlog of items
        # #    that were received before the app was ready.
        # Logger.info("AP: App is ready. Processing item backlog...")
        # self.ap_client._sync_owned_items()
            
        # 4. Now we build the *initial* set of owned albums from the (now populated) list
        owned_item_names = self.ap_client.owned_item_names
        self.owned_albums.clear()
        for name in owned_item_names:
            uri = self.name_to_uri_map.get(name)
            if uri and uri in self.album_data_cache:
                self.owned_albums.add(uri)
        Logger.info(f"AP: Synced {len(self.owned_albums)} owned albums.")

        checked_locations = self.ap_client.checked_locations
        id_to_location_name = self.ap_client.id_to_location_name
        updated_uris = set()
        
        for loc_id, apworld_name in id_to_location_name.items():
            uri = self.name_to_uri_map.get(apworld_name)
            if uri and uri in self.track_progress:
                track_data = self.track_progress[uri]
                track_data['location_id'] = loc_id
                if loc_id in checked_locations:
                    track_data['is_finished'] = True
                    updated_uris.add(uri)
                else:
                    track_data['is_finished'] = False
        Logger.info(f"AP: Synced {len(id_to_location_name)} locations. {len(updated_uris)} tracks are already checked.")

        try:
            track_rv = self.root.ids.list_container.ids.track_rv
            if not track_rv.data: return
            Logger.info("AP: Refreshing visible track list with 'checked' data...")
            for i, item_data in enumerate(track_rv.data):
                uri = item_data.get('raw_uri')
                if uri in updated_uris:
                    item_data['is_finished'] = True
            track_rv.refresh_from_data()
        except Exception as e:
            Logger.error(f"AP: Failed to refresh UI: {e}")

    def load_datapackage_from_cache(self, *args):
        if not self.ap_client: return
        Logger.info("AP: Loading all DataPackages from cache...")
        try:
            for game_name in self.ap_client.server_checksums.keys():
                cache_file_path = os.path.join(self.cache_dir, f"{game_name}.json")
                if not os.path.exists(cache_file_path): continue
                with open(cache_file_path, 'r', encoding='utf-8') as f:
                    game_data = json.load(f)
                item_map = game_data.get('item_name_to_id', {})
                location_map = game_data.get('location_name_to_id', {})
                self.ap_client.game_data_packages[game_name] = {
                    'id_to_item_name': {v: k for k, v in item_map.items()},
                    'id_to_location_name': {v: k for k, v in location_map.items()}
                }
                Logger.info(f"AP: Loaded cached maps for '{game_name}'.")
                if game_name == self.ap_client.game_name:
                    self.ap_client.id_to_item_name = self.ap_client.game_data_packages[game_name]['id_to_item_name']
                    self.ap_client.id_to_location_name = self.ap_client.game_data_packages[game_name]['id_to_location_name']
            self.ap_client._sync_owned_items()
            Logger.info("AP: Finished loading all cached datapackages.")
            self.ap_client.on_datapackage_received()
        except Exception as e:
            Logger.error(f"AP: Failed to load/parse cached DataPackage: {e}")

def main():
    MusipelagoClientApp().run()

if __name__ == '__main__':
    main()