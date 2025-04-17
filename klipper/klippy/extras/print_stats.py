# ~/klipper/klippy/extras/print_stats.py
# Virtual SDCard print stat tracking
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging

class PrintStats:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.print_start_time = None
        self.print_duration = 0.
        self.filament_used = 0.
        self.filename = ""
        self.state = "standby"
        self.message = ""
        self.file_position = 0
        self.file_size = 0
        self.total_duration = 0.
        self.total_layer = 0  # Total number of layers
        self.current_layer = 0  # Current layer being printed
        # Add info field for Mainsail compatibility
        self.info = {
            "total_layer": 0,
            "current_layer": 0
        }
        self.last_total_duration = 0.
        self.last_print_duration = 0.
        self.last_filament_used = 0.
        self.last_file_position = 0
        self.last_file_size = 0
        self.last_total_layer = 0
        self.last_current_layer = 0
        self.last_state = ""
        self.last_message = ""
        self.last_stats_event = 0.
        self.stats_timer = self.reactor.register_timer(self._update_stats)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._handle_disconnect)
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command("SET_PRINT_STATS_INFO",
                                    self.cmd_SET_PRINT_STATS_INFO,
                                    desc=self.cmd_SET_PRINT_STATS_INFO_help)

    def _handle_ready(self):
        self.reactor.update_timer(self.stats_timer, self.reactor.NOW)

    def _handle_shutdown(self):
        self.reset()

    def _handle_disconnect(self):
        self.reset()

    def _update_stats(self, eventtime):
        # send print_stats update
        update = False
        if self.total_duration != self.last_total_duration:
            self.last_total_duration = self.total_duration
            update = True
            logging.debug(f"Total duration changed: {self.total_duration}")
        if self.print_duration != self.last_print_duration:
            self.last_print_duration = self.print_duration
            update = True
            logging.debug(f"Print duration changed: {self.print_duration}")
        if self.filament_used != self.last_filament_used:
            self.last_filament_used = self.filament_used
            update = True
            logging.debug(f"Filament used changed: {self.filament_used}")
        if self.file_position != self.last_file_position:
            self.last_file_position = self.file_position
            update = True
            logging.debug(f"File position changed: {self.file_position}")
        if self.file_size != self.last_file_size:
            self.last_file_size = self.file_size
            update = True
            logging.debug(f"File size changed: {self.file_size}")
        if self.state != self.last_state:
            self.last_state = self.state
            update = True
            logging.debug(f"State changed: {self.state}")
        if self.message != self.last_message:
            self.last_message = self.message
            update = True
            logging.debug(f"Message changed: {self.message}")
        if self.total_layer != self.last_total_layer:
            self.last_total_layer = self.total_layer
            # Also update the info field for Mainsail compatibility
            self.info['total_layer'] = self.total_layer
            update = True
            logging.debug(f"Total layer changed: {self.total_layer}")
        if self.current_layer != self.last_current_layer:
            self.last_current_layer = self.current_layer
            # Also update the info field for Mainsail compatibility
            self.info['current_layer'] = self.current_layer
            update = True
            logging.debug(f"Current layer changed: {self.current_layer}")
        if update:
            self.last_stats_event = eventtime
            logging.info(f"Sending notify_status_update: {self.get_status(eventtime)}")
            self.printer.send_event("print_stats:stats_changed", eventtime)
        else:
            logging.debug("No stats update needed")
        return eventtime + 1.

    def set_current_file(self, filename):
        logging.info(f"Setting print job name to: '{filename}' (previous value: '{self.filename}')")
        if not filename and self.filename:
            logging.warning(f"Attempting to reset filename from '{self.filename}' to empty string; preserving existing value")
            return
        self.filename = filename

    def get_status(self, eventtime):
        # Defensive: ensure singular/plural layer fields are always present
        info = dict(self.info) if hasattr(self, 'info') else {}
        # Singular/plural for total_layer
        total_layer = getattr(self, 'total_layer', 0) or info.get('total_layer', 0)
        info['total_layer'] = total_layer
        info['total_layers'] = total_layer
        # Singular/plural for current_layer
        current_layer = getattr(self, 'current_layer', 0) or info.get('current_layer', 0)
        info['current_layer'] = current_layer
        info['current_layers'] = current_layer
        return {
            'filename': self.filename,
            'total_duration': self.total_duration,
            'print_duration': self.print_duration,
            'filament_used': self.filament_used,
            'state': self.state,
            'message': self.message,
            'info': info,  # Add info field for Mainsail compatibility
            'file_position': self.file_position,
            'file_size': self.file_size,
            'total_layer': total_layer,
            'total_layers': total_layer,
            'current_layer': current_layer,
            'current_layers': current_layer
        }

    def reset(self):
        logging.info(f"Resetting print_stats, filename was: {self.filename}")
        self.print_start_time = None
        self.total_duration = self.print_duration = self.filament_used = 0.
        self.file_position = self.file_size = 0
        self.state = "standby"
        self.message = ""
        # Preserve the filename to prevent it from being reset
        # self.filename = ""

    def note_start(self):
        if self.state in ["printing", "paused"]:
            return
        logging.info(f"Starting print, filename is: {self.filename}")
        self.state = "printing"
        self.message = ""
        if self.print_start_time is None:
            self.print_start_time = self.reactor.monotonic()

    def note_pause(self):
        if self.state != "printing":
            return
        logging.info(f"Pausing print, filename is: {self.filename}")
        self.state = "paused"
        self.message = ""

    def note_complete(self):
        if self.state not in ["printing", "paused"]:
            return
        logging.info(f"Completing print, filename is: {self.filename}")
        self.state = "complete"
        self.message = ""

    def note_error(self, message):
        if self.state in ["standby", "complete", "error"]:
            return
        logging.info(f"Print error, filename is: {self.filename}")
        self.state = "error"
        self.message = message

    def note_cancel(self):
        if self.state in ["standby", "complete", "error"]:
            return
        logging.info(f"Cancelling print, filename is: {self.filename}")
        self.state = "cancelled"
        self.message = ""

    def set_position(self, position, total_size=None):
        """Set the file position and optionally the total file size, triggering a notification."""
        self.file_position = position
        if total_size is not None:
            self.file_size = total_size
        # Trigger a notification by updating the stats
        self._update_stats(self.reactor.monotonic())

    cmd_SET_PRINT_STATS_INFO_help = "Set print stats info"
    def cmd_SET_PRINT_STATS_INFO(self, gcmd):
        """Set the current layer and total layer count"""
        self.current_layer = gcmd.get_int('CURRENT_LAYER', self.current_layer)
        self.total_layer = gcmd.get_int('TOTAL_LAYER', self.total_layer)
        # Also update the info field for Mainsail compatibility
        self.info['current_layer'] = self.current_layer
        self.info['total_layer'] = self.total_layer
        total_time = gcmd.get_float('TOTAL_TIME', None)
        if total_time is not None:
            self.total_duration = total_time
            logging.debug(f"Updated total_duration to {total_time}")
        print_time = gcmd.get_float('PRINT_TIME', None)
        if print_time is not None:
            self.print_duration = print_time
            logging.debug(f"Updated print_duration to {print_time}")
        filament_used = gcmd.get_float('FILAMENT_USED', None)
        if filament_used is not None:
            self.filament_used = filament_used
            logging.debug(f"Updated filament_used to {filament_used}")
        file_size = gcmd.get_int('TOTAL_SIZE', None)
        if file_size is not None:
            self.file_size = file_size
            logging.debug(f"Updated file_size to {file_size}")
        printing = gcmd.get_int('PRINTING', None)
        if printing is not None:
            if printing == 1 and self.state in ["standby", "complete", "error", "cancelled"]:
                self.note_start()
            elif printing == 0 and self.state in ["printing", "paused"]:
                self.note_complete()
        self._update_stats(self.reactor.monotonic())

def load_config(config):
    return PrintStats(config)