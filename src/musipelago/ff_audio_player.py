# -*- coding: utf-8 -*-
from kivy.logger import Logger
from kivy.clock import Clock
from ffpyplayer.player import MediaPlayer
import os

class GenericAudioPlayer:
    def __init__(self, on_finish_callback=None):
        self.player = None
        self.on_finish_callback = on_finish_callback
        self.volume = 0.5
        self._update_event = None
        self._pending_load_event = None
        self._zombies = [] 

    def play(self, source):
        """
        Stops current playback and starts new.
        """
        # 1. Move current player to graveyard
        self.stop()

        if os.path.exists(source):
            source = os.path.abspath(source)
            
        # 2. Schedule Load
        # Keep the 0.2s delay to ensure the previous player is fully dead.
        Logger.info(f"AudioPlayer: Scheduling load for {os.path.basename(source)}")
        self._pending_load_event = Clock.schedule_once(
            lambda dt: self._start_player(source), 
            0.2
        )

    def _start_player(self, source):
        self._pending_load_event = None
        
        try:
            # A. Initialize PAUSED
            ff_opts = {
                'paused': True, 
                'vn': True, 
                'auto_exit': False 
            }
            
            self.player = MediaPlayer(source, ff_opts=ff_opts)
            
            # B. Mute IMMEDIATELY
            # This fills the initial buffer with silence.
            self.player.set_volume(0.0)
            
            # --- CHANGE: DO NOT START LOOP YET ---
            # We don't want to consume frames while waiting.
            
            # C. Schedule Unpause
            # We stick to the safe 0.2s delay to prevent volume blasts.
            Clock.schedule_once(self._deferred_unpause, 0.2)
            
            Logger.info(f"AudioPlayer: Loaded {os.path.basename(source)}")

        except Exception as e:
            Logger.error(f"AudioPlayer: FFPyPlayer failed to load {source}: {e}")
            self.stop()

    def _deferred_unpause(self, dt):
        """
        Called 0.2s after init. Safe to start playing now.
        """
        if self.player:
            # 1. Restore Volume
            try:
                self.player.set_volume(self.volume)
            except: pass
            
            # 2. Seek to 0 (Fixes the "Skip" issue)
            # Ensures we start exactly at the beginning even if buffering advanced slightly.
            self.player.seek(0.0)
            
            # 3. Unpause
            self.player.set_pause(False)
            
            # 4. Start the Update Loop NOW
            # This ensures we process frames only when we are ready to hear them.
            self._update_event = Clock.schedule_interval(self._update, 0.05)
            
            # Redundant Volume Enforcement
            Clock.schedule_once(lambda d: self.set_volume(self.volume), 0.1)

    def stop(self):
        """
        Moves the current player to the zombie list.
        """
        if self._update_event:
            self._update_event.cancel()
            self._update_event = None
            
        if self._pending_load_event:
            self._pending_load_event.cancel()
            self._pending_load_event = None
        
        if self.player:
            try:
                self.player.set_volume(0.0)
                self.player.set_pause(True)
            except Exception:
                pass 
            
            self._zombies.append(self.player)
            self.player = None
            
            Clock.schedule_once(self._clean_graveyard, 2.0)

    def _clean_graveyard(self, dt):
        if not self._zombies: return
        zombie = self._zombies.pop(0)
        try:
            zombie.close_player()
        except Exception as e:
            Logger.error(f"AudioPlayer: Failed to close zombie: {e}")

    def set_volume(self, volume_0_to_1):
        self.volume = max(0.0, min(1.0, volume_0_to_1))
        if self.player:
            try: self.player.set_volume(self.volume)
            except: pass

    def pause(self):
        if self.player: self.player.set_pause(True)

    def resume(self):
        if self.player: self.player.set_pause(False)

    def get_position(self):
        return self.player.get_pts() if self.player else 0

    def get_duration(self):
        return self.player.get_metadata().get('duration', 0) if self.player else 0

    def _update(self, dt):
        if not self.player: return

        try:
            frame, val = self.player.get_frame()
            
            pts = self.player.get_pts()
            if pts is None: pts = 0.0
            
            duration = 0.0
            meta = self.player.get_metadata()
            if meta:
                d = meta.get('duration')
                if d is not None: duration = d
            
            if duration > 0 and pts >= (duration - 0.2):
                Logger.info("AudioPlayer: Track finished (Time Check).")
                self.stop()
                if self.on_finish_callback:
                    Clock.schedule_once(lambda dt: self.on_finish_callback(), 0)
                return

            if val == 'eof':
                Logger.info("AudioPlayer: Track finished (EOF).")
                self.stop()
                if self.on_finish_callback:
                    Clock.schedule_once(lambda dt: self.on_finish_callback(), 0)
            elif val == 'error':
                Logger.error("AudioPlayer: Internal playback error.")
                self.stop()
                
        except Exception as e:
            Logger.error(f"AudioPlayer: Update loop error: {e}")
            self.stop()