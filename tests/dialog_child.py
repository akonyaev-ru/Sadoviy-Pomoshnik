"""Подделка окна «Действительно закрыть» для проверок `confirm_quit_dialog`.

Настоящий диалог Windows (класс `#32770`, как у приложения upjers Home после
обновления 2026-09-18) с двумя кнопками. Подписи берутся из аргументов —
по умолчанию те же, что у приложения: «Отменить» и «Закрыть». Третий
аргумент — текст вопроса: так подделывается и окно «всё равно стартовать
игру?» при нехватке памяти. Печатает номер нажатой кнопки: 101 — первая,
102 — вторая.
"""
import ctypes, sys
from ctypes import wintypes

class TASKDIALOG_BUTTON(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("nButtonID", ctypes.c_int), ("pszButtonText", ctypes.c_wchar_p)]

class TASKDIALOGCONFIG(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("hwndParent", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", ctypes.c_int), ("dwCommonButtons", ctypes.c_int),
        ("pszWindowTitle", ctypes.c_wchar_p), ("pszMainIcon", ctypes.c_void_p),
        ("pszMainInstruction", ctypes.c_wchar_p), ("pszContent", ctypes.c_wchar_p),
        ("cButtons", ctypes.c_uint), ("pButtons", ctypes.POINTER(TASKDIALOG_BUTTON)),
        ("nDefaultButton", ctypes.c_int), ("cRadioButtons", ctypes.c_uint),
        ("pRadioButtons", ctypes.c_void_p), ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", ctypes.c_wchar_p), ("pszExpandedInformation", ctypes.c_wchar_p),
        ("pszExpandedControlText", ctypes.c_wchar_p), ("pszCollapsedControlText", ctypes.c_wchar_p),
        ("pszFooterIcon", ctypes.c_void_p), ("pszFooter", ctypes.c_wchar_p),
        ("pfCallback", ctypes.c_void_p), ("lpCallbackData", ctypes.c_void_p), ("cxWidth", ctypes.c_uint),
    ]

podpisi = sys.argv[1:3] if len(sys.argv) >= 3 else ["Отменить", "Закрыть"]
vopros = sys.argv[3] if len(sys.argv) >= 4 else "Закроется сам."
buttons = (TASKDIALOG_BUTTON * 2)(TASKDIALOG_BUTTON(101, podpisi[0]), TASKDIALOG_BUTTON(102, podpisi[1]))
cfg = TASKDIALOGCONFIG()
cfg.cbSize = ctypes.sizeof(cfg)
cfg.pszWindowTitle = "Действительно закрыть"
cfg.pszMainInstruction = "Проверочный диалог помощника"
cfg.pszContent = vopros
cfg.cButtons = 2
cfg.pButtons = buttons
cfg.nDefaultButton = 101
pressed = ctypes.c_int(0)
hr = ctypes.windll.comctl32.TaskDialogIndirect(ctypes.byref(cfg), ctypes.byref(pressed), None, None)
sys.stdout.reconfigure(encoding="utf-8")
print(hr, pressed.value)
sys.exit(0 if hr == 0 else 1)
