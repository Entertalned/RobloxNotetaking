"""
Roblox Friend Notes - Desktop GUI (Dark Red Theme)
-----------------------------------------------------
A Tkinter desktop app: enter a Roblox User ID, pull their friends list
(name, display name, ID), and attach/edit a personal note for each friend.
Notes are saved locally in friend_notes.json so they persist between runs.

Requires: requests
    pip install requests

(tkinter ships with standard Python installs on Windows/Mac. On some
Linux distros you may need: sudo apt install python3-tk)

Run:
    python roblox_friend_notes_gui.py
"""

import base64
import json
import os
import sys
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

import requests

NOTES_FILE = "friend_notes.json"
FRIENDS_API_URL = "https://friends.roblox.com/v1/users/{user_id}/friends"
USER_LOOKUP_URL = "https://users.roblox.com/v1/users/{user_id}"
BATCH_USER_LOOKUP_URL = "https://users.roblox.com/v1/users"
THUMBNAIL_API_URL = "https://thumbnails.roblox.com/v1/users/avatar-headshot"
LICENSE_URL = "https://github.com/Entertalned/RobloxNotetaking/blob/main/LICENSE"

APP_ICON_FILE = "app_icon.ico"
APP_LOGO_FILE = "app_logo.png"
LICENSE_ICON_FILE = "license_icon.png"


