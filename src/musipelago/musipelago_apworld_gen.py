# -*- coding: utf-8 -*-
import os, sys, json, zipfile, ctypes
import requests, threading, hashlib, shutil
import dataclasses

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
from kivy.uix.spinner import Spinner
from kivy.metrics import dp
from kivy.properties import StringProperty, ListProperty, ObjectProperty
from kivy.storage.jsonstore import JsonStore
from kivy.resources import resource_add_path

from jinja2 import Environment, FileSystemLoader

# --- Local Imports ---
from musipelago.utils import (
    filter_to_ascii, filter_py_json, KIVY_ICON
)
from musipelago.backends import (
    GenericAlbum, GenericArtist, GenericPlaylist,
    AbstractMusicBackend, AbstractPluginHost
)
from musipelago.plugin_loader import PluginManager
# Not necessary per se, but fixes PyInstaller build
# import musipelago.client_ui_components


class AsyncImageWithHeaders(Image):
    web_source = StringProperty(None)
    _cache_dir = ''
    _cache_path = ''
    _headers = {} # Will be set by the app after login

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not AsyncImageWithHeaders._cache_dir:
            app_dir = App.get_running_app().user_data_dir
            AsyncImageWithHeaders._cache_dir = os.path.join(app_dir, 'image_cache')
            if not os.path.exists(AsyncImageWithHeaders._cache_dir):
                os.makedirs(AsyncImageWithHeaders._cache_dir)
        
        # Set default headers
        if not AsyncImageWithHeaders._headers:
             AsyncImageWithHeaders._headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'


    def on_web_source(self, instance, url):
        if not url:
            self.source = KIVY_ICON
            return
        
        if url.startswith('http://') or url.startswith('https://'):
            filename = hashlib.md5(url.encode('utf-8')).hexdigest() + '.jpg'
            self._cache_path = os.path.join(AsyncImageWithHeaders._cache_dir, filename)

            if os.path.exists(self._cache_path):
                self.source = self._cache_path
            else:
                self.source = KIVY_ICON
                thread = threading.Thread(target=self._download_image, args=(url, self._cache_path))
                thread.start()
        else:
            self.source = url

    def _download_image(self, url, cache_path):
        try:
            response = requests.get(url, headers=self._headers, stream=True)
            if response.status_code == 200:
                with open(cache_path, 'wb') as f:
                    response.raw.decode_content = True
                    shutil.copyfileobj(response.raw, f)
                Clock.schedule_once(lambda dt: self._set_source(cache_path))
            else:
                Logger.error(f"ImageDownloader: Failed {url}, status {response.status_code}")
        except Exception as e:
            Logger.error(f"ImageDownloader: Exception for {url}: {e}")
    
    def _set_source(self, cache_path):
        if self._cache_path == cache_path:
            self.source = cache_path
    
    @classmethod
    def set_http_headers(cls, headers: dict):
        cls._headers = headers

class CustomLoginPopup(Popup):
    """
    A generic popup that hosts a widget provided by a plugin.
    """
    def __init__(self, login_widget: object, backend: object, **kwargs):
        super().__init__(**kwargs)
        self.title = f"Login to {backend.service_name.capitalize()}"
        self.size_hint = (0.9, None)
        self.auto_dismiss = False
        
        self.backend = backend
        self.login_widget = login_widget
        
        # Build the popup content
        main_layout = BoxLayout(orientation='vertical', spacing='10dp', padding='10dp')
        
        # Add the plugin's custom UI
        main_layout.add_widget(self.login_widget)
        
        # Add standard buttons
        button_layout = BoxLayout(size_hint_y=None, height='44dp', spacing='10dp')
        cancel_btn = Button(text='Cancel', on_release=self.dismiss)
        login_btn = Button(text='Login', on_release=self.on_login_press)
        button_layout.add_widget(cancel_btn)
        button_layout.add_widget(login_btn)
        
        main_layout.add_widget(button_layout)
        
        self.content = main_layout
        self.height = getattr(login_widget, 'desired_popup_height', dp(400))

    def on_login_press(self, instance):
        """
        Passes the login widget back to the backend's login method.
        """
        Logger.info(f"CustomLoginPopup: Calling {self.backend.service_name}.login()")
        # The backend's login method will handle threading
        self.backend.login(login_widget=self.login_widget)
        self.dismiss()

