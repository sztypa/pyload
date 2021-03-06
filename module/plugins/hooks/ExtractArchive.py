# -*- coding: utf-8 -*-

from __future__ import with_statement

import os
import sys

from copy import copy
from traceback import print_exc

# monkey patch bug in python 2.6 and lower
# http://bugs.python.org/issue6122 , http://bugs.python.org/issue1236 , http://bugs.python.org/issue1731717
if sys.version_info < (2, 7) and os.name != "nt":
    import errno

    from subprocess import Popen

    def _eintr_retry_call(func, *args):
        while True:
            try:
                return func(*args)

            except OSError, e:
                if e.errno == errno.EINTR:
                    continue
                raise


    # unsued timeout option for older python version
    def wait(self, timeout=0):
        """Wait for child process to terminate.  Returns returncode
        attribute."""
        if self.returncode is None:
            try:
                pid, sts = _eintr_retry_call(os.waitpid, self.pid, 0)

            except OSError, e:
                if e.errno != errno.ECHILD:
                    raise
                    # This happens if SIGCLD is set to be ignored or waiting
                # for child processes has otherwise been disabled for our
                # process.  This child is dead, we can't get the status.
                sts = 0
            self._handle_exitstatus(sts)
        return self.returncode

    Popen.wait = wait

if os.name != "nt":
    from grp import getgrnam
    from pwd import getpwnam

from module.plugins.Hook import Hook, threaded, Expose
from module.plugins.internal.Extractor import ArchiveError, CRCError, PasswordError
from module.plugins.internal.SimpleHoster import replace_patterns
from module.utils import fs_encode, save_join, uniqify


class ArchiveQueue(object):

    def __init__(self, plugin, storage):
        self.plugin  = plugin
        self.storage = storage


    def get(self):
        try:
            return [int(pid) for pid in self.plugin.getStorage("ExtractArchive:%s" % self.storage, "").decode('base64').split()]
        except Exception:
            return []


    def set(self, value):
        if isinstance(value, list):
            item = str(value)[1:-1].replace(' ', '').replace(',', ' ')
        else:
            item = str(value).strip()
        return self.plugin.setStorage("ExtractArchive:%s" % self.storage, item.encode('base64')[:-1])


    def delete(self):
        return self.plugin.delStorage("ExtractArchive:%s" % self.storage)


    def add(self, item):
        queue = self.get()
        if item not in queue:
            return self.set(queue + [item])
        else:
            return True


    def remove(self, item):
        queue = self.get()
        try:
            queue.remove(item)

        except ValueError:
            pass

        if queue == []:
            return self.delete()

        return self.set(queue)



