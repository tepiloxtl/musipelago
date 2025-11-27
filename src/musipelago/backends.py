# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from kivy.uix.boxlayout import BoxLayout
from kivy.app import App
from kivy.properties import BooleanProperty
from kivy.event import EventDispatcher

# --- Generic Data Models ---
@dataclass
class GenericTrack:
    uri: str
    title: str
    artist: str
    album_title: str
    duration_ms: int
    service: str
    
@dataclass
class GenericAlbum:
    uri: str
    title: str
    artist: str
    image_url: str
    total_tracks: int
    album_type: str
    service: str
    tracks: list[GenericTrack] = field(default_factory=list)
    display_image_url: str = ""

@dataclass
class GenericArtist:
    uri: str
    name: str
    image_url: str
    service: str
    metadata: dict = field(default_factory=dict)
    display_image_url: str = ""

@dataclass
class GenericPlaylist:
    uri: str
    name: str
    owner: str
    image_url: str
    total_tracks: int
    service: str
    display_image_url: str = ""


# --- Abstract Backend Interface ---

class AbstractMusicBackend(ABC):
    """
    Defines the interface for all music backends.
    """
    def __init__(self, service_name_key: str, on_login_success, on_login_failure):
        self.on_login_success = on_login_success
        self.on_login_failure = on_login_failure
        self.is_authenticated = False
        self.user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36' # Fallback
        self.service_name = service_name_key # This is now the module name

    @abstractmethod
    def get_login_ui(self) -> object:
        """
        Contract for login UI.
        - Return None for external/browser login.
        - Return a Kivy Widget for a custom UI popup.
        """
        return None

    @abstractmethod
    def login(self, login_widget: object = None):
        """
        Starts the authentication process.
        - If login is external, login_widget will be None.
        - If login is custom, login_widget is the UI provided
          by get_login_ui().
        """
        pass

    @abstractmethod
    def search(self, query: str, search_type: str, limit: int = 20, offset: int = 0):
        """Returns: A list of [GenericAlbum | GenericArtist | GenericPlaylist]"""
        pass

    @abstractmethod
    def get_album_with_tracks(self, album: GenericAlbum):
        """Returns: A GenericAlbum object with the 'tracks' list populated."""
        pass

    @abstractmethod
    def get_playlist_with_tracks(self, playlist: GenericPlaylist):
        """Returns: A GenericAlbum object (representing the playlist)"""
        pass

    @abstractmethod
    def get_all_artist_albums(self, artist: GenericArtist):
        """Returns: A list of GenericAlbum objects."""
        pass

    @abstractmethod
    def get_artist_albums_for_display(self, artist: GenericArtist):
        """Returns: A list of GenericAlbum objects (tracks not required)."""
        pass

    @abstractmethod
    def get_client_data(self) -> dict:
        """
        Returns a dictionary of extra data this plugin wants to
        pass to the client via the generated JSON file.
        """
        return {}
    
    @abstractmethod
    def initialize_client(self, client_data: dict, app: App):
        """
        Called by the client app *after* successful login
        to pass plugin-specific data (from the .json) to the backend
        and create the final API client object (like 'sp').
        """
        pass
    
    @abstractmethod
    def client_requires_display_data(self) -> bool:
        """
        Returns True if the client for this backend relies on
        the 'display_data' key in the JSON.
        Returns False if the client fetches this data live (e.g., Subsonic).
        """
        pass

class AbstractPluginHost(ABC):
    """
    Defines the UI LOGIC interface for plugins.
    An instance of this will be created by the main app after login.
    """
    def __init__(self):
        self.root_layout = None
        self.backend = None
        self.app = None

    def initialize(self, root_layout, backend_instance):
        """
        Called by the main app to give the plugin references to the
        main UI and its own backend logic.
        """
        self.root_layout = root_layout
        self.backend = backend_instance
        self.app = App.get_running_app()
        
        # --- This is the "contract" ---
        # Plugins MUST override this method
        self.setup_ui()

    @abstractmethod
    def setup_ui(self):
        """
        Plugin's main entry point to configure the UI.
        - Hide/show search bar
        - Populate left pane
        """
        pass

    def on_search_click(self, search_text, search_type):
        """
        Called by RootLayout's search button.
        Plugins that don't use the search bar can ignore this.
        """
        pass
        
    def add_to_apworld(self, generic_album: GenericAlbum):
        """
        Helper method for plugins to add data to the right pane.
        """
        if self.root_layout:
            list_container = self.root_layout.ids.list_container
            list_container.add_apworld_item(generic_album)
    
    def on_item_menu_click(self, item_list_id: str, generic_item: any) -> bool:
        """
        (Optional) Called when the '...' menu button on a list item is clicked.
        
        Return True if the plugin handled this click directly (and no menu should open).
        Return False to let the default menu logic (search, apworld) proceed.
        """
        return False # Default: do nothing, let the default menu open
    
class AbstractClientHost(EventDispatcher, ABC):
    """
    Defines the UI and PLAYBACK logic interface for client plugins.
    """
    is_playing = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root_layout = None

    def initialize(self, root_layout, backend_instance):
        """
        Called by the main app to give the plugin references to the
        main UI and its own backend logic.
        """
        self.root_layout = root_layout
        self.backend = backend_instance
        self.app = App.get_running_app()
        self.setup_ui()

    @abstractmethod
    def setup_ui(self):
        """
        Plugin's main entry point to configure the client UI.
        """
        pass
        
    @abstractmethod
    def fetch_game_data_threaded(self, game_data: dict):
        """
        Called by the main app after the JSON is loaded.
        The plugin must parse the 'game_data' dict (e.g., "apworld"
        or "display_data" key) to get the items, fetch any
        necessary API data, and populate the app's caches.
        
        When complete, it MUST call self.app._populate_initial_lists()
        on the main thread (using Clock.schedule_once).
        """
        pass
        
    @abstractmethod
    def start_polling(self):
        """Start polling for playback progress."""
        pass
        
    @abstractmethod
    def stop_polling(self):
        """Stop polling for playback progress."""
        pass
        
    # --- UI/Playback Command Passthroughs ---
    
    @abstractmethod
    def on_play_pause_click(self):
        """Called by the Play/Pause button."""
        pass

    def on_stop_click(self):
        """Called by the Stop button."""
        pass
        
    @abstractmethod
    def on_device_select(self, device_name: str):
        """Called by the device Spinner."""
        pass
        
    @abstractmethod
    def on_volume_change(self, new_volume: int):
        """Called by the volume Slider."""
        pass

    @abstractmethod
    def on_mute_toggle(self, is_muted: bool):
        """Called by the global Mute button."""
        pass
            
    @abstractmethod
    def on_list_item_click(self, list_item_widget: object):
        """
        Called when a CustomListItem (album or track) is clicked.
        """
        pass
        
    @abstractmethod
    def on_menu_action(self, option_text: str, list_item_widget: object):
        """
        Called when an option from the '...' menu is selected.
        """
        pass

    def on_playback_finished(self):
        """
        Called by the GenericAudioPlayer when a track finishes.
        Only used by plugins that use the generic player.
        """
        pass

    @abstractmethod
    def get_settings_ui(self) -> BoxLayout | None:
        """
        Returns a custom Kivy Widget (BoxLayout) for the settings popup,
        or None if the plugin has no extra options.
        """
        return None