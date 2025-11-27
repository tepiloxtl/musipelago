# -*- coding: utf-8 -*-
import os
import threading
import requests
import hashlib
import random
import string
import urllib.parse

# --- Kivy imports ---
from kivy.app import App
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.image import Image

# --- Imports from the main application's interface ---
from musipelago.backends import (
    AbstractMusicBackend, AbstractPluginHost, 
    GenericAlbum, GenericArtist, GenericPlaylist, GenericTrack,
    AbstractClientHost
)
from musipelago.utils_client import KIVY_ICON

# --- Import Generic UI ---
from musipelago.client_ui_components import GenericPlaybackInfo, ItemMenu

# -------------------------------------------------------------------
# 1. LOGIN UI WIDGET
# -------------------------------------------------------------------
class SubsonicLoginUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.spacing = dp(10)
        self.padding = dp(10)
        self.size_hint_y = None
        self.height = dp(260)
        self.desired_popup_height = dp(390)

        # Server
        self.add_widget(Label(text="Server URL (e.g. http://music.server.com):", halign='left', size_hint_y=None, height=dp(30)))
        self.server_input = TextInput(hint_text='http://...', multiline=False, write_tab=False, size_hint_y=None, height=dp(40))
        self.add_widget(self.server_input)

        # Username
        self.add_widget(Label(text="Username:", halign='left', size_hint_y=None, height=dp(30)))
        self.username_input = TextInput(hint_text='user', multiline=False, write_tab=False, size_hint_y=None, height=dp(40))
        self.add_widget(self.username_input)
        
        # Password
        self.add_widget(Label(text="Password:", halign='left', size_hint_y=None, height=dp(30)))
        self.password_input = TextInput(hint_text='••••••', password=True, multiline=False, write_tab=False, size_hint_y=None, height=dp(40))
        self.add_widget(self.password_input)

        self.add_widget(Label(
            text="Note: If playback fails/skips, enable 'Transcode to MP3' in the settings menu after logging in.",
            font_size='11sp',
            color=(1, 0.8, 0.2, 1),
            size_hint_y=None,
            height=dp(30),
            text_size=(self.width, None),
            halign='center'
        ))

        self._load_cached_credentials()
    
    def _load_cached_credentials(self):
        app = App.get_running_app()
        # Check if the app has a store (both Client and Generator now do)
        if hasattr(app, 'store') and app.store.exists('subsonic_credentials'):
            data = app.store.get('subsonic_credentials')
            self.server_input.text = data.get('server', '')
            self.username_input.text = data.get('username', '')
            # self.password_input.text = data.get('password', '')


