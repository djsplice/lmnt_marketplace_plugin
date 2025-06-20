#!/usr/bin/env python3

import sys, pathlib
import socket
import struct
import json
import logging

if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

    from klippy.printer import main

    class Dispatcher:
        def __init__(self):
            self.handlers = {}

        def register(self, method, handler):
            self.handlers[method] = handler

        def get(self, method):
            return self.handlers.get(method)

    class Server:
        def __init__(self, sock):
            self.sock = sock
            self.dispatcher = Dispatcher()

        def run(self):
            while True:
                msg, ancdata, _, _ = self.sock.recvmsg(4096, socket.CMSG_SPACE(4))
                fd = None
                if ancdata:
                    for cmsg_level, cmsg_type, cmsg_data in ancdata:
                        if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
                            fd = struct.unpack('i', cmsg_data)[0]
                            logging.info(f"Received FD {fd} from Moonraker")
                try:
                    command = json.loads(msg.decode())
                    handler = self.dispatcher.get(command.get('method', 'unknown'))
                    if handler:
                        handler(command.get('params', {}), fd=fd)
                    else:
                        logging.warning(f"Unknown command received: {command.get('method', 'unknown')}")
                except json.JSONDecodeError:
                    logging.error("Failed to decode JSON message from Moonraker")
                except Exception as e:
                    logging.error(f"Error processing command: {str(e)}")

    def main():
        # Create a socket and start the server
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server = Server(sock)
        server.run()

    main()
