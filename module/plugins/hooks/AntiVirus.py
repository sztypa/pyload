# -*- coding: utf-8 -*-

import os
import shutil
import subprocess

from module.plugins.Hook import Hook, Expose, threaded
from module.utils import fs_encode, save_join


class AntiVirus(Hook):
    __name__    = "AntiVirus"
    __type__    = "hook"
    __version__ = "0.04"

    __config__ = [("action"    , "Antivirus default;Delete;Quarantine", "Manage infected files"                    , "Antivirus default"),  #@TODO: add trash option (use Send2Trash lib)
                  ("quardir"   , "folder"                             , "Quarantine folder"                        , ""                 ),
                  ("scanfailed", "bool"                               , "Scan incompleted files (failed downloads)", False              ),
                  ("cmdfile"   , "file"                               , "Antivirus executable"                     , ""                 ),
                  ("cmdargs"   , "str"                                , "Scan options"                             , ""                 ),
                  ("ignore-err", "bool"                               , "Ignore scan errors"                       , False              )]

    __description__ = """Scan downloaded files with antivirus program"""
    __license__     = "GPLv3"
    __authors__     = [("Walter Purcaro", "vuolter@gmail.com")]


    #@TODO: Remove in 0.4.10
    def initPeriodical(self):
        pass


    @Expose
    @threaded
    def scan(self, pyfile, thread):
        file     = fs_encode(pyfile.plugin.lastDownload)
        filename = os.path.basename(pyfile.plugin.lastDownload)
        cmdfile  = fs_encode(self.getConfig('cmdfile'))
        cmdargs  = fs_encode(self.getConfig('cmdargs').strip())

        if not os.path.isfile(file) or not os.path.isfile(cmdfile):
            return

        thread.addActive(pyfile)
        pyfile.setCustomStatus(_("virus scanning"))

        try:
            p = subprocess.Popen([cmdfile, cmdargs, file], bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            out, err = map(str.strip, p.communicate())

            if out:
                self.logInfo(filename, out)

            if err:
                self.logWarning(filename, err)
                if not self.getConfig('ignore-err')
                    self.logDebug("Delete/Quarantine task is aborted")
                    return

            if p.returncode:
                pyfile.error = _("infected file")
                action = self.getConfig('action')
                try:
                    if action == "Delete":
                        os.remove(file)

                    elif action == "Quarantine":
                        pyfile.setCustomStatus(_("file moving"))
                        pyfile.setProgress(0)
                        shutil.move(file, self.getConfig('quardir'))

                except (IOError, shutil.Error), e:
                    self.logError(filename, action + " action failed!", e)

            elif not out and not err:
                self.logDebug(filename, "No infected file found")

        finally:
            pyfile.setProgress(100)
            thread.finishFile(pyfile)


    def downloadFinished(self, pyfile):
        return self.scan(pyfile)


    def downloadFailed(self, pyfile):
        #: Check if pyfile is still "failed",
        #  maybe might has been restarted in meantime
        if pyfile.status == 8 and self.getConfig('scanfailed'):
            return self.scan(pyfile)
