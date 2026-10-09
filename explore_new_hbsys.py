"""Explore HBSYS_NEW (Ribbon version) controls."""
from pywinauto import Desktop
import time

time.sleep(2)

# Find HBSYS_NEW window
d = Desktop()
hbsys_new = None
for w in d.windows():
    txt = w.window_text()
    if 'HBSYS_NEW' in txt:
        hbsys_new = w
        break

if hbsys_new is None:
    print("HBSYS_NEW window not found!")
    exit(1)

print("HBSYS_NEW window found!")
print("Title:", hbsys_new.window_text())
print("Class:", hbsys_new.Class)
print("Size:", hbsys_new.rectangle())
print()

children = hbsys_new.children()
print("Total children: %d" % len(children))
print()

# Show only controls with visible text
for i, child in enumerate(children):
    ctxt = child.window_text()
    if ctxt.strip():
        try:
            is_enabled = child.is_enabled()
        except:
            is_enabled = "?"
        try:
            has_focus = child.is_focused()
        except:
            has_focus = "?"
        print("  [%d] %s (enabled=%s, focused=%s)" % (i, ctxt[:50], is_enabled, has_focus))