class ExtractArchive(Hook):
    __name__    = "ExtractArchive"
    __type__    = "hook"
    __version__ = "1.33"

    __config__ = [("activated"      , "bool"  , "Activated"                                 , True                                                                     ),
                  ("fullpath"       , "bool"  , "Extract with full paths"                   , True                                                                     ),
                  ("overwrite"      , "bool"  , "Overwrite files"                           , False                                                                    ),
                  ("keepbroken"     , "bool"  , "Try to extract broken archives"            , False                                                                    ),
                  ("repair"         , "bool"  , "Repair broken archives (rar required)"     , False                                                                    ),
                  ("test"           , "bool"  , "Test archive before extracting"            , False                                                                    ),
                  ("usepasswordfile", "bool"  , "Use password file"                         , True                                                                     ),
                  ("passwordfile"   , "file"  , "Password file"                             , "archive_password.txt"                                                   ),
                  ("delete"         , "bool"  , "Delete archive when successfully extracted", False                                                                    ),
                  ("subfolder"      , "bool"  , "Create subfolder for each package"         , False                                                                    ),
                  ("destination"    , "folder", "Extract files to folder"                   , ""                                                                       ),
                  ("extensions"     , "str"   , "Extract the following extensions"          , "7z,bz2,bzip2,gz,gzip,lha,lzh,lzma,rar,tar,taz,tbz,tbz2,tgz,xar,xz,z,zip"),
                  ("excludefiles"   , "str"   , "Don't extract the following files"         , "*.nfo,*.DS_Store,index.dat,thumb.db"                                    ),
                  ("recursive"      , "bool"  , "Extract archives in archives"              , True                                                                     ),
                  ("waitall"        , "bool"  , "Wait for all downloads to be finished"     , False                                                                    ),
                  ("renice"         , "int"   , "CPU priority"                              , 0                                                                        )]

    __description__ = """Extract different kind of archives"""
    __license__     = "GPLv3"
    __authors__     = [("Walter Purcaro", "vuolter@gmail.com"),
                       ("Immenz"        , "immenz@gmx.net"   )]


    event_list = ["allDownloadsProcessed","packageDeleted"]

    NAME_REPLACEMENTS = [(r'\.part\d+\.rar$', ".part.rar")]


    def setup(self):
        self.queue  = ArchiveQueue(self, "Queue")
        self.failed = ArchiveQueue(self, "Failed")

        self.interval    = 60
        self.extracting  = False
        self.lastPackage = False
        self.extractors  = []
        self.passwords   = []
        self.repair      = False


    def coreReady(self):
        for p in ("UnRar", "SevenZip", "UnZip"):
            try:
                module = self.core.pluginManager.loadModule("internal", p)
                klass  = getattr(module, p)
                if klass.isUsable():
                    self.extractors.append(klass)
                if klass.REPAIR:
                    self.repair = self.getConfig("repair")

            except OSError, e:
                if e.errno == 2:
                    self.logInfo(_("No %s installed") % p)
                else:
                    self.logWarning(_("Could not activate: %s") % p, e)
                    if self.core.debug:
                        print_exc()

            except Exception, e:
                self.logWarning(_("Could not activate: %s") % p, e)
                if self.core.debug:
                    print_exc()

        if self.extractors:
            self.logInfo(_("Activated") + " " + "|".join("%s %s" % (Extractor.__name__,Extractor.VERSION) for Extractor in self.extractors))
            self.extractQueued()  #: Resume unfinished extractions
        else:
            self.logInfo(_("No Extract plugins activated"))


    @threaded
    def extractQueued(self,thread):
        packages = self.queue.get()
        while packages:
            if self.lastPackage: # called from allDownloadsProcessed
                self.lastPackage = False
                if self.extract(packages, thread):  #@NOTE: check only if all gone fine, no failed reporting for now
                    self.manager.dispatchEvent("all_archives_extracted")
                self.manager.dispatchEvent("all_archives_processed")
            else:
                if self.extract(packages, thread):  #@NOTE: check only if all gone fine, no failed reporting for now
                    pass

            packages = self.queue.get() # check for packages added during extraction


    @Expose
    def extractPackage(self, *ids):
        """ Extract packages with given id"""
        for id in ids:
            self.queue.add(id)
        if not self.getConfig("waitall") and not self.extracting:
            self.extractQueued()


    def packageDeleted(self, pid):
        self.queue.remove(pid)


    def packageFinished(self, pypack):
        self.queue.add(pypack.id)
        if not self.getConfig("waitall") and not self.extracting:
            self.extractQueued()


    def allDownloadsProcessed(self):
        self.lastPackage = True
        if not self.extracting:
            self.extractQueued()


    def extract(self, ids, thread=None):
        if not ids:
            return False

        self.extracting = True

        processed = []
        extracted = []
        failed    = []

        toList = lambda string: string.replace(' ', '').replace(',', '|').replace(';', '|').split('|')

        destination  = self.getConfig("destination")
        subfolder    = self.getConfig("subfolder")
        fullpath     = self.getConfig("fullpath")
        overwrite    = self.getConfig("overwrite")
        renice       = self.getConfig("renice")
        recursive    = self.getConfig("recursive")
        delete       = self.getConfig("delete")
        keepbroken   = self.getConfig("keepbroken")

        extensions   = [x.lstrip('.').lower() for x in toList(self.getConfig("extensions"))]
        excludefiles = toList(self.getConfig("excludefiles"))

        if extensions:
            self.logDebug("Use for extensions: %s" % "|.".join(extensions))

        # reload from txt file
        self.reloadPasswords()

        # dl folder
        dl = self.config['general']['download_folder']

        #iterate packages -> extractors -> targets
        for pid in ids:
            pypack = self.core.files.getPackage(pid)

            if not pypack:
                self.queue.remove(pid)
                continue

            self.logInfo(_("Check package: %s") % pypack.name)

            # determine output folder
            out = save_join(dl, pypack.folder, destination, "")  #: force trailing slash

            if subfolder:
                out = save_join(out, pypack.folder)

            if not os.path.exists(out):
                os.makedirs(out)

            matched   = False
            success   = True
            files_ids = dict((pylink['name'],((save_join(dl, pypack.folder, pylink['name'])), pylink['id'], out)) for pylink \
                        in sorted(pypack.getChildren().itervalues(), key=lambda k: k['name'])).values()    # remove duplicates

            # check as long there are unseen files
            while files_ids:
                new_files_ids = []

                if extensions:
                    files_ids = [(fname, fid, fout) for fname, fid, fout in files_ids \
                                 if filter(lambda ext: fname.lower().endswith(ext), extensions)]

                for Extractor in self.extractors:
                    targets = Extractor.getTargets(files_ids)
                    if targets:
                        self.logDebug("Targets for %s: %s" % (Extractor.__name__, targets))
                        matched = True

                    for fname, fid, fout in targets:
                        name = os.path.basename(fname)

                        if not os.path.exists(fname):
                            self.logDebug(name, "File not found")
                            continue

                        self.logInfo(name, _("Extract to: %s") % fout)
                        try:
                            archive = Extractor(self,
                                                fname,
                                                fout,
                                                fullpath,
                                                overwrite,
                                                excludefiles,
                                                renice,
                                                delete,
                                                keepbroken,
                                                fid)
                            archive.init()

                            new_files = self._extract(archive, fid, pypack.password, thread)

                        except Exception, e:
                            self.logError(name, e)
                            success = False
                            continue

                        files_ids.remove((fname, fid, fout)) # don't let other extractors spam log
                        self.logDebug("Extracted files: %s" % new_files)
                        self.setPermissions(new_files)

                        for filename in new_files:
                            file = fs_encode(save_join(os.path.dirname(archive.filename), filename))
                            if not os.path.exists(file):
                                self.logDebug("New file %s does not exists" % filename)
                                continue

                            if recursive and os.path.isfile(file):
                                new_files_ids.append((filename, fid, os.path.dirname(filename)))  # append as new target

                        pyfile = self.core.files.getFile(fid)
                        self.manager.dispatchEvent("archive_extracted", pyfile, archive.out, archive.filename, new_files)

                files_ids = new_files_ids  # also check extracted files

            if matched:
                if success:
                    extracted.append(pid)
                    self.manager.dispatchEvent("package_extracted", pypack)

                else:
                    failed.append(pid)
                    self.manager.dispatchEvent("package_extract_failed", pypack)

                    self.failed.add(pid)
            else:
                self.logInfo(_("No files found to extract"))

            if not matched or not success and subfolder:
                try:
                    os.rmdir(out)

                except OSError:
                    pass

            self.queue.remove(pid)

        self.extracting = False
        return True if not failed else False


    def _extract(self, archive, fid, password, thread):
        pyfile = self.core.files.getFile(fid)
        name   = os.path.basename(archive.filename)

        thread.addActive(pyfile)
        pyfile.setStatus("processing")

        encrypted = False
        try:
            self.logDebug("Password: %s" % (password or "None provided"))
            passwords = uniqify([password] + self.getPasswords(False)) if self.getConfig("usepasswordfile") else [password]
            for pw in passwords:
                try:
                    if self.getConfig("test") or self.repair:
                        pyfile.setCustomStatus(_("testing"))
                        if pw:
                            self.logDebug("Testing with password: %s" % pw)
                        pyfile.setProgress(0)
                        archive.test(pw)
                        pyfile.setProgress(100)
                    else:
                        archive.check(pw)

                    self.addPassword(pw)
                    break

                except PasswordError:
                    if not encrypted:
                        self.logInfo(name, _("Password protected"))
                        encrypted = True

                except CRCError, e:
                    self.logDebug(name, e)
                    self.logInfo(name, _("CRC Error"))

                    if self.repair:
                        self.logWarning(name, _("Repairing..."))

                        pyfile.setCustomStatus(_("repairing"))
                        pyfile.setProgress(0)
                        repaired = archive.repair()
                        pyfile.setProgress(100)

                        if not repaired and not self.getConfig("keepbroken"):
                            raise CRCError("Archive damaged")

                        self.addPassword(pw)
                        break

                    raise CRCError("Archive damaged")

                except ArchiveError, e:
                    raise ArchiveError(e)

            pyfile.setCustomStatus(_("extracting"))
            pyfile.setProgress(0)

            if not encrypted or not self.getConfig("usepasswordfile"):
                self.logDebug("Extracting using password: %s" % (password or "None"))
                archive.extract(password)
            else:
                for pw in filter(None, uniqify([password] + self.getPasswords(False))):
                    try:
                        self.logDebug("Extracting using password: %s" % pw)

                        archive.extract(pw)
                        self.addPassword(pw)
                        break

                    except PasswordError:
                        self.logDebug("Password was wrong")
                else:
                    raise PasswordError

            pyfile.setProgress(100)
            pyfile.setCustomStatus(_("finalizing"))

            if self.core.debug:
                self.logDebug("Would delete: %s" % ", ".join(archive.getDeleteFiles()))

            if self.getConfig("delete"):
                files = archive.getDeleteFiles()
                self.logInfo(_("Deleting %s files") % len(files))
                for f in files:
                    file = fs_encode(f)
                    if os.path.exists(file):
                        os.remove(file)
                    else:
                        self.logDebug("%s does not exists" % f)

            self.logInfo(name, _("Extracting finished"))
            extracted_files = archive.files or archive.list()

            return extracted_files

        except PasswordError:
            self.logError(name, _("Wrong password" if password else "No password found"))

        except CRCError, e:
            self.logError(name, _("CRC mismatch"), e)

        except ArchiveError, e:
            self.logError(name, _("Archive error"), e)

        except Exception, e:
            self.logError(name, _("Unknown error"), e)
            if self.core.debug:
                print_exc()

        finally:
            thread.finishFile(pyfile)

        self.manager.dispatchEvent("archive_extract_failed", pyfile)

        raise Exception(_("Extract failed"))


    @Expose
    def getPasswords(self, reload=True):
        """ List of saved passwords """
        if reload:
            self.reloadPasswords()

        return self.passwords


    def reloadPasswords(self):
        try:
            passwords = []

            file = fs_encode(self.getConfig("passwordfile"))
            with open(file) as f:
                for pw in f.read().splitlines():
                    passwords.append(pw)

        except IOError, e:
            self.logError(e)

        else:
            self.passwords = passwords


    @Expose
    def addPassword(self, password):
        """  Adds a password to saved list"""
        try:
            self.passwords = uniqify([password] + self.passwords)

            file = fs_encode(self.getConfig("passwordfile"))
            with open(file, "wb") as f:
                for pw in self.passwords:
                    f.write(pw + '\n')

        except IOError, e:
            self.logError(e)


    def setPermissions(self, files):
        for f in files:
            if not os.path.exists(f):
                continue

            try:
                if self.config['permission']['change_file']:
                    if os.path.isfile(f):
                        os.chmod(f, int(self.config['permission']['file'], 8))

                    elif os.path.isdir(f):
                        os.chmod(f, int(self.config['permission']['folder'], 8))

                if self.config['permission']['change_dl'] and os.name != "nt":
                    uid = getpwnam(self.config['permission']['user'])[2]
                    gid = getgrnam(self.config['permission']['group'])[2]
                    os.chown(f, uid, gid)

            except Exception, e:
                self.logWarning(_("Setting User and Group failed"), e)
