# Virtual SDCard print stat tracking
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io
import queue

VALID_GCODE_EXTS = ["gcode", "g", "gco"]

DEFAULT_ERROR_GCODE = """
{% if 'heaters' in printer %}
   TURN_OFF_HEATERS
{% endif %}
"""

class VirtualSDGCodeProvider:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        # sdcard state
        sd = config.get("path")
        self.with_subdirs = config.getboolean("with_subdirs", False)
        self.sdcard_dirname = os.path.normpath(os.path.expanduser(sd))
        self.filename = ""
        self.current_file = None
        self.file_position = self.file_size = 0
        self.next_file_position = 0
        # Streaming state
        self.is_streaming = False
        self.stream_position = 0
        self.stream_size = 0  # Total size of the G-code for streaming
        # Register commands
        self.gcode = self.printer.lookup_object("gcode")
        for cmd in ["M20", "M21", "M26", "M27"]:
            self.gcode.register_command(cmd, getattr(self, "cmd_" + cmd))
        for cmd in ["M28", "M29", "M30"]:
            self.gcode.register_command(cmd, self.cmd_error)

    def set_filename(self, filename):
        """Set the filename for this provider."""
        self.filename = filename
        logging.info(f"Set VirtualSDGCodeProvider filename to: {self.filename}")

    # Generic methods of GCode provider
    def handle_shutdown(self):
        if self.current_file is not None:
            try:
                readpos = max(self.file_position - 1024, 0)
                readcount = self.file_position - readpos
                self.current_file.seek(readpos)
                data = self.current_file.read(readcount + 128)
            except:
                logging.exception("virtual_sdcard shutdown read")
                return
            logging.info(
                "Virtual sdcard (%d): %s\nUpcoming (%d): %s",
                readpos,
                repr(data[:readcount]),
                self.file_position,
                repr(data[readcount:]),
            )

    def get_stats(self, eventtime):
        return True, "sd_pos=%d" % (self.file_position if not self.is_streaming else self.stream_position,)

    def get_status(self, eventtime):
        return {
            "file_path": self.file_path(),
            "progress": self.progress(),
            "file_position": self.file_position if not self.is_streaming else self.stream_position,
            "file_size": self.file_size if not self.is_streaming else self.stream_size,
        }

    def is_active(self):
        return self.current_file is not None or self.is_streaming

    def get_name(self):
        return self.filename

    def reset(self):
        if self.current_file is not None:
            self.current_file.close()
            self.current_file = None
            self.filename = ""
        self.file_position = self.file_size = 0
        self.is_streaming = False
        self.stream_position = 0
        self.stream_size = 0

    def get_gcode(self):
        if self.is_streaming:
            # Streaming mode: return an empty iterator since we'll handle G-code via the queue
            return
        logging.info("Starting SD card print (position %d)", self.file_position)
        try:
            self.current_file.seek(self.file_position)
        except:
            logging.exception("virtual_sdcard seek")
            return
        partial_input = ""
        lines = []
        while True:
            if not lines:
                # Read more data
                try:
                    data = self.current_file.read(8192)
                except:
                    logging.exception("virtual_sdcard read")
                    break
                if not data:
                    # End of file
                    self.current_file.close()
                    self.current_file = None
                    logging.info("Finished SD card print")
                    self.gcode.respond_raw("Done printing file")
                    break
                lines = data.split("\n")
                lines[0] = partial_input + lines[0]
                partial_input = lines.pop()
                lines.reverse()
                self.reactor.pause(self.reactor.NOW)
                continue
            line = lines.pop()
            if sys.version_info.major >= 3:
                next_file_position = self.file_position + len(line.encode()) + 1
            else:
                next_file_position = self.file_position + len(line) + 1
            self.next_file_position = next_file_position
            yield line
            self.file_position = self.next_file_position
            # Do we need to skip around?
            if self.next_file_position != next_file_position:
                try:
                    self.current_file.seek(self.file_position)
                except:
                    logging.exception("virtual_sdcard seek")
                    return
                lines = []
                partial_input = ""
        logging.info("Exiting SD card print (position %d)", self.file_position)

    # Virtual SD Card file management
    def get_file_list(self, check_subdirs=False):
        if check_subdirs:
            flist = []
            for root, dirs, files in os.walk(
                self.sdcard_dirname, followlinks=True
            ):
                for name in files:
                    ext = name[name.rfind(".") + 1 :]
                    if ext not in VALID_GCODE_EXTS:
                        continue
                    full_path = os.path.join(root, name)
                    r_path = full_path[len(self.sdcard_dirname) + 1 :]
                    size = os.path.getsize(full_path)
                    flist.append((r_path, size))
            return sorted(flist, key=lambda f: f[0].lower())
        else:
            dname = self.sdcard_dirname
            try:
                filenames = os.listdir(self.sdcard_dirname)
                return [
                    (fname, os.path.getsize(os.path.join(dname, fname)))
                    for fname in sorted(filenames, key=str.lower)
                    if not fname.startswith(".")
                    and os.path.isfile((os.path.join(dname, fname)))
                ]
            except:
                logging.exception("virtual_sdcard get_file_list")
                raise self.gcode.error("Unable to get file list")

    def file_path(self):
        if self.current_file:
            return self.current_file.name
        return None

    def progress(self):
        if self.file_size and not self.is_streaming:
            return float(self.file_position) / self.file_size
        elif self.stream_size and self.is_streaming:
            return float(self.stream_position) / self.stream_size
        else:
            return 0.0

    def load_file(self, gcmd, filename, check_subdirs=False):
        files = self.get_file_list(check_subdirs)
        flist = [f[0] for f in files]
        files_by_lower = {fname.lower(): fname for fname, fsize in files}
        fname = filename
        try:
            if fname not in flist:
                fname = files_by_lower[fname.lower()]
            fname = os.path.join(self.sdcard_dirname, fname)
            f = io.open(fname, "r", newline="")
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            f.seek(0)
        except:
            logging.exception("virtual_sdcard file open")
            raise gcmd.error("Unable to open file")
        gcmd.respond_raw("File opened: %s Size: %d" % (filename, fsize))
        gcmd.respond_raw("File selected")
        self.current_file = f
        self.file_position = 0
        self.file_size = fsize
        self.filename = filename
        self.printer.send_event("virtual_sdcard:load_file")

    def get_file_position(self):
        return self.next_file_position

    def set_file_position(self, pos):
        self.next_file_position = pos

    # G-Code commands
    def cmd_error(self, gcmd):
        raise gcmd.error("SD write not supported")

    def cmd_M20(self, gcmd):
        # List SD card
        files = self.get_file_list()
        gcmd.respond_raw("Begin file list")
        for fname, fsize in files:
            gcmd.respond_raw("%s %d" % (fname, fsize))
        gcmd.respond_raw("End file list")

    def cmd_M21(self, gcmd):
        # Initialize SD card
        gcmd.respond_raw("SD card ok")

    def cmd_M26(self, gcmd):
        # Set SD position
        if not self.is_active():
            gcmd.respond_raw("Not printing from SD card.")
        pos = gcmd.get_int("S", minval=0)
        self.set_file_position(pos)

    def cmd_M27(self, gcmd):
        # Report SD print status
        if not self.is_active():
            gcmd.respond_raw("Not printing from SD card.")
            return
        if self.is_streaming:
            gcmd.respond_raw("SD streaming byte %d" % (self.stream_position,))
        else:
            gcmd.respond_raw(
                "SD printing byte %d/%d" % (self.file_position, self.file_size)
            )


