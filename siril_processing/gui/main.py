"""
Graphical user interface for SirilProcessing using tkinter.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from ..core import DarkLibrary, BiasLibrary


class SirilProcessingGUI:
    """Main GUI application for managing calibration libraries."""
    
    def __init__(self, root: tk.Tk):
        """Initialize the GUI."""
        self.root = root
        self.root.title("Siril Processing - Calibration Library Manager")
        self.root.geometry("900x600")
        
        # Library instances
        self.dark_library: Optional[DarkLibrary] = None
        self.bias_library: Optional[BiasLibrary] = None
        
        # Create UI
        self.create_widgets()
    
    def create_widgets(self):
        """Create the main UI widgets."""
        # Create notebook (tabs)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create tabs
        self.dark_tab = ttk.Frame(self.notebook)
        self.bias_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.dark_tab, text="Dark Library")
        self.notebook.add(self.bias_tab, text="Bias Library")
        
        # Setup dark tab
        self.setup_dark_tab()
        
        # Setup bias tab
        self.setup_bias_tab()
    
    def setup_dark_tab(self):
        """Setup the dark library tab."""
        # Library path selection
        path_frame = ttk.LabelFrame(self.dark_tab, text="Library Path", padding=10)
        path_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.dark_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.dark_path_var, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="Browse...", command=self.browse_dark_library).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="Open Library", command=self.open_dark_library).pack(side=tk.LEFT, padx=5)
        
        # Actions frame
        action_frame = ttk.LabelFrame(self.dark_tab, text="Actions", padding=10)
        action_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(action_frame, text="Add Dark Master", command=self.add_dark_master).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Remove Selected", command=self.remove_dark_master).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Refresh", command=self.refresh_dark_list).pack(side=tk.LEFT, padx=5)
        
        # Masters list
        list_frame = ttk.LabelFrame(self.dark_tab, text="Dark Masters", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Create treeview
        columns = ("filename", "exposure", "temperature", "binning", "gain")
        self.dark_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)
        
        self.dark_tree.heading("filename", text="Filename")
        self.dark_tree.heading("exposure", text="Exposure (s)")
        self.dark_tree.heading("temperature", text="Temp (°C)")
        self.dark_tree.heading("binning", text="Binning")
        self.dark_tree.heading("gain", text="Gain")
        
        self.dark_tree.column("filename", width=250)
        self.dark_tree.column("exposure", width=100)
        self.dark_tree.column("temperature", width=100)
        self.dark_tree.column("binning", width=80)
        self.dark_tree.column("gain", width=80)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.dark_tree.yview)
        self.dark_tree.configure(yscrollcommand=scrollbar.set)
        
        self.dark_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Info label
        self.dark_info_var = tk.StringVar(value="No library opened")
        ttk.Label(self.dark_tab, textvariable=self.dark_info_var).pack(padx=10, pady=5)
    
    def setup_bias_tab(self):
        """Setup the bias library tab."""
        # Library path selection
        path_frame = ttk.LabelFrame(self.bias_tab, text="Library Path", padding=10)
        path_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.bias_path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.bias_path_var, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="Browse...", command=self.browse_bias_library).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="Open Library", command=self.open_bias_library).pack(side=tk.LEFT, padx=5)
        
        # Actions frame
        action_frame = ttk.LabelFrame(self.bias_tab, text="Actions", padding=10)
        action_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(action_frame, text="Add Bias Master", command=self.add_bias_master).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Remove Selected", command=self.remove_bias_master).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Refresh", command=self.refresh_bias_list).pack(side=tk.LEFT, padx=5)
        
        # Masters list
        list_frame = ttk.LabelFrame(self.bias_tab, text="Bias Masters", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Create treeview
        columns = ("filename", "temperature", "binning", "gain")
        self.bias_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)
        
        self.bias_tree.heading("filename", text="Filename")
        self.bias_tree.heading("temperature", text="Temp (°C)")
        self.bias_tree.heading("binning", text="Binning")
        self.bias_tree.heading("gain", text="Gain")
        
        self.bias_tree.column("filename", width=300)
        self.bias_tree.column("temperature", width=100)
        self.bias_tree.column("binning", width=80)
        self.bias_tree.column("gain", width=80)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.bias_tree.yview)
        self.bias_tree.configure(yscrollcommand=scrollbar.set)
        
        self.bias_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Info label
        self.bias_info_var = tk.StringVar(value="No library opened")
        ttk.Label(self.bias_tab, textvariable=self.bias_info_var).pack(padx=10, pady=5)
    
    # Dark library methods
    
    def browse_dark_library(self):
        """Browse for dark library directory."""
        directory = filedialog.askdirectory(title="Select Dark Library Directory")
        if directory:
            self.dark_path_var.set(directory)
    
    def open_dark_library(self):
        """Open the dark library."""
        path = self.dark_path_var.get()
        if not path:
            messagebox.showerror("Error", "Please select a library path")
            return
        
        try:
            self.dark_library = DarkLibrary(path)
            self.refresh_dark_list()
            messagebox.showinfo("Success", "Dark library opened successfully")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open library: {e}")
    
    def refresh_dark_list(self):
        """Refresh the dark masters list."""
        if not self.dark_library:
            return
        
        # Clear existing items
        for item in self.dark_tree.get_children():
            self.dark_tree.delete(item)
        
        # Add masters
        masters = self.dark_library.list_masters()
        for master in masters:
            props = master.get("properties", {})
            self.dark_tree.insert("", tk.END, values=(
                master["filename"],
                props.get("exposure", "N/A"),
                props.get("temperature", "N/A"),
                props.get("binning", "N/A"),
                props.get("gain", "N/A")
            ))
        
        # Update info
        info = self.dark_library.get_library_info()
        self.dark_info_var.set(f"Library: {info['library_path']} | Total: {info['total_masters']} masters")
    
    def add_dark_master(self):
        """Add a dark master to the library."""
        if not self.dark_library:
            messagebox.showerror("Error", "Please open a library first")
            return
        
        # Open file dialog
        file_path = filedialog.askopenfilename(
            title="Select Dark Master FITS File",
            filetypes=[("FITS files", "*.fit *.fits *.fts"), ("All files", "*.*")]
        )
        
        if not file_path:
            return
        
        # Create dialog for properties
        dialog = AddDarkDialog(self.root)
        self.root.wait_window(dialog.dialog)
        
        if dialog.result:
            try:
                success = self.dark_library.add_dark_master(
                    master_path=file_path,
                    exposure=dialog.result["exposure"],
                    temperature=dialog.result["temperature"],
                    binning=dialog.result["binning"],
                    gain=dialog.result.get("gain"),
                    offset=dialog.result.get("offset"),
                    camera=dialog.result.get("camera")
                )
                
                if success:
                    messagebox.showinfo("Success", "Dark master added successfully")
                    self.refresh_dark_list()
                else:
                    messagebox.showerror("Error", "Failed to add dark master")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add dark master: {e}")
    
    def remove_dark_master(self):
        """Remove selected dark master from library."""
        if not self.dark_library:
            return
        
        selection = self.dark_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a master to remove")
            return
        
        item = self.dark_tree.item(selection[0])
        filename = item["values"][0]
        
        if messagebox.askyesno("Confirm", f"Remove {filename} from library?"):
            success = self.dark_library.remove_master(filename)
            if success:
                messagebox.showinfo("Success", "Dark master removed successfully")
                self.refresh_dark_list()
            else:
                messagebox.showerror("Error", "Failed to remove dark master")
    
    # Bias library methods
    
    def browse_bias_library(self):
        """Browse for bias library directory."""
        directory = filedialog.askdirectory(title="Select Bias Library Directory")
        if directory:
            self.bias_path_var.set(directory)
    
    def open_bias_library(self):
        """Open the bias library."""
        path = self.bias_path_var.get()
        if not path:
            messagebox.showerror("Error", "Please select a library path")
            return
        
        try:
            self.bias_library = BiasLibrary(path)
            self.refresh_bias_list()
            messagebox.showinfo("Success", "Bias library opened successfully")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open library: {e}")
    
    def refresh_bias_list(self):
        """Refresh the bias masters list."""
        if not self.bias_library:
            return
        
        # Clear existing items
        for item in self.bias_tree.get_children():
            self.bias_tree.delete(item)
        
        # Add masters
        masters = self.bias_library.list_masters()
        for master in masters:
            props = master.get("properties", {})
            self.bias_tree.insert("", tk.END, values=(
                master["filename"],
                props.get("temperature", "N/A"),
                props.get("binning", "N/A"),
                props.get("gain", "N/A")
            ))
        
        # Update info
        info = self.bias_library.get_library_info()
        self.bias_info_var.set(f"Library: {info['library_path']} | Total: {info['total_masters']} masters")
    
    def add_bias_master(self):
        """Add a bias master to the library."""
        if not self.bias_library:
            messagebox.showerror("Error", "Please open a library first")
            return
        
        # Open file dialog
        file_path = filedialog.askopenfilename(
            title="Select Bias Master FITS File",
            filetypes=[("FITS files", "*.fit *.fits *.fts"), ("All files", "*.*")]
        )
        
        if not file_path:
            return
        
        # Create dialog for properties
        dialog = AddBiasDialog(self.root)
        self.root.wait_window(dialog.dialog)
        
        if dialog.result:
            try:
                success = self.bias_library.add_bias_master(
                    master_path=file_path,
                    temperature=dialog.result["temperature"],
                    binning=dialog.result["binning"],
                    gain=dialog.result.get("gain"),
                    offset=dialog.result.get("offset"),
                    camera=dialog.result.get("camera")
                )
                
                if success:
                    messagebox.showinfo("Success", "Bias master added successfully")
                    self.refresh_bias_list()
                else:
                    messagebox.showerror("Error", "Failed to add bias master")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add bias master: {e}")
    
    def remove_bias_master(self):
        """Remove selected bias master from library."""
        if not self.bias_library:
            return
        
        selection = self.bias_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a master to remove")
            return
        
        item = self.bias_tree.item(selection[0])
        filename = item["values"][0]
        
        if messagebox.askyesno("Confirm", f"Remove {filename} from library?"):
            success = self.bias_library.remove_master(filename)
            if success:
                messagebox.showinfo("Success", "Bias master removed successfully")
                self.refresh_bias_list()
            else:
                messagebox.showerror("Error", "Failed to remove bias master")


class AddDarkDialog:
    """Dialog for adding a dark master."""
    
    def __init__(self, parent):
        """Initialize the dialog."""
        self.result = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Add Dark Master")
        self.dialog.geometry("400x300")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Create fields
        frame = ttk.Frame(self.dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Exposure
        ttk.Label(frame, text="Exposure (seconds):*").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.exposure_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.exposure_var, width=20).grid(row=0, column=1, sticky=tk.EW, pady=5)
        
        # Temperature
        ttk.Label(frame, text="Temperature (°C):*").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.temp_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.temp_var, width=20).grid(row=1, column=1, sticky=tk.EW, pady=5)
        
        # Binning
        ttk.Label(frame, text="Binning:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.binning_var = tk.StringVar(value="1x1")
        ttk.Entry(frame, textvariable=self.binning_var, width=20).grid(row=2, column=1, sticky=tk.EW, pady=5)
        
        # Gain
        ttk.Label(frame, text="Gain:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.gain_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.gain_var, width=20).grid(row=3, column=1, sticky=tk.EW, pady=5)
        
        # Offset
        ttk.Label(frame, text="Offset:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.offset_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.offset_var, width=20).grid(row=4, column=1, sticky=tk.EW, pady=5)
        
        # Camera
        ttk.Label(frame, text="Camera:").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.camera_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.camera_var, width=20).grid(row=5, column=1, sticky=tk.EW, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)
        
        ttk.Button(button_frame, text="OK", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel).pack(side=tk.LEFT, padx=5)
        
        frame.columnconfigure(1, weight=1)
    
    def ok(self):
        """Handle OK button."""
        try:
            exposure = float(self.exposure_var.get())
            temperature = float(self.temp_var.get())
            binning = self.binning_var.get() or "1x1"
            
            self.result = {
                "exposure": exposure,
                "temperature": temperature,
                "binning": binning
            }
            
            if self.gain_var.get():
                self.result["gain"] = int(self.gain_var.get())
            if self.offset_var.get():
                self.result["offset"] = int(self.offset_var.get())
            if self.camera_var.get():
                self.result["camera"] = self.camera_var.get()
            
            self.dialog.destroy()
        except ValueError:
            messagebox.showerror("Error", "Invalid input values")
    
    def cancel(self):
        """Handle Cancel button."""
        self.dialog.destroy()


class AddBiasDialog:
    """Dialog for adding a bias master."""
    
    def __init__(self, parent):
        """Initialize the dialog."""
        self.result = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Add Bias Master")
        self.dialog.geometry("400x250")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Create fields
        frame = ttk.Frame(self.dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Temperature
        ttk.Label(frame, text="Temperature (°C):*").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.temp_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.temp_var, width=20).grid(row=0, column=1, sticky=tk.EW, pady=5)
        
        # Binning
        ttk.Label(frame, text="Binning:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.binning_var = tk.StringVar(value="1x1")
        ttk.Entry(frame, textvariable=self.binning_var, width=20).grid(row=1, column=1, sticky=tk.EW, pady=5)
        
        # Gain
        ttk.Label(frame, text="Gain:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.gain_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.gain_var, width=20).grid(row=2, column=1, sticky=tk.EW, pady=5)
        
        # Offset
        ttk.Label(frame, text="Offset:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.offset_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.offset_var, width=20).grid(row=3, column=1, sticky=tk.EW, pady=5)
        
        # Camera
        ttk.Label(frame, text="Camera:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.camera_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.camera_var, width=20).grid(row=4, column=1, sticky=tk.EW, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=5, column=0, columnspan=2, pady=20)
        
        ttk.Button(button_frame, text="OK", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel).pack(side=tk.LEFT, padx=5)
        
        frame.columnconfigure(1, weight=1)
    
    def ok(self):
        """Handle OK button."""
        try:
            temperature = float(self.temp_var.get())
            binning = self.binning_var.get() or "1x1"
            
            self.result = {
                "temperature": temperature,
                "binning": binning
            }
            
            if self.gain_var.get():
                self.result["gain"] = int(self.gain_var.get())
            if self.offset_var.get():
                self.result["offset"] = int(self.offset_var.get())
            if self.camera_var.get():
                self.result["camera"] = self.camera_var.get()
            
            self.dialog.destroy()
        except ValueError:
            messagebox.showerror("Error", "Invalid input values")
    
    def cancel(self):
        """Handle Cancel button."""
        self.dialog.destroy()


def main():
    """Main entry point for the GUI."""
    root = tk.Tk()
    app = SirilProcessingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
