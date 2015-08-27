__all__ = ['DocumentSharingLocalServer']

from zope.interface import implements
from application.notification import IObserver, NotificationCenter, NotificationData


class DocumentSharingLocalServer(object):
    implements(IObserver)

    # copied from chatwindow.py, hoping for consistent colors
    __colors__ = ["aqua", "aquamarine", "blue", "blueviolet", "brown", "burlywood", "cadetblue", "chartreuse", "chocolate", "coral", "cornflowerblue", "crimson", "cyan", "darkblue", "darkcyan",
                  "darkgoldenrod", "darkgreen", "darkgrey", "darkkhaki", "darkmagenta", "darkolivegreen", "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen", "darkslateblue",
                  "darkslategrey", "darkturquoise", "darkviolet", "deeppink", "deepskyblue", "dimgrey", "dodgerblue", "firebrick", "forestgreen", "fuchsia", "gold", "goldenrod", "green",
                  "greenyellow", "grey", "hotpink", "indianred", "indigo", "lawngreen", "lightblue", "lightcoral", "lightgreen", "lightgrey", "lightpink", "lightsalmon", "lightseagreen",
                  "lightskyblue", "lightslategrey", "lightsteelblue", "lime", "limegreen", "magenta", "maroon", "mediumaquamarine", "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen",
                  "mediumslateblue", "mediumspringgreen", "mediumturquoise", "mediumvioletred", "midnightblue", "navy", "olive", "olivedrab", "orange", "orangered", "orchid", "palegreen",
                  "paleturquoise", "palevioletred", "peru", "pink", "plum", "powderblue", "purple", "red", "rosybrown", "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen", "sienna",
                  "silver", "skyblue", "slateblue", "slategrey", "springgreen", "steelblue", "tan", "teal", "thistle", "tomato", "turquoise", "violet", "yellowgreen"]

    def __init__(self, stream, localClient):
        notification_center = NotificationCenter()
        notification_center.add_observer(self, name = 'DocumentSharingStreamConnected')
        notification_center.add_observer(self, name = 'DocumentSharingStreamDisconnected')
        notification_center.add_observer(self, name = 'DocumentSharingStreamInitRequest')
        notification_center.add_observer(self, name = 'DocumentSharingStreamReplayRequest')
        notification_center.add_observer(self, name = 'DocumentSharingStreamNewOps')

        self._remoteConnected = False
        self._remoteReconnected = False
        self.stream = stream
        self.account = stream.session.account
        self.localClient = localClient

        self.masterOpspecStack = []
        self.url = stream.filename
        self._genesisSent = False


    def getAllOpspecs(self):
        return self.masterOpspecStack

    def getHeadId(self):
        return str(len(self.masterOpspecStack))

    def _sendMessageToRemoteClient(self, messageType, messageBody):
        assert self.stream is not None
        self.stream.sendMessage(messageType, messageBody)


    def addMember(self, memberId, fullName, accountId, accountUri):
        addMemberOp = {
            'optype': 'AddMember',
            'timestamp': 0,
            'memberid': memberId,

            'setProperties': {
                'fullName': fullName,
                'color': self._colorFromAccountId(accountId),
                'imageUrl': "/avatar/"+str(accountUri).encode("hex")
            }
        }

        self.masterOpspecStack.append(addMemberOp)

    def addLocalOpspecs(self, opspecs, baseHeadId):
        # check if ops are based on current master
        if baseHeadId != self.getHeadId():
            # too bad, not based on latest state, so reject
            response = {
                'result':  'conflict'
            }
            return response

        # accept
        self.masterOpspecStack.extend(opspecs)

        headId = self.getHeadId()

        if self._genesisSent and self._remoteConnected:
            # send to other client
            self._sendMessageToRemoteClient('NewOps', {
                'opspecs':  opspecs,
                'headId': headId
            })

        response = {
            'result': 'success',
            'headId': headId
        }
        return response

    def handleReconnection(self, stream):
        self.stream = stream
        self._genesisSent = False
        self._remoteReconnected = True

    # IObserver API
    def handle_notification(self, notification):
        handler = getattr(self, "_NH_%s" % notification.name, None)
        if handler:
            handler(notification)

    def _NH_DocumentSharingStreamConnected(self, notification):
        if(notification.sender != self.stream):
            return

        self._remoteConnected = True

    def _NH_DocumentSharingStreamDisconnected(self, notification):
        if(notification.sender != self.stream):
            return

        self._remoteConnected = False

    def _NH_DocumentSharingStreamNewOps(self, notification):
        if(notification.sender != self.stream):
            return

        newOps = notification.data.content

        # check if ops are based on current master
        if newOps['baseHeadId'] != self.getHeadId():
            # too bad, not based on latest state, so reject
            self._sendMessageToRemoteClient('NewOpsReply', {
                'result':  'conflict'
            })
            return

        # accept
        opspecs = newOps['opspecs']
        self.masterOpspecStack.extend(opspecs)
        headId = self.getHeadId()
        # and confirm to remote client
        self._sendMessageToRemoteClient('NewOpsReply', {
            'result': 'success',
            'headId': headId
        })
        # extend info
        newOps['headId'] = headId

        # pass to local client
        self.localClient.handleNewOpspecsFromServer(newOps)

    def _NH_DocumentSharingStreamInitRequest(self, notification):
        if(notification.sender != self.stream):
            return

        # TODO: injecting this op should be done when the other person is accepted to the session, not here
        initData = notification.data.content
        continueSession = initData["continueSession"]
        self._genesisSent = continueSession
        print "InitRequest - continueSession: ", continueSession
        if continueSession:
            baseHeadId = initData["baseHeadId"]
            opspecs = self.masterOpspecStack[int(baseHeadId):]
            print "InitRequest - sending ops starting at: ", int(baseHeadId), " op count: ", len(opspecs)
            self._sendMessageToRemoteClient('InitRequestReply', {
                'opspecs':  opspecs,
                'headId': self.getHeadId()
            })
        else:
            self.stream.sendGenesisFile(self.url)

    def _NH_DocumentSharingStreamReplayRequest(self, notification):
        if(notification.sender != self.stream):
            return

        # TODO: injecting this op should be done when the other person is accepted to the session, not here
        memberData = notification.data.content
        memberId = memberData['memberId']
        op = None
        if self._remoteReconnected:
            # TODO: check if there is a cursor to remove
            op = {
                'optype': 'RemoveCursor',
                'timestamp': 0,
                'memberid':  memberId
            }
        else:
            op = {
                'optype': 'AddMember',
                'timestamp': 0,
                'memberid':  memberId,

                'setProperties': {
                    'fullName': memberData['fullName'],
                    'color':    self._colorFromAccountId(memberData['color']),
                    'imageUrl': memberData['imageUrl']
                }
            }
        self.masterOpspecStack.append(op)

        headId = self.getHeadId()

        # do the actual task, send over the complete current op stack
        self._sendMessageToRemoteClient('ReplayRequestReply', {
            'opspecs':  self.getAllOpspecs(),
            'headId':   headId
        })
        self._genesisSent = True

        # also inform local client
        newOpspecs = {
            'opspecs':  [op],
            'headId':   headId
        }
        self.localClient.handleNewOpspecsFromServer(newOpspecs)

    def _colorFromAccountId(self, accountId):
        # copied from chatwindow.py, hoping for consistent colors
        return self.__colors__[hash(accountId) % len(self.__colors__)]
