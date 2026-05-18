import ctypes
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import winreg
from PIL import Image, ImageDraw
import pystray
import tkinter.font as tkfont
from zoneinfo import ZoneInfo, available_timezones


APP_NAME = "Sunsor"
CURSOR_KEY = r"Control Panel\Cursors"
SCHEMES_KEY = r"Control Panel\Cursors\Schemes"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CHECK_INTERVAL_MS = 1_000
MIN_WIDTH = 980
MIN_HEIGHT = 720
STARTUP_VALUE_NAME = "Sunsor"
STARTUP_TASK_NAME = "Sunsor Startup"
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def app_resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


RESOURCE_DIR = app_resource_dir()
USER_DATA_DIR = user_data_dir()
SETTINGS_FILE = USER_DATA_DIR / "sunsor_settings.json"
LOGO_FILE = RESOURCE_DIR / "Sunsor.png"
ICON_FILE = RESOURCE_DIR / "Sunsor.ico"
TIMEZONE_ALIASES = {
    "EEST": "Europe/Bucharest",
    "EET": "Europe/Bucharest",
    "UTC": "UTC",
    "GMT": "Etc/GMT",
}

CURSOR_ROLES = [
    "Arrow",
    "Help",
    "AppStarting",
    "Wait",
    "Crosshair",
    "IBeam",
    "NWPen",
    "No",
    "SizeNS",
    "SizeWE",
    "SizeNWSE",
    "SizeNESW",
    "SizeAll",
    "UpArrow",
    "Hand",
]

ROLE_LABELS = {
    "Arrow": "Normal Select",
    "Help": "Help Select",
    "AppStarting": "Working In Background",
    "Wait": "Busy",
    "Crosshair": "Precision Select",
    "IBeam": "Text Select",
    "NWPen": "Handwriting",
    "No": "Unavailable",
    "SizeNS": "Vertical Resize",
    "SizeWE": "Horizontal Resize",
    "SizeNWSE": "Diagonal Resize 1",
    "SizeNESW": "Diagonal Resize 2",
    "SizeAll": "Move",
    "UpArrow": "Alternate Select",
    "Hand": "Link Select",
}


def expand_path(value: str) -> str:
    return os.path.expandvars(value.strip())


def read_registry_schemes() -> dict[str, list[str]]:
    schemes: dict[str, list[str]] = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCHEMES_KEY) as key:
        count = winreg.QueryInfoKey(key)[1]
        for index in range(count):
            name, raw_value, _ = winreg.EnumValue(key, index)
            paths = [expand_path(part) for part in raw_value.split(",")[: len(CURSOR_ROLES)]]
            if len(paths) == len(CURSOR_ROLES):
                schemes[name] = paths
    return schemes


def read_current_cursor_paths() -> list[str]:
    paths: list[str] = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSOR_KEY) as key:
        for role in CURSOR_ROLES:
            value, _ = winreg.QueryValueEx(key, role)
            paths.append(expand_path(value))
    return paths


def read_active_scheme_name() -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSOR_KEY) as key:
        try:
            value, _ = winreg.QueryValueEx(key, "")
        except FileNotFoundError:
            return ""
    return value


def apply_scheme(name: str, paths: list[str]) -> None:
    if len(paths) != len(CURSOR_ROLES):
        raise ValueError("The selected profile is missing one or more cursor files.")

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSOR_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for role, path in zip(CURSOR_ROLES, paths):
            winreg.SetValueEx(key, role, 0, winreg.REG_EXPAND_SZ, path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, name)

    spi_setcursors = 0x0057
    if not ctypes.windll.user32.SystemParametersInfoW(spi_setcursors, 0, None, 0):
        raise ctypes.WinError()


def system_cursor_path(filename: str) -> str:
    return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "Cursors" / filename)


def builtin_profiles() -> dict[str, list[str]]:
    return {
        "Windows Default White": [
            system_cursor_path("arrow_i.cur"),
            system_cursor_path("help_i.cur"),
            system_cursor_path("busy_i.cur"),
            system_cursor_path("wait_i.cur"),
            system_cursor_path("cross_i.cur"),
            system_cursor_path("beam_i.cur"),
            system_cursor_path("pen_i.cur"),
            system_cursor_path("no_i.cur"),
            system_cursor_path("size4_i.cur"),
            system_cursor_path("size3_i.cur"),
            system_cursor_path("size1_i.cur"),
            system_cursor_path("size2_i.cur"),
            system_cursor_path("move_i.cur"),
            system_cursor_path("up_i.cur"),
            system_cursor_path("link_i.cur"),
        ],
        "Windows Default Dark": [
            system_cursor_path("arrow_r.cur"),
            system_cursor_path("help_r.cur"),
            system_cursor_path("busy_r.cur"),
            system_cursor_path("wait_r.cur"),
            system_cursor_path("cross_r.cur"),
            system_cursor_path("beam_r.cur"),
            system_cursor_path("pen_r.cur"),
            system_cursor_path("no_r.cur"),
            system_cursor_path("size4_r.cur"),
            system_cursor_path("size3_r.cur"),
            system_cursor_path("size1_r.cur"),
            system_cursor_path("size2_r.cur"),
            system_cursor_path("move_r.cur"),
            system_cursor_path("up_r.cur"),
            system_cursor_path("link_r.cur"),
        ],
    }


def detect_local_timezone_name() -> str:
    local_tz = datetime.now().astimezone().tzinfo
    zone_name = getattr(local_tz, "key", None)
    if zone_name:
        return zone_name
    tz_name = datetime.now().astimezone().tzname()
    return tz_name or "UTC"


def normalize_timezone_choice(choice: str) -> str:
    if choice.startswith("Auto detect"):
        return "AUTO"
    if choice == "PC local clock":
        return "LOCAL"
    return TIMEZONE_ALIASES.get(choice.upper(), choice)


def display_timezone_choice(choice: str, detected: str) -> str:
    if choice == "AUTO":
        return f"Auto detect ({detected})"
    if choice == "LOCAL":
        return "PC local clock"
    return choice


def default_settings() -> dict:
    return {
        "timezone": "AUTO",
        "day_start": "07:00",
        "night_start": "18:00",
        "day_profile": "Windows Default White",
        "night_profile": "Windows Default Dark",
        "scheduler_enabled": True,
        "show_popup_warning": True,
        "start_with_windows": True,
        "startup_privileged": False,
        "dark_theme": False,
        "custom_profiles": {},
    }


def load_settings() -> dict:
    settings = default_settings()
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            save_settings(settings)
            return settings
        settings.update({key: value for key, value in saved.items() if key in settings})
        if saved != settings:
            save_settings(settings)
        return settings
    save_settings(settings)
    return settings


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def launch_command() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    return f'"{executable}" "{Path(__file__).resolve()}"'


