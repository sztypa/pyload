# -*- coding: utf-8 -*-

import socket
import time

from threading import Lock

from module.plugins.Hook import Hook, threaded


def forward(source, destination):
    try:
        bufsize = 1024
        bufdata = source.recv(bufsize)
        while bufdata:
            destination.sendall(bufdata)
            bufdata = source.recv(bufsize)
    finally:
        destination.shutdown(socket.SHUT_WR)
        # destination.close()


#@TODO: IPv6 support
class ClickAndLoad(Hook):
    __name__    = "ClickAndLoad"
    __type__    = "hook"
    __version__ = "0.40"

    __config__ = [("activated", "bool", "Activated"                             , True),
                  ("port"     , "int" , "Port"                                  , 9666),
                  ("extern"   , "bool", "Listen on the public network interface", True)]

    __description__ = """Click'n'Load hook plugin"""
    __license__     = "GPLv3"
    __authors__     = [("RaNaN"         , "RaNaN@pyload.de"  ),
                       ("Walter Purcaro", "vuolter@gmail.com")]


    #@TODO: Remove in 0.4.10
    def initPeriodical(self):
        pass


    def coreReady(self):
        if not self.config['webinterface']['activated']:
            return

        ip      = "" if self.getConfig('extern') else "127.0.0.1"
        webport = self.config['webinterface']['port']
        cnlport = self.getConfig('port')

        self.proxy(ip, webport, cnlport)


    @threaded
    def proxy(self, ip, webport, cnlport):
        self.logInfo(_("Proxy listening on %s:%s") % (ip or "0.0.0.0", cnlport))

        self._server(ip, webport, cnlport)

        lock = Lock()
        lock.acquire()
        lock.acquire()


    @threaded
    def _server(self, ip, webport, cnlport):
        try:
            dock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            dock_socket.bind((ip, cnlport))
            dock_socket.listen(5)

            while True:
                client_socket, client_addr = dock_socket.accept()
                self.logDebug("Connection from %s:%s" % client_addr)

                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.connect(("127.0.0.1", webport))

                self.manager.startThread(forward, client_socket, server_socket)
                self.manager.startThread(forward, server_socket, client_socket)

        except socket.timeout:
            self.logDebug("Connection timed out, retrying...")
            return self._server(ip, webport, cnlport)

        except socket.error, e:
            self.logError(e)
            time.sleep(240)
            return self._server(ip, webport, cnlport)
