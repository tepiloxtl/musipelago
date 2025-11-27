# -*- coding: utf-8 -*-
import os, threading, hashlib, base64

from kivy.logger import Logger
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.popup import Popup
from kivy.uix.gridlayout import GridLayout
from kivy.uix.dropdown import DropDown
from kivy.uix.progressbar import ProgressBar
from kivy.uix.filechooser import FileChooserListView
from kivy.app import App

# --- Plyer import ---
try:
    from plyer import filechooser
except ImportError:
    Logger.warning("LocalFilesBackend: 'plyer' not installed.")
    filechooser = None

# --- Mutagen import for ID3 tags ---
try:
    import mutagen
    from mutagen import File as MutagenFile
    from mutagen.mp3 import MP3, EasyMP3
    from mutagen.id3 import ID3, APIC
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
except ImportError:
    Logger.warning("LocalFilesBackend: 'mutagen' not installed.")
    mutagen = None
    MutagenFile = None

# --- Imports from the main application's interface ---
from musipelago.backends import (
    AbstractMusicBackend, AbstractPluginHost, AbstractClientHost,
    GenericAlbum, GenericArtist, GenericPlaylist, GenericTrack
)
from musipelago.utils import KIVY_ICON, filter_to_ascii
from musipelago.client_ui_components import GenericPlaybackInfo, ItemMenu

# --- Plugin-specific helper UI ---

class DirectoryPickerPopup(Popup):
    """
    A pure Kivy popup that lets the user select a directory.
    Replaces the need for tkinter or inconsistent plyer behavior.
    """
    def __init__(self, initial_path, on_selection, **kwargs):
        super().__init__(**kwargs)
        self.title = "Select Album Directory"
        self.size_hint = (0.9, 0.9)
        self.on_selection = on_selection
        
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        
        # 1. File Chooser (List View)
        # filters=[lambda folder, filename: not filename.startswith('.')] # Optional: hide hidden files
        self.file_chooser = FileChooserListView(
            path=initial_path,
            dirselect=True, # CRITICAL: Allow directory selection
            filters=[''] # Show directories only (mostly)
        )
        layout.add_widget(self.file_chooser)
        
        # 2. Buttons
        btn_layout = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        
        cancel_btn = Button(text="Cancel", on_release=self.dismiss)
        select_btn = Button(text="Select This Folder", on_release=self.select_current)
        
        btn_layout.add_widget(cancel_btn)
        btn_layout.add_widget(select_btn)
        
        layout.add_widget(btn_layout)
        self.content = layout

    def select_current(self, *args):
        # If user clicked a folder in the list, use that. 
        # Otherwise use the currently open path.
        selection = self.file_chooser.selection
        if selection:
            # selection is a list, usually [full_path]
            path = selection[0]
        else:
            path = self.file_chooser.path
            
        self.on_selection(path)
        self.dismiss()

class CreateAlbumPopup(Popup):
    """
    A plugin-specific popup for creating a new local album.
    """
    def __init__(self, title, artist, on_create_callback, **kwargs):
        super().__init__(**kwargs)
        self.title = "Create New Local Album"
        self.size_hint = (0.8, None)
        self.auto_dismiss = False
        
        self.on_create_callback = on_create_callback
        
        layout = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        form_grid = GridLayout(cols=2, spacing=dp(10), size_hint_y=None, height=dp(88))
        
        form_grid.add_widget(Label(text="Album Title:", size_hint_x=0.3))
        self.album_input = TextInput(text=title, multiline=False, write_tab=False)
        form_grid.add_widget(self.album_input)
        
        form_grid.add_widget(Label(text="Artist:", size_hint_x=0.3))
        self.artist_input = TextInput(text=artist, multiline=False, write_tab=False)
        form_grid.add_widget(self.artist_input)
        
        layout.add_widget(form_grid)

        button_layout = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = Button(text="Cancel", on_release=self.dismiss)
        create_btn = Button(text="Create", on_release=self.on_create_press)
        button_layout.add_widget(cancel_btn)
        button_layout.add_widget(create_btn)
        
        layout.add_widget(button_layout)
        
        self.content = layout
        self.height = dp(220)

    def on_create_press(self, *args):
        album_title = self.album_input.text.strip()
        artist_name = self.artist_input.text.strip()
        
        if not album_title:
            self.album_input.text = ""
            return
        
        if not artist_name:
            artist_name = "Unknown Artist"
            
        self.on_create_callback(self, album_title, artist_name)