# -------------------------------------------------------------------
# 2. DATA LOGIC CLASS (Shared)
# -------------------------------------------------------------------
class SubsonicBackendLogic(AbstractMusicBackend):
    def __init__(self, service_name_key, on_login_success, on_login_failure):
        super().__init__(service_name_key, on_login_success, on_login_failure)
        self.server_url = None
        self.username = None
        self.password = None
        self.api_version = '1.16.1'
        self.client_name = 'Musipelago'
        
    def get_login_ui(self) -> object:
        return SubsonicLoginUI()

    def login(self, login_widget=None):
        if not login_widget:
            self.on_login_failure("Login UI not provided.")
            return
            
        server = login_widget.server_input.text.strip().rstrip('/')
        user = login_widget.username_input.text.strip()
        pwd = login_widget.password_input.text.strip()
        
        if not server or not user or not pwd:
            self.on_login_failure("All fields are required.")
            return
            
        if not server.startswith(('http://', 'https://')):
            server = 'http://' + server

        try:
            app = App.get_running_app()
            if hasattr(app, 'store'):
                app.store.put('subsonic_credentials', 
                    server=server, 
                    username=user, 
                    # password=pwd
                )
        except Exception as e:
            Logger.error(f"Subsonic: Failed to cache credentials: {e}")

        threading.Thread(target=self._auth_thread, args=(server, user, pwd)).start()

    def _auth_thread(self, server, user, pwd):
        try:
            # Ping to test creds
            params = self._build_params(user, pwd)
            url = f"{server}/rest/ping"
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code != 200:
                raise Exception(f"HTTP Error {response.status_code}")
                
            data = response.json()
            sub_resp = data.get('subsonic-response', {})
            
            if sub_resp.get('status') == 'ok':
                self.server_url = server
                self.username = user
                self.password = pwd
                self.is_authenticated = True
                
                Clock.schedule_once(lambda dt: self.on_login_success({'display_name': user}))
            else:
                err = sub_resp.get('error', {}).get('message', 'Unknown Error')
                raise Exception(f"API Error: {err}")
                
        except Exception as e:
            Logger.error(f"Subsonic Login Error: {e}")
            Clock.schedule_once(lambda dt: self.on_login_failure(str(e)))

    def _build_params(self, user=None, pwd=None):
        """Generates the salt/token auth parameters."""
        u = user or self.username
        p = pwd or self.password
        
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        token = hashlib.md5((p + salt).encode('utf-8')).hexdigest()
        
        return {
            'u': u,
            't': token,
            's': salt,
            'v': self.api_version,
            'c': self.client_name,
            'f': 'json'
        }

    def get_client_data(self) -> dict:
        # Pass the server URL so the client knows which server to expect
        return {"server_url": self.server_url}

    def client_requires_display_data(self) -> bool:
        # YES. We want to cache metadata in the JSON to avoid
        # hammering the API for track titles/durations on startup.
        return True 

    def initialize_client(self, client_data, app):
        """
        Called on Client. We verify the logged-in server matches the JSON.
        """
        json_server = client_data.get('server_url')
        
        # Normalize for comparison
        if json_server: json_server = json_server.rstrip('/')
        current_server = self.server_url.rstrip('/') if self.server_url else ""
        
        if json_server and json_server != current_server:
            Logger.warning(f"Subsonic: JSON is for {json_server}, but logged into {current_server}")
            # We don't fail hard, but we warn.
            
        Logger.info("SubsonicClient: Initialized.")

    # --- API METHODS (Generator) ---
    
    def search(self, query: str, search_type: str, limit: int = 20, offset: int = 0):
        if not self.is_authenticated: return []
        
        params = self._build_params()
        params['query'] = query
        
        # Map generic offset to Subsonic specific offsets
        if search_type == 'artist':
            params['artistCount'] = limit
            params['artistOffset'] = offset
        elif search_type == 'album':
            params['albumCount'] = limit
            params['albumOffset'] = offset
        
        try:
            resp = requests.get(f"{self.server_url}/rest/search3", params=params).json()
            results = resp.get('subsonic-response', {}).get('searchResult3', {})
            
            generic_results = []
            
            if search_type == 'artist':
                for item in results.get('artist', []):
                    raw_art = self._get_cover_id_string(item.get('coverArt'))
                    generic_results.append(GenericArtist(
                        uri=f"subsonic:artist:{item['id']}",
                        name=item['name'],
                        image_url=raw_art,                   # STORE: coverArt:123
                        display_image_url=self._sign_url(raw_art), # SHOW: http://...
                        service='subsonic'
                    ))
            elif search_type == 'album':
                for item in results.get('album', []):
                    raw_art = self._get_cover_id_string(item.get('coverArt'))
                    generic_results.append(GenericAlbum(
                        uri=f"subsonic:album:{item['id']}",
                        title=item['name'],
                        artist=item.get('artist', 'Unknown'),
                        image_url=raw_art,                   # STORE: coverArt:123
                        display_image_url=self._sign_url(raw_art), # SHOW: http://...
                        total_tracks=item.get('songCount', 0),
                        album_type="Album",
                        service='subsonic'
                    ))
            return generic_results
        except Exception as e:
            Logger.error(f"Subsonic Search Error: {e}")
            return []

    def _get_cover_id_string(self, cover_id):
        if not cover_id: return ""
        return f"coverArt:{cover_id}"
    
    def _sign_url(self, fragment):
        if not fragment or not fragment.startswith("coverArt:"): return ""
        cid = fragment.split(":")[1]
        params = self._build_params()
        params['id'] = cid
        query = urllib.parse.urlencode(params)
        return f"{self.server_url}/rest/getCoverArt?{query}"

    def get_album_with_tracks(self, album):
        # Parse ID from "subsonic:album:123"
        album_id = album.uri.split(':')[-1]
        params = self._build_params()
        params['id'] = album_id
        
        try:
            resp = requests.get(f"{self.server_url}/rest/getAlbum", params=params).json()
            data = resp.get('subsonic-response', {}).get('album', {})
            
            tracks = []
            for song in data.get('song', []):
                tracks.append(GenericTrack(
                    uri=f"subsonic:track:{song['id']}",
                    title=song['title'],
                    artist=song.get('artist', album.artist),
                    album_title=album.title,
                    duration_ms=song.get('duration', 0) * 1000,
                    service='subsonic'
                ))
            
            album.tracks = tracks
            return album
        except Exception as e:
            Logger.error(f"Subsonic getAlbum Error: {e}")
            return album

    def get_all_artist_albums(self, artist):
        artist_id = artist.uri.split(':')[-1]
        params = self._build_params()
        params['id'] = artist_id
        
        try:
            resp = requests.get(f"{self.server_url}/rest/getArtist", params=params).json()
            data = resp.get('subsonic-response', {}).get('artist', {})
            
            albums = []
            for alb in data.get('album', []):
                # Create GenericAlbum (we have to fetch tracks separately later if needed)
                # For the "Add all" action, this list is iterated and get_album_with_tracks called
                albums.append(GenericAlbum(
                    uri=f"subsonic:album:{alb['id']}",
                    title=alb['name'],
                    artist=artist.name,
                    image_url=self._get_cover_url(alb.get('coverArt')),
                    total_tracks=alb.get('songCount', 0),
                    album_type="Album",
                    service='subsonic'
                ))
            return albums
        except Exception as e:
            Logger.error(f"Subsonic getArtist Error: {e}")
            return []

    def get_artist_albums_for_display(self, artist):
        # Same as above, but maybe lightweight
        return self.get_all_artist_albums(artist)

    def get_playlist_with_tracks(self, playlist):
        return None # Not implemented yet

    def _get_cover_url(self, cover_id):
        if not cover_id: return KIVY_ICON
        # Return a relative URL we can construct later
        # The client needs to sign this URL to use it
        return f"coverArt:{cover_id}"


