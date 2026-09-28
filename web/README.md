# MaRGE in the browser

MaRGE runs in a browser tab as the console of
[pulserver](https://github.com/pulserver/pulserver)'s virtual scanner. The
parameters and the prescription set in MaRGE travel to `pulserver console` in
the interpreter's text blocks; pulserver designs the sequence, builds its IR,
plays it on a phantom and reconstructs it, and the images return to MaRGE as
DICOM. The subject's name chooses the phantom (`brainweb`, or the vials
otherwise), and each exam opens on its three-plane localizer, drawn from the
phantom's ground truth.

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

Start a console, with a reconstruction proxy behind it, and open the page with
its address:

```
http://127.0.0.1:8000/index.html?console=ws://127.0.0.1:8765
```

Without `console`, the page connects to port 8765 on its own host.
pulserver's `docker/compose.yaml` starts the console with Gadgetron behind the
proxy.

## What differs from MaRGE on a desktop

- MaRGE's sequences are pulserver's scanner-sequence plugins and its
  localizer; its own sequences are hidden, and no MaRCoS hardware is driven.
- The session starts configured with a project, a study, a Red Pitaya address
  and an RF coil, which the virtual scanner does not use.
- The tab has no threads, processes or serial ports (`marge/browser`): a
  thread runs on the event loop, and the modules without a WebAssembly build
  import as stand-ins that raise when used.
- A dialog does not wait for its answer: a message box answers Ok, and a file
  dialog or a text prompt answers as if cancelled, since the tab's file system
  is its own and lasts as long as the tab.

## Test

`tests/test_browser.py` drives the page in Chromium from the exam to a
reconstructed scan, given `MARGE_WEB` and `MARGE_PULSERVER`; the Browser
workflow runs it against a console built from pulserver's main branch.

## Licences

The PyQt6 build of Pyodide
([pyodide-with-pyqt6](https://github.com/JarrettSJohnson/pyodide-with-pyqt6))
carries Qt 6 and PyQt6 under the GPL v3, which therefore covers the assembled
page.