def resource_path(relative_path):
    """Resolve a bundled file's path, whether running as a script or a
    PyInstaller --onefile exe (which unpacks data files to a temp folder)."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

# ---------- Theme palette ----------
BG_DARK = "#161616"        # main background
BG_PANEL = "#1f1f1f"       # panel / card background
BG_INPUT = "#2a2a2a"       # entry / text box background
RED_ACCENT = "#c1272d"     # primary accent (buttons, headings)
RED_ACCENT_HOVER = "#e0332f"
RED_SUBTLE = "#7a2020"     # borders / secondary accent
TEXT_LIGHT = "#f2f2f2"
TEXT_MUTED = "#a6a6a6"
ROW_ALT = "#1c1c1c"
FONT_FAMILY = "Segoe UI"


# ---------- Data helpers ----------

def load_notes():
    if os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_notes(notes):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)


def get_username(user_id):
    resp = requests.get(USER_LOOKUP_URL.format(user_id=user_id), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("name"), data.get("displayName")


def get_friends(user_id):
    resp = requests.get(FRIENDS_API_URL.format(user_id=user_id), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def get_usernames_batch(user_ids):
    """Batch-lookup names, working around the friends API's name/displayName bug."""
    results = {}
    chunk_size = 100
    for i in range(0, len(user_ids), chunk_size):
        chunk = user_ids[i:i + chunk_size]
        resp = requests.post(
            BATCH_USER_LOOKUP_URL,
            json={"userIds": chunk, "excludeBannedUsers": False},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("data", []):
            results[entry["id"]] = {
                "name": entry.get("name", "Unknown"),
                "displayName": entry.get("displayName", entry.get("name", "Unknown")),
            }
    return results


def get_avatar_urls_batch(user_ids):
    """Batch-lookup avatar headshot image URLs for a list of user IDs.
    Returns a dict: {id: imageUrl}"""
    results = {}
    chunk_size = 100
    for i in range(0, len(user_ids), chunk_size):
        chunk = user_ids[i:i + chunk_size]
        resp = requests.get(
            THUMBNAIL_API_URL,
            params={
                "userIds": ",".join(str(u) for u in chunk),
                "size": "100x100",
                "format": "Png",
                "isCircular": "false",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("data", []):
            if entry.get("state") == "Completed" and entry.get("imageUrl"):
                results[entry["targetId"]] = entry["imageUrl"]
    return results


def download_image_bytes(url):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.content


# ---------- Tooltip helper ----------

class ToolTip:
    """Small hover tooltip for any widget, styled to match the app theme."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw, text=self.text, justify="left",
            background=BG_INPUT, foreground=TEXT_LIGHT,
            highlightbackground=RED_SUBTLE, highlightthickness=1,
            font=(FONT_FAMILY, 9), wraplength=220, padx=8, pady=6,
        ).pack()

    def hide(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


# ---------- GUI App ----------

class FriendNotesApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Roblox Friend Note-taking")
        self.root.geometry("780x540")
        self.root.minsize(680, 440)
        self.root.configure(bg=BG_DARK)

        self.notes = load_notes()
        self.friends = []
        self.selected_friend_id = None
        self.avatar_image_cache = {}   # friend id (str) -> PhotoImage
        self.avatar_request_token = 0  # guards against race conditions on fast reselect
        self.header_logo_img = None    # keep a reference so it isn't garbage-collected

        self._set_window_icon()
        self._setup_styles()
        self._build_ui()

    def _set_window_icon(self):
        try:
            self.root.iconbitmap(resource_path(APP_ICON_FILE))
        except (tk.TclError, FileNotFoundError):
            pass  # fine to skip on platforms/setups where .ico isn't supported

    # ---------- Styling ----------

    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        # General frame/label
        style.configure("TFrame", background=BG_DARK)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG_DARK, foreground=TEXT_LIGHT, font=(FONT_FAMILY, 10))
        style.configure("Muted.TLabel", background=BG_DARK, foreground=TEXT_MUTED, font=(FONT_FAMILY, 9))
        style.configure("Header.TLabel", background=BG_DARK, foreground=RED_ACCENT,
                         font=(FONT_FAMILY, 18, "bold"))
        style.configure("PanelLabel.TLabel", background=BG_PANEL, foreground=TEXT_LIGHT, font=(FONT_FAMILY, 10))
        style.configure("PanelBold.TLabel", background=BG_PANEL, foreground=RED_ACCENT,
                         font=(FONT_FAMILY, 11, "bold"))

        # Entry
        style.configure("TEntry",
                         fieldbackground=BG_INPUT,
                         foreground=TEXT_LIGHT,
                         insertcolor=TEXT_LIGHT,
                         borderwidth=1,
                         relief="flat")
        style.map("TEntry", fieldbackground=[("focus", BG_INPUT)])

        # Buttons
        style.configure("Accent.TButton",
                         background=RED_ACCENT,
                         foreground=TEXT_LIGHT,
                         font=(FONT_FAMILY, 10, "bold"),
                         borderwidth=0,
                         focusthickness=0,
                         padding=(14, 8))
        style.map("Accent.TButton",
                  background=[("active", RED_ACCENT_HOVER), ("disabled", "#4a2222")],
                  foreground=[("disabled", TEXT_MUTED)])

        # Treeview
        style.configure("Treeview",
                         background=BG_PANEL,
                         fieldbackground=BG_PANEL,
                         foreground=TEXT_LIGHT,
                         rowheight=28,
                         borderwidth=0,
                         font=(FONT_FAMILY, 10))
        style.map("Treeview",
                  background=[("selected", RED_ACCENT)],
                  foreground=[("selected", TEXT_LIGHT)])
        style.configure("Treeview.Heading",
                         background=RED_SUBTLE,
                         foreground=TEXT_LIGHT,
                         font=(FONT_FAMILY, 10, "bold"),
                         borderwidth=0,
                         relief="flat")
        style.map("Treeview.Heading", background=[("active", RED_ACCENT)])

        # Scrollbar
        style.configure("Vertical.TScrollbar",
                         background=BG_PANEL,
                         troughcolor=BG_DARK,
                         bordercolor=BG_DARK,
                         arrowcolor=TEXT_LIGHT,
                         relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", RED_ACCENT)])

    # ---------- UI construction ----------

    def _build_ui(self):
        # --- Header banner ---
        header = ttk.Frame(self.root, padding=(20, 18, 20, 10))
        header.pack(fill="x")

        try:
            self.header_logo_img = tk.PhotoImage(file=resource_path(APP_LOGO_FILE))
            logo_label = tk.Label(header, image=self.header_logo_img, bg=BG_DARK, bd=0)
            logo_label.pack(side="left", padx=(0, 14))
        except (tk.TclError, FileNotFoundError):
            pass  # app still works fine without the logo file present

        # License button, top-right corner
        try:
            self.license_icon_img = tk.PhotoImage(file=resource_path(LICENSE_ICON_FILE))
            license_btn = tk.Button(
                header, image=self.license_icon_img, bg=BG_DARK, activebackground=BG_PANEL,
                bd=0, highlightthickness=0, cursor="hand2",
                command=lambda: webbrowser.open(LICENSE_URL),
            )
        except (tk.TclError, FileNotFoundError):
            self.license_icon_img = None
            license_btn = tk.Button(
                header, text="License", bg=RED_ACCENT, fg=TEXT_LIGHT,
                font=(FONT_FAMILY, 9, "bold"), bd=0, cursor="hand2",
                command=lambda: webbrowser.open(LICENSE_URL),
            )
        license_btn.pack(side="right", anchor="n")
        ToolTip(license_btn, "View License")

        title_block = ttk.Frame(header)
        title_block.pack(side="left", fill="both", expand=True)
        ttk.Label(title_block, text="Roblox Friend Note-taking", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_block, text="Look up a Roblox user's friends and keep private notes on each one.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        divider = tk.Frame(self.root, bg=RED_SUBTLE, height=2)
        divider.pack(fill="x", padx=20, pady=(0, 10))

        # --- Search bar ---
        search_frame = ttk.Frame(self.root, padding=(20, 0, 20, 10))
        search_frame.pack(fill="x")

        ttk.Label(search_frame, text="Roblox User ID").pack(side="left")
        self.user_id_var = tk.StringVar()
        entry = ttk.Entry(search_frame, textvariable=self.user_id_var, width=20, style="TEntry")
        entry.pack(side="left", padx=(10, 10), ipady=4)
        entry.bind("<Return>", lambda e: self.fetch_friends())

        self.fetch_btn = ttk.Button(search_frame, text="FETCH FRIENDS", style="Accent.TButton",
                                     command=self.fetch_friends)
        self.fetch_btn.pack(side="left")

        # Unknowns counter + info tooltip
        self.unknown_count = 0
        self.unknown_var = tk.StringVar(value="Unknowns: 0")
        ttk.Label(search_frame, textvariable=self.unknown_var, style="Muted.TLabel").pack(
            side="left", padx=(24, 4)
        )
        info_icon = tk.Label(
            search_frame, text="?", bg=RED_ACCENT, fg=TEXT_LIGHT,
            font=(FONT_FAMILY, 9, "bold"), width=2, cursor="question_arrow",
        )
        info_icon.pack(side="left")
        ToolTip(info_icon, "Users who show up as unknown are usually deleted accounts.")

        self.status_var = tk.StringVar(value="Enter a User ID and click Fetch Friends.")
        ttk.Label(self.root, textvariable=self.status_var, style="Muted.TLabel",
                  padding=(20, 0)).pack(fill="x", pady=(0, 8))

        # --- Main content area ---
        main_frame = ttk.Frame(self.root, padding=(20, 0, 20, 20))
        main_frame.pack(fill="both", expand=True)

        # Friends list panel
        list_panel = ttk.Frame(main_frame, style="Panel.TFrame")
        list_panel.pack(side="left", fill="both", expand=True, padx=(0, 12))

        # Search bar
        search_bar = tk.Frame(list_panel, bg=BG_PANEL)
        search_bar.pack(fill="x", padx=1, pady=(1, 0))

        ttk.Label(search_bar, text="Search:", style="PanelLabel.TLabel").pack(
            side="left", padx=(8, 6), pady=6
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.apply_filter)
        search_entry = tk.Entry(
            search_bar, textvariable=self.search_var, bg=BG_INPUT, fg=TEXT_LIGHT,
            insertbackground=TEXT_LIGHT, relief="flat", font=(FONT_FAMILY, 10)
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=6, ipady=4)

        columns = ("display_name", "username", "id", "note")
        self.tree = ttk.Treeview(list_panel, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("display_name", text="DISPLAY NAME")
        self.tree.heading("username", text="USERNAME")
        self.tree.heading("id", text="ID")
        self.tree.heading("note", text="NOTE")
        self.tree.column("display_name", width=150)
        self.tree.column("username", width=140)
        self.tree.column("id", width=90, anchor="center")
        self.tree.column("note", width=220)
        self.tree.tag_configure("oddrow", background=BG_PANEL)
        self.tree.tag_configure("evenrow", background=ROW_ALT)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)

        scrollbar = ttk.Scrollbar(list_panel, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="left", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_select_friend)

        # Note editor panel (card style)
        note_panel = tk.Frame(main_frame, bg=BG_PANEL, highlightbackground=RED_SUBTLE,
                               highlightthickness=1, bd=0)
        note_panel.pack(side="left", fill="both", expand=False, ipadx=14, ipady=14)
        note_panel.configure(width=260)

        inner = ttk.Frame(note_panel, style="Panel.TFrame")
        inner.pack(fill="both", expand=True, padx=6, pady=6)

        ttk.Label(inner, text="SELECTED FRIEND", style="PanelLabel.TLabel",
                  font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")

        selected_row = ttk.Frame(inner, style="Panel.TFrame")
        selected_row.pack(fill="x", pady=(6, 16))

        # Fixed-size frame keeps a stable footprint whether we're showing
        # placeholder text or the actual avatar image.
        avatar_frame = tk.Frame(selected_row, bg=BG_INPUT, width=90, height=90,
                                 highlightbackground=RED_SUBTLE, highlightthickness=1)
        avatar_frame.pack(side="right", padx=(8, 0))
        avatar_frame.pack_propagate(False)

        self.avatar_label = tk.Label(avatar_frame, bg=BG_INPUT, text="", fg=TEXT_MUTED,
                                      font=(FONT_FAMILY, 8), justify="center")
        self.avatar_label.pack(fill="both", expand=True)

        name_frame = ttk.Frame(selected_row, style="Panel.TFrame")
        name_frame.pack(side="left", fill="both", expand=True)

        self.selected_label_var = tk.StringVar(value="No friend selected")
        ttk.Label(name_frame, textvariable=self.selected_label_var, style="PanelBold.TLabel",
                  wraplength=140).pack(anchor="w")

        ttk.Label(inner, text="NOTE", style="PanelLabel.TLabel",
                  font=(FONT_FAMILY, 8, "bold")).pack(anchor="w")
        self.note_text = tk.Text(inner, width=28, height=12, wrap="word",
                                  bg=BG_INPUT, fg=TEXT_LIGHT, insertbackground=TEXT_LIGHT,
                                  relief="flat", padx=8, pady=8, font=(FONT_FAMILY, 10))
        self.note_text.pack(fill="both", expand=True, pady=(6, 12))

        self.save_btn = ttk.Button(inner, text="SAVE NOTE", style="Accent.TButton",
                                    command=self.save_current_note, state="disabled")
        self.save_btn.pack(fill="x")

    # ---------- Actions ----------

    def fetch_friends(self):
        user_id = self.user_id_var.get().strip()
        if not user_id.isdigit():
            messagebox.showerror("Invalid ID", "User ID must be numeric.")
            return

        self.fetch_btn.config(state="disabled")
        self.status_var.set("Fetching...")
        self.unknown_var.set("Unknowns: 0")
        threading.Thread(target=self._fetch_friends_worker, args=(user_id,), daemon=True).start()

    def _fetch_friends_worker(self, user_id):
        try:
            username, display_name = get_username(user_id)
            raw_friends = get_friends(user_id)

            known_friends = []
            unknown_count = 0

            if raw_friends:
                friend_ids = [f["id"] for f in raw_friends]
                name_lookup = get_usernames_batch(friend_ids)
                for f in raw_friends:
                    info = name_lookup.get(f["id"])
                    if not info:
                        # No user record came back -> account is deleted/unknown.
                        unknown_count += 1
                        continue
                    f["name"] = info.get("name", "Unknown")
                    f["displayName"] = info.get("displayName", f["name"])
                    known_friends.append(f)

                if known_friends:
                    try:
                        avatar_urls = get_avatar_urls_batch([f["id"] for f in known_friends])
                        for f in known_friends:
                            f["avatarUrl"] = avatar_urls.get(f["id"])
                    except requests.exceptions.RequestException:
                        # Avatars are a nice-to-have; don't fail the whole fetch over them.
                        for f in known_friends:
                            f.setdefault("avatarUrl", None)

            self.root.after(0, self._on_fetch_success, username, display_name, known_friends, unknown_count)
        except requests.exceptions.RequestException as e:
            self.root.after(0, self._on_fetch_error, str(e))

    def _on_fetch_success(self, username, display_name, friends, unknown_count):
        self.friends = friends
        self.unknown_count = unknown_count
        self.unknown_var.set(f"Unknowns: {unknown_count}")
        self.fetch_btn.config(state="normal")

        if not friends and unknown_count == 0:
            self.status_var.set(f"No friends found for {display_name} (@{username}), or their list is private.")
        else:
            self.status_var.set(f"Loaded {len(friends)} friends for {display_name} (@{username}).")

        self.refresh_tree()

    def _on_fetch_error(self, error_msg):
        self.fetch_btn.config(state="normal")
        self.status_var.set("Error fetching friends.")
        messagebox.showerror("Error", f"Could not fetch friends:\n{error_msg}")

    def apply_filter(self, *args):
        self.refresh_tree()

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        visible_index = 0
        for friend in self.friends:
            fid = str(friend["id"])
            note = self.notes.get(fid, {}).get("note", "")

            if query:
                haystack = " ".join([
                    friend.get("displayName", ""),
                    friend.get("name", ""),
                    fid,
                    note,
                ]).lower()
                if query not in haystack:
                    continue

            tag = "evenrow" if visible_index % 2 == 0 else "oddrow"
            self.tree.insert(
                "", "end", iid=fid,
                values=(friend.get("displayName", ""), friend.get("name", ""), fid, note),
                tags=(tag,)
            )
            visible_index += 1

    def on_select_friend(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        fid = selection[0]
        friend = next((f for f in self.friends if str(f["id"]) == fid), None)
        if not friend:
            return

        self.selected_friend_id = fid
        self.selected_label_var.set(f"{friend.get('displayName')} (@{friend.get('name')})")

        existing_note = self.notes.get(fid, {}).get("note", "")
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", existing_note)
        self.save_btn.config(state="normal")

        self._show_avatar_for(fid, friend.get("avatarUrl"))

    def _show_avatar_for(self, fid, avatar_url):
        # Bump the token so any in-flight load for a previous selection
        # knows to discard its result instead of overwriting the UI late.
        self.avatar_request_token += 1
        my_token = self.avatar_request_token

        cached = self.avatar_image_cache.get(fid)
        if cached is not None:
            self.avatar_label.config(image=cached, text="")
            return

        if not avatar_url:
            self.avatar_label.config(image="", text="No\navatar")
            return

        self.avatar_label.config(image="", text="Loading...")
        threading.Thread(
            target=self._load_avatar_worker, args=(fid, avatar_url, my_token), daemon=True
        ).start()

    def _load_avatar_worker(self, fid, avatar_url, token):
        try:
            image_bytes = download_image_bytes(avatar_url)
            self.root.after(0, self._on_avatar_loaded, fid, image_bytes, token)
        except requests.exceptions.RequestException:
            self.root.after(0, self._on_avatar_error, token)

    def _on_avatar_loaded(self, fid, image_bytes, token):
        if token != self.avatar_request_token:
            return  # user already selected someone else; discard this result
        try:
            photo = tk.PhotoImage(data=base64.b64encode(image_bytes))
        except tk.TclError:
            self._on_avatar_error(token)
            return
        self.avatar_image_cache[fid] = photo
        self.avatar_label.config(image=photo, text="")

    def _on_avatar_error(self, token):
        if token != self.avatar_request_token:
            return
        self.avatar_label.config(image="", text="No\navatar")

    def save_current_note(self):
        if not self.selected_friend_id:
            return
        friend = next((f for f in self.friends if str(f["id"]) == self.selected_friend_id), None)
        if not friend:
            return

        note_value = self.note_text.get("1.0", "end").strip()
        self.notes[self.selected_friend_id] = {
            "name": friend.get("name"),
            "displayName": friend.get("displayName"),
            "note": note_value,
        }
        save_notes(self.notes)
        self.refresh_tree()
        self.tree.selection_set(self.selected_friend_id)
        self.status_var.set(f"Note saved for {friend.get('displayName')}.")


def main():
    root = tk.Tk()
    app = FriendNotesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
