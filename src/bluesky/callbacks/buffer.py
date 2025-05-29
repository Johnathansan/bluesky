import atexit
import logging
import threading
from queue import Empty, Queue

logger = logging.getLogger(__name__)


class BufferingWrapper:
    """A wrapper for callbacks that processes documents in a separate thread.

    This class allows a callback to be executed in a background thread, processing
    documents as they are received. This prevent the blocking of RunEngine on any
    slow I/O operations by the callback. It handles graceful shutdown on exit or signal
    termination, ensuring that no new documents are accepted after shutdown has been
    initiated.

    The wrapped callback should be thread-safe and not subscribed to the RE directly.
    If it maintains shared mutable state, it must protect it using internal locking.

    This is mainly a development feature to allow subscribing (potentially many)
    experimental callbacks to a `RunEngine` without the risk of blocking the experiment.
    The use in production is currently not encouraged (at least not without a proper
    testing and risk assessment).

    Parameters
    ----------
        target : callable
            The instance of a callback that will be called with the documents.
            It should accept two parameters: `name` and `doc`.

    Usage
    -----
        tw = TiltedWriter(client)
        buff_tw = BufferingWrapper(tw)
        RE.subscribe(buff_tw)
    """

    def __init__(self, target, queue_size=1_000_000):
        self._wrapped_callback = target
        self._queue = Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._shutdown_lock = threading.Lock()

        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self._thread.start()

        atexit.register(self.shutdown)

    def __call__(self, name, doc):
        if self._stop_event.is_set():
            raise RuntimeError("Cannot accept new data after shutdown.")
        try:
            self._queue.put((name, doc))
        except Exception as e:
            logger.exception(f"Failed to put document {name} in queue: {e}")

    def _process_queue(self):
        while True:
            try:
                if item := self._queue.get(timeout=1):
                    self._wrapped_callback(*item)  # Delegate to wrapped callback
                else:
                    break  # Received sentinel value to stop processing
            except Empty:
                if self._stop_event.is_set():
                    break
            except Exception as e:
                logger.exception(f"Exception in {self._wrapped_callback.__class__.__name__}: {e}")

    def shutdown(self, wait=True):
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._queue.put(None)

        atexit.unregister(self.shutdown)

        if wait:
            self._thread.join()
        print(f"{self._wrapped_callback.__class__.__name__} shut down gracefully.")
