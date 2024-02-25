
from aqt import QDialog
def check_exec(dialog:QDialog):
    if hasattr(dialog, 'exec_'):
        return dialog.exec_()
    else:
        return dialog.exec()


is_leech_update = False

def shigeLeech_update():
    global is_leech_update
    is_leech_update = True

def shigeLeech_return():
    return is_leech_update

def shigeLeech_reset():
    global is_leech_update
    is_leech_update = False