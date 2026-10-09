"""Test pywinauto clicking on all HBSys controls by text label."""
from pywinauto import Desktop
import time

print("=" * 60)
print("PYWINAUTO HBSYS CONTROL VERIFICATION")
print("=" * 60)

# 1. Find HBSys window using Desktop
d = Desktop()
hbsys_window = None
for w in d.windows():
    txt = w.window_text()
    if 'HBSys' in txt:
        hbsys_window = w
        break

if hbsys_window is None:
    print("\nHBSys window not found!")
    print("Make sure HBSys is open.")
    exit(1)

print("\nFound HBSys window: '%s'" % (hbsys_window.window_text()))

# 2. Get the main dialog (the DialogWrapper)
try:
    dlg = hbsys_window
    print("Main dialog acquired")
except Exception as e:
    print("\nFailed to acquire main dialog: %s" % e)
    exit(1)

children = dlg.children()
print("\nTotal children found: %d" % len(children))

# Filter to only controls with visible text (skip MDIClient, empty labels)
clickable = []
for i, child in enumerate(children):
    ctxt = child.window_text()
    if ctxt.strip() and ctxt != '':
        clickable.append((i, child, ctxt))
    else:
        print("  [%d] SKIP (empty text): %s" % (i, child.Class))

print("\nTesting %d controls with text labels..." % len(clickable))

# Try clicking each one
results = []
for i, child, ctxt in clickable:
    try:
        print("  Trying index [%d]: '%s' ..." % (i, ctxt), end=" ")
        clicked = False
        
        # Method 1: Click using the child directly
        try:
            child.click()
            clicked = True
        except Exception:
            pass
        
        if not clicked:
            # Method 2: Try finding by text pattern - re-enumerate
            try:
                # Re-get children since window state may have changed
                children2 = dlg.children()
                for c in children2:
                    if c.window_text() == ctxt:
                        c.click()
                        clicked = True
                        break
            except:
                pass
        
        if clicked:
            time.sleep(0.3)
            try:
                # Check if window still exists
                current_text = d.window_text()
                results.append((i, ctxt, "SUCCESS"))
                print("SUCCESS")
            except:
                results.append((i, ctxt, "CLICKED_AWAY"))
                print("Window may have closed")
        else:
            results.append((i, ctxt, "FAILED"))
            print("FAILED")
            
    except Exception as e:
        results.append((i, ctxt, "ERROR: %s" % str(e)[:30]))
        print("ERROR: %s" % str(e)[:30])

# Summary
print("\n" + "=" * 60)
print("SUMMARY OF CLICK RESULTS")
print("=" * 60)

success = sum(1 for r in results if r[2] == "SUCCESS")
failed = sum(1 for r in results if r[2] in ("FAILED", "ERROR"))
clicked_away = sum(1 for r in results if r[2] == "CLICKED_AWAY")

print("Successful clicks: %d" % success)
print("Failed clicks: %d" % failed)
print("Clicks that closed window: %d" % clicked_away)
print("Total tested: %d" % len(results))

if success > 0:
    print("\nWORKING controls (pywinauto clickable by text):")
    for idx, txt, status in results:
        if status == "SUCCESS":
            print("   - Index %d: '%s'" % (idx, txt))

if failed > 0:
    print(f"\nNOT working (need pyautogui fallback):")
    for idx, txt, status in results:
        if status in ("FAILED", "ERROR"):
            print("   - Index %d: '%s'" % (idx, txt))