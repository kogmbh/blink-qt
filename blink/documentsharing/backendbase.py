__all__ = ['BlinkWebODFSessionBackendBase']

from PyQt4.QtCore import QObject, pyqtSignal, pyqtSlot

from sipsimple.core import SIPURI

from blink.resources import IconManager, Resources
from blink.contacts import URIUtils


class BlinkWebODFSessionBackendBase(QObject):

    default_user_icon_filename = Resources.get('icons/default-avatar.png')

    serverOpspecsArrived = pyqtSignal('QVariantMap')
    replayFinished = pyqtSignal()
    reconnectFinished = pyqtSignal()
    clientOpspecsSent = pyqtSignal('QVariantMap')
    closed = pyqtSignal()
    connectedChanged = pyqtSignal(bool)

    def __init__(self, account):
        super(BlinkWebODFSessionBackendBase, self).__init__();

        self.account = account
        self._hasUnsynchronizedChanges = False
        self._baseHeadId = ""

    # SessionBackend API
    @pyqtSlot(result="QString")
    def memberId(self):
        return str(self.account.uri)

    # SessionBackend API
    def resolveAvatarUrl(self, url):
        if url.startswith("/avatar/"):
            uri = SIPURI.parse(url[8:].decode("hex"))

            # TODO: see if there is an official way to get this, including notification of changes
            # also needs fixing of webodf, allowing custom avatar renderer
            if self.account.uri == uri:
                avatar = IconManager().get('avatar')
                return avatar.filename if avatar != None else self.default_user_icon_filename

            contact, contact_uri = URIUtils.find_contact(uri)
            return contact.icon.filename

        return ""

    # SessionBackend API
    @pyqtSlot()
    def close(self):
        self.closed.emit()

    # SessionBackend API
    @pyqtSlot('bool', 'QString')
    def updateSyncState(self, hasUnsynchronizedChanges, baseHeadId):
        print "updateSyncState: ", hasUnsynchronizedChanges, " baseHeadId: ", baseHeadId
        self._hasUnsynchronizedChanges = hasUnsynchronizedChanges
        self._baseHeadId = baseHeadId
