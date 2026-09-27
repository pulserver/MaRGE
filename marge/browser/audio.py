"""A scan's sound in a browser tab, through the Web Audio API."""

from __future__ import annotations

import numpy as np


class Speaker:
    """Play sound as it streams, each span after the one before.

    A span that arrives after its predecessor has finished starts at once.
    The browser starts the audio once the page has had a user gesture.
    """

    def __init__(self) -> None:
        from js import AudioContext

        self._context = AudioContext.new()
        self._next = 0.0
        #: Seconds of sound scheduled so far.
        self.played = 0.0

    def __call__(self, samples: np.ndarray, rate: float) -> None:
        """Schedule ``samples``, ``(n, channels)`` in [-1, 1], at ``rate`` Hz."""
        from pyodide.ffi import to_js

        if len(samples) == 0:
            return
        if self._context.state == "suspended":
            self._context.resume()
        buffer = self._context.createBuffer(samples.shape[1], len(samples), rate)
        for channel in range(samples.shape[1]):
            data = np.ascontiguousarray(samples[:, channel], dtype=np.float32)
            buffer.copyToChannel(to_js(data), channel)
        source = self._context.createBufferSource()
        source.buffer = buffer
        source.connect(self._context.destination)
        start = max(self._next, self._context.currentTime)
        source.start(start)
        self._next = start + len(samples) / rate
        self.played += len(samples) / rate