class LoginPopup(Popup):
    def __init__(self, app_instance, **kwargs):
        super(LoginPopup, self).__init__(**kwargs)
        self.title = 'Select Service'
        self.size_hint = (0.8, 0.6)
        self.auto_dismiss = False
        self.app = app_instance
        self.friendly_to_module_map = {}

        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        layout.add_widget(Label(text="Musipelago", font_size='24sp'))
        layout.add_widget(Label(text="Please select a music service to connect."))
        
        # --- NEW SPINNER ---
        layout.add_widget(Label(text="Service:"))
        self.backend_spinner = Spinner(
            text='No plugins found',
            values=[],
            size_hint_y=None,
            height='44dp'
        )
        layout.add_widget(self.backend_spinner)
        # --- END NEW ---

        self.status_label = Label(text="Status: Not Logged In", size_hint_y=0.4)
        layout.add_widget(self.status_label)

        self.login_button = Button(text="Connect", on_press=self.authenticate, size_hint_y=None, height='48dp')
        layout.add_widget(self.login_button)

        self.content = layout

    def authenticate(self, instance):
        if self.backend_spinner.text == 'No plugins found':
            self.status_label.text = "Error: Cannot login, no plugins loaded."
            return

        selected_friendly_name = self.backend_spinner.text
        selected_backend_name = self.friendly_to_module_map.get(selected_friendly_name)

        if not selected_backend_name:
             self.app.on_login_failure(f"Could not find plugin for: {selected_friendly_name}")
             return
        
        # --- MODIFIED ---
        # 1. Get the 'generator' class for the selected plugin
        BackendClass = self.app.plugin_manager.get_plugin_component_class(
            selected_backend_name, 
            "generator_backend" 
        )
        # --- END MODIFY ---
        
        if not BackendClass:
            self.app.on_login_failure(f"Could not load plugin: {selected_backend_name}")
            return
            
        # 2. Instantiate the backend
        self.app.backend = BackendClass(
            service_name_key=selected_backend_name, # <-- Pass the key
            on_login_success=self.app.on_login_success,
            on_login_failure=self.app.on_login_failure
        )
        
        Logger.info(f"LoginPopup: Authenticating with {selected_backend_name}")
        self.login_button.disabled = True
        self.status_label.text = "Initializing login..."
        
        # 3. Ask the backend for its login UI (this logic is unchanged)
        try:
            login_widget = self.app.backend.get_login_ui()
        except Exception as e:
            Logger.error(f"Plugin Error: {selected_backend_name}.get_login_ui() failed: {e}")
            self.app.on_login_failure(f"Plugin error: {e}")
            return
            
        # 4. Decide on the login strategy (this logic is unchanged)
        if login_widget is None:
            # External Login
            Logger.info("LoginPopup: Backend provided no UI. Assuming external login.")
            Window.minimize()
            self.app.backend.login(login_widget=None)
            self.dismiss()
        else:
            # Custom UI Login (Subsonic)
            Logger.info("LoginPopup: Backend provided a custom UI. Showing CustomLoginPopup.")
            custom_popup = CustomLoginPopup(
                login_widget=login_widget, 
                backend=self.app.backend
            )
            custom_popup.open()
            self.dismiss()

