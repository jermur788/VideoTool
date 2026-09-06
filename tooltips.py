"""Small, non-interactive hover hints for desktop controls."""


class Tooltip:
    def __init__(self, widget, text, delay=500):
        import tkinter as tk
        self.tk = tk
        self.widget = widget
        self.text = text
        self.delay = delay
        self.pending = None
        self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        for event in ('<Leave>', '<ButtonPress>', '<FocusOut>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')
        root = widget.winfo_toplevel()
        root.bind('<Escape>', self.hide, add='+')
        for event in ('<Configure>', '<Unmap>', '<FocusOut>'):
            root.bind(event, lambda e, root=root: self.hide() if e.widget == root else None, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(self.delay, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
            return
        tip = self.tk.Toplevel(self.widget)
        self.window = tip
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes('-topmost', True)
        self.tk.Label(tip, text=self.text, justify='left', wraplength=340,
                      background='#fffbe6', foreground='#202020', relief='solid',
                      borderwidth=1, padx=10, pady=7, font=('Sans', 10)).pack()
        tip.update_idletasks()
        width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - width - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + height > self.widget.winfo_screenheight() - 8:
            y = self.widget.winfo_rooty() - height - 6
        tip.geometry(f'+{max(0, x)}+{max(0, y)}')
        tip.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except self.tk.TclError:
                pass
            self.pending = None
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except self.tk.TclError:
                pass