# -------------------------------------------------------------------
# 3. GENERATOR UI HOST
# -------------------------------------------------------------------
class SubsonicHostUI(AbstractPluginHost):
    def setup_ui(self):
        Logger.info("SubsonicHostUI: Setting up default search UI.")
        self.root_layout.ids.search_container.disabled = False
        self.root_layout.ids.search_container.opacity = 1
        self.root_layout.ids.list_container.list_one_data = []
        
    def on_search_click(self, search_text, search_type):
        if not self.backend.is_authenticated:
            self.root_layout.status_text = "Please log in."
            return
        self.root_layout.status_text = f"Searching Subsonic for '{search_text}'..."
        threading.Thread(target=self._search_thread, args=(search_text, search_type)).start()

    def _search_thread(self, text, type):
        try:
            results = self.backend.search(text, type)
            Clock.schedule_once(lambda dt: self._update_list(results, type))
        except Exception as e:
            Logger.error(f"Search failed: {e}")

    def _update_list(self, results, type):
        data = []
        for item in results:
            # Convert Generic objects to UI dicts
            # Using the backend to generate a signed image URL for display
            img = self._sign_url(item.image_url)
            
            data.append({
                'text_line_1': item.title if hasattr(item, 'title') else item.name,
                'text_line_2': getattr(item, 'artist', 'Artist'),
                'text_line_3': getattr(item, 'album_type', ''),
                'text_line_4': item.uri,
                'image_source': img,
                'list_id': 'search',
                'generic_item': item
            })
        self.root_layout.ids.list_container.list_one_data = data
        self.root_layout.status_text = f"Found {len(data)} results."
        
    def on_item_menu_click(self, list_id, item): return False


# -------------------------------------------------------------------
# 4. CLIENT UI HOST
# -------------------------------------------------------------------
class SubsonicSettingsWidget(BoxLayout):
    def __init__(self, host_instance, **kwargs):
        super().__init__(**kwargs)
        self.host = host_instance
        self.orientation = 'vertical'
        self.spacing = dp(10)
        self.padding = dp(10)
        
        # Transcoding Options
        box = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(5))
        box.add_widget(Label(text="Stream Format:", size_hint_x=0.4))
        
        # Options: 'raw' (Original) or 'mp3' (Transcode)
        self.format_spinner = Spinner(
            text=self.host.transcode_format,
            values=('mp3', 'raw'),
            size_hint_x=0.6
        )
        self.format_spinner.bind(text=self._on_format_change)
        box.add_widget(self.format_spinner)
        
        self.add_widget(box)
        
        # Info Label
        self.add_widget(Label(
            text="Select 'mp3' if playback fails or skips.",
            font_size='11sp',
            color=(0.7, 0.7, 0.7, 1)
        ))
        self.add_widget(BoxLayout()) # Spacer

    def _on_format_change(self, instance, value):
        self.host.set_transcode_format(value)