class VirtualSD:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler(
            "klippy:shutdown", self.handle_shutdown
        )
        # Print Stat Tracking
        self.print_stats = self.printer.load_object(config, 'print_stats')
        # sdcard state
        self.virtualsd_gcode_provider = VirtualSDGCodeProvider(config)
        self.sdcard_dirname = self.virtualsd_gcode_provider.sdcard_dirname
        self.gcode_provider = None
        # Work timer
        self.reactor = self.printer.get_reactor()
        self.must_pause_work = self.cmd_from_sd = False
        self.work_timer = None
        # Streaming queue
        self.stream_queue = None
        # Error handling
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.on_error_gcode = gcode_macro.load_template(
            config, "on_error_gcode", DEFAULT_ERROR_GCODE
        )
        # Register commands
        self.gcode = self.printer.lookup_object("gcode")
        for cmd in ["M23", "M24", "M25"]:
            self.gcode.register_command(cmd, getattr(self, "cmd_" + cmd))
        self.gcode.register_command(
            "SDCARD_RESET_FILE",
            self.cmd_SDCARD_RESET_FILE,
            desc=self.cmd_SDCARD_RESET_FILE_help,
        )
        self.gcode.register_command(
            "SDCARD_PRINT_FILE",
            self.cmd_SDCARD_PRINT_FILE,
            desc=self.cmd_SDCARD_PRINT_FILE_help,
        )
        self.gcode.register_command(
            "SDCARD_STREAM_GCODE",
            self.cmd_SDCARD_STREAM_GCODE,
            desc=self.cmd_SDCARD_STREAM_GCODE_help,
        )
        self.gcode.register_command(
            "STREAM_GCODE_LINE",
            self.cmd_STREAM_GCODE_LINE,
            desc="Stream a single G-code line to the queue",
        )

    def handle_shutdown(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            self.gcode_provider.handle_shutdown()

    def stats(self, eventtime):
        if self.work_timer is None:
            return False, ""
        return self.gcode_provider.get_stats(eventtime)

    def get_status(self, eventtime):
        sts = {"is_active": self.is_active()}
        if self.gcode_provider:
            sts.update(self.gcode_provider.get_status(eventtime))
        else:
            sts.update(self.virtualsd_gcode_provider.get_status(eventtime))
        return sts

    def is_active(self):
        return self.work_timer is not None

    def do_pause(self):
        if self.work_timer is not None:
            self.must_pause_work = True
            while self.work_timer is not None and not self.cmd_from_sd:
                self.reactor.pause(self.reactor.monotonic() + 0.001)

    def do_resume(self):
        if self.work_timer is not None:
            raise self.gcode.error("SD busy")
        self.must_pause_work = False
        self.work_timer = self.reactor.register_timer(
            self.work_handler, self.reactor.NOW
        )

    def do_cancel(self):
        if self.gcode_provider is not None:
            self.do_pause()
            self.gcode_provider.reset()
            self.print_stats.note_cancel()
            self.gcode_provider = None

    def _set_gcode_provider(self, gcode_provider):
        if self.gcode_provider is not None:
            raise self.gcode.error(
                "Print is active when resetting GCode provider"
            )
        self.gcode_provider = gcode_provider
        # Set the filename with the full extension to match the file on disk
        current_filename = self.print_stats.get_status(self.reactor.monotonic())['filename']
        if not current_filename:
            full_filename = gcode_provider.get_name() + ".gcode"  # Ensure .gcode extension
            self.print_stats.set_current_file(full_filename)
        self.gcode_lines = gcode_provider.get_gcode()
        self.current_line = ""

    def print_with_gcode_provider(self, gcode_provider):
        self._reset_print()
        self._set_gcode_provider(gcode_provider)
        self.do_resume()

    # G-Code commands
    def _reset_print(self):
        if self.gcode_provider is not None:
            self.do_pause()
            self.gcode_provider.reset()
            self.gcode_provider = None
        self.stream_queue = None
        self.print_stats.reset()
        self.printer.send_event("virtual_sdcard:reset_file")

    cmd_SDCARD_RESET_FILE_help = (
        "Clears a loaded SD File. Stops the print if necessary"
    )

    def cmd_SDCARD_RESET_FILE(self, gcmd):
        if self.cmd_from_sd:
            raise gcmd.error("SDCARD_RESET_FILE cannot be run from the sdcard")
        self._reset_print()
        self.print_stats.state = "standby"  # Explicitly set state to standby
        logging.info("Reset printer state to standby")
        # Force a state update notification
        self.gcode.respond_info("Printer state reset to standby")
        self.reactor.pause(self.reactor.monotonic() + 2.0)  # Add a longer delay
        # Clear any pending G-code or errors
        try:
            self.gcode.run_script_from_command("CLEAR_PAUSE")
            self.gcode.run_script_from_command("TURN_OFF_HEATERS")
            logging.info("Cleared Klipper pause state and turned off heaters")
        except Exception as e:
            logging.error(f"Failed to clear Klipper pause state or turn off heaters: {str(e)}")
        # Trigger a Klipper event to notify Moonraker
        self.printer.send_event("virtual_sdcard:reset_file")

    cmd_SDCARD_PRINT_FILE_help = (
        "Loads a SD file and starts the print. May "
        "include files in subdirectories."
    )

    def cmd_SDCARD_PRINT_FILE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_print()
        filename = gcmd.get("FILENAME")
        if filename[0] == "/":
            filename = filename[1:]
        self.virtualsd_gcode_provider.load_file(
            gcmd, filename, check_subdirs=True
        )
        self._set_gcode_provider(self.virtualsd_gcode_provider)
        self.print_stats.note_start()
        self.do_resume()

    cmd_SDCARD_STREAM_GCODE_help = "Start a print job with a streaming G-code source"
    def cmd_SDCARD_STREAM_GCODE(self, gcmd):
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_print()
        try:
            # Initialize the streaming queue
            self.stream_queue = queue.Queue()
            self.virtualsd_gcode_provider.is_streaming = True
            self.virtualsd_gcode_provider.stream_position = 0
            # Set the filename in the provider with the full extension
            self.virtualsd_gcode_provider.set_filename("hedera_streamed_print")
            self.print_stats.note_start()
            self.print_stats.set_current_file("hedera_streamed_print.gcode")  # Use full filename with .gcode
            # Notify the user that the print has started
            self.gcode.run_script_from_command("M117 Print Started")
            gcmd.respond_info("Print Started")
            self._set_gcode_provider(self.virtualsd_gcode_provider)
            self.do_resume()
        except Exception as e:
            gcmd.error(f"Failed to start streaming print: {str(e)}")
            self.virtualsd_gcode_provider.is_streaming = False
            self.print_stats.note_error(str(e))

    def cmd_STREAM_GCODE_LINE(self, gcmd):
        """Stream a single G-code line to the queue."""
        if not self.virtualsd_gcode_provider.is_streaming or self.stream_queue is None:
            raise gcmd.error("Not in streaming mode or queue not initialized")
        gcode_line = gcmd.get("LINE", None)
        if gcode_line is None:
            # End of stream signal
            self.stream_queue.put(None)
            logging.info("Received end of stream signal")
            return
        try:
            logging.debug(f"Received G-code line in cmd_STREAM_GCODE_LINE: {gcode_line}")
            self.stream_queue.put(gcode_line)
        except Exception as e:
            logging.error(f"Failed to queue streamed G-code: {str(e)}")
            self.virtualsd_gcode_provider.is_streaming = False
            self.print_stats.note_error(str(e))
            # Execute CANCEL_PRINT macro to ensure safe shutdown
            try:
                self.gcode.run_script_from_command("CANCEL_PRINT")
                self.gcode.respond_info("Print canceled due to error")
            except Exception as cancel_e:
                self.gcode.respond_error(f"Failed to execute CANCEL_PRINT: {str(cancel_e)}")
            # Fallback: Explicitly turn off heaters
            try:
                self.gcode.run_script_from_command("M104 S0")  # Turn off extruder heater
                self.gcode.run_script_from_command("M140 S0")  # Turn off bed heater
                self.gcode.respond_info("Turned off heaters as fallback")
            except Exception as heater_e:
                self.gcode.respond_error(f"Failed to turn off heaters: {str(heater_e)}")
            self._reset_print()
            raise

    def cmd_M23(self, gcmd):
        # Select SD file
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_print()
        filename = gcmd.get_raw_command_parameters().strip()
        if filename.startswith("/"):
            filename = filename[1:]
        self.virtualsd_gcode_provider.load_file(
            gcmd, filename, check_subdirs=True
        )
        self._set_gcode_provider(self.virtualsd_gcode_provider)

    def cmd_M24(self, gcmd):
        # Start/resume SD print
        self.do_resume()

    def cmd_M25(self, gcmd):
        # Pause SD print
        self.do_pause()

    def get_virtual_sdcard_gcode_provider(self):
        return self.virtualsd_gcode_provider

    def get_gcode_provider(self):
        return self.gcode_provider

    def is_cmd_from_sd(self):
        return self.cmd_from_sd

    # Background work timer
    def work_handler(self, eventtime):
        gcode_mutex = self.gcode.get_mutex()
        error_message = None
        if self.gcode_provider.is_streaming and self.stream_queue is not None:
            # Streaming mode: process G-code lines from the queue
            max_wait_time = 600  # Increased to 10 minutes to account for delays
            elapsed_time = 0
            start_time = self.reactor.monotonic()
            logging.info("Entering streaming mode in work_handler")
            # Directly update print_stats state
            self.print_stats.state = "printing"
            # Update print duration periodically
            while not self.must_pause_work and elapsed_time < max_wait_time:
                # Pause if any other request is pending in the gcode class
                if gcode_mutex.test():
                    logging.debug("Pausing work_handler due to gcode mutex")
                    self.reactor.pause(self.reactor.monotonic() + 0.100)
                    continue
                try:
                    # Get the next G-code line from the queue (non-blocking)
                    logging.debug("Checking stream_queue for G-code line")
                    gcode_line = self.stream_queue.get_nowait()
                    logging.debug(f"Received G-code line from queue: {gcode_line}")
                    if gcode_line is None:  # End of stream
                        logging.info("End of G-code stream received")
                        self.gcode_provider.is_streaming = False
                        self.print_stats.state = "complete"
                        self.print_stats.note_complete()
                        self.gcode.respond_info("Print Complete")
                        self.gcode.respond_info("Done printing file")
                        self._reset_print()
                        self.print_stats.state = "standby"  # Reset state to standby
                        logging.info("Reset printer state to standby after print completion")
                        # Force a state update notification
                        self.gcode.respond_info("Printer state reset to standby")
                        self.reactor.pause(self.reactor.monotonic() + 2.0)  # Add a longer delay
                        # Clear any pending G-code or errors
                        try:
                            self.gcode.run_script_from_command("CLEAR_PAUSE")
                            self.gcode.run_script_from_command("TURN_OFF_HEATERS")
                            self.gcode.run_script_from_command("SDCARD_RESET_FILE")
                            logging.info("Cleared Klipper pause state, turned off heaters, and reset file")
                        except Exception as e:
                            logging.error(f"Failed to clear Klipper pause state or turn off heaters: {str(e)}")
                        # Trigger a Klipper event to notify Moonraker
                        self.printer.send_event("virtual_sdcard:reset_file")
                        self.work_timer = None
                        return self.reactor.NEVER
                    # Dispatch command
                    self.cmd_from_sd = True
                    try:
                        self.gcode.run_script_from_command(gcode_line)
                        self.gcode_provider.stream_position += len(gcode_line) + 1
                        # Update print_stats with the current position using set_position
                        self.print_stats.set_position(self.gcode_provider.stream_position)
                        # Update print duration
                        current_time = self.reactor.monotonic()
                        self.print_stats.print_duration = current_time - start_time
                        self.print_stats.total_duration = self.print_stats.print_duration
                    except self.gcode.error as e:
                        error_message = str(e)
                        try:
                            self.gcode.run_script(self.on_error_gcode.render())
                        except:
                            logging.exception("virtual_sdcard on_error")
                        break
                    except:
                        logging.exception("virtual_sdcard dispatch")
                        break
                    finally:
                        self.cmd_from_sd = False
                except queue.Empty:
                    # No G-code line available yet; pause briefly and retry
                    elapsed_time += 0.100  # Increased interval to 0.1 seconds
                    if elapsed_time >= max_wait_time:
                        error_message = "Streaming timed out waiting for G-code lines"
                        logging.error(error_message)
                        break
                    # Keep the timer running by returning a future timestamp
                    logging.debug(f"Queue empty, waiting for G-code lines (elapsed: {elapsed_time}/{max_wait_time} seconds)")
                    return self.reactor.monotonic() + 0.100
                except Exception as e:
                    error_message = str(e)
                    logging.exception("virtual_sdcard streaming error")
                    break
        else:
            # File-based mode: process G-code lines from get_gcode
            logging.info("Entering file-based mode in work_handler")
            while not self.must_pause_work:
                if not self.current_line:
                    try:
                        self.current_line = next(self.gcode_lines)
                    except self.gcode.error as e:
                        error_message = str(e)
                        try:
                            self.gcode.run_script(self.on_error_gcode.render())
                        except:
                            logging.exception("virtual_sdcard on_error")
                        break
                    except StopIteration:
                        self.gcode_provider = None
                        break
                # Pause if any other request is pending in the gcode class
                if gcode_mutex.test():
                    self.reactor.pause(self.reactor.monotonic() + 0.100)
                    continue
                # Dispatch command
                self.cmd_from_sd = True
                line = self.current_line
                try:
                    self.gcode.run_script(line)
                except self.gcode.error as e:
                    error_message = str(e)
                    try:
                        self.gcode.run_script(self.on_error_gcode.render())
                    except:
                        logging.exception("virtual_sdcard on_error")
                    break
                except:
                    logging.exception("virtual_sdcard dispatch")
                    break
                self.cmd_from_sd = False
                self.current_line = ""
        self.work_timer = None
        self.cmd_from_sd = False
        if error_message is not None:
            self.print_stats.note_error(error_message)
            self.gcode.respond_raw(error_message)
            self.gcode_provider = None
        elif self.gcode_provider is not None:
            self.print_stats.note_pause()
        else:
            self.print_stats.note_complete()
            self.print_stats.state = "standby"  # Ensure state is reset to standby
            logging.info("Reset printer state to standby after print completion")
            # Force a state update notification
            self.gcode.respond_info("Printer state reset to standby")
            self.reactor.pause(self.reactor.monotonic() + 2.0)  # Add a longer delay
            # Clear any pending G-code or errors
            try:
                self.gcode.run_script_from_command("CLEAR_PAUSE")
                self.gcode.run_script_from_command("TURN_OFF_HEATERS")
                self.gcode.run_script_from_command("SDCARD_RESET_FILE")
                logging.info("Cleared Klipper pause state, turned off heaters, and reset file")
            except Exception as e:
                logging.error(f"Failed to clear Klipper pause state or turn off heaters: {str(e)}")
            # Trigger a Klipper event to notify Moonraker
            self.printer.send_event("virtual_sdcard:reset_file")
        return self.reactor.NEVER

def load_config(config):
    return VirtualSD(config)