import functools
import importlib
import multiprocessing
import queue
import threading
from contextvars import ContextVar

from django.conf import settings

from .exceptions import ProcessingError


_IN_PROCESSING_CHILD = ContextVar('in_processing_child', default=False)


class ProcessingAdmission:
    _global = threading.BoundedSemaphore(settings.MAX_CONCURRENT_PROCESSING)
    _clients = {}
    _clients_lock = threading.Lock()

    @classmethod
    def _client_semaphore(cls, identity):
        with cls._clients_lock:
            return cls._clients.setdefault(
                identity, threading.BoundedSemaphore(settings.MAX_CONCURRENT_PER_CLIENT)
            )

    @classmethod
    def acquire(cls, identity):
        acquired_global = cls._global.acquire(timeout=settings.PROCESSING_ACQUIRE_TIMEOUT_SECONDS)
        if not acquired_global:
            return False
        client = cls._client_semaphore(identity)
        acquired_client = client.acquire(timeout=settings.PROCESSING_ACQUIRE_TIMEOUT_SECONDS)
        if not acquired_client:
            cls._global.release()
            return False
        return True

    @classmethod
    def release(cls, identity):
        client = cls._client_semaphore(identity)
        client.release()
        cls._global.release()


def _processing_worker(result_queue, module_name, function_name, args, kwargs):
    _IN_PROCESSING_CHILD.set(True)
    try:
        function = getattr(importlib.import_module(module_name), function_name)
        result_queue.put(('ok', function(*args, **kwargs)))
    except Exception as exc:
        result_queue.put(('error', str(exc)))


def run_with_timeout(function, *args, timeout=None, **kwargs):
    """Run untrusted document processing outside the web worker process."""
    if _IN_PROCESSING_CHILD.get():
        return function(*args, **kwargs)

    timeout = timeout or settings.PROCESSING_TIMEOUT_SECONDS
    module_name = function.__module__
    function_name = getattr(function, '__pdfino_worker_name__', function.__name__)
    context = multiprocessing.get_context('spawn')
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_processing_worker,
        args=(result_queue, module_name, function_name, args, kwargs),
        daemon=True,
    )
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise ProcessingError('Processing took too long and was stopped. Please try a smaller file.')

    try:
        status, value = result_queue.get(timeout=1)
    except queue.Empty as exc:
        raise ProcessingError('Processing stopped unexpectedly. Please try again.') from exc
    finally:
        result_queue.close()
        result_queue.join_thread()

    if status == 'error':
        raise ProcessingError(value or 'The document could not be processed.')
    return value


def guarded(function):
    worker_name = f'_pdfino_worker_{function.__name__}'
    module = importlib.import_module(function.__module__)
    setattr(module, worker_name, function)

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return run_with_timeout(function, *args, **kwargs)
    function.__pdfino_worker_name__ = worker_name
    wrapper.__pdfino_worker_name__ = worker_name
    return wrapper