class LocalFilesLoginUI(BoxLayout):
    """
    This is the Kivy widget that the main app will show in a popup.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.spacing = '10dp'
        self.padding = '10dp'
        
        # Set a fixed height for this widget so it doesn't collapse
        self.size_hint_y = None
        # Label (30) + TextInput (40) + Button (44) + Spacing (2*10) + Padding (2*10) = 154
        self.height = dp(154) 
        self.desired_popup_height = dp(280)

        # Label
        self.add_widget(Label(
            text="Select your root music directory:",
            halign='left',
            size_hint_y=None,
            height=dp(30)
        ))
        
        # Text Input to display the path
        self.path_input = TextInput(
            hint_text='No directory selected...',
            readonly=True,
            size_hint_y=None,
            height=dp(40)
        )
        self.add_widget(self.path_input)
        
        # Button to open the dialog
        self.choose_btn = Button(
            text="Choose Directory...",
            size_hint_y=None,
            height=dp(44)
        )
        self.choose_btn.bind(on_release=self.open_dialog)
        self.add_widget(self.choose_btn)
        
        if filechooser is None:
            self.choose_btn.text = "'plyer' is not installed"
            self.choose_btn.disabled = True

    def open_dialog(self, *args):
        """
        Uses plyer to open the native directory chooser.
        """
        if filechooser:
            filechooser.choose_dir(on_selection=self.on_selection_callback)
            
    def on_selection_callback(self, selection: list):
        """
        Callback fired by plyer after the user selects a directory.
        """
        if selection and isinstance(selection, list):
            self.path_input.text = selection[0]
            Logger.info(f"LocalFilesLoginUI: Path selected: {selection[0]}")
        else:
            Logger.info("LocalFilesLoginUI: No path selected.")


class LocalFilesBackendLogic(AbstractMusicBackend):
    """
    "Authentication" is just selecting a root music directory.
    """

    def __init__(self, service_name_key: str, on_login_success, on_login_failure):
        super().__init__(service_name_key, on_login_success, on_login_failure)
        self.root_directory = None

    def get_login_ui(self) -> object:
        """
        This is the "contract". We return a Kivy widget.
        """
        Logger.info("LocalFilesBackend: Providing custom login UI.")
        return LocalFilesLoginUI()

    def login(self, login_widget: object = None):
        """
        The main app calls this AFTER the user clicks "Login"
        on the custom popup.
        """
        if login_widget is None or not isinstance(login_widget, LocalFilesLoginUI):
            msg = "LocalFilesBackend: Login failed, custom UI was not provided."
            Logger.error(msg)
            Clock.schedule_once(lambda dt: self.on_login_failure(msg))
            return

        # 1. Extract the path from the UI widget
        directory_path = login_widget.path_input.text
        
        # 2. Check if a path was actually selected
        if not directory_path or not os.path.isdir(directory_path):
            msg = "No valid directory selected."
            Logger.warning(f"LocalFilesBackend: {msg}")
            Clock.schedule_once(lambda dt: self.on_login_failure(msg))
            return
            
        # 3. --- LOGIN SUCCESS ---
        self.root_directory = directory_path
        self.is_authenticated = True
        
        folder_name = os.path.basename(directory_path)
        user_data = {'display_name': f"Folder: ...{folder_name}"}
        
        Logger.info(f"LocalFilesBackend: 'Logged in' to {directory_path}")
        # Call success on the main thread
        Clock.schedule_once(lambda dt: self.on_login_success(user_data))

    # --- Abstract Method Stubs ---

    def search(self, query: str, search_type: str, limit: int = 20):
        Logger.info(f"LocalFilesBackend: Searching... (not implemented)")
        return []

    def get_album_with_tracks(self, album: GenericAlbum): return album
    def get_playlist_with_tracks(self, playlist: GenericPlaylist): return None
    def get_all_artist_albums(self, artist: GenericArtist): return []
    def get_artist_albums_for_display(self, artist: GenericArtist): return []

    def get_client_data(self) -> dict:
        """
        Pass the selected root music directory to the client.
        """
        return {
            "root_directory": self.root_directory
        }

    def initialize_client(self, client_data: dict, app: App):
        """
        Called by the client app *after* successful login.
        This just sets the root directory.
        """
        self.root_directory = client_data.get('root_directory')
        if not self.root_directory:
            Logger.error("LocalFilesClient: No 'root_directory' found in JSON data.")
            # This will fail gracefully
            app.on_login_failure("Invalid JSON: Missing root_directory")
            return
            
        Logger.info(f"LocalFilesClient: Initialized with root: {self.root_directory}")
        self.is_authenticated = True
        # No return value, no callback

    def client_requires_display_data(self) -> bool:
        """
        Local Files client relies *entirely* on the display_data
        block, as it has no API to call.
        """
        return True

class LocalFilesHostUI(AbstractPluginHost):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._temp_track_info = []
        self._temp_chosen_dir = ""

    def setup_ui(self):
        # ... (implementation unchanged)
        Logger.info("LocalFilesHostUI: Setting up custom 'Local Files' UI.")
        self.root_layout.ids.search_container.disabled = True
        self.root_layout.ids.search_container.opacity = 0
        custom_ui_data = [
            {
                'text_line_1': 'Create New Album',
                'text_line_2': 'Scan a folder to create a new album',
                'text_line_3': '',
                'text_line_4': '',
                'image_source': KIVY_ICON,
                'list_id': 'local_files_action',
                'generic_item': 'create_album_action'
            },
            {
                'text_line_1': 'Scan Root Directory',
                'text_line_2': f"Scan '{self.backend.root_directory}' (Not Implemented)",
                'text_line_3': '',
                'text_line_4': '',
                'image_source': KIVY_ICON,
                'list_id': 'local_files_action',
                'generic_item': 'scan_dir_action'
            }
            # ... (other actions like 'scan_dir_action' can be added here) ...
        ]
        self.root_layout.ids.list_container.list_one_data = custom_ui_data

    def on_search_click(self, search_text, search_type):
        pass

    def on_item_menu_click(self, item_list_id: str, generic_item: any) -> bool:
        if item_list_id == 'local_files_action':
            action_id = str(generic_item)
            
            if action_id == 'create_album_action':
                self.start_create_album_flow()
                return True # We handled the click

            elif action_id == 'scan_dir_action':
                self.root_layout.status_text = "Scan Directory feature is not yet implemented."
                Logger.info("UI: 'Scan Directory' (Not Implemented) clicked.")
                return True # We handled the click
            
        return False

    # --- NEW ALBUM CREATION FLOW ---

    # --- Plyer implementation ---
    # def start_create_album_flow(self):
    #     """
    #     Starts the process by opening the plyer directory chooser.
    #     """
    #     if not filechooser:
    #         self.root_layout.status_text = "Error: 'plyer' library is not installed."
    #         return
            
    #     Logger.info("LocalFiles: Opening directory chooser...")
    #     self.root_layout.status_text = "Opening directory chooser..."
        
    #     # Get the root directory from the backend
    #     start_path = self.backend.root_directory
    #     if not start_path or not os.path.isdir(start_path):
    #         start_path = os.path.expanduser("~") # Fallback to home dir
            
    #     filechooser.open_file(
    #         on_selection=self.on_album_dir_selected,
    #         path=start_path
    #     )

    # def on_album_dir_selected(self, selection: list):
    #     """
    #     Callback from plyer after a directory is chosen.
    #     """
    #     if not selection or not selection[0]:
    #         self.root_layout.status_text = "Album creation cancelled."
    #         return
            
    #     chosen_dir = selection[0]
    #     self.root_layout.status_text = f"Scanning folder: {os.path.basename(chosen_dir)}..."
        
    #     # Run the scan in a background thread
    #     threading.Thread(target=self._scan_dir_thread, args=(chosen_dir,)).start()

    # --- Kivy Filechooser Popup implementation ---
    def start_create_album_flow(self):
        """
        Starts the process using our custom DirectoryPickerPopup.
        This respects 'initial_path' perfectly because it's pure Kivy.
        """
        # Determine start path
        start_path = self.backend.root_directory
        if not start_path or not os.path.isdir(start_path):
            start_path = os.path.expanduser("~")
            
        Logger.info(f"LocalFiles: Opening Kivy file chooser at {start_path}")
        
        # Open the popup directly (no need for threads)
        popup = DirectoryPickerPopup(
            initial_path=start_path,
            on_selection=self._on_kivy_dir_selected
        )
        popup.open()

    def _on_kivy_dir_selected(self, chosen_path):
        """Callback from our custom popup."""
        if not chosen_path:
            self.root_layout.status_text = "Album creation cancelled."
            return
            
        self.root_layout.status_text = f"Scanning folder: {os.path.basename(chosen_path)}..."
        
        # Run the scan in a background thread to keep UI responsive
        threading.Thread(target=self._scan_dir_thread, args=(chosen_path,)).start()
    # ---------------------------------------

    def _scan_dir_thread(self, chosen_dir: str):
        """
        (THREAD) Scans the directory for MP3s and reads their tags.
        """
        if not mutagen:
            Logger.error("Cannot scan: 'mutagen' is not installed.")
            Clock.schedule_once(lambda dt: setattr(self.root_layout, 'status_text', "Error: 'mutagen' is not installed."))
            return

        track_info_list = [] # Stores (filepath, title_tag, artist_tag, duration_ms)
        album_tags = []
        artist_tags = []
        valid_exts = ('.mp3', '.flac', '.m4a', '.ogg', '.wma')

        try:
            for filename in os.listdir(chosen_dir):
                # --- 2. CHECK EXTENSION ---
                if not filename.lower().endswith(valid_exts):
                    continue
                
                filepath = os.path.join(chosen_dir, filename)
                
                title_tag = None
                artist_tag = None
                duration_ms = 0
                
                try:
                    # --- 3. USE GENERIC LOADER ---
                    # mutagen.File detects format from header/extension.
                    # easy=True normalizes keys to 'title', 'artist', 'album' regardless of format.
                    audio = MutagenFile(filepath, easy=True)
                    
                    if audio:
                        # Extract Tags (if present)
                        # Mutagen returns lists for tags, e.g. ['Title']
                        if 'title' in audio:
                            title_tag = audio['title'][0]
                        if 'artist' in audio:
                            artist_tag = audio['artist'][0]
                            artist_tags.append(artist_tag)
                        if 'album' in audio:
                            album_tags.append(audio['album'][0])
                        
                        # Extract Duration
                        # audio.info.length is standard across all mutagen types (in seconds)
                        if audio.info and audio.info.length:
                            duration_ms = int(audio.info.length * 1000)
                            
                except Exception as e:
                    # Don't crash on one bad file, just log and continue (will fallback to filename)
                    Logger.warning(f"LocalFiles: Could not read metadata for {filename}: {e}")
                
                track_info_list.append((filepath, title_tag, artist_tag, duration_ms))

            if not track_info_list:
                Clock.schedule_once(lambda dt: setattr(self.root_layout, 'status_text', f"No supported audio files found in '{os.path.basename(chosen_dir)}'."))
                return

            # Determine consensus for Album/Artist
            consensus_album = max(set(album_tags), key=album_tags.count) if album_tags else ""
            consensus_artist = max(set(artist_tags), key=artist_tags.count) if artist_tags else ""
            
            self._temp_track_info = track_info_list
            self._temp_chosen_dir = chosen_dir
            
            Clock.schedule_once(
                lambda dt: self._open_create_album_popup(
                    consensus_album, 
                    consensus_artist
                )
            )

        except Exception as e:
            Logger.error(f"LocalFiles: Failed to scan directory: {e}")
            Clock.schedule_once(lambda dt: setattr(self.root_layout, 'status_text', f"Error: {e}"))

    def _open_create_album_popup(self, consensus_album: str, consensus_artist: str):
        """
        (MAIN THREAD) Opens the new CreateAlbumPopup.
        """
        self.root_layout.status_text = f"Found {len(self._temp_track_info)} tracks. Please confirm album details."
        
        popup = CreateAlbumPopup(
            title=consensus_album,
            artist=consensus_artist,
            on_create_callback=self.on_album_popup_create
        )
        popup.open()

    def on_album_popup_create(self, popup_instance: Popup, new_album_title: str, new_artist_name: str):
        """
        (MAIN THREAD) Callback from the CreateAlbumPopup.
        This is where we finally create the GenericAlbum.
        """
        try:
            root_dir = self.backend.root_directory
            
            # 1. Create album URI (relative path)
            album_uri = os.path.relpath(self._temp_chosen_dir, root_dir).replace("\\", "/")
            
            # 2. Create track list
            generic_tracks = []
            for filepath, title_tag, artist_tag, duration_ms in self._temp_track_info:
                # Use tag, or fallback to filename
                track_title = title_tag or os.path.splitext(os.path.basename(filepath))[0]
                
                # Use tag, or fallback to album artist
                track_artist = artist_tag or new_artist_name
                
                # Create track URI (relative path)
                track_uri = os.path.relpath(filepath, root_dir).replace("\\", "/")
                
                generic_tracks.append(GenericTrack(
                    uri=track_uri,
                    title=track_title,
                    artist=track_artist,
                    album_title=new_album_title,
                    duration_ms=duration_ms,
                    service='local'
                ))
                
            # 3. Create the album
            new_album = GenericAlbum(
                uri=album_uri,
                title=new_album_title,
                artist=new_artist_name,
                image_url="",
                total_tracks=len(generic_tracks),
                album_type="Album",
                service='local',
                tracks=generic_tracks
            )
            
            # 4. Add to the APWorld (right pane)
            self.add_to_apworld(new_album)
            
            self.root_layout.status_text = f"Added album '{new_album.title}'."
            
        except Exception as e:
            Logger.error(f"LocalFiles: Failed to create album: {e}")
            self.root_layout.status_text = "Error creating album. Check logs."
            
        finally:
            # 5. Clean up temp data and close popup
            self._temp_track_info = []
            self._temp_chosen_dir = ""
            popup_instance.dismiss()

