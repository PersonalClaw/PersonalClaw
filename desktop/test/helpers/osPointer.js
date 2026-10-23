"use strict";

/**
 * Real pointer input, posted to the X server the way a mouse sends it (#5216), so a test sees
 * where the operating system delivers a click: which window takes it, and which it passes through.
 *
 * Only on a display of the test's own, the one `xvfb-run` starts for the run: real input moves the
 * pointer and clicks, and on a desktop someone is using it would land in their windows. Posted with
 * the X server's XTEST extension from Python's ctypes, through the libX11 and libXtst Electron
 * already loads on Linux, so it needs nothing installed.
 *
 * `postPointer({sweep, clicks})`: moves the pointer over every pixel of `sweep` (`{x, y, width,
 * height}`, row by row), then clicks the left button at each `[x, y]` of `clicks`, in screen
 * points. Returns `{ok: true}`, or `{ok: false, error}` with why it could not.
 *
 * `stacking()`: the root window's children, bottom to top, as X window ids: with no window
 * manager on the run's display, which window is above which is the order they were created or
 * raised in, and nothing keeps an always-on-top window above.
 *
 * `shapeOf(id)`: the window's bounding and input regions as the X server holds them (SHAPE
 * extension, through libXext), each a list of `[x, y, width, height]` in the window's own points.
 */

const { spawnSync } = require("node:child_process");

const XTEST = `
import ctypes, ctypes.util, json, sys, time
plan = json.load(sys.stdin)
x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
xt = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
xt.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
d = x11.XOpenDisplay(None)
if not d:
    sys.exit("cannot open the X display")
sweep = plan.get("sweep")
if sweep:
    for y in range(sweep["y"], sweep["y"] + sweep["height"]):
        for x in range(sweep["x"], sweep["x"] + sweep["width"]):
            xt.XTestFakeMotionEvent(d, -1, x, y, 0)
        x11.XSync(d, 0)
for x, y in plan.get("clicks", []):
    xt.XTestFakeMotionEvent(d, -1, x, y, 0)
    xt.XTestFakeButtonEvent(d, 1, 1, 0)
    xt.XTestFakeButtonEvent(d, 1, 0, 0)
    x11.XSync(d, 0)
    time.sleep(0.003)
x11.XCloseDisplay(d)
`;

const STACKING = `
import ctypes, ctypes.util, json, sys
x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
x11.XFree.argtypes = [ctypes.c_void_p]
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
d = x11.XOpenDisplay(None)
if not d:
    sys.exit("cannot open the X display")
root, parent, children, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
x11.XQueryTree(d, x11.XDefaultRootWindow(d), ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(n))
print(json.dumps([children[i] for i in range(n.value)]))
if n.value:
    x11.XFree(children)
x11.XCloseDisplay(d)
`;

const SHAPE = `
import ctypes, ctypes.util, json, sys
class Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]
x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XFree.argtypes = [ctypes.c_void_p]
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
xext.XShapeGetRectangles.restype = ctypes.POINTER(Rect)
xext.XShapeGetRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
d = x11.XOpenDisplay(None)
if not d:
    sys.exit("cannot open the X display")
out = {}
for name, kind in (("bounding", 0), ("input", 2)):
    count, ordering = ctypes.c_int(), ctypes.c_int()
    rects = xext.XShapeGetRectangles(d, int(sys.argv[1]), kind, ctypes.byref(count), ctypes.byref(ordering))
    out[name] = [[rects[i].x, rects[i].y, rects[i].width, rects[i].height] for i in range(count.value)]
    if rects:
        x11.XFree(rects)
x11.XCloseDisplay(d)
print(json.dumps(out))
`;

/** The window's bounding and input regions, as the X server holds them, or `{error}`. */
function shapeOf(id) {
  if (process.platform !== "linux") return { error: `reads X shapes only, not on ${process.platform}` };
  const res = spawnSync("python3", ["-I", "-c", SHAPE, String(id)], { encoding: "utf8", timeout: 30_000 });
  if (res.error || res.status !== 0) return { error: String((res.error && res.error.message) || res.stderr || `exit ${res.status}`).trim() };
  return JSON.parse(res.stdout);
}

/** The root window's children, bottom to top, or `{error}`. */
function stacking() {
  if (process.platform !== "linux") return { error: `reads X stacking only, not on ${process.platform}` };
  const res = spawnSync("python3", ["-I", "-c", STACKING], { encoding: "utf8", timeout: 30_000 });
  if (res.error || res.status !== 0) return { error: String((res.error && res.error.message) || res.stderr || `exit ${res.status}`).trim() };
  return { ids: JSON.parse(res.stdout) };
}

/** A window's X id, from Electron's native handle. */
const xid = (win) => win.getNativeWindowHandle().readUInt32LE(0);

function postPointer(plan) {
  if (process.platform !== "linux") return { ok: false, error: `posts X input only, not on ${process.platform}` };
  const res = spawnSync("python3", ["-I", "-c", XTEST], { input: JSON.stringify(plan), encoding: "utf8", timeout: 300_000 });
  if (res.error) return { ok: false, error: String(res.error.message || res.error) };
  if (res.status !== 0) return { ok: false, error: (res.stderr || "").trim().split("\n").slice(-3).join(" ") || `exit ${res.status}` };
  return { ok: true };
}

module.exports = { postPointer, shapeOf, stacking, xid };
