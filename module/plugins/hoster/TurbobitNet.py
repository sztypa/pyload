# -*- coding: utf-8 -*-
"""
    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 3 of the License,
    or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
    See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, see <http://www.gnu.org/licenses/>.

    @author: zoidberg
"""

import re

from module.plugins.internal.SimpleHoster import SimpleHoster, create_getInfo
from module.plugins.ReCaptcha import ReCaptcha

from pycurl import HTTPHEADER

class TurbobitNet(SimpleHoster):
    __name__ = "TurbobitNet"
    __type__ = "hoster"
    __pattern__ = r"http://(?:\w*\.)?turbobit.net/(?P<ID>\w+).*"
    __version__ = "0.01"
    __description__ = """Turbobit.net plugin"""
    __author_name__ = ("zoidberg")
    __author_mail__ = ("zoidberg@mujmail.cz")
    
    FILE_SIZE_PATTERN = r"<span class='file-icon1 document'>[^<]*</span>\s*\((?P<S>[^\)]+)\)"
    FILE_NAME_PATTERN = r'<meta name="keywords" content="(?P<N>[^,]+)'
    FILE_OFFLINE_PATTERN = r'<h2>File Not Found</h2>'
    FILE_URL_REPLACEMENTS = [(r'(?<=http://)(.*?)(?=turbobit.net/)', '')]
    SH_COOKIES = [("turbobit.net", "user_lang", "en")]
    
    CAPTCHA_KEY_PATTERN = r'src="http://api\.recaptcha\.net/challenge\?k=([^"]+)"'
    DOWNLOAD_URL_PATTERN = r'(?P<url>/download/redirect/[^"\']+)'
    LIMIT_WAIT_PATTERN = r'<div id="time-limit-text">\s*.*?<span id=\'timeout\'>(\d+)</span> seconds'      

    def handleFree(self):         
        self.url = "http://turbobit.net/download/free/%s" % self.file_info['ID']
        self.html = self.load(self.url)
        
        found = re.search(self.CAPTCHA_KEY_PATTERN, self.html)
        captcha_key = found.group(1) if found else '6LcTGLoSAAAAAHCWY9TTIrQfjUlxu6kZlTYP50_c'
        recaptcha = ReCaptcha(self)                                    

        for i in range(5):
            found = re.search(self.LIMIT_WAIT_PATTERN, self.html)
            if found:
                self.setWait(int(found.group(1)), True)
                self.wait()
                self.retry()
        
            action, inputs = self.parseHtmlForm("action='#'")            
            inputs['recaptcha_challenge_field'], inputs['recaptcha_response_field'] = recaptcha.challenge(captcha_key)
            if not inputs: self.parseError("inputs")
            self.logDebug(inputs)

            self.html = self.load(self.url, post = inputs)
            
            if not "<div class='download-timer-header'>" in self.html:
                self.invalidCaptcha()
            else:
                self.correctCaptcha()
                break
        else: self.fail("Invalid captcha")
        
        self.req.http.lastURL = self.url
        self.req.http.c.setopt(HTTPHEADER, ["X-Requested-With: XMLHttpRequest"])
        
        self.setWait(60, False)
        self.wait()
        
        self.html = self.load("http://turbobit.net/download/getLinkAfterTimeout/" + self.file_info['ID'])
        found = re.search(self.DOWNLOAD_URL_PATTERN, self.html)
        if not found: self.parseError("free download link")        
        self.url = "http://turbobit.net" + found.group('url')
        self.logDebug(self.url)
        self.download(self.url)

getInfo = create_getInfo(TurbobitNet)