# main.py
import tkinter as tk
from ui.dashboard_window import DashboardWindow

if __name__ == "__main__":
    root = tk.Tk()
    app = DashboardWindow(root)
    root.mainloop() 