class GeneratePopup(Popup):
    apworld_data = ObjectProperty(None) # This will be a list of GenericAlbum

    def __init__(self, apworld_data, **kwargs):
        super().__init__(**kwargs)
        self.apworld_data = apworld_data # List of GenericAlbum objects

    def on_popup_generate(self, apworld_name):
        app = App.get_running_app()
        if not apworld_name.strip():
            app.root.status_text = "Error: APWorld name cannot be empty."
            return
        
        # Run file generation in a thread to avoid blocking UI
        threading.Thread(target=self.generate_files, args=(apworld_name,)).start()
        self.dismiss()

    def generate_files(self, apworld_name):
        app = App.get_running_app()
        Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Generation started for: {apworld_name}"))
        Logger.info(f"Generate: Button clicked for {apworld_name}")
        
        try:
            template_dir = resource_path('apworld_template')
            base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(base_dir, "output", "Musipelago_" + apworld_name)

            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
            
            Logger.info(f"Generate: Reading templates from: {template_dir}")
            Logger.info(f"Generate: Saving files to: {output_dir}")
        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', "Error: Could not find template directory."))
            Logger.error(f"Generate: Failed to access directories: {e}")
            return

        try:
            env = Environment(loader=FileSystemLoader(template_dir))
            env.filters['to_ascii'] = filter_to_ascii
            env.filters['py_json'] = filter_py_json
            
            files_to_generate = [
                "Locations.py.j2", "Items.py.j2", "Options.py.j2",
                "Types.py.j2", "Regions.py.j2", "Rules.py.j2",
                "__init__.py.j2", "archipelago.json.j2"
            ]
            
            # The context now uses the generic data models
            context = {
                'apworld_data': self.apworld_data, # List of GenericAlbum
                'apworld_name': apworld_name
            }

            for template_name in files_to_generate:
                Logger.info(f"Generate: Processing template: {template_name}")
                template = env.get_template(template_name)
                processed_content = template.render(context)
                
                output_filename = template_name.rsplit('.j2', 1)[0]
                output_file_path = os.path.join(output_dir, output_filename)
                
                with open(output_file_path, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
            
            Logger.info("Generate: Creating simplified JSON file...")
            
            # 1. Build the "apworld" key (for AP name mapping)
            apworld_content = []
            for album in self.apworld_data: # album is GenericAlbum
                album_name_str = f"[{album.artist}] [{album.title}]"
                ap_safe_name = filter_to_ascii(album_name_str)
                new_album_obj = {"name": filter_to_ascii(ap_safe_name), "uri": album.uri, "tracks": []}
                for track in album.tracks: # track is GenericTrack
                    track_name_str = f"[{track.artist}] [{album.title}] [{track.title}]"
                    ap_safe_track_name = filter_to_ascii(track_name_str)
                    new_track_obj = {"title": filter_to_ascii(ap_safe_track_name), "uri": track.uri, "artist": track.artist}
                    new_album_obj["tracks"].append(new_track_obj)
                apworld_content.append(new_album_obj)
            
            # 2. Get the backend config data
            backend_info = {
                "name": app.backend.service_name,
                "data": app.backend.get_client_data()
            }
            
            # 3. Check if we need to build and add the "display_data" key
            display_data_list = None # Default to None
            if app.backend.client_requires_display_data():
                Logger.info("Generate: Backend requires display_data. Serializing...")
                display_data_list = []
                for album in self.apworld_data:
                    album_dict = dataclasses.asdict(album)
                    if 'display_image_url' in album_dict:
                        del album_dict['display_image_url']
                    display_data_list.append(album_dict)
            else:
                Logger.info("Generate: Backend does not require display_data. Skipping.")
            
            # 5. Build the new top-level JSON structure
            final_json_data = {
                "backend": backend_info,
                "apworld": apworld_content,
                "display_data": display_data_list  # <-- NEW KEY
            }
            
            # 6. Save the new structure
            json_filename = os.path.basename(output_dir) + ".json"
            json_parent_dir = os.path.dirname(output_dir)
            json_output_path = os.path.join(json_parent_dir, json_filename)
            
            with open(json_output_path, 'w', encoding='utf-8') as f:
                json.dump(final_json_data, f, indent=4)

            # Copy 'docs' folder
            docs_src = os.path.join(template_dir, 'docs')
            docs_dest = os.path.join(output_dir, 'docs')
            if os.path.exists(docs_src) and os.path.isdir(docs_src):
                shutil.copytree(docs_src, docs_dest, dirs_exist_ok=True)
            
            # Create .apworld zip
            zip_filename = f"{os.path.basename(output_dir)}.apworld"
            parent_dir = os.path.dirname(output_dir) 
            zip_path = os.path.join(parent_dir, zip_filename)

            Logger.info(f"Generate: creating .apworld archive at {zip_path}...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, start=parent_dir)
                        zipf.write(file_path, arcname)
            
            Logger.info(f"Generate: .apworld file created successfully.")
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Generation complete for '{apworld_name}'!"))

        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', "Generation failed. Check logs."))
            Logger.error(f"Generate: Failed during file processing: {e}")

class ItemMenu(DropDown):
    caller = ObjectProperty(None) 
    def on_option_select(self, option_text):
        if self.caller:
            self.caller.menu_action(option_text)
        self.dismiss()

class CustomListItem(BoxLayout):
    # --- Visual Properties (for KV) ---
    text_line_1 = StringProperty('Line 1')
    text_line_2 = StringProperty('Line 2')
    text_line_3 = StringProperty('Line 3')
    text_line_4 = StringProperty('Line 4')
    image_source = StringProperty(KIVY_ICON)
    
    # --- Data Properties (set from search) ---
    list_id = StringProperty('')
    generic_item = ObjectProperty(None, allownone=True) # This holds the GenericAlbum/Artist/Playlist

    def open_menu(self, button_widget):
        app = App.get_running_app()

        # --- NEW PLUGIN HOOK ---
        if app.plugin_host_ui:
            # Ask the plugin if it wants to handle this click directly
            was_handled = app.plugin_host_ui.on_item_menu_click(self.list_id, self.generic_item)
            if was_handled:
                return # The plugin did something, so don't open a menu
        # --- END NEW HOOK ---
        
        if self.list_id == 'load_more_button':
            # Directly trigger the next page load
            App.get_running_app().root.load_next_page()
            return

        menu = ItemMenu(caller=self)
        menu.auto_width = False
        button_added = False 
        
        item_type = ""
        if isinstance(self.generic_item, GenericAlbum): item_type = "album"
        elif isinstance(self.generic_item, GenericArtist): item_type = "artist"
        elif isinstance(self.generic_item, GenericPlaylist): item_type = "playlist"

        if self.list_id == 'search':
            # ... (this logic is unchanged)
            if item_type == 'album' or item_type == 'playlist':
                btn = Button(text="Add to APWorld", size_hint_y=None, height='44dp', on_release=lambda x: menu.on_option_select('Add to APWorld'))
                menu.add_widget(btn); button_added = True
            elif item_type == 'artist':
                btn1 = Button(text="Add all artist albums", size_hint_y=None, height='44dp', on_release=lambda x: menu.on_option_select('Add all artist albums'))
                menu.add_widget(btn1)
                btn2 = Button(text="Show all albums", size_hint_y=None, height='44dp', on_release=lambda x: menu.on_option_select('Show all albums'))
                menu.add_widget(btn2); button_added = True

        elif self.list_id == 'apworld':
            # ... (this logic is unchanged)
            btn = Button(text="Remove from APWorld", size_hint_y=None, height='44dp', on_release=lambda x: menu.on_option_select('Remove'))
            menu.add_widget(btn); button_added = True
            
        if button_added:
            menu.open(button_widget)
        else:
            app.root.status_text = f"No actions available for '{self.text_line_1}'"

    def menu_action(self, text):
        app = App.get_running_app()
        app.root.status_text = f"Action: {text} on {self.text_line_1}"

        if text == 'Add to APWorld':
            app.root.status_text = f"Adding '{self.text_line_1}' to APWorld..."
            # Run the correct backend fetch in a thread
            if isinstance(self.generic_item, GenericAlbum):
                threading.Thread(target=self._add_album_thread, args=(self.generic_item,)).start()
            elif isinstance(self.generic_item, GenericPlaylist):
                threading.Thread(target=self._add_playlist_thread, args=(self.generic_item,)).start()
            
        elif text == 'Add all artist albums':
            app.root.status_text = f"Fetching albums for '{self.text_line_1}'..."
            threading.Thread(target=self._add_all_artist_albums_thread, args=(self.generic_item,)).start()

        elif text == 'Show all albums':
            app.root.status_text = f"Fetching albums for '{self.text_line_1}'..."
            threading.Thread(target=self._show_artist_albums_thread, args=(self.generic_item,)).start()

        elif text == 'Remove':
            app.root.status_text = f"Removing '{self.text_line_1}'..."
            app.root.ids.list_container.remove_apworld_item(self.generic_item.uri)
    
    # --- Threaded Backend Calls ---    
    def _add_album_thread(self, album: GenericAlbum):
        app = App.get_running_app()
        try:
            # Call the backend to get the album with its tracks
            populated_album = app.backend.get_album_with_tracks(album)
            Clock.schedule_once(lambda dt: self._add_data_to_apworld_list(populated_album))
        except Exception as e:
            Logger.error(f"APWorld: Failed to get tracks for {album.uri}: {e}")
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Failed to add '{album.title}'"))
    
    def _add_playlist_thread(self, playlist: GenericPlaylist):
        app = App.get_running_app()
        try:
            # Call the backend to get playlist tracks (returns a GenericAlbum-like object)
            populated_item = app.backend.get_playlist_with_tracks(playlist)
            Clock.schedule_once(lambda dt: self._add_data_to_apworld_list(populated_item))
        except Exception as e:
            Logger.error(f"APWorld: Failed to get playlist {playlist.uri}: {e}")
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Failed to add '{playlist.name}'"))

    def _add_all_artist_albums_thread(self, artist: GenericArtist):
        app = App.get_running_app()
        try:
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Finding albums for '{artist.name}'..."))
            
            # This backend call returns a list of *fully populated* GenericAlbum objects
            all_populated_albums = app.backend.get_all_artist_albums(artist)
            
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Found {len(all_populated_albums)} albums. Adding to list..."))
            
            for album in all_populated_albums:
                Clock.schedule_once(lambda dt, a=album: self._add_data_to_apworld_list(a))
            
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Finished adding albums for '{artist.name}'."))
        except Exception as e:
            Logger.error(f"APWorld: Failed to get all albums for {artist.uri}: {e}")
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Failed to get albums for '{artist.name}'"))

    def _show_artist_albums_thread(self, artist: GenericArtist):
        app = App.get_running_app()
        try:
            # This backend call returns a list of GenericAlbum objects (no tracks)
            albums_for_display = app.backend.get_artist_albums_for_display(artist)
            
            # We must convert these to the dict format the RecycleView expects
            new_display_data = []
            for item in albums_for_display:
                new_display_data.append({
                    'text_line_1': item.title,
                    'text_line_2': item.artist,
                    'text_line_3': f"{item.album_type} • Tracks: {item.total_tracks}",
                    'text_line_4': item.uri,
                    'image_source': item.image_url or KIVY_ICON,
                    'list_id': 'search',
                    'generic_item': item # Pass the object itself
                })

            def update_ui(dt):
                app.root.ids.list_container.list_one_data = new_display_data
                app.root.status_text = f"Showing {len(new_display_data)} albums for '{artist.name}'."
            
            Clock.schedule_once(update_ui)

        except Exception as e:
            Logger.error(f"Failed to show albums for {artist.uri}: {e}")
            Clock.schedule_once(lambda dt: setattr(app.root, 'status_text', f"Failed to load albums for '{artist.name}'"))

    def _add_data_to_apworld_list(self, album_data: GenericAlbum):
        """This is the final step, adding the populated data to the list."""
        app = App.get_running_app()
        list_container = app.root.ids.list_container
        
        # This will trigger the on_apworld_data binding
        list_container.add_apworld_item(album_data) 

class ListContainer(BoxLayout):
    list_one_data = ListProperty()  # Visual data for search list
    list_two_data = ListProperty()  # Visual data for APWorld list
    
    # This is the "source of truth" list, holding the full generic data
    apworld_data = ListProperty()   # List of GenericAlbum objects

    def add_apworld_item(self, album_data: GenericAlbum):
        # Check for duplicates
        for item in self.apworld_data:
            if item.uri == album_data.uri:
                Logger.info(f"APWorld: Item {album_data.title} already in list. Skipping.")
                App.get_running_app().root.status_text = f"'{album_data.title}' is already in the list."
                return
        
        # This append() triggers on_apworld_data
        self.apworld_data.append(album_data)
        App.get_running_app().root.status_text = f"Added '{album_data.title}' to APWorld."

    def remove_apworld_item(self, item_uri):
        item_to_remove = next((item for item in self.apworld_data if item.uri == item_uri), None)
        
        if item_to_remove:
            self.apworld_data.remove(item_to_remove) # This triggers on_apworld_data
            Logger.info(f"APWorld: Removed '{item_to_remove.title}'.")
            App.get_running_app().root.status_text = f"Removed '{item_to_remove.title}'."
        else:
            Logger.warning(f"APWorld: Could not find item to remove with URI: {item_uri}")

    def on_apworld_data(self, instance, new_data_list: list[GenericAlbum]):
        """
        Fires when apworld_data changes.
        Rebuilds the *visual* list (list_two_data) from the *data* list (apworld_data).
        """
        Logger.info("APWorld: Rebuilding right-side visual list.")
        visual_list = []
        for album in new_data_list:
            visual_list.append({
                'text_line_1': album.title,
                'text_line_2': album.artist,
                'text_line_3': f"{album.album_type} • Tracks: {len(album.tracks)}", # Use actual track count
                'text_line_4': album.uri,
                'image_source': album.display_image_url or album.image_url or KIVY_ICON,
                'list_id': 'apworld',
                'generic_item': album # Pass the object itself for the 'Remove' action
            })
        self.list_two_data = visual_list

class RootLayout(BoxLayout):
    status_text = StringProperty("App started. Ready.")
    current_search_query = ""
    current_search_type = ""
    current_search_offset = 0
    search_limit = 20

    def on_search_click(self, search_text, search_type):
        app = App.get_running_app()

        if app.backend and app.backend.is_authenticated:
            Logger.info(f"Search: Searching for '{search_text}' in '{search_type}'")
            self.status_text = f"Searching for '{search_text}'..."
            
            # Reset state
            self.current_search_query = search_text
            self.current_search_type = search_type
            self.current_search_offset = 0
            
            # Clear list
            self.ids.list_container.list_one_data = []
            
            # Start search at offset 0
            threading.Thread(target=self._search_thread, args=(search_text, search_type, 0)).start()
        else:
            # ... (error handling) ...
            pass
    
    def load_next_page(self):
        """Called when 'Load More' is clicked."""
        self.current_search_offset += self.search_limit
        self.status_text = f"Loading page {int(self.current_search_offset/self.search_limit) + 1}..."
        
        threading.Thread(
            target=self._search_thread, 
            args=(self.current_search_query, self.current_search_type, self.current_search_offset)
        ).start()

    def _search_thread(self, search_text, search_type, offset):
        app = App.get_running_app()
        try:
            # Call backend with offset
            results = app.backend.search(search_text, search_type, limit=self.search_limit, offset=offset)
            
            Clock.schedule_once(lambda dt: self._update_search_list(results, search_type, offset))
        except Exception as e:
            Logger.error(f"Search failed: {e}")
            Clock.schedule_once(lambda dt: setattr(self, 'status_text', "Search failed. See log."))

    def _update_search_list(self, results, search_type, offset):
        new_data = []
        
        # Convert results to UI dicts (Standard logic)
        for item in results:
            img = item.display_image_url or item.image_url or KIVY_ICON
            if isinstance(item, GenericArtist):
                new_data.append({
                    'text_line_1': item.name,
                    'text_line_2': f"Albums: {item.metadata.get('album_count', '?')}",
                    'text_line_3': f"Genres: {', '.join(item.metadata.get('genres', [])[:2])}",
                    'text_line_4': item.uri,
                    'image_source': img,
                    'list_id': 'search',
                    'generic_item': item
                })
            elif isinstance(item, GenericAlbum):
                new_data.append({
                    'text_line_1': item.title,
                    'text_line_2': item.artist,
                    'text_line_3': f"{item.album_type} • Tracks: {item.total_tracks}",
                    'text_line_4': item.uri,
                    'image_source': img,
                    'list_id': 'search',
                    'generic_item': item
                })
            elif isinstance(item, GenericPlaylist):
                new_data.append({
                    'text_line_1': item.name,
                    'text_line_2': f"Owner: {item.owner}",
                    'text_line_3': f"Tracks: {item.total_tracks}",
                    'text_line_4': item.uri,
                    'image_source': img,
                    'list_id': 'search',
                    'generic_item': item
                })

        # --- MODIFIED PAGINATION LOGIC ---
        
        # 2. Get current list
        current_list = list(self.ids.list_container.list_one_data)
        
        # 3. Remove old "Load More" button if present
        if current_list and current_list[-1]['list_id'] == 'load_more_button':
            current_list.pop()
        
        # 4. Append new results
        current_list.extend(new_data)
        
        # 5. Append new "Load More" button if needed
        if len(results) >= self.search_limit:
            current_list.append({
                'text_line_1': 'Load More Results...',
                'text_line_2': '',
                'text_line_3': '',
                'text_line_4': '',
                'image_source': KIVY_ICON,
                'list_id': 'load_more_button',
                'generic_item': None
            })

        # 6. Update the data
        self.ids.list_container.list_one_data = current_list
        self.status_text = f"Loaded {len(new_data)} more items."

        # 7. Restore Scroll Position (The Fix)
        if offset > 0 and new_data:
            def scroll_fix(dt):
                rv = self.ids.list_container.ids.search_rv
                
                # Calculate heights
                # Items are 100dp + 10px spacing. 
                # We use dp(110) as a close approximation.
                item_height = dp(110)
                
                items_added = len(new_data)
                height_added = items_added * item_height
                
                # Total scrollable area height (approximate)
                total_content_height = len(rv.data) * item_height
                scrollable_distance = max(1, total_content_height - rv.height)
                
                # We added items to the bottom, so we are currently at 0.0 (bottom).
                # We want to move UP by the height of the items we added.
                # target_y = height_added / scrollable_distance
                
                target_y = height_added / scrollable_distance
                
                # Clamp and apply
                rv.scroll_y = max(0.0, min(1.0, target_y))

            Clock.schedule_once(scroll_fix, 0.1)

    def on_settings_click(self):
        print("Settings button clicked!")
        self.status_text = "Settings panel opened (not really)."

    def on_generate_click(self):
        apworld_data = self.ids.list_container.apworld_data
        
        if not apworld_data:
            self.status_text = "APWorld list is empty. Nothing to generate."
            Logger.info("Generate: APWorld list is empty.")
            return

        # Open the popup and pass it the *generic data*
        popup = GeneratePopup(apworld_data=apworld_data)
        popup.open()

class MusipelagoAPWGenApp(App):

    plugin_host_ui = ObjectProperty(None)
    
    def build(self):
        self.backend = None
        self.login_popup = None
        resource_add_path(resource_path(''))

        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
        else:
            base_path = os.path.abspath(os.path.dirname(__file__))
        
        store_path = os.path.join(base_path, 'musipelago_gen.json')
        self.store = JsonStore(store_path)
        
        # Check env vars before trying to init backend
        self.plugin_manager = PluginManager(plugin_dir=resource_path('plugins'))
        self.plugin_manager.discover_plugins()
        
        return RootLayout()

    def on_start(self):
        self.login_popup = LoginPopup(app_instance=self)
        
        backend_names = self.plugin_manager.get_available_backends(
            app_type_key="generator_backend"
        )
        
        if backend_names:
            # We can now show friendly names if we want
            friendly_names = []
            for name in backend_names:
                manifest = self.plugin_manager.get_plugin_manifest(name)
                friendly_names.append(manifest.get("name", name))
            
            self.login_popup.backend_spinner.values = friendly_names
            self.login_popup.backend_spinner.text = friendly_names[0]
            # Store the real module names, mapped from the friendly names
            self.login_popup.friendly_to_module_map = dict(zip(friendly_names, backend_names))
            
            self.login_popup.status_label.text = "Please select a service."
        else:
            self.login_popup.status_label.text = "Error: No generator plugins found in 'plugins' folder."
            self.login_popup.login_button.disabled = True
            
        self.login_popup.open()

    def on_login_success(self, user_data):
        Window.restore()
        if self.login_popup:
            self.login_popup.dismiss(); self.login_popup = None
            
        # --- MODIFIED: Use friendly name ---
        manifest = self.plugin_manager.get_plugin_manifest(self.backend.service_name)
        friendly_name = manifest.get("name", self.backend.service_name.capitalize())
        
        self.root.status_text = f"Logged into {friendly_name} as: {user_data.get('display_name', 'Unknown')}"
        # --- END MODIFY ---
        
        if self.backend.user_agent:
            AsyncImageWithHeaders.set_http_headers({'User-Agent': self.backend.user_agent})
        
        Logger.info(f"Loading UI host for {self.backend.service_name}")
        
        # 1. Get the UI Host class
        UIHostClass = self.plugin_manager.get_plugin_component_class(
            self.backend.service_name,
            'generator_ui'
        )
        
        if not UIHostClass:
            Logger.error(f"FATAL: Plugin {self.backend.service_name} has no 'generator_ui' class!")
            self.root.status_text = "Error: Plugin UI failed to load."
            return
            
        # 2. Create an instance and initialize it
        self.plugin_host_ui = UIHostClass()
        self.plugin_host_ui.initialize(self.root, self.backend)
        # --- END NEW UI SETUP ---
            
    def on_login_failure(self, error_message):
        Window.restore()
        if self.login_popup:
            self.login_popup.status_label.text = f"Auth Failed!\n{error_message}"
            self.login_popup.login_button.disabled = False
        else:
            Logger.error(f"Login: Failure: {error_message}")
            self.root.status_text = f"Auth Failed: {error_message}"
            self.on_start()
            def update_popup_status(dt):
                if self.login_popup:
                    self.login_popup.status_label.text = f"Auth Failed!\n{error_message}"
                    self.login_popup.login_button.disabled = False
            Clock.schedule_once(update_popup_status, 0)

def main():
    MusipelagoAPWGenApp().run()

if __name__ == '__main__':
    main()