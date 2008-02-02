import datetime
import traceback

import persistent.dict
from BTrees.Length import Length
from ZODB.POSException import ConflictError
from zope import component
from zope import interface
import zope.interface.common.mapping
import zope.lifecycleevent

import zc.queue.interfaces

from collective.singing import interfaces

class Message(object):
    interface.implements(interfaces.IMessage)

    _status = None
    
    def __init__(self, payload, subscription,
                 status=u'new', status_message=None):
        self.payload = payload
        self.subscription = subscription
        self.status_message = status_message
        self.status = status

    @apply
    def status():
        def get(self):
            return self._status
        def set(self, value):
            assert value in interfaces.MESSAGE_STATES, value
            old_status = self.status
            self._status = value
            zope.event.notify(MessageChanged(self, old_status))
            self.status_changed = datetime.datetime.now()

        return property(get, set)

class MessageQueues(persistent.dict.PersistentDict):
    interface.implements(interfaces.IMessageQueues)

    def __init__(self, *args, **kwargs):
        super(MessageQueues, self).__init__(*args, **kwargs)
        for status in interfaces.MESSAGE_STATES:
            self[status] = zc.queue.Queue()
        self._messages_sent = Length()

    @property
    def messages_sent(self):
        return self._messages_sent()

    def dispatch(self):
        sent = 0
        for name in 'new', 'retry':
            queue = self[name]
            while True:
                try:
                    message = queue.pull()
                except IndexError:
                    break
                else:
                    dispatcher = interfaces.IDispatch(message.payload)
                    try:
                        status, msg = dispatcher()
                    except ConflictError:
                        raise
                    except Exception, e:
                        # TODO: log
                        status = u'error'
                        msg = traceback.format_exc(e)

                    if status == 'sent':
                        sent += 1
                    message.status_message = msg
                    message.status = status

        self._messages_sent.change(sent)
        return sent

    def flush(self):
        for name in 'error', 'sent':
            queue = self[name]
            try:
                while True:
                    queue.pull()
            except IndexError:
                pass

class MessageChanged(zope.lifecycleevent.ObjectModifiedEvent):
    interface.implements(interfaces.IMessageChanged)

    def __init__(self, object, old_status, *descriptions):
        super(MessageChanged, self).__init__(object, *descriptions)
        self.old_status = old_status

@component.adapter(interfaces.IMessage, interfaces.IMessageChanged)
def queue_message(message, event):
    # We expect the message to be in *no queue* when we receive this
    # event!
    queue = message.subscription.channel.queue
    queue[message.status].put(message)
