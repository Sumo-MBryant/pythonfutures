#!/usr/bin/env python

from futures._base import (PENDING, RUNNING, CANCELLED,
                           CANCELLED_AND_NOTIFIED, FINISHED,
                           ALL_COMPLETED,
                           LOGGER,
                           set_future_exception, set_future_result,
                           Executor, Future, FutureList, ThreadEventSink)
import Queue
import threading

class _WorkItem(object):
    def __init__(self, call, future, completion_tracker):
        self.call = call
        self.future = future
        self.completion_tracker = completion_tracker

    def run(self):
        self.future._condition.acquire()
        try:
            if self.future._state == PENDING:
                self.future._state = RUNNING
            elif self.future._state == CANCELLED:
                self.completion_tracker._condition.acquire()
                try:
                    self.future._state = CANCELLED_AND_NOTIFIED
                    self.completion_tracker.add_cancelled()
                    return
                finally:
                    self.completion_tracker._condition.release()
            else:
                LOGGER.critical('Future %s in unexpected state: %d',
                                id(self.future),
                                self.future._state)
                return
        finally:
            self.future._condition.release()

        try:
            result = self.call()
        except Exception, e:
            set_future_exception(self.future, self.completion_tracker, e)
        else:
            set_future_result(self.future, self.completion_tracker, result)

class ThreadPoolExecutor(Executor):
    def __init__(self, max_threads):
        self._max_threads = max_threads
        self._work_queue = Queue.Queue()
        self._threads = set()
        self._shutdown = False
        self._shutdown_lock = threading.Lock()

    def _worker(self):
        empty = Queue.Empty
        try:
            while True:
                try:
                    work_item = self._work_queue.get(block=True, timeout=0.1)
                except empty:
                    if self._shutdown:
                        return
                else:
                    work_item.run()
        except BaseException, e:
            LOGGER.critical('Exception in worker', exc_info=True)

    def _adjust_thread_count(self):
        for _ in range(len(self._threads),
                       min(self._max_threads, self._work_queue.qsize())):
            t = threading.Thread(target=self._worker)
            t.start()
            self._threads.add(t)

    def run_to_futures(self, calls, timeout=None, return_when=ALL_COMPLETED):
        self._shutdown_lock.acquire()
        try:
            if self._shutdown:
                raise RuntimeError('cannot run new futures after shutdown')

            futures = []
            event_sink = ThreadEventSink()
            for index, call in enumerate(calls):
                f = Future(index)
                w = _WorkItem(call, f, event_sink)
                self._work_queue.put(w)
                futures.append(f)
    
            self._adjust_thread_count()
            fl = FutureList(futures, event_sink)
            fl.wait(timeout=timeout, return_when=return_when)
            return fl
        finally:
            self._shutdown_lock.release()

    def shutdown(self):
        self._shutdown_lock.acquire()
        try:
            self._shutdown = True
        finally:
            self._shutdown_lock.release()
