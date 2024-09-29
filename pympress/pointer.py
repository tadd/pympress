# -*- coding: utf-8 -*-
#
#       pointer.py
#
#       Copyright 2017 Cimbali <me@cimba.li>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
"""
:mod:`pympress.pointer` -- Manage when and where to draw a software-emulated laser pointer on screen
----------------------------------------------------------------------------------------------------
"""

import logging
logger = logging.getLogger(__name__)

import enum

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib

from pympress import util, extras


class PointerMode(enum.Enum):
    """ Possible values for the pointer.
    """
    #: Pointer switched on continuously
    CONTINUOUS = 2
    #: Pointer switched on only manual
    MANUAL = 1
    #: Pointer never switched on
    DISABLED = 0


class Pointer(object):
    """ Manage and draw the software “laser pointer” to point at the slide.

    Displays a pointer of chosen color on the current slide (in both windows), either on all the time or only when
    clicking while ctrl pressed.

    Args:
        config (:class:`~pympress.config.Config`): A config object containing preferences
        builder (:class:`~pympress.builder.Builder`): A builder from which to load widgets
    """
    #: A `dict` of the :class:`~GdkPixbuf.Pixbuf` to read XML descriptions of GUIs and load them.
    pointers = {}
    #: `(float, float)` of position relative to slide, where the pointer should appear
    pointer_pos = (.5, .5)
    #: A `float` of drawing size of the pointer in ratio to the screen height
    size = .035
    #: A `str` name of the pointer to load
    color = None
    #: `bool` indicating whether we should show the pointer
    show_pointer = False
    #: :class:`~pympress.pointer.PointerMode` indicating the pointer mode
    pointer_mode = PointerMode.MANUAL
    #: The :class:`~pympress.pointer.PointerMode` to which we toggle back
    old_pointer_mode = PointerMode.CONTINUOUS
    #: A reference to the UI's :class:`~pympress.config.Config`, to update the pointer preference
    config = None
    #: :class:`~Gtk.DrawingArea` Slide in the Presenter window, used to reliably set cursors.
    p_da_cur = None
    #: :class:`~Gtk.DrawingArea` Slide in the Contents window, used to reliably set cursors.
    c_da     = None
    #: :class:`~Gtk.AspectFrame` Frame of the Contents window, used to reliably set cursors.
    c_frame  = None
    #: a `dict` of the :class:`~Gtk.RadioMenuItem` selecting the pointer mode
    pointermode_radios = {}

    #: callback, to be connected to :func:`~pympress.ui.UI.redraw_current_slide`
    redraw_current_slide = lambda *args: None
    #: callback, to be connected to :meth:`~pympress.app.Pympress.set_action_state`
    set_action_state = None

    def __init__(self, config, builder):
        super(Pointer, self).__init__()
        self.config = config

        builder.load_widgets(self)

        self.redraw_current_slide = builder.get_callback_handler('redraw_current_slide')
        self.set_action_state = builder.get_callback_handler('app.set_action_state')

        default_mode = config.get('presenter', 'pointer_mode')
        self.color = config.get('presenter', 'pointer')
        self.size = config.getfloat('presenter', 'pointer_size')

        try:
            default_mode = PointerMode[default_mode.upper()]
        except KeyError:
            default_mode = PointerMode.MANUAL

        self.activate_pointermode(default_mode)

        self.action_map = builder.setup_actions({
            'pointer-color': dict(activate=self.change_pointercolor, state=self.color, parameter_type=str),
            'pointer-mode': dict(activate=self.change_pointermode, state=default_mode.name.lower(), parameter_type=str),
        })


    def load_pointer(self, name, base_size):
        """ Perform the change of pointer using its size and widget name where to draw.

        Args:
            name (`str`): The widget name which associated with the loaded pointer for caching
            base_size (`int`): The basis of pointer size in pixels

        Returns:
            :class:`~GdkPixbuf.Pixbuf`: A pointer from the source image
        """
        if self.color not in ['red', 'green', 'blue']:
            raise ValueError('Wrong color name')
        size = round(base_size * self.size)  # in pixels
        pointer = self.pointers.get(name)
        if pointer is None or pointer.get_height() != size:
            path = util.get_icon_path('pointer_' + self.color + '.png')
            try:
                pointer = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
            except Exception:
                logger.exception(_('Failed loading pixbuf for pointer "{}" from: {}'.format(name, path)))
            self.pointers[name] = pointer
        return pointer

    def change_pointercolor(self, action, target):
        """ Callback for a radio item selection as pointer mode (continuous, manual, none).

        Args:
            action (:class:`~Gio.Action`): The action activatd
            target (:class:`~GLib.Variant`): The selected mode
        """
        self.color = target.get_string()
        self.pointers.clear()
        self.config.set('presenter', 'pointer', self.color)
        action.change_state(target)


    def activate_pointermode(self, mode=None):
        """ Activate the pointer as given by mode.

        Depending on the given mode, shows or hides the laser pointer and the normal mouse pointer.

        Args:
            mode (:class:`~pympress.pointer.PointerMode`): The mode to activate
        """
        # Set internal variables, unless called without mode (from ui, after windows have been mapped)
        if mode == self.pointer_mode:
            return
        elif mode is not None:
            self.old_pointer_mode, self.pointer_mode = self.pointer_mode, mode
            self.config.set('presenter', 'pointer_mode', self.pointer_mode.name.lower())


        # Set mouse pointer and cursors on/off, if windows are already mapped
        self.show_pointer = False
        for slide_widget in [self.p_da_cur, self.c_da]:
            ww, wh = slide_widget.get_allocated_width(), slide_widget.get_allocated_height()
            if max(ww, wh) == 1:
                continue

            pointer_x, pointer_y = -1, -1
            window = slide_widget.get_window()
            if window is not None:
                pointer_coords = window.get_pointer()
                pointer_x, pointer_y = pointer_coords.x, pointer_coords.y

            if 0 < pointer_x < ww and 0 < pointer_y < wh \
                    and self.pointer_mode == PointerMode.CONTINUOUS:
                # Laser activated right away
                self.pointer_pos = (pointer_x / ww, pointer_y / wh)
                self.show_pointer = True
                extras.Cursor.set_cursor(slide_widget, 'invisible')
            else:
                extras.Cursor.set_cursor(slide_widget, 'parent')

        self.redraw_current_slide()


    def change_pointermode(self, action, target):
        """ Callback for a radio item selection as pointer mode (continuous, manual, none).

        Args:
            action (:class:`~Gio.Action`): The action activatd
            target (:class:`~GLib.Variant`): The selected mode
        """
        if target is None or target.get_string() == 'toggle':
            mode = self.old_pointer_mode if self.pointer_mode == PointerMode.CONTINUOUS else PointerMode.CONTINUOUS
        else:
            mode = PointerMode[target.get_string().upper()]
        self.activate_pointermode(mode)

        action.change_state(GLib.Variant.new_string(mode.name.lower()))


    def render_pointer(self, cairo_context, widget, ww, wh):
        """ Draw the laser pointer on screen.

        Args:
            cairo_context (:class:`~cairo.Context`): The canvas on which to render the pointer
            widget (:class:`~Gtk.DrawingArea`): The widget to update
            ww (`int`): The widget width
            wh (`int`): The widget height
        """
        if self.show_pointer:
            pointer = self.load_pointer(widget.get_name(), wh)
            x = ww * self.pointer_pos[0] - pointer.get_width() / 2
            y = wh * self.pointer_pos[1] - pointer.get_height() / 2
            Gdk.cairo_set_source_pixbuf(cairo_context, pointer, x, y)
            cairo_context.paint()


    def track_pointer(self, widget, event):
        """ Move the laser pointer at the mouse location.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event`):  the GTK event.

        Returns:
            `bool`: whether the event was consumed
        """
        if self.show_pointer:
            ww, wh = widget.get_allocated_width(), widget.get_allocated_height()
            ex, ey = event.get_coords()
            self.pointer_pos = (ex / ww, ey / wh)
            self.redraw_current_slide()
            return True

        else:
            return False


    def track_enter_leave(self, widget, event):
        """ Switches laser off/on in continuous mode on leave/enter slides.

        In continuous mode, the laser pointer is switched off when the mouse leaves the slide
        (otherwise the laser pointer "sticks" to the edge of the slide).
        It is switched on again when the mouse reenters the slide.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event`):  the GTK event.

        Returns:
            `bool`: whether the event was consumed
        """
        # Only handle enter/leave events on one of the current slides
        if self.pointer_mode != PointerMode.CONTINUOUS or widget not in [self.c_da, self.p_da_cur]:
            return False

        if event.type == Gdk.EventType.ENTER_NOTIFY:
            self.show_pointer = True
            extras.Cursor.set_cursor(widget, 'invisible')

        elif event.type == Gdk.EventType.LEAVE_NOTIFY:
            self.show_pointer = False
            extras.Cursor.set_cursor(widget, 'parent')

        self.redraw_current_slide()
        return True


    def toggle_pointer(self, widget, event):
        """ Track events defining when the laser is pointing.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event`):  the GTK event.

        Returns:
            `bool`: whether the event was consumed
        """
        if self.pointer_mode in {PointerMode.DISABLED, PointerMode.CONTINUOUS}:
            return False

        ctrl_pressed = event.get_state() & Gdk.ModifierType.CONTROL_MASK

        if ctrl_pressed and event.type == Gdk.EventType.BUTTON_PRESS:
            self.show_pointer = True
            extras.Cursor.set_cursor(widget, 'invisible')

            # Immediately place & draw the pointer
            return self.track_pointer(widget, event)

        elif self.show_pointer and event.type == Gdk.EventType.BUTTON_RELEASE:
            self.show_pointer = False
            extras.Cursor.set_cursor(widget, 'parent')
            self.redraw_current_slide()
            return True

        else:
            return False


