__all__ = ['BlinkWebODFLocalSessionBackend']

from PyQt4.QtCore import QObject, pyqtSignal, pyqtSlot

from blink.documentsharing.backendbase import BlinkWebODFSessionBackendBase
from blink.documentsharing.localserver import DocumentSharingLocalServer


class BlinkWebODFLocalSessionBackend(BlinkWebODFSessionBackendBase):
    def __init__(self, stream):
        super(BlinkWebODFLocalSessionBackend, self).__init__(stream.session.account);

        self._savedBasedHeadId = self._baseHeadId
        self.server = DocumentSharingLocalServer(stream, self)

        self.server.addMember(self.memberId(), self.account.display_name, self.account.id, self.account.uri)

    # SessionBackend API
    @pyqtSlot(result="QString")
    def genesisUrl(self):
        return self.server.url

    # SessionBackend API
    @pyqtSlot(result="bool")
    def isConnected(self):
        return True

    # SessionBackend API
    @pyqtSlot()
    def requestReplay(self):
        serverOpspecs = {
            'opspecs':  self.server.getAllOpspecs(),
            'headId':   self.server.getHeadId()
        }
        self.serverOpspecsArrived.emit(serverOpspecs);
        self.replayFinished.emit()

    # SessionBackend API
    @pyqtSlot('QVariantList', 'QString')
    def sendClientOpspecs(self, opspecs, baseHeadId):
        response = self.server.addLocalOpspecs(opspecs, baseHeadId)
        self.clientOpspecsSent.emit(response);

    def handleNewOpspecsFromServer(self, opspecs):
        self.serverOpspecsArrived.emit(opspecs);

    def handleReconnection(self, stream):
        self.server.handleReconnection(stream)

    def hasUnsavedChanges(self):
        return (self._savedBasedHeadId != self._baseHeadId)

    def tagCurrentStateAsSavedToDisc(self):
        self._savedBasedHeadId = self._baseHeadId
