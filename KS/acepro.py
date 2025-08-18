import logging
import json
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango, Gdk, GLib
from ks_includes.screen_panel import ScreenPanel
from ks_includes.widgets.keypad import Keypad


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title)
        self.current_slot_settings = {"type": "PLA", "color": "255,255,255", "temp": "200"}
        self.ace_status = {}
        self.slot_inventory = []
        self.dryer_enabled = False
        self.current_loaded_slot = -1  # Cache the loaded slot
        self.numpad_visible = False  # Track numpad state
        self.endless_spool_enabled = False  # Track endless spool status
        
        # Initialize slot components lists
        self.slot_boxes = []
        self.slot_labels = []
        self.slot_color_boxes = []
        self.slot_buttons = []
        
        # Store actual slot data for configuration screen
        self.slot_data = [
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"},
            {"material": "PLA", "color": [255, 255, 255], "temp": 200, "status": "empty"}
        ]
        
        # Create main screen layout
        self.create_main_screen()
        
        # Add custom CSS for rounded boxes and color indicators
        self.add_custom_css()
        
        # Subscribe to saved_variables updates
        if hasattr(self._screen.printer, 'klippy') and hasattr(self._screen.printer.klippy, 'subscribe_object'):
            try:
                self._screen.printer.klippy.subscribe_object("saved_variables", ["variables"])
                logging.info("ACE: Subscribed to saved_variables updates")
            except Exception as e:
                logging.error(f"ACE: Failed to subscribe to saved_variables: {e}")
        
        # Initialize loaded slot from saved_variables (will be updated in get_current_loaded_slot)
        
        # Try to initialize the current loaded slot immediately
        self.initialize_loaded_slot()
    
    def add_custom_css(self):
        """Add custom CSS for slot appearance - using specific ACE classes to avoid conflicts"""
        css_provider = Gtk.CssProvider()
        css = """
        .ace_slot_color_indicator {
            border: 1px solid #333333;
            border-radius: 3px;
        }
        
        .ace_slot_button {
            border-radius: 10px;
            background-color: #2a2a2a;
            border: 2px solid #444444;
        }
        
        .ace_slot_button:hover {
            border-color: #666666;
        }
        
        .ace_slot_loaded {
            background-color: white;
            color: black;
        }
        
        .ace_slot_loaded .ace_slot_label {
            color: black;
        }
        
        .ace_slot_loaded .ace_slot_number {
            color: black;
        }
        
        .ace_slot_loaded * {
            color: black;
        }
        
        .ace_slot_empty {
            background-color: #2a2a2a;
            color: white;
        }
        
        .ace_slot_empty .ace_slot_label {
            color: white;
        }
        
        .ace_slot_empty .ace_slot_number {
            color: white;
            opacity: 0.8;
        }
        
        .ace_slot_number {
            font-size: 0.9em;
            opacity: 0.8;
        }
        
        .ace_slot_label {
            color: inherit;
        }
        
        .ace_color_preview {
            border: 2px solid #333333;
            border-radius: 5px;
            min-width: 20px;
            min-height: 15px;
        }
        
        .ace_numpad_button {
            background-color: #4a4a4a;
            color: white;
            border: 1px solid #666666;
            border-radius: 5px;
            font-size: 14px;
            font-weight: bold;
        }
        
        .ace_numpad_button:hover {
            background-color: #5a5a5a;
        }
        
        .ace_numpad_function {
            background-color: #666666;
            color: white;
        }
        """
        css_provider.load_from_data(css.encode())
        
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    
    def set_slot_color(self, color_box, rgb_color):
        """Set the color of a slot's color indicator"""
        r, g, b = rgb_color
        color = Gdk.RGBA(r/255.0, g/255.0, b/255.0, 1.0)
        color_box.override_background_color(Gtk.StateFlags.NORMAL, color)
    
    def get_current_loaded_slot(self):
        """Get the currently loaded slot"""
        try:
            # Try to get from printer data first
            if hasattr(self._screen, 'printer') and hasattr(self._screen.printer, 'data'):
                printer_data = self._screen.printer.data
                
                # Check for saved_variables
                if 'saved_variables' in printer_data:
                    save_vars = printer_data['saved_variables']
                    
                    if isinstance(save_vars, dict) and 'variables' in save_vars:
                        variables = save_vars['variables']
                        
                        if 'ace_current_index' in variables:
                            value = int(variables['ace_current_index'])
                            self.current_loaded_slot = value
                            return value
            
            # Return cached value or default
            return getattr(self, 'current_loaded_slot', -1)
            
        except Exception as e:
            logging.error(f"ACE: Error reading ace_current_index: {e}")
            return getattr(self, 'current_loaded_slot', -1)
    
    def initialize_loaded_slot(self):
        """Initialize the loaded slot from saved_variables or query ACE status"""
        # Try to get the current loaded slot
        current_slot = self.get_current_loaded_slot()
        
        # If we still don't have a valid slot, query ACE for current status
        if current_slot == -1:
            logging.info("ACE: No loaded slot found, will query ACE status")
            # Query ACE for current loaded slot index
            if hasattr(self._screen, '_ws') and hasattr(self._screen._ws, 'klippy'):
                self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
                logging.info("ACE: Sent ACE_GET_CURRENT_INDEX command")
    
    def show_number_input(self, title, message, current_value, min_val, max_val, callback):
        """Show number input dialog using compact custom numpad"""
        # Create a very compact custom numpad that fits in dialog
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_margin_left(5)
        vbox.set_margin_right(5)
        vbox.set_margin_top(5)
        vbox.set_margin_bottom(5)
        
        # Store callback and constraints
        self.temp_input_callback = callback
        self.temp_min = min_val
        self.temp_max = max_val
        
        # Compact title
        title_label = Gtk.Label(label=f"{message} ({min_val}-{max_val})")
        title_label.get_style_context().add_class("description")
        vbox.pack_start(title_label, False, False, 0)
        
        # Entry field with close button
        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        self.temp_entry = Gtk.Entry()
        self.temp_entry.set_text(str(current_value))
        self.temp_entry.set_halign(Gtk.Align.CENTER)
        self.temp_entry.set_size_request(100, 30)
        entry_box.pack_start(self.temp_entry, True, True, 0)
        
        # Close button
        close_btn = self._gtk.Button("cancel", scale=0.6)
        close_btn.set_size_request(30, 30)
        close_btn.connect("clicked", self.close_temp_dialog)
        entry_box.pack_start(close_btn, False, False, 0)
        
        vbox.pack_start(entry_box, False, False, 2)
        
        # Compact number grid (smaller buttons)
        numpad = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        numpad.set_row_spacing(2)
        numpad.set_column_spacing(2)
        
        # Number buttons 1-9, 0, backspace, decimal
        buttons = [
            ['1', '2', '3'],
            ['4', '5', '6'], 
            ['7', '8', '9'],
            ['⌫', '0', '.']
        ]
        
        for row, button_row in enumerate(buttons):
            for col, btn_text in enumerate(button_row):
                btn = Gtk.Button(label=btn_text)
                btn.set_size_request(50, 35)  # Small compact buttons
                btn.get_style_context().add_class("numpad_key")
                if btn_text == '⌫':
                    btn.connect("clicked", self.numpad_backspace)
                else:
                    btn.connect("clicked", self.numpad_clicked, btn_text)
                numpad.attach(btn, col, row, 1, 1)
        
        vbox.pack_start(numpad, False, False, 2)
        
        # OK button
        ok_btn = self._gtk.Button("complete", "OK", "color1")
        ok_btn.set_size_request(-1, 35)
        ok_btn.connect("clicked", self.handle_temp_ok)
        vbox.pack_start(ok_btn, False, False, 2)
        
        # Create dialog with no extra buttons
        buttons = []
        
        def response_callback(dialog, response_id):
            self.temp_input_dialog = None
        
        self.temp_input_dialog = self._gtk.Dialog(title, buttons, vbox, response_callback)
    
    def numpad_clicked(self, widget, digit):
        """Handle number button clicks"""
        current = self.temp_entry.get_text()
        self.temp_entry.set_text(current + digit)
    
    def numpad_backspace(self, widget):
        """Handle backspace button"""
        current = self.temp_entry.get_text()
        if len(current) > 0:
            self.temp_entry.set_text(current[:-1])
    
    def handle_temp_ok(self, widget):
        """Handle OK button click"""
        try:
            value = int(float(self.temp_entry.get_text()))
            if self.temp_min <= value <= self.temp_max:
                self.temp_input_callback(value)
                self.close_temp_dialog()
            else:
                self._screen.show_popup_message(f"Value must be between {self.temp_min}-{self.temp_max}")
        except (ValueError, TypeError):
            self._screen.show_popup_message("Invalid number")
    
    def close_temp_dialog(self, widget=None):
        """Close temperature input dialog"""
        if hasattr(self, 'temp_input_dialog') and self.temp_input_dialog:
            if hasattr(self._gtk, 'remove_dialog'):
                self._gtk.remove_dialog(self.temp_input_dialog)
            self.temp_input_dialog = None
    
    def show_color_picker(self, title, current_color, callback):
        """Show very compact color picker dialog"""
        # Store the callback for the color picker
        self.color_picker_callback = callback
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # Minimal spacing
        main_box.set_margin_left(10)  # Minimal margins
        main_box.set_margin_right(10)
        main_box.set_margin_top(10)
        main_box.set_margin_bottom(10)
        
        # Current color values
        self.picker_rgb = list(current_color)
        
        # Single row with preview and RGB display
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # Color preview
        self.color_preview_widget = Gtk.EventBox()
        self.color_preview_widget.get_style_context().add_class("ace_color_preview")
        self.color_preview_widget.set_size_request(60, 30)  # Smaller preview
        self.set_color_preview(self.color_preview_widget, self.picker_rgb)
        top_row.pack_start(self.color_preview_widget, False, False, 0)
        
        # RGB values display
        self.rgb_label_widget = Gtk.Label(label=f"RGB: {self.picker_rgb[0]},{self.picker_rgb[1]},{self.picker_rgb[2]}")
        self.rgb_label_widget.set_halign(Gtk.Align.START)
        top_row.pack_start(self.rgb_label_widget, True, True, 0)
        
        main_box.pack_start(top_row, False, False, 0)
        
        # Very compact sliders - horizontal layout
        sliders_grid = Gtk.Grid()
        sliders_grid.set_row_spacing(3)
        sliders_grid.set_column_spacing(5)
        
        # Create RGB sliders with references to update function
        self.create_mini_slider("R", 0, 0, sliders_grid)
        self.create_mini_slider("G", 1, 1, sliders_grid)
        self.create_mini_slider("B", 2, 2, sliders_grid)
        
        main_box.pack_start(sliders_grid, False, False, 0)
        
        # Minimal preset colors - single row
        #presets_label = Gtk.Label(label="Presets:")
        #presets_label.set_halign(Gtk.Align.START)
        #main_box.pack_start(presets_label, False, False, 0)
        
        # Just 6 most common colors in one row
        preset_colors = [
            ("W", [255, 255, 255]),
            ("K", [0, 0, 0]),
            ("R", [255, 0, 0]),
            ("G", [0, 255, 0]),
            ("B", [0, 0, 255]),
            ("Y", [255, 255, 0])
        ]
        
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        preset_row.set_homogeneous(True)
        
        for name, rgb in preset_colors:
            preset_btn = Gtk.Button()
            preset_btn.set_size_request(35, 25)  # Very small buttons
            preset_btn.set_label(name)
            
            # Set button colors
            preset_color = Gdk.RGBA(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0, 1.0)
            preset_btn.override_background_color(Gtk.StateFlags.NORMAL, preset_color)
            
            # Set text color
            brightness = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
            text_color = Gdk.RGBA(0, 0, 0, 1) if brightness > 128 else Gdk.RGBA(1, 1, 1, 1)
            preset_btn.override_color(Gtk.StateFlags.NORMAL, text_color)
            
            def on_preset_click(widget, preset_rgb):
                self.picker_rgb[:] = preset_rgb
                self.update_color_preview()
            
            preset_btn.connect("clicked", on_preset_click, rgb[:])
            preset_row.pack_start(preset_btn, True, True, 0)
        
        main_box.pack_start(preset_row, False, False, 0)
        
        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "OK", "response": Gtk.ResponseType.OK}
        ]
        
        # Use the KlipperScreen dialog method
        self._gtk.Dialog(title, buttons, main_box, self.color_picker_response)
    
    def create_mini_slider(self, color_name, color_index, row, grid):
        """Create a mini slider for RGB color component"""
        # Label
        label = Gtk.Label(label=f"{color_name}:")
        label.set_size_request(25, -1)
        grid.attach(label, 0, row, 1, 1)
        
        # Value label
        value_label = Gtk.Label(label=str(self.picker_rgb[color_index]))
        value_label.set_size_request(30, -1)
        value_label.set_halign(Gtk.Align.END)
        grid.attach(value_label, 1, row, 1, 1)
        
        # Slider
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
        slider.set_value(self.picker_rgb[color_index])
        slider.set_size_request(150, 20)  # Compact slider
        slider.set_draw_value(False)
        
        def on_slider_change(widget):
            value = int(widget.get_value())
            self.picker_rgb[color_index] = value
            value_label.set_text(str(value))
            self.update_color_preview()
        
        slider.connect("value-changed", on_slider_change)
        grid.attach(slider, 2, row, 1, 1)
    
    def update_color_preview(self):
        """Update the color preview in the color picker"""
        self.set_color_preview(self.color_preview_widget, self.picker_rgb)
        self.rgb_label_widget.set_text(f"RGB: {self.picker_rgb[0]},{self.picker_rgb[1]},{self.picker_rgb[2]}")
    
    def color_picker_response(self, dialog, response_id):
        """Handle color picker dialog response"""
        logging.info(f"ACE: Color picker response: {response_id}")
        try:
            if response_id == Gtk.ResponseType.OK:
                logging.info(f"ACE: Color picker OK clicked, RGB: {self.picker_rgb}")
                if self.color_picker_callback:
                    self.color_picker_callback(self.picker_rgb[:])
            else:
                logging.info("ACE: Color picker cancelled")
        finally:
            # Ensure dialog is closed by removing it
            if hasattr(self._gtk, 'remove_dialog') and dialog:
                self._gtk.remove_dialog(dialog)
                logging.info("ACE: Color picker dialog closed")
    
    def set_color_preview(self, widget, rgb_color):
        """Set the color preview widget background"""
        r, g, b = rgb_color
        color = Gdk.RGBA(r/255.0, g/255.0, b/255.0, 1.0)
        widget.override_background_color(Gtk.StateFlags.NORMAL, color)
    
    def update_slot_loaded_states(self):
        """Update all slot loaded states based on ace_current_index"""
        current_loaded = self.get_current_loaded_slot()
        
        logging.info(f"ACE: Current loaded slot: {current_loaded}")
        
        for slot in range(4):
            slot_btn = self.slot_buttons[slot]
            if slot == current_loaded:
                slot_btn.get_style_context().remove_class("ace_slot_empty")
                slot_btn.get_style_context().add_class("ace_slot_loaded")
            else:
                slot_btn.get_style_context().remove_class("ace_slot_loaded")
                slot_btn.get_style_context().add_class("ace_slot_empty")
        
        # Update status label
        if current_loaded != -1:
            self.status_label.set_text(f"ACE: Ready - Slot {current_loaded} loaded")
        else:
            self.status_label.set_text("ACE: Ready")
    
    def on_endless_spool_toggled(self, switch, state):
        """Handle endless spool switch toggle"""
        self.endless_spool_enabled = state
        
        # Send command to ACE system to enable/disable endless spool
        if state:
            self._screen._ws.klippy.gcode_script("ACE_ENABLE_ENDLESS_SPOOL")
            self._screen.show_popup_message("Endless spool enabled", 1)
            logging.info("ACE: Endless spool enabled")
        else:
            self._screen._ws.klippy.gcode_script("ACE_DISABLE_ENDLESS_SPOOL")
            self._screen.show_popup_message("Endless spool disabled", 1)
            logging.info("ACE: Endless spool disabled")
    
    def on_slot_clicked(self, widget, slot):
        """Handle slot button clicks"""
        current_loaded = self.get_current_loaded_slot()
        
        if current_loaded == slot:
            # Clicked on loaded slot - ask to unload
            self.show_unload_confirmation(slot)
        else:
            # Clicked on unloaded slot - ask to load
            self.show_load_confirmation(slot)
    
    def show_load_confirmation(self, slot):
        """Show confirmation dialog to load a slot"""
        slot_info = self.slot_labels[slot].get_text()
        if slot_info == "Empty":
            self._screen.show_popup_message("Slot is empty. Configure it first using the settings button.")
            return
        
        current_loaded = self.get_current_loaded_slot()
        message = f"Load Slot {slot}?\n\n{slot_info}"
        if current_loaded != -1:
            message += f"\n\nThis will unload Slot {current_loaded}"
        
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        
        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "Load", "response": Gtk.ResponseType.OK}
        ]
        
        def load_response(dialog, response_id):
            try:
                if response_id == Gtk.ResponseType.OK:
                    # Update cached value immediately for responsive UI
                    self.current_loaded_slot = slot
                    self.update_slot_loaded_states()
                    
                    # Send the actual command
                    self._screen._ws.klippy.gcode_script(f"ACE_CHANGE_TOOL TOOL={slot}")
                    self._screen.show_popup_message(f"Loading slot {slot}...", 1)
                    
                    # Return to main screen if we're in configuration mode
                    if hasattr(self, 'current_config_slot'):
                        self.return_to_main_screen()
                elif response_id == Gtk.ResponseType.CANCEL:
                    # Just close the dialog - no action needed
                    logging.info(f"ACE: Load slot {slot} cancelled by user")
            finally:
                # Ensure dialog is closed
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
        
        self._gtk.Dialog(f"Load Slot {slot}", buttons, label, load_response)
    
    def show_unload_confirmation(self, slot):
        """Show confirmation dialog to unload a slot"""
        slot_info = self.slot_labels[slot].get_text()
        message = f"Unload Slot {slot}?\n\n{slot_info}"
        
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        
        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "Unload", "response": Gtk.ResponseType.OK}
        ]
        
        def unload_response(dialog, response_id):
            try:
                if response_id == Gtk.ResponseType.OK:
                    # Update cached value immediately for responsive UI
                    self.current_loaded_slot = -1
                    self.update_slot_loaded_states()
                    
                    # Send the actual command
                    self._screen._ws.klippy.gcode_script(f"ACE_CHANGE_TOOL TOOL=-1")
                    self._screen.show_popup_message(f"Unloading slot {slot}...", 1)
                elif response_id == Gtk.ResponseType.CANCEL:
                    # Just close the dialog - no action needed
                    logging.info(f"ACE: Unload slot {slot} cancelled by user")
            finally:
                # Ensure dialog is closed
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
        
        self._gtk.Dialog(f"Unload Slot {slot}", buttons, label, unload_response)
    
    def activate(self):
        """Called when panel is shown"""
        logging.info("ACE: Panel activated")
        
        # Try to initialize the loaded slot from saved_variables again
        self.initialize_loaded_slot()
        
        # Update status which will query ACE and update display
        self.update_status()
    
    def delayed_init(self):
        """Delayed initialization to allow save_variables to load"""
        logging.info("ACE: Delayed initialization called")
        current_slot = self.get_current_loaded_slot()
        if current_slot != -1:
            logging.info(f"ACE: Delayed init found slot: {current_slot}")
            self.update_slot_loaded_states()
        return False  # Don't repeat the timeout
    
    def refresh_status(self, widget):
        """Manual refresh button"""
        # Query ACE data
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        
        # Query current loaded index
        self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
        
        # Update loaded state
        self.update_slot_loaded_states()
        self._screen.show_popup_message("Refreshing spool data...", 1)
    
    def show_slot_settings(self, widget, slot):
        """Show two-column slot configuration screen"""
        self.current_config_slot = slot
        self.show_slot_config_screen(slot)
    
    def show_slot_config_screen(self, slot):
        """Create compact two-column configuration screen that fits 480px"""
        # Load current values from slot data
        slot_info = self.slot_data[slot]
        self.config_material = slot_info["material"]
        self.config_color = slot_info["color"][:]  # Copy the color array
        self.config_temp = slot_info["temp"]
        
        logging.info(f"ACE: Loading slot {slot} config - Material: {self.config_material}, Color: {self.config_color}, Temp: {self.config_temp}")
        
        # Clear current content and create two-column layout
        for child in self.content.get_children():
            self.content.remove(child)
        
        # Create main grid with two columns - compact spacing
        main_grid = Gtk.Grid()
        main_grid.set_column_homogeneous(True)
        main_grid.set_row_spacing(2)  # Reduced from 10
        main_grid.set_column_spacing(1)  # Reduced from 10
        main_grid.set_margin_left(5)  # Reduced from 15
        main_grid.set_margin_right(10)
        main_grid.set_margin_top(2)   # Reduced from 15
        main_grid.set_margin_bottom(1)
        
        # Left column - Configuration options (compact)
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # Reduced from 15
        
        # Compact title for left column
        config_title = Gtk.Label(label=f"Configure Slot {slot}")
        config_title.get_style_context().add_class("description")  # Smaller than temperature_entry
        left_box.pack_start(config_title, False, False, 0)
        
        # Material selection button - smaller, with current value
        self.material_btn = self._gtk.Button("filament", f"Material: {self.config_material}", "color1")
        self.material_btn.set_size_request(-1, 45)  # Reduced from 60
        self.material_btn.connect("clicked", self.show_material_selection)
        left_box.pack_start(self.material_btn, False, False, 0)
        
        # Color selection button with preview - smaller, with current color
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # Reduced from 10
        
        # Smaller color preview with current color
        self.config_color_preview = Gtk.EventBox()
        self.config_color_preview.set_size_request(30, 30)  # Reduced from 40x40
        self.config_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_color_preview(self.config_color_preview, self.config_color)
        color_box.pack_start(self.config_color_preview, False, False, 0)
        
        # Smaller color button
        self.color_btn = self._gtk.Button("palette", "Select Color", "color2")
        self.color_btn.set_size_request(-1, 45)  # Reduced from 60
        self.color_btn.connect("clicked", self.show_color_selection)
        color_box.pack_start(self.color_btn, True, True, 0)
        
        left_box.pack_start(color_box, False, False, 0)
        
        # Temperature selection button - smaller, with current value
        self.temp_btn = self._gtk.Button("heat-up", f"Temperature: {self.config_temp}°C", "color3")
        self.temp_btn.set_size_request(-1, 45)  # Reduced from 60
        self.temp_btn.connect("clicked", self.show_temperature_selection)
        left_box.pack_start(self.temp_btn, False, False, 0)
        
        # Compact action buttons
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)  # Reduced from 10
        action_box.set_homogeneous(True)
        
        # Smaller save button
        save_btn = self._gtk.Button("complete", "Save", "color1")
        save_btn.set_size_request(-1, 40)  # Reduced from 50
        save_btn.connect("clicked", self.save_slot_config)
        action_box.pack_start(save_btn, True, True, 0)
        
        # Smaller cancel button
        cancel_btn = self._gtk.Button("cancel", "Cancel", "color4")
        cancel_btn.set_size_request(-1, 40)  # Reduced from 50
        cancel_btn.connect("clicked", self.cancel_slot_config)
        action_box.pack_start(cancel_btn, True, True, 0)
        
        left_box.pack_end(action_box, False, False, 0)
        
        # Right column - Selection panels (compact)
        self.right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)  # Reduced from 10
        
        # Compact welcome message for right column
        welcome_label = Gtk.Label(label="Select an option from the left\nto configure the slot")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)
        
        # Add columns to main grid
        main_grid.attach(left_box, 0, 0, 1, 1)
        main_grid.attach(self.right_box, 1, 0, 1, 1)
        
        self.content.add(main_grid)
        self.content.show_all()
    
    def show_material_selection(self, widget):
        """Show compact material selection in right column"""
        # Clear right column
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # Compact material selection title
        title = Gtk.Label(label="Select Material")
        title.get_style_context().add_class("description")  # Smaller title
        self.right_box.pack_start(title, False, False, 0)
        
        # Compact material list
        materials = ["PLA", "ABS", "PETG", "TPU", "ASA", "PVA", "HIPS", "PC"]
        
        for material in materials:
            material_btn = self._gtk.Button("filament", material, "color2")
            material_btn.set_size_request(-1, 35)  # Reduced from 50
            material_btn.connect("clicked", self.select_material, material)
            
            # Highlight current selection
            if material == self.config_material:
                material_btn.get_style_context().add_class("button_active")
            
            self.right_box.pack_start(material_btn, False, False, 3)  # Reduced spacing
        
        self.right_box.show_all()
    
    def select_material(self, widget, material):
        """Handle material selection"""
        self.config_material = material
        self.material_btn.set_label(f"Material: {material}")
        
        # Clear right column back to welcome message
        self.clear_right_column()
    
    def show_color_selection(self, widget):
        """Show compact color picker in right column"""
        # Clear right column
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # Compact color selection title
        #title = Gtk.Label(label="Select Color")
        #title.get_style_context().add_class("description")
        #self.right_box.pack_start(title, False, False, 0)
        
        # Compact current color preview and RGB display
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # Reduced spacing
        preview_box.set_halign(Gtk.Align.CENTER)
        
        self.right_color_preview = Gtk.EventBox()
        self.right_color_preview.set_size_request(40, 40)  # Smaller preview
        self.right_color_preview.get_style_context().add_class("ace_color_preview")
        self.set_color_preview(self.right_color_preview, self.config_color)
        preview_box.pack_start(self.right_color_preview, False, False, 0)
        
        self.rgb_display = Gtk.Label(label=f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
        self.rgb_display.get_style_context().add_class("description")
        preview_box.pack_start(self.rgb_display, False, False, 0)
        
        self.right_box.pack_start(preview_box, False, False, 5)  # Reduced margin
        
        # Compact RGB sliders
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)  # Reduced spacing
        
        self.color_sliders = {}
        for i, color_name in enumerate(['Red', 'Green', 'Blue']):
            color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)  # Reduced spacing
            
            label = Gtk.Label(label=f"{color_name[0]}:")  # Just first letter
            label.set_size_request(20, -1)  # Smaller label
            color_row.pack_start(label, False, False, 0)
            
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            slider.set_value(self.config_color[i])
            slider.set_size_request(150, 25)  # Smaller slider
            slider.set_draw_value(True)
            slider.set_value_pos(Gtk.PositionType.RIGHT)
            slider.connect("value-changed", self.on_color_slider_changed, i)
            self.color_sliders[i] = slider
            color_row.pack_start(slider, True, True, 0)
            
            slider_box.pack_start(color_row, False, False, 0)
        
        self.right_box.pack_start(slider_box, False, False, 5)
        
        # Compact color presets
        #presets_label = Gtk.Label(label="Presets")
        #presets_label.get_style_context().add_class("description")
        #self.right_box.pack_start(presets_label, False, False, 3)
        
        preset_colors = [
            ("White", [255, 255, 255]),
            ("Black", [0, 0, 0]),
            ("Red", [255, 0, 0]),
            ("Green", [0, 255, 0]),
            ("Blue", [0, 0, 255]),
            ("Yellow", [255, 255, 0])
        ]
        
        preset_grid = Gtk.Grid()
        preset_grid.set_row_spacing(3)  # Reduced spacing
        preset_grid.set_column_spacing(3)
        preset_grid.set_halign(Gtk.Align.CENTER)
        
        for i, (name, rgb) in enumerate(preset_colors):
            preset_btn = Gtk.Button(label=name)
            preset_btn.set_size_request(60, 25)  # Much smaller buttons
            
            # Set button color
            color = Gdk.RGBA(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0, 1.0)
            preset_btn.override_background_color(Gtk.StateFlags.NORMAL, color)
            
            # Set text color based on brightness
            brightness = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
            text_color = Gdk.RGBA(0, 0, 0, 1) if brightness > 128 else Gdk.RGBA(1, 1, 1, 1)
            preset_btn.override_color(Gtk.StateFlags.NORMAL, text_color)
            
            preset_btn.connect("clicked", self.select_color_preset, rgb[:])
            preset_grid.attach(preset_btn, i % 3, i // 3, 1, 1)  # 3 columns instead of 4
        
        self.right_box.pack_start(preset_grid, False, False, 5)
        
        # Compact apply color button
        apply_btn = self._gtk.Button("complete", "Apply Color", "color1")
        apply_btn.set_size_request(-1, 30)  # Smaller button
        apply_btn.connect("clicked", self.apply_color_selection)
        self.right_box.pack_end(apply_btn, False, False, 0)
        
        self.right_box.show_all()
    
    def on_color_slider_changed(self, slider, color_index):
        """Handle color slider changes"""
        value = int(slider.get_value())
        self.config_color[color_index] = value
        
        # Update preview and RGB display
        self.set_color_preview(self.right_color_preview, self.config_color)
        self.rgb_display.set_text(f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
    
    def select_color_preset(self, widget, rgb):
        """Handle color preset selection"""
        self.config_color = rgb[:]
        
        # Update sliders
        for i, value in enumerate(rgb):
            self.color_sliders[i].set_value(value)
        
        # Update preview and RGB display
        self.set_color_preview(self.right_color_preview, self.config_color)
        self.rgb_display.set_text(f"RGB: {self.config_color[0]},{self.config_color[1]},{self.config_color[2]}")
    
    def apply_color_selection(self, widget):
        """Apply selected color"""
        self.set_color_preview(self.config_color_preview, self.config_color)
        self.clear_right_column()
    
    def show_temperature_selection(self, widget):
        """Show temperature selection using Keypad like temperature.py"""
        # Clear right column
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        # Temperature selection title
        title = Gtk.Label(label="Set Temperature")
        title.get_style_context().add_class("temperature_entry")
        self.right_box.pack_start(title, False, False, 0)
        
        # Create keypad widget exactly like temperature.py
        if not hasattr(self, 'config_keypad'):
            self.config_keypad = Keypad(
                self._screen,
                self.handle_temperature_input,
                None,  # No PID calibrate
                self.clear_right_column,  # Close callback
            )
        
        # Set current temperature value
        self.config_keypad.clear()
        self.config_keypad.labels['entry'].set_text(str(self.config_temp))
        
        # Hide PID button
        self.config_keypad.show_pid(False)
        
        # Add keypad to right column
        self.right_box.pack_start(self.config_keypad, True, True, 0)
        
        self.right_box.show_all()
    
    def handle_temperature_input(self, temp):
        """Handle temperature input from keypad"""
        try:
            temp_value = int(float(temp))
            if 0 <= temp_value <= 300:
                self.config_temp = temp_value
                self.temp_btn.set_label(f"Temperature: {temp_value}°C")
                self.clear_right_column()
            else:
                self._screen.show_popup_message("Temperature must be between 0-300°C")
        except (ValueError, TypeError):
            self._screen.show_popup_message("Invalid temperature value")
    
    def clear_right_column(self, widget=None):
        """Clear right column and show welcome message"""
        for child in self.right_box.get_children():
            self.right_box.remove(child)
        
        welcome_label = Gtk.Label(label="Select an option from the left\nto configure the slot")
        welcome_label.set_justify(Gtk.Justification.CENTER)
        welcome_label.get_style_context().add_class("description")
        self.right_box.pack_start(welcome_label, True, True, 0)
        
        self.right_box.show_all()
    
    def save_slot_config(self, widget):
        """Save slot configuration"""
        slot = self.current_config_slot
        material = self.config_material
        color = f"{self.config_color[0]},{self.config_color[1]},{self.config_color[2]}"
        temp = self.config_temp
        
        # Update the stored slot data
        self.slot_data[slot] = {
            "material": material,
            "color": self.config_color[:],  # Copy the color array
            "temp": temp,
            "status": "ready"
        }
        
        # Update the slot display immediately
        self.slot_labels[slot].set_text(f"{material} {temp}°C")
        self.set_slot_color(self.slot_color_boxes[slot], self.config_color)
        
        # Send ACE_SET_SLOT command
        cmd = f"ACE_SET_SLOT INDEX={slot} COLOR={color} MATERIAL={material} TEMP={temp}"
        self._screen._ws.klippy.gcode_script(cmd)
        
        self._screen.show_popup_message(f"Slot {slot} configured: {material} {temp}°C", 1)
        
        # Refresh data and return to main screen
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        self.return_to_main_screen()
    
    def cancel_slot_config(self, widget):
        """Cancel configuration and return to main screen"""
        self.return_to_main_screen()
    
    def return_to_main_screen(self):
        """Return to main ACE panel screen"""
        # Clear content and recreate main screen
        for child in self.content.get_children():
            self.content.remove(child)
        
        # Recreate the main ACE panel layout
        self.create_main_screen()
    
    def create_main_screen(self):
        """Create the main ACE panel screen layout"""
        # Create main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_left(15)
        main_box.set_margin_right(15)
        main_box.set_margin_top(15)
        main_box.set_margin_bottom(15)
        
        # Top row with status and endless spool switch
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # ACE Status Display
        self.status_label = Gtk.Label(label="ACE: Ready")
        self.status_label.get_style_context().add_class("temperature_entry")
        self.status_label.set_size_request(-1, 40)
        self.status_label.set_halign(Gtk.Align.START)
        top_row.pack_start(self.status_label, True, True, 0)
        
        # Endless Spool switch section (top right)
        endless_spool_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        endless_spool_box.set_halign(Gtk.Align.END)
        
        # Endless spool label
        endless_label = Gtk.Label(label="Endless Spool:")
        endless_label.get_style_context().add_class("description")
        endless_spool_box.pack_start(endless_label, False, False, 0)
        
        # Endless spool switch
        self.endless_spool_switch = Gtk.Switch()
        self.endless_spool_switch.set_active(self.endless_spool_enabled)
        self.endless_spool_switch.connect("state-set", self.on_endless_spool_toggled)
        endless_spool_box.pack_start(self.endless_spool_switch, False, False, 0)
        
        top_row.pack_end(endless_spool_box, False, False, 0)
        main_box.pack_start(top_row, False, False, 0)
        
        # Slots container - horizontal layout
        slots_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        slots_box.set_homogeneous(True)
        
        self.slot_boxes = []
        self.slot_labels = []
        self.slot_color_boxes = []
        self.slot_buttons = []
        
        for slot in range(4):
            # Create clickable slot button (25% taller)
            slot_btn = Gtk.Button()
            slot_btn.get_style_context().add_class("ace_slot_button")  # More specific class
            slot_btn.set_relief(Gtk.ReliefStyle.NONE)
            slot_btn.set_size_request(-1, 125)  # 25% taller (was ~100px, now 125px)
            slot_btn.connect("clicked", self.on_slot_clicked, slot)
            
            # Slot content box
            slot_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            slot_content.set_margin_left(8)
            slot_content.set_margin_right(8)
            slot_content.set_margin_top(10)  # Slightly more top margin
            slot_content.set_margin_bottom(10)  # Slightly more bottom margin
            
            # Top row: color indicator and status
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            
            # Color rectangle
            color_box = Gtk.EventBox()
            color_box.set_size_request(20, 20)
            color_box.get_style_context().add_class("ace_slot_color_indicator")  # More specific class
            # Default to black color
            self.set_slot_color(color_box, [0, 0, 0])
            top_row.pack_start(color_box, False, False, 0)
            self.slot_color_boxes.append(color_box)
            
            # Slot label
            slot_label = Gtk.Label(label="Empty")
            slot_label.set_ellipsize(Pango.EllipsizeMode.END)  # Fixed: END instead of End
            slot_label.set_halign(Gtk.Align.START)
            slot_label.get_style_context().add_class("ace_slot_label")  # More specific class
            top_row.pack_start(slot_label, True, True, 0)
            self.slot_labels.append(slot_label)
            
            slot_content.pack_start(top_row, True, True, 0)
            
            # Slot number label
            slot_num_label = Gtk.Label(label=f"Slot {slot}")
            slot_num_label.get_style_context().add_class("ace_slot_number")  # More specific class
            slot_content.pack_start(slot_num_label, False, False, 0)
            
            slot_btn.add(slot_content)
            slots_box.pack_start(slot_btn, True, True, 0)
            self.slot_buttons.append(slot_btn)
        
        main_box.pack_start(slots_box, False, False, 0)
        
        # Settings cogs row - below slot boxes (smaller, closer)
        settings_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        settings_box.set_homogeneous(True)
        settings_box.set_margin_top(5)  # Closer to slot boxes
        
        for slot in range(4):
            # Create container to center the smaller button
            settings_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            settings_container.set_halign(Gtk.Align.CENTER)
            
            settings_btn = self._gtk.Button("settings", "", "color2")
            settings_btn.set_size_request(36, 27)  # 10% smaller (was 40x30, now 36x27)
            settings_btn.connect("clicked", self.show_slot_settings, slot)
            settings_btn.set_tooltip_text(f"Configure Slot {slot}")
            
            settings_container.pack_start(settings_btn, False, False, 0)
            settings_box.pack_start(settings_container, True, True, 0)
        
        main_box.pack_start(settings_box, False, False, 0)
        
        # Bottom row - Refresh and Dryer buttons
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        bottom_box.set_homogeneous(True)
        
        # Refresh button
        refresh_btn = self._gtk.Button("refresh", "Refresh Spool Data", "color3")
        refresh_btn.set_size_request(-1, 50)
        refresh_btn.connect("clicked", self.refresh_status)
        bottom_box.pack_start(refresh_btn, True, True, 0)
        
        # Dryer toggle button
        self.dryer_btn = self._gtk.Button("heat-up", "Start Dryer", "color2")
        self.dryer_btn.set_size_request(-1, 50)
        self.dryer_btn.connect("clicked", self.toggle_dryer_btn)
        bottom_box.pack_start(self.dryer_btn, True, True, 0)
        
        main_box.pack_start(bottom_box, False, False, 0)
        
        self.content.add(main_box)
        self.content.show_all()
        
        # Update status
        self.update_status()
    
    def show_slot_dialog(self, slot):
        """Create ultra-compact dialog for slot settings"""
        # Ultra-minimal dialog box
        dialog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)  # Tiny spacing
        dialog_box.set_margin_left(5)   # Minimal margins
        dialog_box.set_margin_right(5)
        dialog_box.set_margin_top(3)
        dialog_box.set_margin_bottom(3)
        
        # Store current values
        self.dialog_material = "PLA"
        self.dialog_color = [255, 255, 255]
        self.dialog_temp = 200
        
        # Material row - ultra compact
        material_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        material_label = Gtk.Label(label="Mat:")  # Shortened label
        material_label.set_size_request(35, -1)  # Smaller width
        material_row.pack_start(material_label, False, False, 0)
        
        type_combo = Gtk.ComboBoxText()
        materials = ["PLA", "ABS", "PETG", "TPU", "ASA"]  # Removed "Other"
        for material in materials:
            type_combo.append_text(material)
        type_combo.set_active(0)
        type_combo.connect("changed", self.on_material_changed)
        material_row.pack_start(type_combo, True, True, 0)
        dialog_box.pack_start(material_row, False, False, 0)
        
        # Color and Temperature in one row to save space
        color_temp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        # Color section
        color_label = Gtk.Label(label="Col:")
        color_label.set_size_request(35, -1)
        color_temp_row.pack_start(color_label, False, False, 0)
        
        # Tiny color preview
        self.dialog_color_preview = Gtk.EventBox()
        self.dialog_color_preview.get_style_context().add_class("ace_color_preview")
        self.dialog_color_preview.set_size_request(20, 15)  # Very small
        self.set_color_preview(self.dialog_color_preview, self.dialog_color)
        color_temp_row.pack_start(self.dialog_color_preview, False, False, 0)
        
        # Color picker button - compact
        color_btn = self._gtk.Button("", "Edit", "color1")
        color_btn.set_size_request(50, -1)  # Fixed small width
        color_btn.connect("clicked", self.on_color_clicked)
        color_temp_row.pack_start(color_btn, False, False, 0)
        
        # Temperature section in same row
        temp_label = Gtk.Label(label="T:")
        color_temp_row.pack_start(temp_label, False, False, 0)
        
        temp_btn = self._gtk.Button("", f"{self.dialog_temp}°", "color1")  # Removed "C"
        temp_btn.set_size_request(50, -1)  # Fixed small width
        temp_btn.connect("clicked", self.on_temp_clicked)
        color_temp_row.pack_start(temp_btn, False, False, 0)
        
        dialog_box.pack_start(color_temp_row, False, False, 0)
        
        # Store references for updating
        self.dialog_color_button = color_btn
        self.dialog_temp_button = temp_btn
        
        # Empty slot option - compact
        empty_check = Gtk.CheckButton(label="Mark empty")  # Shortened label
        dialog_box.pack_start(empty_check, False, False, 0)
        
        # Store reference for checking
        self.dialog_empty_check = empty_check
        
        buttons = [
            {"name": "Cancel", "response": Gtk.ResponseType.CANCEL},
            {"name": "Apply", "response": Gtk.ResponseType.OK}
        ]
        
        def slot_response(dialog, response_id):
            logging.info(f"ACE: Settings dialog response: {response_id}")
            try:
                if response_id == Gtk.ResponseType.OK:
                    if self.dialog_empty_check.get_active():
                        # Use ACE_SET_SLOT to mark as empty
                        self._screen._ws.klippy.gcode_script(f"ACE_SET_SLOT INDEX={slot} EMPTY=1")
                        self._screen.show_popup_message(f"Slot {slot} marked as empty", 1)
                    else:
                        material = self.dialog_material
                        color = f"{self.dialog_color[0]},{self.dialog_color[1]},{self.dialog_color[2]}"
                        temp = self.dialog_temp
                        
                        try:
                            # Validate values
                            if temp < 0 or temp > 300:
                                raise ValueError("Temperature must be between 0-300°C")
                            
                            # Use ACE_SET_SLOT to update slot data
                            cmd = f"ACE_SET_SLOT INDEX={slot} COLOR={color} MATERIAL={material} TEMP={temp}"
                            self._screen._ws.klippy.gcode_script(cmd)
                            
                            self._screen.show_popup_message(f"Slot {slot} configured: {material} {temp}°C", 1)
                            
                            # Refresh data after setting to get updated info
                            self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
                            
                        except ValueError as e:
                            self._screen.show_popup_message(f"Error: {e}")
                else:
                    logging.info("ACE: Settings dialog cancelled")
            except Exception as e:
                logging.error(f"ACE: Error in slot_response: {e}")
            finally:
                # Ensure dialog cleanup
                if hasattr(self._gtk, 'remove_dialog') and dialog:
                    self._gtk.remove_dialog(dialog)
                    logging.info("ACE: Settings dialog closed")
        
        self._gtk.Dialog(f"Slot {slot} Settings", buttons, dialog_box, slot_response)
    
    def on_material_changed(self, combo):
        """Handle material combo change"""
        self.dialog_material = combo.get_active_text()
    
    def on_color_clicked(self, widget):
        """Handle color picker button click"""
        def color_callback(rgb_values):
            logging.info(f"ACE: Color callback received: {rgb_values}")
            self.dialog_color = rgb_values
            self.dialog_color_button.set_label("Edit")  # Keep consistent label
            self.set_color_preview(self.dialog_color_preview, rgb_values)
        
        self.show_color_picker("Choose Color", self.dialog_color, color_callback)
    
    def on_temp_clicked(self, widget):
        """Handle temperature button click"""
        def temp_callback(value):
            self.dialog_temp = value
            self.dialog_temp_button.set_label(f"{value}°")
        
        # Use a simple number entry approach that works within dialogs
        self.show_number_input("Set Temperature", "Enter temperature (0-300°C):", 
                              self.dialog_temp, 0, 300, temp_callback)
    
    def toggle_dryer_btn(self, widget):
        """Toggle dryer on/off"""
        if self.dryer_enabled:
            # Stop dryer
            self._screen._ws.klippy.gcode_script("ACE_STOP_DRYING")
            self.dryer_btn.set_label("Start Dryer")
            self.dryer_btn.get_style_context().remove_class("color4")
            self.dryer_btn.get_style_context().add_class("color2")
            self.dryer_enabled = False
            self._screen.show_popup_message("Dryer stopped", 1)
        else:
            # Start dryer - show temperature dialog
            self.show_dryer_dialog()
    
    def show_dryer_dialog(self):
        """Show dialog to set dryer temperature"""
        def dryer_callback(value):
            # Start dryer
            self._screen._ws.klippy.gcode_script(f"ACE_START_DRYING TEMP={value} DURATION=240")
            self.dryer_btn.set_label("Stop Dryer")
            self.dryer_btn.get_style_context().remove_class("color2")
            self.dryer_btn.get_style_context().add_class("color4")
            self.dryer_enabled = True
            self._screen.show_popup_message(f"Dryer started at {value}°C", 1)
        
        self.show_number_input("Start Dryer", "Enter dryer temperature:", 45, 35, 55, dryer_callback)
    
    def update_status(self):
        """Update ACE status and slot information"""
        # Query ACE data
        self._screen._ws.klippy.gcode_script("ACE_QUERY_SLOTS")
        
        # Query current loaded index
        self._screen._ws.klippy.gcode_script("ACE_GET_CURRENT_INDEX")
        
        # Query endless spool status
        self._screen._ws.klippy.gcode_script("ACE_ENDLESS_SPOOL_STATUS")
        
        # Update loaded states
        self.update_slot_loaded_states()
    
    def process_update(self, action, data):
        """Process updates from Klipper"""
        if action == "notify_status_update":
            # Check for saved_variables updates
            if "saved_variables" in data:
                save_vars = data["saved_variables"]
                
                if isinstance(save_vars, dict) and "variables" in save_vars:
                    variables = save_vars["variables"]
                    if "ace_current_index" in variables:
                        new_value = int(variables["ace_current_index"])
                        logging.info(f"ACE: ace_current_index updated to: {new_value}")
                        if new_value != self.current_loaded_slot:
                            self.current_loaded_slot = new_value
                            self.update_slot_loaded_states()
        
        if action == "notify_gcode_response":
            # Parse different types of ACE responses
            response_str = str(data).strip()
            logging.info(f"ACE: Received gcode response: {response_str}")
            
            # Look for ACE_QUERY_SLOTS response - starts with "// [" 
            if response_str.startswith("// [") and response_str.endswith("]"):
                try:
                    # Remove the "// " prefix and parse JSON
                    json_str = response_str[3:].strip()  # Remove "// " prefix
                    slot_data = json.loads(json_str)
                    if isinstance(slot_data, list) and len(slot_data) > 0:
                        logging.info(f"ACE: Parsed slot data from ACE_QUERY_SLOTS: {slot_data}")
                        self.update_slots_from_data(slot_data)
                except json.JSONDecodeError as e:
                    logging.error(f"ACE: JSON decode error: {e}")
            
            # Look for ACE_GET_CURRENT_INDEX response - simple format like "// 0" or "// -1"
            elif response_str.startswith("// ") and response_str[3:].strip().lstrip('-').isdigit():
                try:
                    # Extract index number from response like "// 0", "// 2", or "// -1"
                    current_index = int(response_str[3:].strip())
                    logging.info(f"ACE: Got current index from ACE_GET_CURRENT_INDEX: {current_index}")
                    if current_index != self.current_loaded_slot:
                        self.current_loaded_slot = current_index
                        self.update_slot_loaded_states()
                except (ValueError, IndexError) as e:
                    logging.error(f"ACE: Error parsing ACE_GET_CURRENT_INDEX response '{response_str}': {e}")
            
            # Look for endless spool status responses - check for "Currently enabled" line with // prefix
            elif response_str.startswith("// - Currently enabled:"):
                if "Currently enabled: True" in response_str:
                    self.endless_spool_enabled = True
                    self.endless_spool_switch.set_active(True)
                    logging.info("ACE: Endless spool currently enabled")
                elif "Currently enabled: False" in response_str:
                    self.endless_spool_enabled = False
                    self.endless_spool_switch.set_active(False)
                    logging.info("ACE: Endless spool currently disabled")
            
            # Look for ACE command responses that might indicate tool changes
            elif "ACE:" in response_str:
                logging.info(f"ACE: Command response: {response_str}")
                
                # Look for tool change confirmations
                if "tool" in response_str.lower() and any(word in response_str.lower() for word in ["loaded", "changed", "active"]):
                    try:
                        # Try to extract slot number from response
                        import re
                        match = re.search(r'(\d+)', response_str)
                        if match:
                            new_slot = int(match.group(1))
                            if 0 <= new_slot <= 3:
                                logging.info(f"ACE: Tool change detected, updating to slot {new_slot}")
                                self.current_loaded_slot = new_slot
                                self.update_slot_loaded_states()
                    except Exception as e:
                        logging.error(f"ACE: Error parsing tool change response: {e}")
    
    def update_slots_from_data(self, slot_data):
        """Update slot display from ACE_QUERY_SLOTS data"""
        logging.info(f"ACE: Updating slots from ACE_QUERY_SLOTS data: {slot_data}")
        
        for i, slot in enumerate(slot_data):
            if i < 4:  # Ensure we don't exceed our 4 slots
                if slot.get('status') == 'ready':
                    material = slot.get('material', 'PLA')
                    temp = slot.get('temp', 200)
                    color = slot.get('color', [255, 255, 255])
                    
                    # Store the actual slot data
                    self.slot_data[i] = {
                        "material": material,
                        "color": color[:],  # Copy the color array
                        "temp": temp,
                        "status": "ready"
                    }
                    
                    self.slot_labels[i].set_text(f"{material} {temp}°C")
                    self.set_slot_color(self.slot_color_boxes[i], color)
                    logging.info(f"ACE: Updated slot {i}: {material} {temp}°C, color: {color}")
                else:
                    # Store empty slot data
                    self.slot_data[i] = {
                        "material": "PLA",
                        "color": [255, 255, 255],
                        "temp": 200,
                        "status": "empty"
                    }
                    
                    self.slot_labels[i].set_text("Empty")
                    self.set_slot_color(self.slot_color_boxes[i], [0, 0, 0])
                    logging.info(f"ACE: Slot {i} marked as empty")
        
        # Update loaded states after updating slot data
        self.update_slot_loaded_states()