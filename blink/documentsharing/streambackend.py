__all__ = ['BlinkWebODFStreamSessionBackend']

from zope.interface import implements
from application.notification import IObserver, NotificationCenter, NotificationData

from PyQt4.QtCore import pyqtSlot

from blink.documentsharing.backendbase import BlinkWebODFSessionBackendBase


class BlinkWebODFStreamSessionBackend(BlinkWebODFSessionBackendBase):
    implements(IObserver)

    def __init__(self, stream):
        super(BlinkWebODFStreamSessionBackend, self).__init__(stream.session.account);

        self.stream = stream
        self._connected = True
        self._reconnected = False

        notification_center = NotificationCenter()
        notification_center.add_observer(self, name = 'DocumentSharingStreamConnected')
        notification_center.add_observer(self, name = 'DocumentSharingStreamDisconnected')
        notification_center.add_observer(self, name = 'DocumentSharingStreamInitRequestReply')
        notification_center.add_observer(self, name = 'DocumentSharingStreamReplayRequestReply')
        notification_center.add_observer(self, name = 'DocumentSharingStreamNewOps')
        notification_center.add_observer(self, name = 'DocumentSharingStreamNewOpsReply')

    # SessionBackend API
    @pyqtSlot(result="QString")
    def genesisUrl(self):
        return self.stream.receivedGenesisFile.fileName()

    # SessionBackend API
    @pyqtSlot(result="bool")
    def isConnected(self):
        return self._connected

    # SessionBackend API
    @pyqtSlot('QString')
    def requestServerOps(self, lastServerSyncHeadId):
        self._sendMessage('InitRequest', {
            'continueSession':  True,
            'baseHeadId': lastServerSyncHeadId
        })

    # SessionBackend API
    @pyqtSlot()
    def requestReplay(self):
        # TODO: instead of sending member data from the client, the host should set this
        self._sendMessage('ReplayRequest', {
            'memberId': self.memberId(),
            'fullName': self.account.display_name,
            'color':    self.account.id,
            'imageUrl': "/avatar/"+str(self.account.uri).encode("hex")
        })

    # SessionBackend API
    @pyqtSlot('QVariantList', 'QString')
    def sendClientOpspecs(self, opspecs, baseHeadId):
        # send to other server
        self._sendMessage('NewOps', {
            'opspecs':  opspecs,
            'baseHeadId':   baseHeadId
        })


    def _setConnected(self, connected):
        self._connected = connected
        self.connectedChanged.emit(connected)

    def _sendMessage(self, messageType, messageBody):
        assert self.stream is not None
        self.stream.sendMessage(messageType, messageBody)

    def handleReconnection(self, stream):
        # TODO: check that stream matches properties
        #if self.host != (stream.mode == 'host')
            #self.url != stream.filename
            #self.account != stream.session.account:
            #mehhh
        self._reconnected = True
        self.stream = stream

    def hasUnsavedChanges(self):
        return self._hasUnsynchronizedChanges

    def tagCurrentStateAsSavedToDisc(self):
        # nothing we care about on guest side for now
        pass

    # IObserver API
    def handle_notification(self, notification):
        handler = getattr(self, "_NH_%s" % notification.name, None)
        if handler:
            handler(notification)

    def _NH_DocumentSharingStreamConnected(self, notification):
        if(notification.sender != self.stream):
            return

        self._setConnected(True)

        baseHeadId = None
        if not self._reconnected:
            self._sendMessage('InitRequest', {
                'continueSession': False
            })

    def _NH_DocumentSharingStreamDisconnected(self, notification):
        if(notification.sender != self.stream):
            return

        self._setConnected(False)

    def _NH_DocumentSharingStreamInitRequestReply(self, notification):
        if(notification.sender != self.stream):
            return

        serverOpspecs = notification.data.content
        self.serverOpspecsArrived.emit(serverOpspecs);
        self.reconnectFinished.emit()

    def _NH_DocumentSharingStreamReplayRequestReply(self, notification):
        if(notification.sender != self.stream):
            return

        serverOpspecs = notification.data.content
        self.serverOpspecsArrived.emit(serverOpspecs);
        self.replayFinished.emit()

    def _NH_DocumentSharingStreamNewOps(self, notification):
        if(notification.sender != self.stream):
            return

        newOps = notification.data.content

        # pass to local session
        self.serverOpspecsArrived.emit(newOps);

    def _NH_DocumentSharingStreamNewOpsReply(self, notification):
        if(notification.sender != self.stream):
            return

        response = notification.data.content
        self.clientOpspecsSent.emit(response);
