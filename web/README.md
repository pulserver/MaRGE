# MaRGE in the browser

MaRGE runs in a browser tab as the console of
[pulserver](https://github.com/pulserver/pulserver)'s virtual scanner. The
parameters and the prescription set in MaRGE travel to `pulserver console` in
the interpreter's text blocks; pulserver designs the sequence, builds its IR,
plays it on a phantom and reconstructs it, and the images return to MaRGE as
DICOM. The subject's name chooses the phantom (`brainweb`, or the vials
otherwise), and each exam opens on its three-plane localizer, drawn from the
phantom's ground truth.

## Open it

The page is served at <https://pulserver.github.io/MaRGE/>. pulserver runs
beside it in Docker, on the same computer; with Docker running, start it once:

```bash
docker run -d --restart unless-stopped --name pulserver -p 127.0.0.1:8765:8765 ghcr.io/pulserver/pulserver
```

The page shows this command, with a launcher for Windows that runs it and
opens the page again, whenever nothing answers on port 8765, and connects as
soon as pulserver does. Docker starts the container again with itself, so the
page connects at once on later visits. The image carries pulserver with
bartorch, which samples the head coils, and reconstructs each scan in its own
process. `docker rm -f pulserver` removes it; running the command again after
`docker pull ghcr.io/pulserver/pulserver` updates it, as the launcher does.
Browsers that restrict public pages' access to local services ask the viewer's
permission first.

## Build and serve

```bash
python web/build.py
python -m http.server 8000 --directory web/dist
```

`web/build.py` assembles `web/dist`: the PyQt6 build of Pyodide 0.29.3, the
packages of the official Pyodide release that MaRGE imports, the pure-Python
wheels of MaRGE's other dependencies at the versions of `uv.lock`, and MaRGE
itself. `--pyodide DIR` takes the packages from an unpacked Pyodide release
instead of its CDN.

Open `http://127.0.0.1:8000/index.html` with pulserver's image running as
above, whose console serves pages from this address and from the hosted page.
Another console is named with its address:

```
http://127.0.0.1:8000/index.html?console=ws://127.0.0.1:8765
```

Without `console`, a page served over HTTP connects to port 8765 on its own
host, and a page served over HTTPS to `ws://localhost:8765`, since such a page
may open an unencrypted WebSocket only to the computer it runs on. A console
on another computer is reached from it only over `wss://`, for example behind
a reverse proxy that terminates TLS, and named as `?console=wss://HOST/PATH`.
pulserver's `docker/compose.yaml` starts a console with Gadgetron behind its
reconstruction proxy.

## What differs from MaRGE on a desktop

- MaRGE's sequences are pulserver's scanner-sequence plugins and its
  localizer; its own sequences are hidden, and no MaRCoS hardware is driven.
- The session starts configured with a project, a study and a Red Pitaya
  address, which the virtual scanner does not use, and with the virtual
  scanner's coils as its RF coils: the one selected is the coil each exam is
  scanned in.
- The tab has no threads, processes or serial ports (`marge/browser`): a
  thread runs on the event loop, and the modules without a WebAssembly build
  import as stand-ins that raise when used.
- A dialog does not wait for its answer: a message box answers Ok, and a file
  dialog or a text prompt answers as if cancelled, since the tab's file system
  is its own and lasts as long as the tab.

## Test

`tests/test_browser.py` drives the page in Chromium from the exam to a
reconstructed scan, given `MARGE_WEB` and `MARGE_PULSERVER`, and checks that a
page with no console answering waits for one and says how to start it; the
Browser workflow runs it against pulserver's image built from its main branch.

## Licences

The PyQt6 build of Pyodide
([pyodide-with-pyqt6](https://github.com/JarrettSJohnson/pyodide-with-pyqt6))
carries Qt 6 and PyQt6 under the GPL v3, which therefore covers the assembled
page.