def set_registry_startup(enabled: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


def delete_startup_task() -> None:
    subprocess.run(
        ["schtasks", "/Delete", "/TN", STARTUP_TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def set_privileged_startup_task(enabled: bool) -> None:
    if not enabled:
        delete_startup_task()
        return

    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            STARTUP_TASK_NAME,
            "/SC",
            "ONLOGON",
            "/TR",
            launch_command(),
            "/RL",
            "HIGHEST",
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        raise RuntimeError(f"Could not create privileged startup task. {details}")


def parse_clock(text: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = text.strip().split(":")
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise ValueError("Time must look like HH:MM, for example 07:00 or 18:30.") from exc

    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Hour must be 00-23 and minute must be 00-59.")
    return hour, minute


def now_for_timezone(choice: str) -> datetime:
    if choice == "LOCAL":
        return datetime.now().astimezone()
    if choice == "AUTO":
        detected = detect_local_timezone_name()
        if detected in available_timezones():
            return datetime.now(ZoneInfo(detected))
        return datetime.now().astimezone()
    if choice in available_timezones():
        return datetime.now(ZoneInfo(choice))
    return datetime.now().astimezone()


def is_daytime(now_value: datetime, day_start: str, night_start: str) -> bool:
    day_hour, day_minute = parse_clock(day_start)
    night_hour, night_minute = parse_clock(night_start)
    current_minutes = now_value.hour * 60 + now_value.minute
    day_minutes = day_hour * 60 + day_minute
    night_minutes = night_hour * 60 + night_minute

    if day_minutes == night_minutes:
        return True
    if day_minutes < night_minutes:
        return day_minutes <= current_minutes < night_minutes
    return current_minutes >= day_minutes or current_minutes < night_minutes


def next_switch_time(now_value: datetime, day_start: str, night_start: str) -> datetime:
    day_hour, day_minute = parse_clock(day_start)
    night_hour, night_minute = parse_clock(night_start)

    candidates = []
    for hour, minute in [(day_hour, day_minute), (night_hour, night_minute)]:
        candidate = now_value.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_value:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates)


def create_tray_image() -> Image.Image:
    if LOGO_FILE.exists():
        try:
            image = Image.open(LOGO_FILE).convert("RGBA")
            return image.resize((64, 64))
        except OSError:
            pass

    image = Image.new("RGBA", (64, 64), "#101728")
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill="#f6b21a")
    draw.ellipse((23, 12, 56, 45), fill="#101728")
    return image


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def compute_window_size(root: tk.Tk) -> tuple[int, int]:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    width = max(MIN_WIDTH, min(1220, int(screen_width * 0.72)))
    height = max(MIN_HEIGHT, min(860, int(screen_height * 0.82)))
    return width, height


def apply_soft_window_style(window) -> None:
    try:
        hwnd = window.winfo_id()
        value = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass


class CustomProfileDialog:
    def __init__(self, parent: tk.Tk, title: str, initial_name: str, initial_paths: list[str]) -> None:
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("900x540")
        self.window.transient(parent)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Profile name:").pack(side="left")
        self.name_var = tk.StringVar(value=initial_name)
        ttk.Entry(header, textvariable=self.name_var, width=40).pack(side="left", padx=(8, 0))

        grid = ttk.Frame(outer)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)

        self.path_vars: dict[str, tk.StringVar] = {}
        for row, role in enumerate(CURSOR_ROLES):
            label = ROLE_LABELS.get(role, role)
            ttk.Label(grid, text=label, width=20).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            var = tk.StringVar(value=initial_paths[row] if row < len(initial_paths) else "")
            self.path_vars[role] = var
            ttk.Entry(grid, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Button(grid, text="Browse", command=lambda r=role: self.browse(r)).grid(row=row, column=2, padx=(8, 0), pady=4)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(buttons, text="Save Profile", command=self.save).pack(side="right", padx=(0, 8))

        self.window.wait_window()

    def browse(self, role: str) -> None:
        filename = filedialog.askopenfilename(
            title=f"Choose file for {ROLE_LABELS.get(role, role)}",
            filetypes=[("Cursor files", "*.cur *.ani"), ("All files", "*.*")],
        )
        if filename:
            self.path_vars[role].set(filename)

    def save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_NAME, "Give the custom profile a name.")
            return

        paths = [self.path_vars[role].get().strip() for role in CURSOR_ROLES]
        if not all(paths):
            messagebox.showwarning(APP_NAME, "Each cursor slot needs a file path.")
            return

        self.result = {"name": name, "paths": paths}
        self.window.destroy()


class SunsorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        width, height = compute_window_size(root)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.logo_image = None
        self.tray_icon = None
        self.tray_thread = None
        self.tray_started = False
        self.is_hidden_to_tray = False
        self.is_exiting = False
        self.settings_window = None
        self.popup_window = None
        self.popup_after_id = None
        self.warning_switch_signature = ""
        self.pending_switch_profile_name = ""
        self.pending_switch_profile_paths = []
        self.pending_switch_deadline = None
        self.day_preview_collapsed = False
        self.night_preview_collapsed = False
        self.preview_window = None
        self.preview_restore_after_id = None
        self.preview_restore_profile_name = ""
        self.preview_restore_paths = []
        self.preview_hover_restore_after_id = None
        self.live_preview_mode = ""
        self.day_hover_name = None
        self.night_hover_name = None
        self.day_normal_hover = None
        self.day_help_hover = None
        self.day_text_hover = None
        self.day_link_hover = None
        self.day_busy_hover = None
        self.day_move_hover = None
        self.day_resize_ns_hover = None
        self.day_resize_we_hover = None
        self.day_resize_nwse_hover = None
        self.day_resize_nesw_hover = None
        self.night_normal_hover = None
        self.night_help_hover = None
        self.night_text_hover = None
        self.night_link_hover = None
        self.night_busy_hover = None
        self.night_move_hover = None
        self.night_resize_ns_hover = None
        self.night_resize_we_hover = None
        self.night_resize_nwse_hover = None
        self.night_resize_nesw_hover = None

        self.detected_timezone = detect_local_timezone_name()
        self.settings = load_settings()
        self.dark_theme_enabled = bool(self.settings.get("dark_theme", False))
        self.status_var = tk.StringVar(value="Ready.")
        self.mode_var = tk.StringVar(value="")
        self.current_scheme_var = tk.StringVar(value=read_active_scheme_name() or "Custom / Unknown")
        self.next_switch_var = tk.StringVar(value="")
        self.preview_var = tk.StringVar(value="day")

        self.timezone_display_values = [
            f"Auto detect ({self.detected_timezone})",
            "PC local clock",
            "EEST",
            "EET",
            "UTC",
            *sorted(available_timezones()),
        ]

        self.registry_schemes: dict[str, list[str]] = {}
        self.profile_map: dict[str, list[str]] = {}
        self.last_applied_by_sunsor = ""

        self.timezone_var = tk.StringVar()
        self.day_start_var = tk.StringVar()
        self.night_start_var = tk.StringVar()
        self.day_profile_var = tk.StringVar()
        self.night_profile_var = tk.StringVar()
        self.scheduler_enabled_var = tk.BooleanVar()
        self.settings_timezone_var = tk.StringVar()
        self.settings_day_start_var = tk.StringVar()
        self.settings_night_start_var = tk.StringVar()
        self.settings_day_profile_var = tk.StringVar()
        self.settings_night_profile_var = tk.StringVar()
        self.summary_line_var = tk.StringVar(value="")
        self.show_popup_warning_var = tk.BooleanVar()
        self.settings_show_popup_warning_var = tk.BooleanVar()
        self.start_with_windows_var = tk.BooleanVar()
        self.startup_privileged_var = tk.BooleanVar()
        self.settings_start_with_windows_var = tk.BooleanVar()
        self.settings_startup_privileged_var = tk.BooleanVar()
        self.dark_theme_var = tk.BooleanVar(value=self.dark_theme_enabled)
        self.settings_dark_theme_var = tk.BooleanVar(value=self.dark_theme_enabled)
        self.active_preview_label_var = tk.StringVar(value="Showing day preview")

        self.configure_styles()
        self.build_ui()
        self.refresh_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.reload_profiles()
        self.load_settings_into_ui()
        self.refresh_theme()
        self.apply_startup_preferences()
        self.apply_profile_on_launch()
        self.refresh_summary()
        self.setup_tray()
        self.run_scheduler_tick()

    def configure_styles(self) -> None:
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=11)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(size=11)
        heading_font = tkfont.nametofont("TkHeadingFont")
        heading_font.configure(size=13, weight="bold")

        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        if self.dark_theme_enabled:
            bg = "#0f1115"
            panel = "#171a21"
            panel_alt = "#1d2230"
            fg = "#f3f4f6"
            muted = "#b8bfcc"
            accent = "#8fb8ff"
            button_bg = "#202636"
            button_active = "#2c3446"
            entry_bg = "#10141c"
            border = "#2a3244"
        else:
            bg = "#f3f6fb"
            panel = "#fbfcfe"
            panel_alt = "#edf2fb"
            fg = "#111827"
            muted = "#4b5563"
            accent = "#1d4ed8"
            button_bg = "#f7f9fd"
            button_active = "#e9eef8"
            entry_bg = "#ffffff"
            border = "#d8e0ee"

        self.theme_colors = {
            "bg": bg,
            "panel": panel,
            "panel_alt": panel_alt,
            "fg": fg,
            "muted": muted,
            "accent": accent,
            "button_bg": button_bg,
            "button_active": button_active,
            "entry_bg": entry_bg,
            "border": border,
        }

        style.configure(".", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg, bordercolor=border, relief="flat", borderwidth=1)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TNotebook", background=bg, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=panel_alt, foreground=fg, padding=(16, 10), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", panel), ("active", button_active)], foreground=[("selected", fg)])
        style.configure("TButton", background=button_bg, foreground=fg, borderwidth=0, focusthickness=0, padding=(12, 8))
        style.map("TButton", background=[("active", button_active), ("pressed", button_active)])
        style.configure("Big.TButton", background=button_bg, foreground=fg, borderwidth=0, padding=(18, 14), font=("Segoe UI", 11, "bold"))
        style.map("Big.TButton", background=[("active", button_active), ("pressed", button_active)])
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg, bordercolor=border, lightcolor=border, darkcolor=border, padding=6)
        style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg, arrowsize=18, bordercolor=border, lightcolor=border, darkcolor=border, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", entry_bg)])
        style.configure("CardTitle.TLabel", background=bg, foreground=muted, font=("Segoe UI", 10))
        style.configure("CardValue.TLabel", background=bg, foreground=fg, font=("Segoe UI", 14, "bold"))
        style.configure("Hero.TLabel", background=bg, foreground=fg, font=("Segoe UI", 22, "bold"))
        style.configure("SubHero.TLabel", background=bg, foreground=muted, font=("Segoe UI", 11))

    def apply_theme_to_widget_tree(self, widget) -> None:
        colors = self.theme_colors

        try:
            if isinstance(widget, tk.Tk) or isinstance(widget, tk.Toplevel):
                widget.configure(bg=colors["bg"])
                apply_soft_window_style(widget)
            elif isinstance(widget, tk.Text):
                widget.configure(
                    bg=colors["entry_bg"],
                    fg=colors["fg"],
                    insertbackground=colors["fg"],
                    selectbackground=colors["accent"],
                    relief="flat",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=colors["border"],
                    highlightcolor=colors["accent"],
                )
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=colors["bg"])
            elif isinstance(widget, tk.Label):
                widget.configure(bg=colors["bg"], fg=colors["fg"])
            elif isinstance(widget, tk.Entry):
                widget.configure(
                    bg=colors["entry_bg"],
                    fg=colors["fg"],
                    insertbackground=colors["fg"],
                    relief="flat",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=colors["border"],
                    highlightcolor=colors["accent"],
                )
        except Exception:
            pass

        for child in widget.winfo_children():
            self.apply_theme_to_widget_tree(child)

    def refresh_theme(self) -> None:
        self.dark_theme_enabled = bool(self.dark_theme_var.get())
        self.configure_styles()
        apply_soft_window_style(self.root)
        self.apply_theme_to_widget_tree(self.root)
        if self.settings_window is not None and self.settings_window.winfo_exists():
            apply_soft_window_style(self.settings_window)
            self.apply_theme_to_widget_tree(self.settings_window)
        if self.preview_window is not None and self.preview_window.winfo_exists():
            apply_soft_window_style(self.preview_window)
            self.apply_theme_to_widget_tree(self.preview_window)

    def apply_profile_on_launch(self) -> None:
        try:
            if not self.scheduler_enabled_var.get():
                return
            self.clear_pending_switch()
            profile_name, profile_paths = self.selected_target_profile()
            current_name = read_active_scheme_name()
            if current_name != profile_name:
                apply_scheme(profile_name, profile_paths)
                self.current_scheme_var.set(profile_name)
                self.status_var.set(f"Loaded {profile_name} on startup.")
        except Exception as exc:
            self.status_var.set(f"Startup profile warning: {exc}")

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)

        self.load_logo(header)

        text_header = ttk.Frame(header)
        text_header.grid(row=0, column=1, sticky="w")
        ttk.Label(text_header, text="Sunsor", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            text_header,
            text="A simple day-and-night cursor scheduler for Windows.",
            style="SubHero.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        header_actions = ttk.Frame(header)
        header_actions.grid(row=0, column=2, sticky="e")
        ttk.Button(header_actions, text="⚙ Settings", style="Big.TButton", command=self.open_settings_window).pack(side="left")

        notebook = ttk.Notebook(outer)
        notebook.grid(row=1, column=0, sticky="nsew")

        main_tab = ttk.Frame(notebook, padding=12)
        credits_tab = ttk.Frame(notebook, padding=12)
        notebook.add(main_tab, text="Home")
        notebook.add(credits_tab, text="Credits")

        main_tab.columnconfigure(0, weight=1)
        main_tab.rowconfigure(3, weight=1)
        credits_tab.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(main_tab, text="Live Status", padding=14)
        top.grid(row=0, column=0, sticky="ew")
        for index in range(3):
            top.columnconfigure(index, weight=1)

        self.build_status_card(top, 0, "Current scheme", self.current_scheme_var)
        self.build_status_card(top, 1, "Current mode", self.mode_var)
        self.build_status_card(top, 2, "Next switch", self.next_switch_var)

        center = ttk.Frame(main_tab)
        center.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        center.columnconfigure(0, weight=1)
        ttk.Label(center, textvariable=self.summary_line_var, font=("Segoe UI", 12)).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(center, text="Scheduler enabled", variable=self.scheduler_enabled_var, command=self.on_scheduler_toggle).grid(row=0, column=1, sticky="e")

        actions = ttk.LabelFrame(main_tab, text="Quick Actions", padding=14)
        actions.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        for index in range(3):
            actions.columnconfigure(index, weight=1)
        ttk.Button(actions, text="Apply Current Mode Now", style="Big.TButton", command=self.apply_current_target).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="Preview Day Cursor", style="Big.TButton", command=lambda: self.activate_preview("day")).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(actions, text="Preview Night Cursor", style="Big.TButton", command=lambda: self.activate_preview("night")).grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Button(actions, text="Hide To Tray", style="Big.TButton", command=self.hide_to_tray).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(10, 0))
        ttk.Button(actions, text="Refresh Profiles", style="Big.TButton", command=self.refresh_installed_schemes).grid(row=1, column=1, sticky="ew", padx=8, pady=(10, 0))
        ttk.Button(actions, text="Open Settings", style="Big.TButton", command=self.open_settings_window).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Button(actions, text="Open Preview Window", style="Big.TButton", command=self.open_preview_window).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        preview_frame = ttk.LabelFrame(main_tab, text="Profile Details", padding=12)
        preview_frame.grid(row=3, column=0, sticky="nsew", pady=(14, 0))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)

        preview_header = ttk.Frame(preview_frame)
        preview_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(preview_header, text="Show Day", command=lambda: self.activate_preview("day")).pack(side="left")
        ttk.Button(preview_header, text="Show Night", command=lambda: self.activate_preview("night")).pack(side="left", padx=(8, 0))

        self.preview_title = ttk.Label(preview_header, text="Profile details")
        self.preview_title.pack(side="left", padx=(16, 0))
        ttk.Label(preview_header, textvariable=self.active_preview_label_var).pack(side="right")

        self.details = tk.Text(preview_frame, wrap="word")
        self.details.grid(row=1, column=0, sticky="nsew")
        self.details.configure(state="disabled")

        credits_box = ttk.LabelFrame(credits_tab, text="Credits", padding=18)
        credits_box.grid(row=0, column=0, sticky="nw")
        ttk.Label(credits_box, text="Sunsor", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(credits_box, text="Creator: Scoofyx", font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 0))
        ttk.Label(credits_box, text="Tray cursor scheduler for day and night profiles.").pack(anchor="w", pady=(8, 0))

        status = ttk.Label(outer, textvariable=self.status_var)
        status.grid(row=2, column=0, sticky="w", pady=(12, 0))

    def build_status_card(self, parent: ttk.LabelFrame, column: int, title: str, variable: tk.StringVar) -> None:
        card = ttk.Frame(parent, padding=(2, 4))
        card.grid(row=0, column=column, sticky="ew")
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=variable, style="CardValue.TLabel").pack(anchor="w", pady=(4, 0))

    def build_preview_box(self, parent: ttk.LabelFrame, mode: str) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        name_label = ttk.Label(header, text="", style="CardValue.TLabel")
        name_label.grid(row=0, column=0, sticky="w")
        if mode == "day":
            self.day_hover_name = name_label
        else:
            self.night_hover_name = name_label

        ttk.Button(
            header,
            text="Minimize",
            command=lambda current_mode=mode: self.toggle_preview_box(current_mode),
        ).grid(row=0, column=1, sticky="e")

        content = ttk.Frame(parent)
        content.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        if mode == "day":
            self.day_preview_content = content
        else:
            self.night_preview_content = content

        ttk.Label(content, text="Hover inside this box to activate the full cursor pack").grid(row=0, column=0, columnspan=2, sticky="w")

        normal_label = ttk.Label(content, text="Normal Select hover area", relief="solid", padding=10)
        normal_label.grid(row=1, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))

        help_label = ttk.Label(content, text="Help Select hover area", relief="solid", padding=10)
        help_label.grid(row=1, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        text_entry = ttk.Entry(content)
        text_entry.insert(0, "Text Select hover area")
        text_entry.grid(row=2, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))

        link_label = ttk.Label(content, text="Link Select hover area", foreground="#1d4ed8", relief="solid", padding=10)
        link_label.grid(row=2, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        busy_label = ttk.Label(content, text="Busy hover area", relief="solid", padding=10)
        busy_label.grid(row=3, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))

        move_label = ttk.Label(content, text="Move hover area", relief="solid", padding=10)
        move_label.grid(row=3, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        resize_ns = ttk.Label(content, text="Vertical Resize", relief="solid", padding=10)
        resize_ns.grid(row=4, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))

        resize_we = ttk.Label(content, text="Horizontal Resize", relief="solid", padding=10)
        resize_we.grid(row=4, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        resize_nwse = ttk.Label(content, text="Diagonal Resize 1", relief="solid", padding=10)
        resize_nwse.grid(row=5, column=0, sticky="ew", pady=(10, 0), padx=(0, 6))

        resize_nesw = ttk.Label(content, text="Diagonal Resize 2", relief="solid", padding=10)
        resize_nesw.grid(row=5, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        note_label = ttk.Label(
            content,
            text="Hover areas describe the cursor roles. While your mouse stays inside this preview box, Sunsor activates that full cursor pack.",
            wraplength=360,
        )
        note_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

        if mode == "day":
            self.day_normal_hover = normal_label
            self.day_help_hover = help_label
            self.day_text_hover = text_entry
            self.day_link_hover = link_label
            self.day_busy_hover = busy_label
            self.day_move_hover = move_label
            self.day_resize_ns_hover = resize_ns
            self.day_resize_we_hover = resize_we
            self.day_resize_nwse_hover = resize_nwse
            self.day_resize_nesw_hover = resize_nesw
        else:
            self.night_normal_hover = normal_label
            self.night_help_hover = help_label
            self.night_text_hover = text_entry
            self.night_link_hover = link_label
            self.night_busy_hover = busy_label
            self.night_move_hover = move_label
            self.night_resize_ns_hover = resize_ns
            self.night_resize_we_hover = resize_we
            self.night_resize_nwse_hover = resize_nwse
            self.night_resize_nesw_hover = resize_nesw

        preview_widgets = [
            parent,
            header,
            content,
            name_label,
            normal_label,
            help_label,
            text_entry,
            link_label,
            busy_label,
            move_label,
            resize_ns,
            resize_we,
            resize_nwse,
            resize_nesw,
            note_label,
        ]
        for widget in preview_widgets:
            widget.bind("<Enter>", lambda _event, current_mode=mode: self.begin_live_preview(current_mode))
            widget.bind("<Leave>", lambda _event: self.schedule_live_preview_restore())

    def open_preview_window(self) -> None:
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.deiconify()
            self.preview_window.lift()
            self.preview_window.focus_force()
            return

        self.day_preview_collapsed = False
        self.night_preview_collapsed = False

        window = tk.Toplevel(self.root)
        window.title("Sunsor Preview Window")
        window.geometry("980x620")
        window.minsize(860, 540)
        self.preview_window = window

        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(outer, text="Cursor Preview Window", style="Hero.TLabel").grid(row=0, column=0, sticky="w")

        box_row = ttk.Frame(outer)
        box_row.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        box_row.columnconfigure(0, weight=1)
        box_row.columnconfigure(1, weight=1)

        self.day_hover_frame = ttk.LabelFrame(box_row, text="Day Preview", padding=12)
        self.day_hover_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.day_hover_frame.columnconfigure(0, weight=1)
        self.build_preview_box(self.day_hover_frame, "day")

        self.night_hover_frame = ttk.LabelFrame(box_row, text="Night Preview", padding=12)
        self.night_hover_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.night_hover_frame.columnconfigure(0, weight=1)
        self.build_preview_box(self.night_hover_frame, "night")

        ttk.Label(
            outer,
            text="This window lets you hover-test the separate day and night cursor packs side by side.",
            wraplength=900,
        ).grid(row=2, column=0, sticky="w", pady=(12, 0))

        window.protocol("WM_DELETE_WINDOW", self.close_preview_window)
        self.refresh_hover_previews()

    def close_preview_window(self) -> None:
        self.restore_live_preview()
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None
        self.day_hover_name = None
        self.night_hover_name = None
        self.day_normal_hover = None
        self.day_help_hover = None
        self.day_text_hover = None
        self.day_link_hover = None
        self.day_busy_hover = None
        self.day_move_hover = None
        self.day_resize_ns_hover = None
        self.day_resize_we_hover = None
        self.day_resize_nwse_hover = None
        self.day_resize_nesw_hover = None
        self.night_normal_hover = None
        self.night_help_hover = None
        self.night_text_hover = None
        self.night_link_hover = None
        self.night_busy_hover = None
        self.night_move_hover = None
        self.night_resize_ns_hover = None
        self.night_resize_we_hover = None
        self.night_resize_nwse_hover = None
        self.night_resize_nesw_hover = None

    def toggle_preview_box(self, mode: str) -> None:
        if mode == "day":
            self.day_preview_collapsed = not self.day_preview_collapsed
            content = self.day_preview_content
            button_parent = self.day_hover_frame
            collapsed = self.day_preview_collapsed
        else:
            self.night_preview_collapsed = not self.night_preview_collapsed
            content = self.night_preview_content
            button_parent = self.night_hover_frame
            collapsed = self.night_preview_collapsed

        for child in button_parent.winfo_children():
            if isinstance(child, ttk.Frame) and child is not content:
                for grandchild in child.winfo_children():
                    if isinstance(grandchild, ttk.Button):
                        grandchild.configure(text="Expand" if collapsed else "Minimize")

        if collapsed:
            content.grid_remove()
        else:
            content.grid()

    def activate_preview(self, mode: str) -> None:
        if mode == "day" and self.day_preview_collapsed:
            self.toggle_preview_box("day")
        if mode == "night" and self.night_preview_collapsed:
            self.toggle_preview_box("night")

        self.preview_profile(mode)
        self.active_preview_label_var.set(f"Showing {mode} preview")
        profile_name = self.day_profile_var.get() if mode == "day" else self.night_profile_var.get()
        self.status_var.set(f"Previewing {mode} cursor pack: {profile_name}")
        self.open_preview_window()
        self.details.focus_set()

    def open_settings_window(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        self.copy_main_vars_to_settings_vars()
        window = tk.Toplevel(self.root)
        window.title("Sunsor Settings")
        window.geometry("900x700")
        window.minsize(820, 620)
        window.transient(self.root)
        self.settings_window = window

        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(outer, text="Settings", style="Hero.TLabel").grid(row=0, column=0, sticky="w")

        body = ttk.Frame(outer)
        body.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        schedule = ttk.LabelFrame(body, text="Schedule", padding=14)
        schedule.grid(row=0, column=0, sticky="ew")
        schedule.columnconfigure(1, weight=1)
        schedule.columnconfigure(3, weight=1)

        ttk.Label(schedule, text="Timezone").grid(row=0, column=0, sticky="w")
        self.timezone_combo = ttk.Combobox(schedule, textvariable=self.settings_timezone_var, values=self.timezone_display_values, height=18)
        self.timezone_combo.grid(row=0, column=1, sticky="ew", padx=(10, 18))
        ttk.Button(schedule, text="Use PC Timezone", command=self.use_auto_timezone).grid(row=0, column=2, columnspan=2, sticky="w")

        ttk.Label(schedule, text="Day starts").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(schedule, textvariable=self.settings_day_start_var, width=12).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(12, 0))
        ttk.Label(schedule, text="Night starts").grid(row=1, column=2, sticky="w", pady=(12, 0))
        ttk.Entry(schedule, textvariable=self.settings_night_start_var, width=12).grid(row=1, column=3, sticky="w", padx=(10, 0), pady=(12, 0))

        ttk.Label(
            schedule,
            text="Default setup uses the PC timezone automatically, white cursor in the day, and dark cursor at night.",
            wraplength=760,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Label(
            schedule,
            text="Each profile switches the whole Windows cursor pack, not only the main arrow.",
            wraplength=760,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(schedule, text="Show 5 second popup before cursor changes", variable=self.settings_show_popup_warning_var).grid(row=4, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Checkbutton(schedule, text="Start Sunsor with Windows", variable=self.settings_start_with_windows_var).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(schedule, text="Use privileged startup task (optional)", variable=self.settings_startup_privileged_var).grid(row=6, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(schedule, text="Use black theme", variable=self.settings_dark_theme_var).grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))

        profiles = ttk.LabelFrame(body, text="Cursor Profiles", padding=14)
        profiles.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        profiles.columnconfigure(1, weight=1)
        profiles.columnconfigure(3, weight=1)

        ttk.Label(profiles, text="Day profile").grid(row=0, column=0, sticky="w")
        self.day_profile_combo = ttk.Combobox(profiles, textvariable=self.settings_day_profile_var)
        self.day_profile_combo.grid(row=0, column=1, sticky="ew", padx=(10, 18))
        self.day_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_settings_preview_change("day"))

        ttk.Label(profiles, text="Night profile").grid(row=0, column=2, sticky="w")
        self.night_profile_combo = ttk.Combobox(profiles, textvariable=self.settings_night_profile_var)
        self.night_profile_combo.grid(row=0, column=3, sticky="ew", padx=(10, 0))
        self.night_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_settings_preview_change("night"))

        profile_buttons = ttk.Frame(profiles)
        profile_buttons.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(profile_buttons, text="Save Settings", style="Big.TButton", command=self.save_from_settings_window).pack(side="left")
        ttk.Button(profile_buttons, text="Reset All", style="Big.TButton", command=self.reset_all_settings).pack(side="left", padx=(10, 0))
        ttk.Button(profile_buttons, text="New Custom Profile", style="Big.TButton", command=self.create_custom_profile).pack(side="left", padx=(10, 0))
        ttk.Button(profile_buttons, text="Edit Selected Profile", style="Big.TButton", command=self.edit_preview_profile).pack(side="left", padx=(10, 0))
        ttk.Button(profile_buttons, text="Capture Current Setup", style="Big.TButton", command=self.capture_current_setup).pack(side="left", padx=(10, 0))

        window.protocol("WM_DELETE_WINDOW", self.close_settings_window)
        self.reload_profiles()
        self.update_settings_widgets()

    def close_settings_window(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.settings_window = None

    def use_auto_timezone(self) -> None:
        self.settings_timezone_var.set(f"Auto detect ({self.detected_timezone})")

    def copy_main_vars_to_settings_vars(self) -> None:
        self.settings_timezone_var.set(display_timezone_choice(self.settings["timezone"], self.detected_timezone))
        self.settings_day_start_var.set(self.day_start_var.get())
        self.settings_night_start_var.set(self.night_start_var.get())
        self.settings_day_profile_var.set(self.day_profile_var.get())
        self.settings_night_profile_var.set(self.night_profile_var.get())
        self.settings_show_popup_warning_var.set(self.show_popup_warning_var.get())
        self.settings_start_with_windows_var.set(self.start_with_windows_var.get())
        self.settings_startup_privileged_var.set(self.startup_privileged_var.get())
        self.settings_dark_theme_var.set(self.dark_theme_var.get())

    def copy_settings_vars_to_main_vars(self) -> None:
        self.timezone_var.set(self.settings_timezone_var.get())
        self.day_start_var.set(self.settings_day_start_var.get())
        self.night_start_var.set(self.settings_night_start_var.get())
        self.day_profile_var.set(self.settings_day_profile_var.get())
        self.night_profile_var.set(self.settings_night_profile_var.get())
        self.show_popup_warning_var.set(self.settings_show_popup_warning_var.get())
        self.start_with_windows_var.set(self.settings_start_with_windows_var.get())
        self.startup_privileged_var.set(self.settings_startup_privileged_var.get())
        self.dark_theme_var.set(self.settings_dark_theme_var.get())

    def on_settings_preview_change(self, mode: str) -> None:
        if mode == "day":
            self.day_profile_var.set(self.settings_day_profile_var.get())
        else:
            self.night_profile_var.set(self.settings_night_profile_var.get())
        self.preview_profile(mode)

    def update_settings_widgets(self) -> None:
        if not (self.settings_window is not None and self.settings_window.winfo_exists()):
            return
        profile_names = list(self.profile_map.keys())
        self.day_profile_combo.configure(values=profile_names)
        self.night_profile_combo.configure(values=profile_names)
        self.settings_day_profile_var.set(self.pick_existing_profile(self.settings_day_profile_var.get(), "Windows Default White"))
        self.settings_night_profile_var.set(self.pick_existing_profile(self.settings_night_profile_var.get(), "Windows Default Dark"))

    def load_logo(self, parent: ttk.Frame) -> None:
        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except Exception:
                pass

        if not LOGO_FILE.exists():
            return

        try:
            self.logo_image = tk.PhotoImage(file=str(LOGO_FILE))
        except tk.TclError:
            self.logo_image = None
            self.status_var.set("Sunsor logo could not be loaded.")
            return

        self.root.iconphoto(True, self.logo_image)
        header_image = self.logo_image.subsample(max(1, self.logo_image.width() // 96), max(1, self.logo_image.height() // 96))
        self.header_logo_image = header_image
        ttk.Label(parent, image=self.header_logo_image).grid(row=0, column=0, sticky="w", padx=(0, 14))

    def setup_tray(self) -> None:
        if self.tray_started:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Open Sunsor", self.on_tray_open),
            pystray.MenuItem("Apply Current Profile", self.on_tray_apply),
            pystray.MenuItem("Scheduler Enabled", self.on_tray_toggle_scheduler, checked=lambda item: self.scheduler_enabled_var.get()),
            pystray.MenuItem("Exit", self.on_tray_exit),
        )
        self.tray_icon = pystray.Icon(APP_NAME, create_tray_image(), APP_NAME, menu)
        self.tray_icon.visible = False
        self.tray_thread = threading.Thread(target=self.run_tray_icon, daemon=True)
        self.tray_thread.start()
        self.tray_started = True

    def run_tray_icon(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.run()

    def show_tray_icon(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.visible = True

    def hide_tray_icon(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.visible = False

    def hide_to_tray(self) -> None:
        self.is_hidden_to_tray = True
        self.root.withdraw()
        self.show_tray_icon()
        self.status_var.set("Sunsor is running in the tray.")
        if self.tray_icon is not None:
            try:
                self.tray_icon.notify("Sunsor is still running in the tray and will keep switching cursors automatically.", APP_NAME)
            except Exception:
                pass

    def restore_from_tray(self) -> None:
        self.is_hidden_to_tray = False
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)
        self.root.after(0, self.root.focus_force)
        self.hide_tray_icon()
        self.status_var.set("Sunsor window restored.")

    def on_tray_open(self, icon=None, item=None) -> None:
        self.root.after(0, self.restore_from_tray)

    def on_tray_apply(self, icon=None, item=None) -> None:
        self.root.after(0, self.apply_current_target)

    def on_tray_toggle_scheduler(self, icon=None, item=None) -> None:
        self.root.after(0, self.toggle_scheduler_from_tray)

    def toggle_scheduler_from_tray(self) -> None:
        self.scheduler_enabled_var.set(not self.scheduler_enabled_var.get())
        self.on_scheduler_toggle()

    def on_tray_exit(self, icon=None, item=None) -> None:
        self.root.after(0, self.exit_app)

    def exit_app(self) -> None:
        self.is_exiting = True
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def reload_profiles(self) -> None:
        self.registry_schemes = read_registry_schemes()
        self.profile_map = {}
        self.profile_map.update(builtin_profiles())
        self.profile_map.update(dict(sorted(self.registry_schemes.items(), key=lambda item: item[0].lower())))
        self.profile_map.update(self.settings.get("custom_profiles", {}))
        self.update_settings_widgets()

    def load_settings_into_ui(self) -> None:
        self.timezone_var.set(display_timezone_choice(self.settings["timezone"], self.detected_timezone))
        self.day_start_var.set(self.settings["day_start"])
        self.night_start_var.set(self.settings["night_start"])
        normalized_day = self.pick_existing_profile(self.settings["day_profile"], "Windows Default White")
        normalized_night = self.pick_existing_profile(self.settings["night_profile"], "Windows Default Dark")
        self.day_profile_var.set(normalized_day)
        self.night_profile_var.set(normalized_night)
        self.scheduler_enabled_var.set(bool(self.settings["scheduler_enabled"]))
        self.show_popup_warning_var.set(bool(self.settings.get("show_popup_warning", True)))
        self.start_with_windows_var.set(bool(self.settings.get("start_with_windows", True)))
        self.startup_privileged_var.set(bool(self.settings.get("startup_privileged", False)))
        self.dark_theme_var.set(bool(self.settings.get("dark_theme", False)))
        self.settings["day_profile"] = normalized_day
        self.settings["night_profile"] = normalized_night
        self.settings["dark_theme"] = self.dark_theme_var.get()
        save_settings(self.settings)
        self.copy_main_vars_to_settings_vars()

    def pick_existing_profile(self, preferred: str, fallback: str) -> str:
        if preferred in self.profile_map:
            return preferred
        if fallback in self.profile_map:
            return fallback
        return next(iter(self.profile_map), "")

    def preview_profile(self, mode: str) -> None:
        self.preview_var.set(mode)
        profile_name = self.day_profile_var.get() if mode == "day" else self.night_profile_var.get()
        self.preview_title.config(text=f"{mode.title()} profile: {profile_name}")

        paths = self.profile_map.get(profile_name, [])
        lines = []
        for role, path in zip(CURSOR_ROLES, paths):
            lines.append(f"{ROLE_LABELS.get(role, role)}\n{path}\n")
        if not lines:
            lines = ["No profile data found."]

        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", "\n".join(lines).strip())
        self.details.configure(state="disabled")

    def refresh_summary(self) -> None:
        try:
            choice = normalize_timezone_choice(self.timezone_var.get())
            now_value = now_for_timezone(choice)
            day_now = is_daytime(now_value, self.day_start_var.get(), self.night_start_var.get())
            self.mode_var.set("Day" if day_now else "Night")
            next_time = next_switch_time(now_value, self.day_start_var.get(), self.night_start_var.get())
            self.next_switch_var.set(next_time.strftime("%Y-%m-%d %H:%M %Z"))
            timezone_label = "your PC timezone" if choice in {"AUTO", "LOCAL"} else choice
            self.summary_line_var.set(
                f"Sunsor is using {timezone_label}. Day starts at {self.day_start_var.get()} and night starts at {self.night_start_var.get()}."
            )
        except Exception:
            self.mode_var.set("Check settings")
            self.next_switch_var.set("Invalid time")
            self.summary_line_var.set("Open Settings to fix the time or timezone.")

        self.current_scheme_var.set(read_active_scheme_name() or "Custom / Unknown")
        self.refresh_hover_previews()
        self.preview_profile(self.preview_var.get())

    def refresh_hover_previews(self) -> None:
        if self.day_hover_name is None or self.night_hover_name is None:
            return
        self.apply_preview_pack(
            self.day_profile_var.get(),
            self.day_hover_name,
            self.day_normal_hover,
            self.day_help_hover,
            self.day_text_hover,
            self.day_link_hover,
            self.day_busy_hover,
            self.day_move_hover,
            self.day_resize_ns_hover,
            self.day_resize_we_hover,
            self.day_resize_nwse_hover,
            self.day_resize_nesw_hover,
        )
        self.apply_preview_pack(
            self.night_profile_var.get(),
            self.night_hover_name,
            self.night_normal_hover,
            self.night_help_hover,
            self.night_text_hover,
            self.night_link_hover,
            self.night_busy_hover,
            self.night_move_hover,
            self.night_resize_ns_hover,
            self.night_resize_we_hover,
            self.night_resize_nwse_hover,
            self.night_resize_nesw_hover,
        )

    def apply_preview_pack(
        self,
        profile_name: str,
        text_widget: ttk.Label,
        normal_widget,
        help_widget,
        text_hover_widget,
        link_widget,
        busy_widget,
        move_widget,
        resize_ns_widget,
        resize_we_widget,
        resize_nwse_widget,
        resize_nesw_widget,
    ) -> None:
        paths = self.profile_map.get(profile_name, [])
        self.apply_role_cursor(normal_widget, paths, 0, "arrow")
        self.apply_role_cursor(help_widget, paths, 1, "question_arrow")
        self.apply_role_cursor(text_hover_widget, paths, 5, "xterm")
        self.apply_role_cursor(link_widget, paths, 14, "hand2")
        self.apply_role_cursor(busy_widget, paths, 3, "watch")
        self.apply_role_cursor(move_widget, paths, 12, "fleur")
        self.apply_role_cursor(resize_ns_widget, paths, 8, "sb_v_double_arrow")
        self.apply_role_cursor(resize_we_widget, paths, 9, "sb_h_double_arrow")
        self.apply_role_cursor(resize_nwse_widget, paths, 10, "size_nw_se")
        self.apply_role_cursor(resize_nesw_widget, paths, 11, "size_ne_sw")
        text_widget.configure(text=profile_name or "No profile selected")

    def apply_role_cursor(self, widget, paths: list[str], index: int, fallback: str) -> None:
        cursor_value = fallback
        if len(paths) > index and paths[index]:
            candidate = Path(paths[index])
            if candidate.exists() and candidate.suffix.lower() == ".cur":
                cursor_value = f"@{candidate}"
        try:
            widget.configure(cursor=cursor_value)
        except tk.TclError:
            widget.configure(cursor=fallback)

    def begin_live_preview(self, mode: str) -> None:
        profile_name = self.day_profile_var.get().strip() if mode == "day" else self.night_profile_var.get().strip()
        profile_paths = self.profile_map.get(profile_name)
        if not profile_name or not profile_paths:
            return

        try:
            self.cancel_preview_restore()
            self.cancel_hover_restore()
            if not self.preview_restore_paths:
                self.preview_restore_profile_name = read_active_scheme_name() or "Custom Preview Restore"
                self.preview_restore_paths = read_current_cursor_paths()
            if self.live_preview_mode != mode:
                apply_scheme(profile_name, profile_paths)
        except Exception as exc:
            self.status_var.set(f"Preview hover failed: {exc}")
            return

        self.live_preview_mode = mode
        self.current_scheme_var.set(profile_name)
        self.status_var.set(f"Hover preview active: {profile_name}")

    def restore_live_preview(self) -> None:
        self.cancel_hover_restore()
        if not self.preview_restore_paths:
            return

        try:
            apply_scheme(self.preview_restore_profile_name, self.preview_restore_paths)
        except Exception as exc:
            self.status_var.set(f"Could not restore previous cursor pack: {exc}")
            return

        self.current_scheme_var.set(read_active_scheme_name() or self.preview_restore_profile_name)
        self.status_var.set("Preview hover ended. Restored previous cursor pack.")
        self.live_preview_mode = ""
        self.preview_restore_profile_name = ""
        self.preview_restore_paths = []

    def cancel_preview_restore(self) -> None:
        if self.preview_restore_after_id is not None:
            try:
                self.root.after_cancel(self.preview_restore_after_id)
            except Exception:
                pass
        self.preview_restore_after_id = None

    def schedule_live_preview_restore(self) -> None:
        self.cancel_hover_restore()
        self.preview_hover_restore_after_id = self.root.after(120, self.maybe_restore_live_preview)

    def cancel_hover_restore(self) -> None:
        if self.preview_hover_restore_after_id is not None:
            try:
                self.root.after_cancel(self.preview_hover_restore_after_id)
            except Exception:
                pass
        self.preview_hover_restore_after_id = None

    def maybe_restore_live_preview(self) -> None:
        self.preview_hover_restore_after_id = None
        if self.preview_window is None or not self.preview_window.winfo_exists():
            self.restore_live_preview()
            return
        if self.pointer_inside_widget(self.day_hover_frame):
            self.begin_live_preview("day")
            return
        if self.pointer_inside_widget(self.night_hover_frame):
            self.begin_live_preview("night")
            return
        self.restore_live_preview()

    def pointer_inside_widget(self, widget) -> bool:
        if widget is None or not widget.winfo_exists():
            return False
        pointer_x = widget.winfo_pointerx()
        pointer_y = widget.winfo_pointery()
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
        right = left + widget.winfo_width()
        bottom = top + widget.winfo_height()
        return left <= pointer_x <= right and top <= pointer_y <= bottom

    def maybe_warn_before_switch(self, profile_name: str, seconds_left: int, signature: str) -> None:
        if not self.show_popup_warning_var.get():
            self.close_popup()
            self.warning_switch_signature = signature
            return
        if self.warning_switch_signature == signature:
            return
        self.warning_switch_signature = signature
        self.show_countdown_popup(profile_name, seconds_left)

    def show_countdown_popup(self, profile_name: str, seconds_left: int) -> None:
        self.close_popup()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup_bg = self.theme_colors["panel"] if hasattr(self, "theme_colors") else "#111827"
        popup_fg = self.theme_colors["fg"] if hasattr(self, "theme_colors") else "#f9fafb"
        popup_muted = self.theme_colors["muted"] if hasattr(self, "theme_colors") else "#9ca3af"
        popup_border = self.theme_colors["border"] if hasattr(self, "theme_colors") else "#2a3244"
        popup.configure(bg=popup_bg)
        apply_soft_window_style(popup)

        width = 320
        height = 110
        x = self.root.winfo_screenwidth() - width - 24
        y = 48
        popup.geometry(f"{width}x{height}+{x}+{y}")

        body = tk.Frame(popup, bg=popup_bg, padx=16, pady=14, highlightthickness=1, highlightbackground=popup_border)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Sunsor", bg=popup_bg, fg=popup_fg, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        countdown_var = tk.StringVar()
        tk.Label(body, textvariable=countdown_var, bg=popup_bg, fg=popup_fg, font=("Segoe UI", 12)).pack(anchor="w", pady=(8, 0))
        tk.Label(body, text=profile_name, bg=popup_bg, fg=popup_muted, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))

        self.popup_window = popup
        self.update_popup_countdown(profile_name, seconds_left, countdown_var)

    def update_popup_countdown(self, profile_name: str, seconds_left: int, countdown_var: tk.StringVar) -> None:
        if self.popup_window is None or not self.popup_window.winfo_exists():
            return
        if seconds_left <= 0:
            self.close_popup()
            return
        suffix = "second" if seconds_left == 1 else "seconds"
        countdown_var.set(f"Cursor will change in {seconds_left} {suffix}")
        self.popup_after_id = self.popup_window.after(
            1000,
            lambda: self.update_popup_countdown(profile_name, seconds_left - 1, countdown_var),
        )

    def close_popup(self) -> None:
        if self.popup_window is not None and self.popup_window.winfo_exists():
            if self.popup_after_id is not None:
                try:
                    self.popup_window.after_cancel(self.popup_after_id)
                except Exception:
                    pass
            self.popup_window.destroy()
        self.popup_window = None
        self.popup_after_id = None

    def begin_pending_switch(self, profile_name: str, profile_paths: list[str]) -> None:
        self.pending_switch_profile_name = profile_name
        self.pending_switch_profile_paths = profile_paths
        if self.show_popup_warning_var.get():
            self.pending_switch_deadline = datetime.now().astimezone() + timedelta(seconds=5)
            self.warning_switch_signature = f"{profile_name}:{self.pending_switch_deadline.isoformat()}"
            self.show_countdown_popup(profile_name, 5)
            self.status_var.set(f"Sunsor will switch to {profile_name} in 5 seconds.")
        else:
            self.pending_switch_deadline = datetime.now().astimezone()

    def clear_pending_switch(self) -> None:
        self.pending_switch_profile_name = ""
        self.pending_switch_profile_paths = []
        self.pending_switch_deadline = None
        self.warning_switch_signature = ""
        self.close_popup()

    def validate_ui(self) -> tuple[str, str, str, str, bool]:
        timezone_choice = normalize_timezone_choice(self.timezone_var.get().strip())
        if timezone_choice not in {"AUTO", "LOCAL"} and timezone_choice not in available_timezones():
            raise ValueError("Choose a valid timezone, Auto detect, or PC local clock.")

        day_start = self.day_start_var.get().strip()
        night_start = self.night_start_var.get().strip()
        parse_clock(day_start)
        parse_clock(night_start)

        day_profile = self.day_profile_var.get().strip()
        night_profile = self.night_profile_var.get().strip()
        if day_profile not in self.profile_map or night_profile not in self.profile_map:
            raise ValueError("Choose valid day and night profiles.")

        return timezone_choice, day_start, night_start, day_profile, self.scheduler_enabled_var.get()

    def save_from_settings_window(self) -> None:
        self.copy_settings_vars_to_main_vars()
        previous_status = self.status_var.get()
        self.save_from_ui()
        if self.status_var.get() != "Saved Sunsor settings.":
            self.status_var.set(previous_status if previous_status else self.status_var.get())
            return
        self.preview_profile(self.preview_var.get())
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
            self.settings_window = None

    def reset_all_settings(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Reset all Sunsor settings and custom profiles back to defaults?"):
            return

        self.settings = default_settings()
        save_settings(self.settings)
        self.load_settings_into_ui()
        self.reload_profiles()
        self.copy_main_vars_to_settings_vars()
        self.apply_startup_preferences()
        self.refresh_theme()
        self.clear_pending_switch()
        self.apply_profile_on_launch()
        self.refresh_summary()
        self.status_var.set("Sunsor settings reset to defaults.")

    def save_from_ui(self) -> None:
        try:
            timezone_choice, day_start, night_start, day_profile, scheduler_enabled = self.validate_ui()
            night_profile = self.night_profile_var.get().strip()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.settings["timezone"] = timezone_choice
        self.settings["day_start"] = day_start
        self.settings["night_start"] = night_start
        self.settings["day_profile"] = day_profile
        self.settings["night_profile"] = night_profile
        self.settings["scheduler_enabled"] = scheduler_enabled
        self.settings["show_popup_warning"] = self.show_popup_warning_var.get()
        self.settings["start_with_windows"] = self.start_with_windows_var.get()
        self.settings["startup_privileged"] = self.startup_privileged_var.get()
        self.settings["dark_theme"] = self.dark_theme_var.get()
        save_settings(self.settings)
        self.apply_startup_preferences()
        self.refresh_theme()
        self.status_var.set("Saved Sunsor settings.")
        self.refresh_summary()

    def apply_startup_preferences(self) -> None:
        try:
            if self.start_with_windows_var.get():
                if self.startup_privileged_var.get():
                    set_registry_startup(False)
                    set_privileged_startup_task(True)
                else:
                    set_privileged_startup_task(False)
                    set_registry_startup(True)
            else:
                set_privileged_startup_task(False)
                set_registry_startup(False)
        except Exception as exc:
            messagebox.showwarning(APP_NAME, f"Startup setting could not be applied.\n\n{exc}")

    def selected_target_profile(self) -> tuple[str, list[str]]:
        choice = normalize_timezone_choice(self.timezone_var.get().strip())
        now_value = now_for_timezone(choice)
        day_now = is_daytime(now_value, self.day_start_var.get().strip(), self.night_start_var.get().strip())
        profile_name = self.day_profile_var.get().strip() if day_now else self.night_profile_var.get().strip()
        return profile_name, self.profile_map[profile_name]

    def apply_current_target(self) -> None:
        try:
            self.save_from_ui()
            self.clear_pending_switch()
            profile_name, profile_paths = self.selected_target_profile()
            apply_scheme(profile_name, profile_paths)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            self.status_var.set("Could not apply the active profile.")
            return

        self.last_applied_by_sunsor = profile_name
        self.current_scheme_var.set(profile_name)
        self.status_var.set(f"Applied {profile_name}.")
        self.refresh_summary()

    def run_scheduler_tick(self) -> None:
        try:
            self.refresh_summary()
            if self.scheduler_enabled_var.get():
                profile_name, profile_paths = self.selected_target_profile()
                current_name = read_active_scheme_name()
                if current_name == profile_name:
                    self.clear_pending_switch()
                else:
                    if self.pending_switch_profile_name != profile_name:
                        self.begin_pending_switch(profile_name, profile_paths)
                    if self.pending_switch_deadline is not None and datetime.now().astimezone() >= self.pending_switch_deadline:
                        apply_scheme(self.pending_switch_profile_name, self.pending_switch_profile_paths)
                        self.last_applied_by_sunsor = self.pending_switch_profile_name
                        self.current_scheme_var.set(self.pending_switch_profile_name)
                        self.status_var.set(f"Sunsor switched to {self.pending_switch_profile_name}.")
                        self.clear_pending_switch()
            else:
                self.clear_pending_switch()
        except Exception as exc:
            self.status_var.set(f"Scheduler warning: {exc}")
        finally:
            if not self.is_exiting:
                self.root.after(CHECK_INTERVAL_MS, self.run_scheduler_tick)

    def on_scheduler_toggle(self) -> None:
        self.save_from_ui()
        state = "enabled" if self.scheduler_enabled_var.get() else "paused"
        self.status_var.set(f"Scheduler {state}.")

    def refresh_installed_schemes(self) -> None:
        self.reload_profiles()
        self.day_profile_var.set(self.pick_existing_profile(self.day_profile_var.get(), "Windows Default White"))
        self.night_profile_var.set(self.pick_existing_profile(self.night_profile_var.get(), "Windows Default Dark"))
        self.copy_main_vars_to_settings_vars()
        self.refresh_summary()
        self.status_var.set("Reloaded installed cursor schemes.")

    def create_custom_profile(self) -> None:
        base_profile = self.day_profile_var.get().strip() or self.pick_existing_profile("", "Windows Default White")
        base_paths = self.profile_map.get(base_profile, builtin_profiles()["Windows Default White"])
        dialog = CustomProfileDialog(self.root, "New Custom Profile", "", base_paths)
        if not dialog.result:
            return

        name = dialog.result["name"]
        if name in self.registry_schemes or name in builtin_profiles():
            messagebox.showwarning(APP_NAME, "That name is already used by a built-in or installed scheme.")
            return

        self.settings["custom_profiles"][name] = dialog.result["paths"]
        save_settings(self.settings)
        self.reload_profiles()
        self.day_profile_var.set(name)
        self.settings_day_profile_var.set(name)
        self.preview_profile("day")
        self.status_var.set(f"Saved custom profile {name}.")

    def edit_preview_profile(self) -> None:
        preview_mode = self.preview_var.get()
        profile_name = self.day_profile_var.get().strip() if preview_mode == "day" else self.night_profile_var.get().strip()
        if profile_name in self.registry_schemes or profile_name in builtin_profiles():
            messagebox.showinfo(APP_NAME, "Built-in and installed registry schemes are read-only here. Capture or create a custom profile first if you want to edit one.")
            return

        base_paths = self.profile_map.get(profile_name)
        if not base_paths:
            messagebox.showwarning(APP_NAME, "Pick a profile first.")
            return

        dialog = CustomProfileDialog(self.root, f"Edit {profile_name}", profile_name, base_paths)
        if not dialog.result:
            return

        new_name = dialog.result["name"]
        if new_name != profile_name:
            self.settings["custom_profiles"].pop(profile_name, None)
        self.settings["custom_profiles"][new_name] = dialog.result["paths"]
        save_settings(self.settings)
        self.reload_profiles()

        if preview_mode == "day":
            self.day_profile_var.set(new_name)
            self.settings_day_profile_var.set(new_name)
        else:
            self.night_profile_var.set(new_name)
            self.settings_night_profile_var.set(new_name)
        self.preview_profile(preview_mode)
        self.status_var.set(f"Updated custom profile {new_name}.")

    def capture_current_setup(self) -> None:
        name = simpledialog.askstring(APP_NAME, "Name for the captured profile:")
        if not name:
            return
        if name in self.registry_schemes or name in builtin_profiles():
            messagebox.showwarning(APP_NAME, "That name is already used by a built-in or installed scheme.")
            return

        try:
            self.settings["custom_profiles"][name] = read_current_cursor_paths()
            save_settings(self.settings)
            self.reload_profiles()
            self.day_profile_var.set(name)
            self.settings_day_profile_var.set(name)
            self.preview_profile("day")
            self.status_var.set(f"Captured current cursor setup as {name}.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))


def main() -> None:
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    SunsorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
