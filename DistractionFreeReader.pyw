import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import fitz  # PyMuPDF
from PIL import Image, ImageTk
import time
import json
import os
import sys
from pathlib import Path

# --- Platform-specific imports ---
try:
    import winreg
    IS_WINDOWS = True
except ImportError:
    IS_WINDOWS = False

# --- Configuration ---
BACKGROUND_COLOR = '#2e2e2e'  # Dark grey background
TEXT_COLOR = 'white'
BUTTON_COLOR = '#4a4a4a'
APP_NAME = "DistractionFreeReader"
# --- End Configuration ---

# --- Persistence and Startup Management ---

def get_state_file_path():
    """Gets the path for the state file in the user's AppData directory."""
    # Use a platform-neutral directory for storing app data
    app_data_dir = Path(os.getenv('APPDATA') or os.path.expanduser('~/.config')) / APP_NAME
    app_data_dir.mkdir(parents=True, exist_ok=True)
    return app_data_dir / 'session_state.json'

def save_state(filepath, page_num, end_time):
    """Saves the session state to a file and enables auto-start."""
    state = {
        'pdf_path': filepath,
        'page_num': page_num,
        'end_time': end_time
    }
    state_file = get_state_file_path()
    try:
        with open(state_file, 'w') as f:
            json.dump(state, f)
        add_to_startup()
    except IOError as e:
        print(f"Error saving state: {e}")

def load_state():
    """Loads the session state from a file."""
    state_file = get_state_file_path()
    if not state_file.exists():
        return None
    try:
        with open(state_file, 'r') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading state: {e}")
        return None

def clear_state():
    """Clears the session state file and disables auto-start."""
    state_file = get_state_file_path()
    if state_file.exists():
        try:
            state_file.unlink()
        except OSError as e:
            print(f"Error clearing state file: {e}")
    remove_from_startup()

def add_to_startup():
    """Adds the application to startup using the Windows Registry for faster boot."""
    if not IS_WINDOWS:
        print("INFO: Auto-startup is only supported on Windows.")
        return

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    
    # Determine the correct Python executable (pythonw.exe to hide console)
    python_exe_path = Path(sys.executable)
    # pythonw.exe is in the same directory as python.exe
    pythonw_exe_path = python_exe_path.parent / 'pythonw.exe'
    
    # Use pythonw.exe if it exists, otherwise fall back to the standard python.exe
    app_executable = str(pythonw_exe_path) if pythonw_exe_path.exists() else str(python_exe_path)
    
    app_script_path = os.path.abspath(sys.argv[0])
    command = f'"{app_executable}" "{app_script_path}"'

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        print("INFO: Added application to startup via Registry for session recovery.")
    except OSError as e:
        print(f"Error adding to startup registry: {e}")

def remove_from_startup():
    """Removes the application from startup by deleting the Registry key."""
    if not IS_WINDOWS:
        return

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        print("INFO: Removed application from startup registry.")
    except FileNotFoundError:
        # Key was not there, which is fine.
        pass
    except OSError as e:
        print(f"Error removing from startup registry: {e}")


class TimerSetupDialog(tk.Toplevel):
    """A modal dialog to get the timer duration from the user."""
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent)
        self.title("Set Reading Time")
        self.parent = parent
        self.total_seconds = 0

        self.configure(bg=BACKGROUND_COLOR)
        style = ttk.Style()
        style.configure('Timer.TLabel', background=BACKGROUND_COLOR, foreground=TEXT_COLOR)
        style.configure('Timer.TButton', background=BUTTON_COLOR, foreground=TEXT_COLOR)
        style.map('Timer.TButton', background=[('active', '#6a6a6a')])
        
        frame = ttk.Frame(self, padding="20", style='TFrame')
        frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(frame, text="Set your distraction-free reading time.", style='Timer.TLabel').pack(pady=(0, 10))

        input_frame = ttk.Frame(frame, style='TFrame')
        input_frame.pack(pady=10)

        self.hours_var = tk.StringVar(value='0')
        self.minutes_var = tk.StringVar(value='30')

        ttk.Label(input_frame, text="Hours:", style='Timer.TLabel').grid(row=0, column=0, padx=5, sticky="w")
        ttk.Entry(input_frame, textvariable=self.hours_var, width=5).grid(row=0, column=1, padx=5)
        
        ttk.Label(input_frame, text="Minutes:", style='Timer.TLabel').grid(row=0, column=2, padx=5, sticky="w")
        ttk.Entry(input_frame, textvariable=self.minutes_var, width=5).grid(row=0, column=3, padx=5)

        start_button = ttk.Button(frame, text="Start Session", command=self.on_start, style='Timer.TButton')
        start_button.pack(pady=10)
        
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.grab_set()
        self.wait_window(self)

    def on_start(self):
        try:
            hours = int(self.hours_var.get())
            minutes = int(self.minutes_var.get())
            if hours < 0 or minutes < 0:
                raise ValueError("Time cannot be negative.")
            self.total_seconds = (hours * 3600) + (minutes * 60)
            if self.total_seconds <= 0:
                messagebox.showwarning("Invalid Time", "Please set a duration greater than zero.", parent=self)
                return
            self.destroy()
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numbers for hours and minutes.", parent=self)

    def on_cancel(self):
        self.total_seconds = 0
        self.destroy()


class PDFTimerReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Distraction-Free PDF Reader")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=BACKGROUND_COLOR)

        self.pdf_document = None
        self.current_page_num = 0
        self.total_pages = 0
        self.tk_image = None
        self.image_on_canvas = None
        self.timer_running = False
        self.timer_after_id = None
        self.end_time = 0

        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        self._setup_styles()
        self._setup_gui()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_attempt_close)
        self.enable_controls()

        # Check for a saved state on startup
        self.root.after(100, self.check_for_saved_session)

    def _setup_styles(self):
        style = ttk.Style()
        style.configure('TFrame', background=BACKGROUND_COLOR)
        style.configure('TButton', padding=6, background=BUTTON_COLOR, foreground=TEXT_COLOR)
        style.map('TButton', background=[('active', '#6a6a6a')])
        style.configure('TLabel', background=BACKGROUND_COLOR, foreground=TEXT_COLOR, padding=5, font=('Helvetica', 12))
        style.configure('Timer.TLabel', background=BACKGROUND_COLOR, foreground=TEXT_COLOR, padding=5, font=('Helvetica', 16, 'bold'))
    
    def _setup_gui(self):
        top_frame = ttk.Frame(self.main_frame)
        top_frame.pack(pady=10, fill=tk.X)

        self.control_frame = ttk.Frame(top_frame)
        self.control_frame.pack(side=tk.LEFT, padx=10)

        self.select_button = ttk.Button(self.control_frame, text="Select PDF", command=self.select_pdf)
        self.select_button.pack(side=tk.LEFT, padx=5)
        
        self.prev_button = ttk.Button(self.control_frame, text="< Prev Page", command=self.prev_page, state=tk.DISABLED)
        self.prev_button.pack(side=tk.LEFT, padx=5)

        self.next_button = ttk.Button(self.control_frame, text="Next Page >", command=self.next_page, state=tk.DISABLED)
        self.next_button.pack(side=tk.LEFT, padx=5)

        self.page_label = ttk.Label(self.control_frame, text="Page: -/-")
        self.page_label.pack(side=tk.LEFT, padx=10)
        
        self.timer_label = ttk.Label(top_frame, text="00:00:00", style='Timer.TLabel')
        self.timer_label.pack(side=tk.RIGHT, padx=20)

        self.canvas = tk.Canvas(self.main_frame, bg="white", bd=0, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_mouse_wheel)
        self.canvas.bind("<Button-5>", self.on_mouse_wheel)

    def check_for_saved_session(self):
        """Checks for and automatically restores a previously saved session."""
        saved_state = load_state()
        if saved_state:
            remaining_time = saved_state.get('end_time', 0) - time.time()
            if remaining_time > 0:
                # Automatically resume the session without asking the user.
                self.start_session(
                    filepath=saved_state['pdf_path'],
                    duration_seconds=remaining_time,
                    page_num=saved_state['page_num'],
                    is_resume=True
                )
            else:
                # If the session has expired, silently clear the state.
                clear_state()

    def on_mouse_wheel(self, event):
        if self.pdf_document:
            # Respond to Linux (event.num) and Windows (event.delta) scroll events
            if event.num == 5 or event.delta < 0:
                self.canvas.yview_scroll(1, "units")
            elif event.num == 4 or event.delta > 0:
                self.canvas.yview_scroll(-1, "units")

    def select_pdf(self):
        if self.timer_running:
            messagebox.showwarning("Busy", "Cannot select a new PDF while a session is active.")
            return

        filepath = filedialog.askopenfilename(
            title="Select PDF File",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")]
        )
        if not filepath:
            return
            
        dialog = TimerSetupDialog(self.root)
        duration_seconds = dialog.total_seconds

        if duration_seconds > 0:
            self.start_session(filepath, duration_seconds)
        else:
            messagebox.showinfo("Cancelled", "PDF loading cancelled because no timer was set.")

    def start_session(self, filepath, duration_seconds, page_num=0, is_resume=False):
        self.reset_viewer()
        try:
            self.page_label.config(text=f"Loading: {Path(filepath).name}")
            self.root.update_idletasks()
            self.pdf_document = fitz.open(filepath)
            self.total_pages = self.pdf_document.page_count
            if self.total_pages == 0:
                messagebox.showerror("Error", "Selected PDF has no pages.")
                self.reset_viewer()
                return

            self.current_page_num = page_num
            self.load_page()
            self.start_timer(duration_seconds, is_resume)
            self.disable_controls()

            # Only save state for new sessions, not for resumed ones.
            if not is_resume:
                save_state(filepath, self.current_page_num, self.end_time)

        except Exception as e:
            messagebox.showerror("Error Loading PDF", f"Failed to load or render PDF.\nError: {e}")
            self.reset_viewer()
            clear_state()

    def load_page(self):
        if not self.pdf_document: return
        self.page_label.config(text=f"Rendering Page {self.current_page_num + 1} of {self.total_pages}...")
        self.root.update_idletasks()

        try:
            page = self.pdf_document[self.current_page_num]
            canvas_width = self.canvas.winfo_width()
            if canvas_width <= 1: canvas_width = 1024 # Default width if not rendered yet
            
            # Calculate zoom factor to fit page width to canvas width
            zoom_x = canvas_width / page.rect.width
            mat = fitz.Matrix(zoom_x, zoom_x)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            self.tk_image = ImageTk.PhotoImage(img)

            if self.image_on_canvas: self.canvas.delete(self.image_on_canvas)

            # Center the image horizontally on the canvas
            x_pos = max(0, (canvas_width - pix.width) / 2)
            self.image_on_canvas = self.canvas.create_image(x_pos, 0, anchor=tk.NW, image=self.tk_image)
            self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
            self.canvas.yview_moveto(0)
            
            self.update_nav_buttons()

        except Exception as e:
            messagebox.showerror("Error Rendering Page", f"Failed to render page {self.current_page_num + 1}.\nError: {e}")
            self.finish_session()

    def update_nav_buttons(self):
        self.page_label.config(text=f"Page: {self.current_page_num + 1} / {self.total_pages}")
        self.prev_button.config(state=tk.NORMAL if self.current_page_num > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if self.current_page_num < self.total_pages - 1 else tk.DISABLED)

    def next_page(self):
        if self.pdf_document and self.current_page_num < self.total_pages - 1:
            self.current_page_num += 1
            self.load_page()
            save_state(self.pdf_document.name, self.current_page_num, self.end_time)

    def prev_page(self):
        if self.pdf_document and self.current_page_num > 0:
            self.current_page_num -= 1
            self.load_page()
            save_state(self.pdf_document.name, self.current_page_num, self.end_time)

    def start_timer(self, duration_seconds, is_resume=False):
        if not is_resume:
            self.end_time = time.time() + duration_seconds
        else:
            # For resumed sessions, the end_time is already calculated from the saved state.
            saved_state = load_state()
            if saved_state:
                self.end_time = saved_state['end_time']
            else: # Fallback if state is somehow gone
                self.end_time = time.time() + duration_seconds
                
        self.timer_running = True
        self.update_timer()

    def update_timer(self):
        if not self.timer_running: return

        remaining_seconds = self.end_time - time.time()
        if remaining_seconds <= 0:
            self.timer_label.config(text="00:00:00")
            self.finish_session()
            return

        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        seconds = int(remaining_seconds % 60)
        self.timer_label.config(text=f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        
        self.timer_after_id = self.root.after(1000, self.update_timer)

    def finish_session(self):
        self.timer_running = False
        if self.timer_after_id:
            self.root.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        
        clear_state()
        messagebox.showinfo("Time's Up!", "Your reading session has finished. You can now close the window.")
        self.enable_controls()
        self.page_label.config(text="Session Finished")

    def reset_viewer(self):
        if self.pdf_document:
            try: self.pdf_document.close()
            except Exception: pass
            self.pdf_document = None

        self.current_page_num = 0
        self.total_pages = 0
        if self.image_on_canvas: self.canvas.delete(self.image_on_canvas)
        self.image_on_canvas = None
        self.tk_image = None
        self.canvas.config(scrollregion=(0,0,1,1))
        self.page_label.config(text="Page: -/-")
        self.timer_label.config(text="00:00:00")
        self.update_nav_buttons()

    def on_attempt_close(self):
        if self.timer_running:
            if messagebox.askyesno("Confirm Exit", "A reading session is active. Exiting now will require the app to restart on next login to continue.\n\nAre you sure you want to exit?"):
                self.root.destroy()
        else:
            self.root.destroy()

    def disable_controls(self):
        """Disables controls and shortcuts during a timed session."""
        self.root.attributes('-topmost', True)
        self.select_button.config(state=tk.DISABLED)
        # The on_attempt_close method will handle close attempts
        self.root.protocol("WM_DELETE_WINDOW", self.on_attempt_close)
        self.root.bind("<Alt-F4>", lambda e: "break")
        self.root.bind_all("<Control-Key>", lambda e: "break")
        print("INFO: Distraction-free mode ENABLED. Window controls and Ctrl key are disabled.")

    def enable_controls(self):
        """Enables controls when no session is active."""
        self.root.attributes('-topmost', False)
        self.select_button.config(state=tk.NORMAL)
        self.root.protocol("WM_DELETE_WINDOW", self.on_attempt_close)
        self.root.unbind("<Alt-F4>")
        self.root.unbind_all("<Control-Key>")
        print("INFO: Distraction-free mode DISABLED. Window controls are enabled.")


if __name__ == "__main__":
    # To run this application without a console window appearing,
    # save the file with a .pyw extension and run it manually.
    # The application will automatically use pythonw.exe for startup tasks.
    root = tk.Tk()
    app = PDFTimerReaderApp(root)
    root.mainloop()
