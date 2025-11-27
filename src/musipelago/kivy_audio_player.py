# -*- coding: utf-8 -*-
import os
from kivy.logger import Logger
from kivy.core.audio import SoundLoader
from kivy.clock import Clock

class GenericAudioPlayer:
    def __init__(self, on_finish_callback=None):
        self.player = None
        self.on_finish_callback = on_finish_callback
        self.volume = 0.5
        self._paused_position = 0
        
    def play(self, source):
        """
        Stops current playback and starts new.
        Uses 'Zombie Cleanup' to prevent SDL2 threading crashes.
        """
        # 1. Detach the current player immediately
        if self.player:
            self._detach_and_schedule_cleanup(self.player)
            self.player = None

        self._paused_position = 0
        
        # 2. Prepare path
        if os.path.exists(source):
            source = os.path.abspath(source)
            
        # 3. Load New Player
        try:
            self.player = SoundLoader.load(source)
            
            if self.player:
                self.player.volume = self.volume
                self.player.bind(on_stop=self._on_kivy_stop)
                self.player.play()
                
                # Re-apply volume for safety
                self.player.volume = self.volume
                Clock.schedule_once(self._enforce_volume, 0.1)
                Clock.schedule_once(self._enforce_volume, 0.2)
                
                Logger.info(f"AudioPlayer: Now Playing {os.path.basename(source)}")
            else:
                Logger.error(f"AudioPlayer: SoundLoader failed to load {source}")

        except Exception as e:
            Logger.error(f"AudioPlayer: Critical Exception loading {source}: {e}")

    def stop(self):
        """
        Manually stop playback.
        """
        if self.player:
            self._detach_and_schedule_cleanup(self.player)
            self.player = None
        self._paused_position = 0

    def _detach_and_schedule_cleanup(self, old_player_instance):
        """
        Stops the player and schedules its unload for later.
        This prevents 'double-free' crashes if SDL2 is busy.
        """
        try:
            # 1. Stop events
            old_player_instance.unbind(on_stop=self._on_kivy_stop)
            
            # 2. Stop audio
            if old_player_instance.state == 'play':
                old_player_instance.stop()
            
            # 3. Schedule UNLOAD for 1 second later.
            # We pass the instance to the lambda to keep a reference to it,
            # preventing Garbage Collection until we are ready.
            Clock.schedule_once(lambda dt: self._unsafe_unload(old_player_instance), 1.0)
            
        except Exception as e:
            Logger.error(f"AudioPlayer: Error detaching player: {e}")

    def _unsafe_unload(self, zombie_player):
        """
        Called 1 second after stop. Safe to unload now.
        """
        try:
            zombie_player.unload()
            # Logger.debug("AudioPlayer: Old player unloaded successfully.")
        except Exception:
            pass

    def _on_kivy_stop(self, sound_obj):
        """
        Handler for when the sound stops naturally.
        """
        # Check if we actually reached the end
        if sound_obj.length > 0 and sound_obj.get_pos() >= sound_obj.length - 0.5:
             Logger.info("AudioPlayer: Track finished naturally.")
             
             # Unbind to prevent recursion
             try: sound_obj.unbind(on_stop=self._on_kivy_stop)
             except: pass
             
             # We do NOT stop/unload here. We let the next play() call handle it.
             
             if self.on_finish_callback:
                 Clock.schedule_once(lambda dt: self.on_finish_callback(), 0.1)

    def pause(self):
        if self.player and self.player.state == 'play':
            self._paused_position = self.player.get_pos()
            self.player.stop()

    def resume(self):
        if self.player:
            self.player.play()
            Clock.schedule_once(self._enforce_volume, 0.1)
            Clock.schedule_once(self._enforce_volume, 0.2)
            if self._paused_position > 0:
                Clock.schedule_once(self._deferred_seek, 0.05)

    def _deferred_seek(self, dt):
        if self.player:
            try: self.player.seek(self._paused_position)
            except: pass
    
    def _enforce_volume(self, dt):
        if self.player:
            try: self.player.volume = self.volume
            except: pass

    def set_volume(self, volume_0_to_1):
        self.volume = max(0.0, min(1.0, volume_0_to_1))
        if self.player: 
            try: self.player.volume = self.volume
            except: pass

    def get_position(self):
        if self.player:
            if self.player.state == 'stop':
                return self._paused_position
            try: return self.player.get_pos()
            except: return 0
        return 0

    def get_duration(self):
        if self.player:
            try: return self.player.length
            except: return 0
        return 0
        
    def update(self):
        pass