class SubsonicClientHost(AbstractClientHost):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playback_info_widget = None
        self.playback_queue = []
        self.queue_index = -1
        self.poll_event = None
        self.current_playing_track_uri = None
        self.current_playing_track_title = None
        self.transcode_format = 'raw'
        self._load_settings()

    def setup_ui(self):
        Logger.info("SubsonicClient: Setting up UI.")
        self.bind(is_playing=self.root_layout.setter('is_playing'))
        
        self.playback_info_widget = self.root_layout.playback_info_widget = GenericPlaybackInfo()
        self.root_layout.ids.playback_center.clear_widgets()
        self.root_layout.ids.playback_center.add_widget(self.playback_info_widget)
        
        self.root_layout.set_status("Connected to Subsonic.")

    def get_settings_ui(self):
        return None

    def fetch_game_data_threaded(self, game_data: dict):
        # Logic is identical to LocalFiles because we used client_requires_display_data=True
        Logger.info("SubsonicClient: Parsing display data...")
        display_data = game_data.get("display_data")
        if not display_data: return
        
        threading.Thread(target=self._parse_thread, args=(display_data,)).start()

    def _parse_thread(self, display_data):
        try:
            for album_dict in display_data:
                tracks = [GenericTrack(**t) for t in album_dict.get('tracks', [])]
                album_dict['tracks'] = tracks
                album = GenericAlbum(**album_dict)

                raw_image = album_dict.get('image_url')
                
                # Calculate the signed URL for display
                display_image = self._get_signed_url(raw_image)
                
                # Create object (populating both fields)
                # We must ensure album_dict doesn't overwrite display_image if it's in there
                album_dict['display_image_url'] = display_image
                
                album = GenericAlbum(**album_dict)
                self.app.album_data_cache[album.uri] = album
                self.app.ordered_album_uris.append(album.uri)
                
            Clock.schedule_once(self.app._populate_initial_lists)
        except Exception as e:
            Logger.error(f"Subsonic Parse Error: {e}")

    def _get_signed_url(self, fragment, endpoint="getCoverArt"):
        if not fragment or not fragment.startswith("coverArt:"): return KIVY_ICON
        cid = fragment.split(":")[1]
        params = self.backend._build_params()
        params['id'] = cid
        q = urllib.parse.urlencode(params)
        return f"{self.backend.server_url}/rest/{endpoint}?{q}"

    # --- Playback Logic ---

    def on_list_item_click(self, item):
        if item.raw_item_type == 'album':
            # IMPORTANT: We need to intercept the image URL and sign it
            # before passing it to the UI, otherwise AsyncImage gets a bad URL.
            # However, RootLayout.populate_track_list uses the object from cache.
            # We should probably update the cache object with signed URLs?
            # Better: Override populate_track_list logic by passing a custom image path.
            
            album = self.app.album_data_cache.get(item.raw_uri)
            self.root_layout.populate_track_list(item.raw_uri, local_image_path=album.display_image_url)
            
        elif item.raw_item_type == 'track':
            self._play_track(item.raw_uri, item.raw_title)

    def _play_track(self, uri, title):
        self.stop_polling()
        track_obj = GenericTrack(uri=uri, title=title, artist="", album_title="", duration_ms=0, service="subsonic")
        prog = self.app.track_progress.get(uri)
        if prog:
            parent_uri = prog.get('parent_uri')
            if parent_uri:
                parent_album = self.app.album_data_cache.get(parent_uri)
                if parent_album:
                    # Find the specific track inside this album
                    for t in parent_album.tracks:
                        if t.uri == uri:
                            track_obj = t
                            break
        # --------------------------------------------------

        self.playback_queue = [track_obj]
        self.queue_index = 0
        self._play_track_internal(track_obj)

    def _play_album(self, album_uri):
        album = self.app.album_data_cache.get(album_uri)
        if not album: return
        self.playback_queue = list(album.tracks)
        self.queue_index = 0
        self._play_track_internal(self.playback_queue[0])

    def _play_track_internal(self, track_obj):
        self.stop_polling()
        uri = track_obj.uri
        title = track_obj.title
        
        try:
            tid = uri.split(":")[-1]
        except:
            self.app.show_toast("Invalid Track ID")
            return
        
        params = self.backend._build_params()
        params['id'] = tid
        
        if self.transcode_format != 'raw':
            params['format'] = self.transcode_format
            params['estimateContentLength'] = 'true'
        
        q = urllib.parse.urlencode(params)
        stream_url = f"{self.backend.server_url}/rest/stream?{q}"
        stream_url += "&.mp3" # .mp3 suffix fix for Kivy url parser
                
        # Update UI
        if self.playback_info_widget:
            self.playback_info_widget.track_title = title
            self.playback_info_widget.progress_value = 0
            self.playback_info_widget.current_time = "00:00"
            self.playback_info_widget.total_time = "Loading..."
            self.playback_info_widget.artist_album = f"{track_obj.artist} - {track_obj.album_title}"
            
            # Attempt to get Cover Art
            prog = self.app.track_progress.get(uri)
            if prog and (parent := prog.get('parent_uri')):
                if album := self.app.album_data_cache.get(parent):
                    self.playback_info_widget.art_source = self._get_signed_url(album.image_url)

        Logger.info(f"Subsonic: Streaming ({self.transcode_format}): {stream_url}")
        self.app.audio_player.play(stream_url)
        
        self.current_playing_track_uri = uri
        self.current_playing_track_title = title
        self.is_playing = True
        
        Clock.schedule_once(lambda dt: self.start_polling(), 0.5)
    
    def _load_settings(self):
        app = App.get_running_app()
        if hasattr(app, 'store') and app.store.exists('subsonic_settings'):
            data = app.store.get('subsonic_settings')
            self.transcode_format = data.get('format', 'raw')

    def set_transcode_format(self, fmt):
        """Called by Settings Widget."""
        self.transcode_format = fmt
        Logger.info(f"Subsonic: Transcode format set to {fmt}")
        # Save to store
        app = App.get_running_app()
        if hasattr(app, 'store'):
            app.store.put('subsonic_settings', format=fmt)

    def get_settings_ui(self):
        return SubsonicSettingsWidget(host_instance=self)
    
    def update_playback_state(self): pass

    # --- Polling/Events ---
    def start_polling(self):
        if not self.poll_event: self.poll_event = Clock.schedule_interval(self._update_progress, 0.1)
    def stop_polling(self):
        if self.poll_event: self.poll_event.cancel(); self.poll_event = None
    
    def _update_progress(self, dt):
        if not self.is_playing: return
        player = self.app.audio_player
        if not player: return
        try:
            pos = player.get_position()
            dur = player.get_duration()
            if self.playback_info_widget and dur > 0:
                self.playback_info_widget.progress_value = (pos / dur) * 100
                self.playback_info_widget.current_time = self.root_layout.format_duration(pos * 1000)
                self.playback_info_widget.total_time = self.root_layout.format_duration(dur * 1000)
        except: pass

    def on_playback_finished(self):
        self.stop_polling()
        # ... (Same completion/queue logic as LocalFiles) ...
        # 1. Complete
        if self.current_playing_track_uri:
            uri = self.current_playing_track_uri
            if data := self.app.track_progress.get(uri):
                if not data['is_finished']:
                    data['is_finished'] = True
                    if lid := data.get('location_id'): 
                        if self.app.ap_client: self.app.ap_client.send_location_check(lid)
                    self.root_layout.update_track_ui(uri)
        
        # 2. Advance
        idx = self.queue_index + 1
        if 0 <= idx < len(self.playback_queue):
            self.queue_index = idx
            Clock.schedule_once(lambda dt: self._play_track_internal(self.playback_queue[idx]), 0.2)
        else:
            self.is_playing = False
            self.playback_info_widget.track_title = "Finished"

    # ... (Standard passthroughs) ...
    def on_play_pause_click(self):
        if self.is_playing:
            self.app.audio_player.pause(); self.is_playing = False
        elif self.current_playing_track_uri:
            self.app.audio_player.resume(); self.is_playing = True
    def on_stop_click(self):
        self.stop_polling(); self.app.audio_player.stop(); self.is_playing = False
    def on_volume_change(self, val):
        self.app.audio_player.set_volume(val / 100.0)
    def on_mute_toggle(self, is_muted): pass
    def on_device_select(self, name): pass
    def on_menu_action(self, txt, item):
        if txt == "OPEN_MENU": 
            # (Import and open generic menu)
            menu = ItemMenu(caller=item, auto_width=False, width=dp(200))
            # ... (add buttons) ...
            menu.open(item.ids.menu_button)
        elif txt == "Play Album": self._play_album(item.raw_uri)
        elif txt == "Play Track": self._play_track(item.raw_uri, item.raw_title)
        # ...

# --- MANIFEST ---
MUSIPELAGO_PLUGIN = {
    "name": "OpenSubsonic",
    "generator_backend": SubsonicBackendLogic,
    "generator_ui": SubsonicHostUI,
    "client_backend": SubsonicBackendLogic,
    "client_ui": SubsonicClientHost
}