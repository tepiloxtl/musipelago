# -*- coding: utf-8 -*-
import os
import sys
import ctypes
import platform
from kivy.logger import Logger
from kivy.clock import Clock

# --- VLC BOOTSTRAP LOGIC ---

# 1. Determine where the "Bundled" engine WOULD be if it exists
if getattr(sys, 'frozen', False):
    # PyInstaller: Root of the temp bundle
    BASE_DIR = sys._MEIPASS
else:
    # Source: Relative to this script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VLC_ENGINE_PATH = os.path.join(BASE_DIR, 'vlc_engine')
print(VLC_ENGINE_PATH)
VLC_AVAILABLE = False

# 2. Check if we have a bundled engine
if os.path.exists(VLC_ENGINE_PATH):
    Logger.info("AudioPlayer: Found bundled VLC engine.")
    os.environ['VLC_PLUGIN_PATH'] = os.path.join(VLC_ENGINE_PATH, 'plugins')

    # Windows needs to believe we are IN the folder to resolve dependencies.
    original_cwd = os.getcwd()
    try:
        Logger.info(f"AudioPlayer: Context switch to {VLC_ENGINE_PATH}")
        os.chdir(VLC_ENGINE_PATH)
        
        if platform.system() == 'Windows':
            # Load Core first, then Main
            ctypes.CDLL(r".\libvlccore.dll")
            ctypes.CDLL(r".\libvlc.dll")
            
        import vlc
        VLC_AVAILABLE = True
        Logger.info("AudioPlayer: Bundled VLC loaded.")
        
    except Exception as e:
        Logger.error(f"AudioPlayer: Failed to load bundled VLC: {e}")
    finally:
        # CRITICAL: Restore original directory immediately
        os.chdir(original_cwd)

# 3. Fallback: Try System VLC
if not VLC_AVAILABLE:
    Logger.info("AudioPlayer: Bundled engine missing. Attempting System VLC.")
    try:
        # Standard import will look in Registry (Windows), /Applications (Mac), /usr/lib (Linux)
        import vlc
        VLC_AVAILABLE = True
    except ImportError:
        Logger.critical("AudioPlayer: CRITICAL - VLC not found. Please install VLC Media Player.")

class GenericAudioPlayer:
    def __init__(self, on_finish_callback=None):
        self.on_finish_callback = on_finish_callback
        self.player = None
        self.instance = None
        self.current_volume = 50 

        if not VLC_AVAILABLE:
            Logger.critical("AudioPlayer: No Audio Backend found.")
            return

        try:
            # Initialize Instance
            # --no-video: Save resources
            # --quiet: Suppress logs
            # --reset-plugins-cache: Safe for bundled, might slow down system vlc slightly but safer
            args = ['--no-video', '--quiet']
            
            # Linux specific fix: X11 threading issues
            if platform.system() == 'Linux':
                args.append('--no-xlib')

            self.instance = vlc.Instance(*args)
            self.player = self.instance.media_player_new()
            
            self.events = self.player.event_manager()
            self.events.event_attach(
                vlc.EventType.MediaPlayerEndReached, 
                self._on_end_reached
            )
            
            self.player.audio_set_volume(self.current_volume)
            Logger.info("AudioPlayer: VLC Initialized.")
            
        except Exception as e:
            Logger.error(f"AudioPlayer: Init Error: {e}")

    def play(self, source):
        if not self.instance or not self.player:
            return

        # 1. Check if source is a URL (Network Stream)
        # VLC handles http, https, rtsp, etc.
        is_url = source.startswith(('http://', 'https://', 'rtsp://', 'ftp://'))

        # 2. If it is NOT a URL, we treat it as a local file
        if not is_url:
            if not os.path.exists(source):
                Logger.error(f"AudioPlayer: Local file not found: {source}")
                return
            # Only use abspath for local files
            source = os.path.abspath(source)

        try:
            # 3. Create Media
            # VLC accepts both file paths and URLs directly in media_new
            media = self.instance.media_new(source)
            
            # 4. Assign and Play
            self.player.set_media(media)
            self.player.play()
            
            # Enforce Volume
            self.player.audio_set_volume(self.current_volume)
            
            # Log neatly (mask long URLs for cleaner logs)
            log_name = source if not is_url else "Network Stream"
            Logger.info(f"AudioPlayer: Playing {log_name}")
            
        except Exception as e:
            Logger.error(f"AudioPlayer: Playback failed: {e}")

    def stop(self):
        if self.player:
            self.player.stop()

    def pause(self):
        if self.player:
            self.player.set_pause(1)

    def resume(self):
        if self.player:
            self.player.set_pause(0)

    def set_volume(self, volume_0_to_1):
        if not self.player:
            return
        vol_float = max(0.0, min(1.0, volume_0_to_1))
        self.current_volume = int(vol_float * 100)
        try:
            self.player.audio_set_volume(self.current_volume)
        except Exception:
            pass

    def get_position(self):
        """
        Returns the current playback time in SECONDS.
        Matches the behavior of ffpyplayer's get_pts().
        """
        if self.player:
            # get_time() returns milliseconds. Convert to seconds.
            ms = self.player.get_time()
            if ms > 0:
                return ms / 1000.0
        return 0.0

    def get_duration(self):
        """
        Returns the total track duration in SECONDS.
        """
        if self.player:
            # get_length() returns milliseconds.
            ms = self.player.get_length()
            
            # VLC quirk: returns 0 until the stream is actually parsed.
            # We return 0.0 and let the UI handle the "unknown" state momentarily.
            if ms > 0:
                return ms / 1000.0
        return 0.0

    def get_relative_progress(self):
        """
        New helper: Returns 0.0 to 1.0 (Percentage).
        Useful if your slider relies on normalized values.
        """
        if self.player:
            return max(0.0, self.player.get_position())
        return 0.0

    def _on_end_reached(self, event):
        if self.on_finish_callback:
            Clock.schedule_once(lambda dt: self.on_finish_callback(), 0)