class LocalFilesClientHost(AbstractClientHost):
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_playing_track_uri = None
        self.current_playing_track_title = None
        self.playback_queue = []
        self.queue_index = -1
        
        # UI References
        self.playback_ui = None
        self.track_label = None
        self.progress_bar = None
        self.poll_event = None

    def setup_ui(self):
        Logger.info("LocalFilesClientHost: Setting up UI.")
        
        # 1. Bind is_playing
        self.bind(is_playing=self.root_layout.setter('is_playing'))
        
        # 2. Instantiate and inject GenericPlaybackInfo
        self.playback_info_widget = self.root_layout.playback_info_widget = GenericPlaybackInfo()
        
        # Initialize with empty state
        self.playback_info_widget.track_title = "Not Playing"
        self.playback_info_widget.artist_album = "Select a track"
        self.playback_info_widget.current_time = "00:00"
        self.playback_info_widget.total_time = "00:00"
        self.playback_info_widget.progress_value = 0
        self.playback_info_widget.art_source = KIVY_ICON

        center_slot = self.root_layout.ids.playback_center
        center_slot.clear_widgets()
        center_slot.add_widget(self.playback_info_widget)
        
        self.root_layout.set_status("Local files loaded. Ready to play.")

    def fetch_game_data_threaded(self, game_data: dict):
        Logger.info("LocalFilesClientHost: Parsing and scanning local data...")
        display_data = game_data.get("display_data")
        if not display_data:
            Clock.schedule_once(lambda dt: self.root_layout.set_status("Error: JSON missing 'display_data' key."))
            return
            
        threading.Thread(target=self._parse_thread_target, args=(display_data,)).start()

    def _parse_thread_target(self, display_data: list):
        """
        (THREAD) Parses JSON and scans filesystem for artwork.
        """
        root_dir = self.backend.root_directory
        cache_dir = os.path.join(self.app.user_data_dir, 'image_cache')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        try:
            for album_dict in display_data:
                # 1. Reconstruct Tracks
                track_objects = []
                for track_dict in album_dict.get('tracks', []):
                    track_objects.append(GenericTrack(**track_dict))
                
                # 2. Resolve Album URI to Absolute Path
                album_uri = album_dict.get('uri')
                abs_album_path = os.path.normpath(os.path.join(root_dir, album_uri))
                
                # 3. --- NEW: SCAN FOR ARTWORK ---
                # We look for art now, while parsing the data.
                # This updates the 'image_url' which the UI will eventually use.
                found_art_path = self._find_local_art(abs_album_path, cache_dir)
                album_dict['display_image_url'] = found_art_path or KIVY_ICON
                # --------------------------------
                
                album_dict['tracks'] = track_objects
                album_obj = GenericAlbum(**album_dict)
                
                # Update the object with the found art path (or default if empty)
                album_obj.image_url = found_art_path or KIVY_ICON

                # 4. Populate Cache
                self.app.album_data_cache[album_obj.uri] = album_obj
                self.app.ordered_album_uris.append(album_obj.uri)

            Clock.schedule_once(self.app._populate_initial_lists)
            
        except Exception as e:
            Logger.error(f"LocalFilesClientHost: Threaded Parse Failed: {e}", exc_info=True)
            Clock.schedule_once(lambda dt: self.root_layout.set_status(f"Error: {e}"))

    def _find_local_art(self, album_path: str, cache_dir: str) -> str:
        """
        Helper to find album art.
        Priority:
        1. External files (cover.jpg, etc.)
        2. Embedded tags (MP3, FLAC, M4A, OGG)
        """
        if not os.path.isdir(album_path):
            return ""

        # A. Check for External Files
        common_names = ['cover.jpg', 'cover.png', 'folder.jpg', 'album.jpg']
        for filename in os.listdir(album_path):
            if filename.lower() in common_names:
                return os.path.join(album_path, filename)

        # B. Check for Embedded Art
        if not mutagen: return ""

        # Find any supported audio file
        valid_exts = ('.mp3', '.flac', '.m4a', '.ogg', '.wma')
        first_audio = None
        for filename in os.listdir(album_path):
            if filename.lower().endswith(valid_exts):
                first_audio = os.path.join(album_path, filename)
                break
        
        if first_audio:
            # Use a hash of the file path for the cache key
            file_hash = hashlib.md5(first_audio.encode('utf-8')).hexdigest()
            # We guess .jpg initially, but the extractor might change it
            cached_art_path_base = os.path.join(cache_dir, file_hash)
            
            # Check if cached version exists (try common extensions)
            if os.path.exists(cached_art_path_base + ".jpg"): return cached_art_path_base + ".jpg"
            if os.path.exists(cached_art_path_base + ".png"): return cached_art_path_base + ".png"

            # Attempt extraction
            return self._extract_art_to_cache(first_audio, cached_art_path_base)

        return ""
    
    def _extract_art_to_cache(self, filepath, cache_path_base):
        """
        Inspects the file format and extracts binary image data.
        Returns the full path to the saved image, or "" if failed.
        """
        try:
            f = MutagenFile(filepath)
            if not f: return ""
            
            art_data = None
            ext = "jpg" # Default assumption

            # 1. MP3 (ID3)
            if isinstance(f, MP3) or hasattr(f, 'tags') and isinstance(f.tags, ID3):
                if f.tags:
                    # Look for APIC frames
                    for key in f.tags.keys():
                        if key.startswith("APIC"):
                            pic = f.tags[key]
                            art_data = pic.data
                            if 'png' in pic.mime: ext = "png"
                            break

            # 2. FLAC
            elif isinstance(f, FLAC):
                if f.pictures:
                    for p in f.pictures:
                        if p.type == 3: # 3 = Front Cover
                            art_data = p.data
                            if p.mime == "image/png": ext = "png"
                            break

            # 3. M4A (MP4)
            elif isinstance(f, MP4):
                # 'covr' is a list of data atoms
                if 'covr' in f.tags:
                    art_data = f.tags['covr'][0]
                    # M4A doesn't give mime type easily, need to sniff bytes
                    # PNG starts with 89 50 4E 47
                    if art_data.startswith(b'\x89PNG'):
                        ext = "png"

            # 4. OGG (Vorbis)
            elif isinstance(f, OggVorbis):
                # Vorbis stores art as a base64 encoded string in 'metadata_block_picture'
                if 'metadata_block_picture' in f.tags:
                    try:
                        b64_data = f.tags['metadata_block_picture'][0]
                        binary_data = base64.b64decode(b64_data)
                        # This binary block is actually a FLAC Picture structure
                        pic = Picture(binary_data)
                        art_data = pic.data
                        if pic.mime == "image/png": ext = "png"
                    except Exception as e:
                        Logger.warning(f"LocalFiles: OGG art decode failed: {e}")

            # --- SAVE ---
            if art_data:
                final_path = f"{cache_path_base}.{ext}"
                with open(final_path, 'wb') as img_f:
                    img_f.write(art_data)
                return final_path

        except Exception as e:
            Logger.warning(f"LocalFiles: Failed to extract art from {filepath}: {e}")
            
        return ""

    def start_polling(self):
        """Starts polling the audio player for position updates."""
        if not self.poll_event:
            self.poll_event = Clock.schedule_interval(self._update_progress_ui, 0.1)

    def stop_polling(self):
        if self.poll_event:
            self.poll_event.cancel()
            self.poll_event = None

    def _update_progress_ui(self, dt):
        if not self.is_playing: return
        player = self.app.audio_player
        if not player: return
        
        try:
            pos = player.get_position()
            dur = player.get_duration()
            
            # Update the GenericPlaybackInfo widget
            if self.playback_info_widget and dur > 0:
                self.playback_info_widget.progress_value = (pos / dur) * 100
                self.playback_info_widget.current_time = self.root_layout.format_duration(pos * 1000)
                self.playback_info_widget.total_time = self.root_layout.format_duration(dur * 1000)

        except Exception: pass

    def on_stop_click(self):
        self.stop_polling(); self.is_playing = False; self.current_playing_track_uri = None
        self.playback_queue = []; self.queue_index = -1
        if self.playback_info_widget:
            self.playback_info_widget.track_title = "Stopped"; self.playback_info_widget.progress_value = 0

    def on_playback_finished(self):
        Logger.info("LocalFiles: Track finished naturally.")
        
        # 1. Stop polling while we switch
        self.stop_polling()
        
        # 2. Handle completion logic
        if self.current_playing_track_uri:
            track_uri = self.current_playing_track_uri
            track_data = self.app.track_progress.get(track_uri)
            if track_data and not track_data['is_finished']:
                track_data['is_finished'] = True
                location_id = track_data.get('location_id')
                if location_id and self.app.ap_client:
                    self.app.ap_client.send_location_check(location_id)
                self.root_layout.update_track_ui(track_uri)
                self.app.show_toast(f"Finished: {self.current_playing_track_title}")

        # 3. Advance Queue
        next_index = self.queue_index + 1
        if 0 <= next_index < len(self.playback_queue):
            Logger.info(f"LocalFiles: Advancing queue to index {next_index}")
            self.queue_index = next_index
            next_track = self.playback_queue[next_index]
            self._play_track_internal(next_track)
        else:
            Logger.info("LocalFiles: Queue finished.")
            self.is_playing = False
            self.current_playing_track_uri = None
            if self.playback_info_widget:
                self.playback_info_widget.track_title = "Finished"
                self.playback_info_widget.progress_value = 100
                self.playback_info_widget.current_time = "00:00"
            self.playback_queue = []
            self.queue_index = -1

    def _play_track_internal(self, track_obj: GenericTrack):
        self.stop_polling()
        uri = track_obj.uri; title = track_obj.title
        
        # Check ownership
        prog = self.app.track_progress.get(uri)
        parent = prog.get('parent_uri') if prog else None
        if (not parent or parent not in self.app.owned_albums) and not self.app.cheat_mode:
            self.app.show_toast(f"Skipping unowned: {title}")
            Clock.schedule_once(lambda dt: self.on_playback_finished(), 0.1); return
            
        abs_path = os.path.normpath(os.path.join(self.backend.root_directory, uri))
        if not os.path.exists(abs_path):
            self.app.show_toast(f"File not found: {title}")
            Clock.schedule_once(lambda dt: self.on_playback_finished(), 0.1); return

        # Update UI
        if self.playback_info_widget:
            self.playback_info_widget.track_title = title
            self.playback_info_widget.artist_album = f"{track_obj.artist} - {track_obj.album_title}"
            # For local files, we need to find the album art again or pass it down. 
            # For now, let's try to grab it from the parent album in cache
            parent_album = self.app.album_data_cache.get(parent)
            if parent_album:
                self.playback_info_widget.art_source = parent_album.image_url
            else:
                self.playback_info_widget.art_source = KIVY_ICON

            self.playback_info_widget.progress_value = 0
            self.playback_info_widget.current_time = "00:00"

        # Play
        self.app.audio_player.play(abs_path)
        self.current_playing_track_uri = uri
        self.current_playing_track_title = title
        self.is_playing = True
        Clock.schedule_once(lambda dt: self.start_polling(), 0.5)

    def _play_track(self, track_uri: str, track_title: str):
        self.stop_polling()
        track_obj = GenericTrack(uri=track_uri, title=track_title, artist="", album_title="", duration_ms=0, service="local")
        self.playback_queue = [track_obj]
        self.queue_index = 0
        self._play_track_internal(track_obj)

    def _play_album(self, album_uri):
        """
        Loads all tracks from the album into the queue and starts playback.
        """
        # 1. Check ownership
        if album_uri not in self.app.owned_albums and not self.app.cheat_mode:
            self.app.show_toast("You do not own this album yet.")
            return

        # 2. Get Album Data from Cache
        album = self.app.album_data_cache.get(album_uri)
        if not album or not album.tracks:
            self.app.show_toast("Error: Album has no tracks.")
            return
            
        # 3. Populate Queue
        self.playback_queue = list(album.tracks) # Create a copy
        self.queue_index = 0
        
        Logger.info(f"LocalFiles: Queued {len(self.playback_queue)} tracks for album {album.title}")
        self.app.show_toast(f"Playing Album: {album.title}")
        
        # 4. Play First Track
        if self.playback_queue:
            self._play_track_internal(self.playback_queue[0])

    def on_play_pause_click(self):
        if self.is_playing:
            self.app.audio_player.pause()
            self.is_playing = False
            self.app.show_toast("Paused")
        else:
            if self.current_playing_track_uri:
                self.app.audio_player.resume()
                self.is_playing = True
                self.app.show_toast("Resumed")
            else:
                self.app.show_toast("Select a track to play.")

    def on_volume_change(self, new_volume: int):
        # Slider is 0-100, player wants 0.0-1.0
        vol_float = new_volume / 100.0
        self.app.audio_player.set_volume(vol_float)
        self.root_layout.set_status(f"Volume: {new_volume}%")

    def on_mute_toggle(self, is_muted: bool):
        # We don't need this, the RootLayout handles volume based on the slider state
        pass
        
    def on_device_select(self, device_name: str):
        pass # Not supported

    def get_settings_ui(self) -> BoxLayout | None:
        """Local files require no extra settings."""
        return None

    def on_list_item_click(self, list_item):
        """Handles clicks on albums or tracks."""
        if list_item.raw_item_type == 'album':
            if not list_item.is_owned and not self.app.cheat_mode:
                self.app.show_toast("You do not own this album yet.")
                return
            self.root_layout.set_status(f"Loading tracks for: {list_item.raw_title}")
            self.root_layout.populate_track_list(list_item.raw_uri)
        
        elif list_item.raw_item_type == 'track':
            self._play_track(list_item.raw_uri, list_item.raw_title)

    def on_menu_action(self, option_text, list_item):
        """
        Handles both opening the menu and processing the result.
        """
        if option_text == "OPEN_MENU":
            # --- This is the menu-building logic ---
            menu = ItemMenu(caller=list_item, auto_width=False, width=dp(200))
            menu.clear_widgets()
            app = self.app
            cheat_color = "ff8888"
            button_added = False

            if list_item.raw_item_type == 'album' or list_item.raw_item_type == 'playlist':
                btn = Button(text="Play", size_hint_y=None, height=dp(44))
                btn.bind(on_release=lambda x: menu.on_option_select("Play Album"))
                menu.add_widget(btn)

                btn_hint = Button(text="Hint", size_hint_y=None, height=dp(44))
                btn_hint.bind(on_release=lambda x: menu.on_option_select("Hint"))
                menu.add_widget(btn_hint)
                button_added = True
            
            elif list_item.raw_item_type == 'track':
                btn = Button(text="Play Track", size_hint_y=None, height=dp(44))
                btn.bind(on_release=lambda x: menu.on_option_select("Play Track"))
                menu.add_widget(btn)
                button_added = True

                if app.cheat_mode:
                    cheat_text = f"[color={cheat_color}]Send Location[/color]"
                    btn_send_loc = Button(text=cheat_text, markup=True, size_hint_y=None, height=dp(44))
                    btn_send_loc.bind(on_release=lambda x: menu.on_option_select("Send Location"))
                    menu.add_widget(btn_send_loc)
                    button_added = True

            if not button_added:
                btn = Button(text="No actions", size_hint_y=None, height=dp(44))
                btn.bind(on_release=lambda x: menu.on_option_select("No actions"))
                menu.add_widget(btn)
            
            menu.open(list_item.ids.menu_button) # Open relative to the button
            return

        # --- This is the action-handling logic ---
        elif option_text == "Play Album":
            self._play_album(list_item.raw_uri)
        
        elif option_text == "Play Track":
            self._play_track(list_item.raw_uri, list_item.raw_title)
        
        elif option_text == "Hint":
            apworld_name = list_item.text_line_4
            if self.app.ap_client and apworld_name:
                self.app.ap_client.send_chat_message(f"!hint {apworld_name}")
                self.app.show_toast(f"Hinting for: {apworld_name}")
        
        elif option_text == "Send Location":
            track_uri = list_item.raw_uri
            location_id = self.app.track_progress.get(track_uri, {}).get('location_id')
            if location_id and self.app.ap_client:
                self.app.ap_client.send_location_check(location_id)
                self.app.show_toast(f"CHEAT: Sent check for {list_item.raw_title}")
                if track_uri in self.app.track_progress:
                    self.app.track_progress[track_uri]['is_finished'] = True
                self.root_layout.update_track_ui(track_uri)


# -------------------------------------------------------------------
# 4. PLUGIN MANIFEST
# -------------------------------------------------------------------
MUSIPELAGO_PLUGIN = {
    "name": "Local Files",
    "generator_backend": LocalFilesBackendLogic,
    "generator_ui": LocalFilesHostUI,
    "client_backend": LocalFilesBackendLogic,
    "client_ui": LocalFilesClientHost
}