# MaRGE in the browser

MaRGE runs in a browser tab as the console of
[pulserver](https://github.com/pulserver/pulserver)'s virtual scanner. The
parameters and the prescription set in MaRGE travel to `pulserver console` in
the interpreter's text blocks; pulserver designs the sequence, builds its IR,
plays it on BrainWeb's normal brain, in coils whose fields are solved in that
brain, and reconstructs it, and the images return to MaRGE as DICOM. Each exam
opens on its three-plane localizer, drawn from the brain's ground truth.

## Open it

The page is served at <https://pulserver.github.io/MaRGE/>. pulserver runs
beside it in Docker, on the same computer, started by a launcher the page
offers for download with the scanner settings below written into it:
`pulserver.bat` on Windows, run by double-clicking it, and `pulserver.sh` on
macOS and Linux, run with `sh pulserver.sh`. Neither needs anything but
Docker.

The launcher stops with a message when Docker is not installed (naming its
installation page) or not running. It then reports whether pulserver's image
`ghcr.io/pulserver/pulserver` was absent and is downloaded, was outdated and is
updated, or is up to date, comparing the local image with the published one
through `docker pull`; when the registry does not answer it starts the image
already on the computer. It (re)creates the container with the settings and
opens the page, whose landing view shows whether the console answers on port
8765 and unlocks *Open MaRGE* once it does. Docker starts the container again
with itself, so the launcher is needed again only to change the settings or to
update pulserver. `docker rm -f pulserver` removes it. Browsers that restrict
public pages' access to local services ask the viewer's permission first.

The image carries pulserver with BrainWeb's normal brain, the coils' field
maps solved in it by [mariepy](https://github.com/pulserver/mariepy), and
bartorch, and reconstructs each scan in its own process.

### Scanner settings and plugins

The landing view edits what MaRGE's console does not set and keeps it in the
browser, starting from the image's scanner: B0, the gradient and slew limits
with their units, the design limits per logical axis (the scanner's divided by
√3 when left out), the rasters and dead times, forbidden frequency bands (an
axis `x`, `y`, `z` or `all`, the lowest and highest frequency in Hz, and
optionally the largest amplitude in mT/m), the chronaxie nerve model
(chronaxie, rheobase, alpha and the largest response), and any further line of
the limits block, such as a SAFE nerve model's coefficients. The launcher
writes them as the limits block of `pulserver console`, in seconds and the
units named, to `~/.pulserver/limits.txt` (`%USERPROFILE%\.pulserver` on
Windows; `PULSERVER_HOME` names another directory), mounted over the image's
own.

The two plugin directories name the user's own scanner-sequence plugins and
reconstruction plugins, each `<name>.py`. Each is mounted read-only where the
image's console searches before its own plugins, so MaRGE's sequence list
offers the user's sequences beside pulserver's, and a file named as one of
pulserver's takes its place.

## Scan

1. In the session window, *Subject name* names the exam, which is on
   BrainWeb's normal brain whatever the name.
   *RF coil* is the coils the exam is scanned in, named `transmit/receive`:
   `body`, `body/head48` or `head8/head32`. *Launch GUI* opens the exam on its
   three-plane localizer.
2. The list at the top of the *Custom* tab selects the sequence: one of
   pulserver's scanner-sequence plugins, `gre2d`, `gre_multiecho2d`, `se2d`,
   `bssfp2d`, `gre_radial2d` and `gre_spiral2d`, or the `Localizer`. The *Sequence*
   tab holds the selected sequence's protocol, and the *Image* tab its field of
   view, orientation, FOV centre and rotation.
3. The *FOV* button of a localizer plane draws the field of view on it;
   dragging the box moves it and dragging its corner handle resizes it, which
   sets the field of view and its centre of every sequence.
4. *Acquire*, the ▷| button of the sequence toolbar, scans the selected
   sequence. pulserver simulates the scan ahead of its clock, and the status
   bar shows *preparing* with the time left until the simulation is far enough
   ahead for the clock to run as a scanner's would, without stopping. The
   status bar then shows the scan clock, the sound plays as the scan streams,
   and the reconstruction is drawn and listed in the history on the right,
   where double-clicking a scan draws it again. *Localizer*, the map pin,
   draws the localizer again.
5. Closing the main window returns to the session window, where another
   subject starts another exam.

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
  *Acquire* and *Localizer* run on the console at once, and the waiting list,
  the autocalibration and the sequence plot, which need the MaRCoS server or
  MaRGE's own sequences, are disabled.
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

`tests/test_launcher.py` runs the launchers against a stand-in for Docker,
the shell one on Linux and the batch one on Windows. `tests/test_browser.py` drives the page in Chromium from the exam to a
reconstructed scan and on to another exam, given `MARGE_WEB` and
`MARGE_PULSERVER`, checks that a page with no console answering waits for
one, and checks that the landing view writes its scanner into both launchers; the Browser workflow runs it against pulserver's
image built from its main branch.

## Licences

The PyQt6 build of Pyodide
([pyodide-with-pyqt6](https://github.com/JarrettSJohnson/pyodide-with-pyqt6))
carries Qt 6 and PyQt6 under the GPL v3, which therefore covers the assembled
page.