class PointerEditor(builder.Builder):
    """ UI that allows to configure laser pointer

    Args:
        config (:class:`~pympress.config.Config`): A config object containing preferences
        builder (:class:`~pympress.builder.Builder`): A builder from which to load widgets
    """
    #: Whether we are displaying the interface to configure pointer on screen
    pointer_editor_mode = False
    #: :class:`~Gtk.HBox` that replaces normal panes when pointer editing is on
    pointer_editor_overlay = None
    #: A :class:`~Gtk.OffscreenWindow` where we render the pointer editing interface when it's not shown
    pointer_editor_off_render = None

    def __init__(self, config, builder):
        super(PointerEditor, self).__init__()

        self.load_ui('pointer_editor')
        builder.load_widgets(self)
        self.get_application().add_window(self.pointer_editor_off_render)

        self.connect_signals(self)
        self.config = config

        self.load_preset(self.pen_action, int(active_pen) if active_pen.isnumeric() else 0)
        self.set_mode(None, GLib.Variant.new_string(config.get('highlight', 'mode')))


    def try_cancel(self):
        """ Cancel pointer editing, if it is enabled.

        Returns:
            `bool`: `True` if pointer editing got cancelled, `False` if it was already disabled.
        """
        if not self.pointer_editor_mode:
            return False
        
        self.disable_editing()
        return True


    def key_event(self, widget, event):
        """ Handle key events to activate the eraser while the shortcut is held

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event`):  the GTK event.

        Returns:
            `bool`: whether the event was consumed
        """
        if not self.pointer_editor_mode:
            return False
        elif event.type != Gdk.EventType.KEY_PRESS and event.type != Gdk.EventType.KEY_RELEASE:
            return False
        elif not (*event.get_keyval()[1:], event.get_state()) in self.toggle_erase_shortcuts:
            return False

        if event.type == Gdk.EventType.KEY_PRESS and self.active_preset and self.toggle_erase_source is None:
            self.previous_preset = self.active_preset
            self.toggle_erase_source = 'shortcut'
            self.load_preset(target=0)
        elif event.type == Gdk.EventType.KEY_RELEASE and self.toggle_erase_source == 'shortcut' \
                and self.previous_preset and not self.active_preset:
            self.load_preset(target=self.previous_preset)
            self.previous_preset = 0
            self.toggle_erase_source = None
        else:
            return False
        return True


    def toggle_pointer(self, widget, event):
        """ Start/stop pointer.

        Args:
            widget (:class:`~Gtk.Widget`):  the widget which has received the event.
            event (:class:`~Gdk.Event`):  the GTK event.

        Returns:
            `bool`: whether the event was consumed
        """
        if not self.pointer_editor_mode:
            return False

        if event.get_event_type() == Gdk.EventType.BUTTON_PRESS:
            eraser_button = event.get_source_device().get_source() == Gdk.InputSource.ERASER
            eraser_modifier = any(mod & event.get_state() == mod for mod in self.toggle_erase_modifiers)
            if (eraser_button or eraser_modifier) and self.active_preset and self.toggle_erase_source is None:
                self.previous_preset = self.active_preset
                self.toggle_erase_source = 'modifier'
                self.load_preset(target=0)

            self.scribble_list.append((self.scribble_color, self.scribble_width, [], []))
            self.scribble_drawing = True

            return self.track_scribble(widget, event)
        elif event.get_event_type() == Gdk.EventType.BUTTON_RELEASE:
            self.scribble_drawing = False
            self.prerender()

            if not self.active_preset and self.previous_preset and self.toggle_erase_source == 'modifier':
                self.load_preset(target=self.previous_preset)
                self.previous_preset = 0
                self.toggle_erase_source = None

            return True

        return False


    def update_size(self, widget, event, value):
        """ Callback for the size chooser slider, to set scribbling size.

        Args:
            widget (:class:`~Gtk.Scale`): The slider control used to select the scribble size
            event (:class:`~Gdk.Event`):  the GTK event triggering this update.
            value (`int`): the size of the scribbles to be drawn
        """
        self.scribble_size = max(1, min(100, 10 ** value if value < 1 else 10 + (value - 1) * 90))
        self.update_active_color_size()


    def switch_editing(self, gaction, target=None):
        """ Starts the mode where one can read on top of the screen.

        Args:

        Returns:
            `bool`: whether the event was consumed
        """
        if target is not None and target == self.pointer_editor_mode:
            return False

        # Perform the state toggle
        if self.pointer_editor_mode:
            return self.disable_editing()
        else:
            return self.enable_editing()


    def enable_editing(self):
        """ Enable the pointer editing mode.

        Returns:
            `bool`: whether it was possible to enable (thus if it was not enabled already)
        """
        if self.pointer_editor_mode:
            return False

        self.pointer_editor_off_render.remove(self.pointer_editor_overlay)
        self.load_layout('highlight')

        self.p_central.queue_draw()
        self.pointer_editor_overlay.queue_draw()

        # Get frequent events for smooth drawing
        self.p_central.get_window().set_event_compression(False)

        self.pointer_editor_mode = True
        self.get_application().lookup_action('highlight').change_state(GLib.Variant.new_boolean(self.pointer_editor_mode))
        self.pen_action.set_enabled(self.pointer_editor_mode)

        self.p_central.queue_draw()
        extras.Cursor.set_cursor(self.scribble_p_da, 'invisible')
        return True


    def disable_editing(self):
        """ Disable the pointer editing mode.

        Returns:
            `bool`: whether it was possible to disable (thus if it was not disabled already)
        """
        if not self.pointer_editor_mode:
            return False

        self.pointer_editor_mode = False

        extras.Cursor.set_cursor(self.scribble_p_da, 'default')
        self.load_layout(None)
        self.pointer_editor_off_render.add(self.pointer_editor_overlay)
        window = self.p_central.get_window()
        if window:
            window.set_event_compression(True)

        self.get_application().lookup_action('highlight').change_state(GLib.Variant.new_boolean(self.pointer_editor_mode))
        self.pen_action.set_enabled(self.pointer_editor_mode)

        self.p_central.queue_draw()
        extras.Cursor.set_cursor(self.p_central)
        self.mouse_pos = None

        return True


    def load_preset(self, gaction=None, target=None):
        """ Loads the preset color of a given number or designed by a given widget, as an event handler.

        Args:
            gaction (:class:`~Gio.Action`): the action triggering the call
            target (:class:`~GLib.Variant`): the new preset to load, as a string wrapped in a GLib.Variant

        Returns:
            `bool`: whether the preset was loaded
        """
        if isinstance(target, int):
            self.active_preset = target
        else:
            self.active_preset = int(target.get_string()) if target.get_string() != 'eraser' else 0

        target = str(self.active_preset) if self.active_preset else 'eraser'

        self.config.set('highlight', 'active_pen', target)
        self.pen_action.change_state(GLib.Variant.new_string(target))
        self.scribble_color, self.scribble_size = self.color_size[self.active_preset]

        # Presenter-side setup
        self.scribble_color_selector.set_rgba(self.scribble_color)
        self.scribble_size_selector.set_value(math.log10(self.scribble_size) if self.scribble_size < 10
                                               else 1 + (self.scribble_size - 10) / 90)
        self.scribble_color_selector.set_sensitive(target != 'eraser')

        # Re-draw the eraser
        self.scribble_p_da.queue_draw()
        self.c_da.queue_draw()

        return True
