"""Test clicking HBSYS_NEW Ribbon and TreeView controls."""
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

children = hbsys_new.children()

# Try clicking the Ribbon (index 3)
print("Trying to click Ribbon at index 3...")
try:
    children[3].click()
    time.sleep(1)
    print("  SUCCESS: Clicked Ribbon index 3")
except Exception as e:
    print("  FAILED to click Ribbon index 3: %s" % str(e)[:60])

# Try clicking Navigation buttons (index 11)
print("\nTrying to click Navigation buttons at index 11...")
try:
    children[11].click()
    time.sleep(1)
    print("  SUCCESS: Clicked Navigation buttons index 11")
except Exception as e:
    print("  FAILED to click Navigation buttons: %s" % str(e)[:60])

# Try clicking Tree View (index 34)
print("\nTrying to click Tree View at index 34...")
try:
    children[34].click()
    time.sleep(1)
    print("  SUCCESS: Clicked Tree View index 34")
    # Check if new children appeared
    new_children = hbsys_new.children()
    print("  New children count: %d" % len(new_children))
except Exception as e:
    print("  FAILED to click Tree View: %s" % str(e)[:60])

# Try clicking ShellView (index 43) - this is the main grid
print("\nTrying to click ShellView at index 43...")
try:
    children[43].click()
    time.sleep(1)
    print("  SUCCESS: Clicked ShellView index 43")
except Exception as e:
    print("  FAILED to click ShellView: %s" % str(e)[:60])