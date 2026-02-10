#!/usr/bin/env python3
"""
Program the 12-key + 2 knob macro pad (USB 1189:8840, WCH CH552G).

Uses a JSON config file for key mappings. Supports single keys,
modifier combos (ctrl+c), and multi-key macros (["h","e","l","l","o"]).

Usage:
  python3 program_macropad.py                        # program from macropad.json
  python3 program_macropad.py -c myconfig.json       # use a different config
  python3 program_macropad.py --generate-config      # write a default macropad.json
  python3 program_macropad.py --read                 # read current config from pad
  python3 program_macropad.py --led blue wave         # set all LEDs (color + effect)
  python3 program_macropad.py --dump                 # log sent packets to hex file
"""

import json
import os
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    print("Install pyusb: pip install pyusb", file=sys.stderr)
    sys.exit(1)

# --- Device ---
VENDOR_ID = 0x1189
PRODUCT_ID = 0x8840
REPORT_SIZE = 65  # 1 byte report ID + 64 bytes data

# --- Button IDs (1-based, 24 per layer) ---
# On the 12-key + 2-knob model:
#   Keys 1-12:  0x01-0x0C
#   Knob 2:     0x10 (left), 0x11 (press), 0x12 (right)
#   Knob 1:     0x13 (right), 0x14 (press), 0x15 (left)  -- reversed order
#   IDs 0x0D-0x0F and 0x16-0x18 exist but have no physical control
BUTTONS_PER_LAYER = 24
NUM_LAYERS = 3

# --- HID modifier bits ---
MODIFIER = {
    "none": 0x00,
    "ctrl": 0x01, "control": 0x01, "lctrl": 0x01,
    "shift": 0x02, "lshift": 0x02,
    "alt": 0x04, "option": 0x04, "lalt": 0x04,
    "meta": 0x08, "win": 0x08, "cmd": 0x08, "gui": 0x08,
}

# --- HID key codes ---
KEY = {
    "none": 0x00,
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08, "f": 0x09,
    "g": 0x0A, "h": 0x0B, "i": 0x0C, "j": 0x0D, "k": 0x0E, "l": 0x0F,
    "m": 0x10, "n": 0x11, "o": 0x12, "p": 0x13, "q": 0x14, "r": 0x15,
    "s": 0x16, "t": 0x17, "u": 0x18, "v": 0x19, "w": 0x1A, "x": 0x1B,
    "y": 0x1C, "z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30,
    "backslash": 0x31, "semicolon": 0x33, "quote": 0x34, "grave": 0x35,
    "comma": 0x36, "period": 0x37, "slash": 0x38, "capslock": 0x39,
    "f1": 0x3A, "f2": 0x3B, "f3": 0x3C, "f4": 0x3D, "f5": 0x3E,
    "f6": 0x3F, "f7": 0x40, "f8": 0x41, "f9": 0x42, "f10": 0x43,
    "f11": 0x44, "f12": 0x45,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "home": 0x4A, "pageup": 0x4B,
    "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "f13": 0x68, "f14": 0x69, "f15": 0x6A, "f16": 0x6B,
    "f17": 0x6C, "f18": 0x6D, "f19": 0x6E, "f20": 0x6F,
    "f21": 0x70, "f22": 0x71, "f23": 0x72, "f24": 0x73,
    "mute": 0x7F, "volume_up": 0x80, "volume_down": 0x81,
}

# Reverse lookup for pretty-printing
_KEY_NAME = {v: k for k, v in KEY.items() if v != 0}

# --- Button name <-> ID mapping (for JSON config) ---

BUTTON_NAMES = {
    "key1": 0x01, "key2": 0x02, "key3": 0x03, "key4": 0x04,
    "key5": 0x05, "key6": 0x06, "key7": 0x07, "key8": 0x08,
    "key9": 0x09, "key10": 0x0A, "key11": 0x0B, "key12": 0x0C,
    "knob1_left": 0x15, "knob1_press": 0x14, "knob1_right": 0x13,
    "knob2_left": 0x10, "knob2_press": 0x11, "knob2_right": 0x12,
}
_BUTTON_ID_TO_NAME = {v: k for k, v in BUTTON_NAMES.items()}

# --- LED presets: byte = (color << 4) | effect ---

LED_COLORS = {
    "off": 0,
    "red": 1, "orange": 2, "yellow": 3, "green": 4,
    "cyan": 5, "blue": 6, "purple": 7,
}
LED_EFFECTS = {
    "off": 0, "static": 1, "ripple": 2,
    "wave": 3, "reactive": 4, "white": 5,
}
_LED_COLOR_NAME = {v: k for k, v in LED_COLORS.items()}
_LED_EFFECT_NAME = {v: k for k, v in LED_EFFECTS.items()}


def parse_led_config(layer_dict):
    """
    Extract LED settings from a layer's JSON dict.
    Returns (effect_int, color_int) or None if no LED config present.
    """
    led = layer_dict.get("led")
    if led is None:
        return None

    if isinstance(led, str):
        # Shorthand: just a color name, default to static
        color = _resolve_led_color(led)
        return (1, color)

    if isinstance(led, dict):
        color = _resolve_led_color(led.get("color", "red"))
        effect = _resolve_led_effect(led.get("effect", "static"))
        return (effect, color)

    raise ValueError(f"Invalid led config: {led!r}")


def _resolve_led_color(val):
    """Resolve color name, int, or numeric string to color index 0-7."""
    if isinstance(val, int):
        return val & 0x0F
    val = val.lower().strip()
    if val in LED_COLORS:
        return LED_COLORS[val]
    if val.isdigit():
        return int(val) & 0x0F
    raise ValueError(f"Unknown LED color {val!r}. Known: {', '.join(LED_COLORS.keys())} (or 0-7)")


def _resolve_led_effect(val):
    """Resolve effect name, int, or numeric string to effect index 0-7."""
    if isinstance(val, int):
        return val & 0x0F
    val = val.lower().strip()
    if val in LED_EFFECTS:
        return LED_EFFECTS[val]
    if val.isdigit():
        return int(val) & 0x0F
    raise ValueError(f"Unknown LED effect {val!r}. Known: {', '.join(LED_EFFECTS.keys())} (or 0-7)")


def make_led_byte(effect, color):
    """Encode color and effect into the single LED byte: (color << 4) | effect."""
    return ((color & 0x0F) << 4) | (effect & 0x0F)


DEFAULT_CONFIG = {
    "_comment": "See README.md for config format, available keys, and LED options.",
    "layers": {
        "1": {
            "led": {"color": "red", "effect": "static"},
            "key1": "a", "key2": "b", "key3": "c",
            "key4": "d", "key5": "e", "key6": "f",
            "key7": "g", "key8": "h", "key9": "i",
            "key10": "j", "key11": "k", "key12": "l",
            "knob1_left": "pagedown", "knob1_press": "space", "knob1_right": "pageup",
            "knob2_left": "left", "knob2_press": "enter", "knob2_right": "right"
        },
        "2": {
            "led": {"color": "blue", "effect": "static"},
            "key1": "f1", "key2": "f2", "key3": "f3",
            "key4": "f4", "key5": "f5", "key6": "f6",
            "key7": "f7", "key8": "f8", "key9": "f9",
            "key10": "f10", "key11": "f11", "key12": "f12",
            "knob1_left": "pagedown", "knob1_press": "space", "knob1_right": "pageup",
            "knob2_left": "left", "knob2_press": "enter", "knob2_right": "right"
        },
        "3": {
            "led": {"color": "green", "effect": "static"},
            "key1": "f13", "key2": "f14", "key3": "f15",
            "key4": "f16", "key5": "f17", "key6": "f18",
            "key7": "f19", "key8": "f20", "key9": "f21",
            "key10": "f22", "key11": "f23", "key12": "f24",
            "knob1_left": "pagedown", "knob1_press": "space", "knob1_right": "pageup",
            "knob2_left": "left", "knob2_press": "enter", "knob2_right": "right"
        }
    }
}


def _parse_single_binding(value):
    """Parse one keystroke: "a", "ctrl+c", or {"key": "c", "mod": "ctrl"}."""
    if isinstance(value, str):
        if "+" in value:
            parts = value.lower().split("+")
            mod_val = 0
            for p in parts[:-1]:
                p = p.strip()
                if p not in MODIFIER:
                    raise ValueError(f"Unknown modifier {p!r}. Known: {', '.join(MODIFIER.keys())}")
                mod_val |= MODIFIER[p]
            return (parts[-1].strip(), mod_val)
        return (value, 0)
    if isinstance(value, dict):
        return (value.get("key", "none"), value.get("mod", 0))
    raise ValueError(f"Invalid binding: {value!r}")


def parse_binding(value):
    """
    Parse a button binding from JSON into a list of (key, modifier) tuples.

    Formats: "a", "ctrl+c", {"key":"c","mod":"ctrl"}, ["h","e","l","l","o"]
    """
    if isinstance(value, list):
        return [_parse_single_binding(item) for item in value]
    return [_parse_single_binding(value)]


def load_config(path):
    """
    Load a JSON config file.

    Returns (bindings, leds) where:
      bindings: {layer_int: [(button_id, [(key, mod), ...]), ...]}
      leds: {layer_int: (effect, color)} or empty dict if no LED config
    """
    with open(path) as f:
        raw = json.load(f)

    layers_data = raw.get("layers", {})
    bindings = {}
    leds = {}

    for layer_str, layer_dict in layers_data.items():
        layer_num = int(layer_str)
        if layer_num < 1 or layer_num > NUM_LAYERS:
            print(f"  Warning: ignoring layer {layer_num} (must be 1-{NUM_LAYERS})", file=sys.stderr)
            continue

        # Extract LED config (if present)
        led_cfg = parse_led_config(layer_dict)
        if led_cfg is not None:
            leds[layer_num] = led_cfg

        # Extract button bindings (skip non-button keys like "led", "_comment")
        layer_bindings = []
        for btn_name, value in layer_dict.items():
            btn_name_lower = btn_name.lower().strip()
            if btn_name_lower not in BUTTON_NAMES:
                continue  # skip "led", "_comment", etc.
            btn_id = BUTTON_NAMES[btn_name_lower]
            keys = parse_binding(value)
            layer_bindings.append((btn_id, keys))

        bindings[layer_num] = layer_bindings

    return bindings, leds


def generate_config(path):
    """Write the default config to a JSON file."""
    with open(path, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    print(f"  Default config written to {path}")
    print(f"  Edit it, then run: python3 program_macropad.py -c {path}")


# --- Low-level USB helpers ---

def _keycode(k):
    """Resolve key name (str) or int to HID keycode int."""
    if isinstance(k, str):
        k = k.lower().strip()
        if k not in KEY:
            raise KeyError(f"Unknown key {k!r}. Known keys: {', '.join(sorted(KEY.keys()))}")
        return KEY[k]
    return int(k)


def _modifier(m):
    """Resolve modifier name (str) or int to modifier byte."""
    if isinstance(m, str):
        return MODIFIER.get(m.lower().strip(), 0)
    return int(m or 0)


def make_report(*first_bytes):
    """Build a 65-byte report: first_bytes + zero-padding."""
    data = list(first_bytes) + [0] * (REPORT_SIZE - len(first_bytes))
    return bytes(data[:REPORT_SIZE])


DUMP_SENT_PACKETS = None  # set to a file path to log packets (--dump)


def send(ep, data: bytes):
    """Send one 65-byte report to the interrupt OUT endpoint."""
    assert len(data) == REPORT_SIZE
    if DUMP_SENT_PACKETS:
        with open(DUMP_SENT_PACKETS, "a") as f:
            f.write(data.hex() + "\n")
    ep.write(data, timeout=2000)


# --- Endpoint discovery ---

def _find_endpoint(dev, direction, transfer_type=usb.util.ENDPOINT_TYPE_INTR):
    """Find an endpoint by direction and transfer type."""
    cfg = dev.get_active_configuration()
    for intf in cfg:
        ep = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == direction
                and usb.util.endpoint_type(e.bmAttributes) == transfer_type
            ),
        )
        if ep is not None:
            return ep
    label = "OUT" if direction == usb.util.ENDPOINT_OUT else "IN"
    raise ValueError(f"No interrupt {label} endpoint found")


def find_out_endpoint(dev):
    return _find_endpoint(dev, usb.util.ENDPOINT_OUT)


def find_in_endpoint(dev):
    return _find_endpoint(dev, usb.util.ENDPOINT_IN)


# --- Per-button write ---

def write_button(ep, button_id, layer, keys):
    """Write one button binding and commit (200ms delay)."""
    # Resolve all keys
    resolved = []
    for k, m in keys:
        resolved.append((_modifier(m), _keycode(k)))

    key_count = len(resolved)
    if key_count == 0:
        key_count = 1
        resolved = [(0, 0)]

    # Build the write packet
    #   Bytes 0-8: 03 fd <button> <layer> 01 00 00 00 00
    #   Byte 9:    00 (padding, required)
    #   Byte 10:   type/count (01 for single key, N for N-key macro)
    #   Bytes 11+: (mod, keycode) pairs
    payload = [
        0x03, 0xFD,
        button_id & 0xFF,
        layer & 0xFF,
        0x01, 0x00, 0x00, 0x00, 0x00,  # bytes 4-8
        0x00,                            # byte 9: padding
        key_count & 0xFF,                # byte 10: type/count
    ]
    for mod_byte, kc in resolved:
        payload.append(mod_byte & 0xFF)
        payload.append(kc & 0xFF)

    # Pad to 65 bytes
    payload += [0] * (REPORT_SIZE - len(payload))
    send(ep, bytes(payload[:REPORT_SIZE]))

    # Commit
    send(ep, make_report(0x03, 0xFD, 0xFE, 0xFF))

    # Delay (Windows app uses Sleep(200) after commits)
    time.sleep(0.2)


def write_layer_config(ep, layer, config_byte, config_data=None):
    """Send 03 fe b0 <layer> <config_byte> + 60 bytes, then commit."""
    if config_data is None:
        config_data = bytes(60)
    else:
        config_data = bytes(config_data[:60])
        config_data += bytes(60 - len(config_data))

    payload = [0x03, 0xFE, 0xB0, layer & 0xFF, config_byte & 0xFF] + list(config_data)
    payload = payload[:REPORT_SIZE]
    send(ep, bytes(payload))

    # Commit
    send(ep, make_report(0x03, 0xFD, 0xFE, 0xFF))
    time.sleep(0.2)


def write_all_layer_configs(ep, layers, leds=None, led_only=False):
    """
    Send layer configs for each programmed layer.

    0x08 packet carries LED settings (byte 12 = (color << 4) | effect).
    0x05 packet carries other layer config (skipped in LED-only mode).
    """
    if leds is None:
        leds = {}

    for layer in sorted(layers):
        # Build 0x08 config (carries LED settings)
        config_data_08 = bytearray(60)
        config_data_08[5] = 0x01
        led_cfg = leds.get(layer)
        if led_cfg:
            effect, color = led_cfg
            config_data_08[7] = make_led_byte(effect, color)
            led_desc = f"{_LED_EFFECT_NAME.get(effect, str(effect))} {_LED_COLOR_NAME.get(color, str(color))}"
        else:
            config_data_08[7] = 0x11  # default: static red
            led_desc = "static red (default)"

        write_layer_config(ep, layer, 0x08, config_data_08)

        if not led_only:
            # 0x05 config (other layer settings -- NOT LED)
            # Keep byte 7 at 0x10 (original capture value), don't mirror LED byte
            config_data_05 = bytearray(60)
            config_data_05[0] = 0xd0
            config_data_05[5] = 0x01
            config_data_05[7] = 0x10  # original capture value (not LED-related)
            write_layer_config(ep, layer, 0x05, config_data_05)

        print(f"    Layer {layer}: LED={led_desc}")


def save_to_board(ep):
    """Persist configuration to device flash: 03 ef 03."""
    send(ep, make_report(0x03, 0xEF, 0x03))
    time.sleep(0.2)
    print("  Save to board sent (03 ef 03)")


# --- Read current config ---

def read_all_buttons(ep_out, ep_in, layer):
    """
    Read all 24 button bindings for one layer.

    Sends 03 fa 0f 03 <layer> 05 on OUT, reads 24 responses on IN.
    Returns dict: {button_id: (type, [(mod, keycode), ...])}
    layer: 1-based (1, 2, 3)
    """
    send(ep_out, make_report(0x03, 0xFA, 0x0F, 0x03, layer & 0xFF, 0x05))

    result = {}
    for _ in range(BUTTONS_PER_LAYER):
        try:
            data = ep_in.read(REPORT_SIZE, timeout=1000)
        except usb.core.USBTimeoutError:
            break
        if len(data) < 13:
            continue
        # Response layout matches write: 03 fa <btn> <layer> 01 00 00 00 00 00 <type> <mod> <key> ...
        btn_id = data[2]
        binding_type = data[10]
        keys = []
        for i in range(binding_type):
            off = 11 + i * 2
            if off + 1 < len(data):
                keys.append((data[off], data[off + 1]))
        result[btn_id] = (binding_type, keys)

    return result


def print_config(ep_out, ep_in):
    """Read and print the full device config (all layers)."""
    for layer in range(1, NUM_LAYERS + 1):
        print(f"\n  Layer {layer}:")
        buttons = read_all_buttons(ep_out, ep_in, layer)
        if not buttons:
            print("    (no response)")
            continue
        for btn_id in sorted(buttons.keys()):
            btype, keys = buttons[btn_id]
            if btype == 0 or (btype == 1 and keys and keys[0] == (0, 0)):
                continue  # skip unbound
            names = []
            for mod, kc in keys:
                mod_str = ""
                if mod & 0x01: mod_str += "ctrl+"
                if mod & 0x02: mod_str += "shift+"
                if mod & 0x04: mod_str += "alt+"
                if mod & 0x08: mod_str += "meta+"
                key_str = _KEY_NAME.get(kc, f"0x{kc:02x}")
                names.append(f"{mod_str}{key_str}")
            btn_name = _BUTTON_ID_TO_NAME.get(btn_id, f"button 0x{btn_id:02x}")
            binding_str = ', '.join(names) if names else "(unbound)"
            print(f"    {btn_name}: {binding_str}")


# --- Program all buttons ---

def program_from_config(ep, config, leds=None):
    """
    Program buttons from parsed config: {layer: [(button_id, [(key, mod), ...]), ...]}.
    Then send layer configs (required for knob changes to persist).

    leds: optional {layer_int: (effect, color)} for per-layer LED settings.
    """
    total = 0
    for layer_num in sorted(config.keys()):
        bindings = config[layer_num]
        print(f"  Programming layer {layer_num} ({len(bindings)} buttons)...")
        for btn_id, keys in bindings:
            # Skip unbound
            if len(keys) == 1:
                kc = _keycode(keys[0][0])
                if kc == 0 and _modifier(keys[0][1]) == 0:
                    continue

            write_button(ep, btn_id, layer_num, keys)
            total += 1

            btn_name = _BUTTON_ID_TO_NAME.get(btn_id, f"0x{btn_id:02x}")
            key_desc = _describe_keys(keys)
            print(f"    {btn_name} -> {key_desc}")

    print(f"  Wrote {total} buttons total.")

    # Send layer configs with LED settings (required by capture protocol)
    print("  Sending layer configs...")
    write_all_layer_configs(ep, sorted(config.keys()), leds=leds)


def _describe_keys(keys):
    """Pretty-print a list of (key, mod) tuples."""
    parts = []
    for k, m in keys:
        mod_val = _modifier(m)
        prefix = ""
        if mod_val & 0x01: prefix += "ctrl+"
        if mod_val & 0x02: prefix += "shift+"
        if mod_val & 0x04: prefix += "alt+"
        if mod_val & 0x08: prefix += "meta+"
        parts.append(f"{prefix}{k}")
    if len(parts) == 1:
        return parts[0]
    return "[" + ", ".join(parts) + "]"


# --- Device setup ---

def open_device():
    """Find and configure the macropad, return (dev, ep_out, ep_in)."""
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print(f"Device {VENDOR_ID:04x}:{PRODUCT_ID:04x} not found. Plug in the macro pad.", file=sys.stderr)
        sys.exit(1)

    # Detach kernel HID driver if needed, then set configuration
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno == 16:  # Resource busy
            for i in range(4):
                try:
                    if dev.is_kernel_driver_active(i):
                        dev.detach_kernel_driver(i)
                except (NotImplementedError, usb.core.USBError):
                    pass
            try:
                dev.set_configuration()
            except usb.core.USBError as e2:
                print("Cannot configure device. Try: sudo python3 program_macropad.py", file=sys.stderr)
                raise e from e2
        else:
            raise

    ep_out = find_out_endpoint(dev)
    try:
        ep_in = find_in_endpoint(dev)
    except ValueError:
        ep_in = None  # read won't work but write still can

    print(f"  Device found: {VENDOR_ID:04x}:{PRODUCT_ID:04x}")
    print(f"  OUT endpoint: 0x{ep_out.bEndpointAddress:02x}")
    if ep_in:
        print(f"  IN endpoint:  0x{ep_in.bEndpointAddress:02x}")

    return dev, ep_out, ep_in


# --- Main ---

DEFAULT_CONFIG_PATH = "macropad.json"


def main():
    global DUMP_SENT_PACKETS

    # Parse args
    args = sys.argv[1:]
    config_path = DEFAULT_CONFIG_PATH
    i = 0
    while i < len(args):
        if args[i] in ("-c", "--config") and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        else:
            i += 1

    if "--dump" in args:
        DUMP_SENT_PACKETS = "macropad_sent_packets.hex"
        open(DUMP_SENT_PACKETS, "w").close()
        print(f"  Dumping sent packets to {DUMP_SENT_PACKETS}")

    # --generate-config: write default JSON and exit
    if "--generate-config" in args:
        generate_config(config_path)
        return

    # --read: read current config from the pad and exit
    if "--read" in args:
        dev, ep_out, ep_in = open_device()
        if ep_in is None:
            print("No IN endpoint found; cannot read config.", file=sys.stderr)
            sys.exit(1)
        print_config(ep_out, ep_in)
        return

    # --led <color> [effect]: quick LED-only programming for all layers
    if "--led" in args:
        led_idx = args.index("--led")
        led_args = args[led_idx + 1:]
        if not led_args:
            print("Usage: --led <color> [effect]", file=sys.stderr)
            print(f"  Colors: {', '.join(LED_COLORS.keys())}", file=sys.stderr)
            print(f"  Effects: {', '.join(LED_EFFECTS.keys())}", file=sys.stderr)
            sys.exit(1)
        color = _resolve_led_color(led_args[0])
        effect = _resolve_led_effect(led_args[1]) if len(led_args) > 1 else 1
        leds = {layer: (effect, color) for layer in range(1, NUM_LAYERS + 1)}
        dev, ep_out, ep_in = open_device()
        print("  Setting LEDs (all layers)...")
        write_all_layer_configs(ep_out, list(range(1, NUM_LAYERS + 1)), leds=leds, led_only=True)
        save_to_board(ep_out)
        print("\n  Done.")
        return

    # Normal mode: load config and program
    if not os.path.exists(config_path):
        print(f"  Config file not found: {config_path}")
        print(f"  Generating default config...")
        generate_config(config_path)
        print(f"  Edit {config_path} to set your key mappings, then run again.")
        return

    print(f"  Loading config: {config_path}")
    config, leds = load_config(config_path)
    if not config:
        print("  No layers/bindings found in config.", file=sys.stderr)
        sys.exit(1)

    total_bindings = sum(len(b) for b in config.values())
    led_msg = f", {len(leds)} LED setting(s)" if leds else ""
    print(f"  {len(config)} layer(s), {total_bindings} binding(s) to write{led_msg}")

    dev, ep_out, ep_in = open_device()
    program_from_config(ep_out, config, leds=leds)
    save_to_board(ep_out)

    # Verify writes by reading back layer 1 knob buttons
    if ep_in is not None:
        print("\n  Verifying writes (reading back layer 1)...")
        verify_ok = True
        try:
            readback = read_all_buttons(ep_out, ep_in, 1)
            # Check a few buttons to confirm writes took effect
            if 1 in config:
                for btn_id, expected_keys in config[1]:
                    if btn_id not in readback:
                        continue
                    btype, actual_keys = readback[btn_id]
                    if len(expected_keys) == 1:
                        exp_kc = _keycode(expected_keys[0][0])
                        exp_mod = _modifier(expected_keys[0][1])
                        if actual_keys and (actual_keys[0] != (exp_mod, exp_kc)):
                            btn_name = _BUTTON_ID_TO_NAME.get(btn_id, f"0x{btn_id:02x}")
                            actual_str = f"mod=0x{actual_keys[0][0]:02x} key=0x{actual_keys[0][1]:02x}"
                            expected_str = f"mod=0x{exp_mod:02x} key=0x{exp_kc:02x}"
                            print(f"    WARNING: {btn_name}: expected {expected_str}, got {actual_str}")
                            verify_ok = False
                if verify_ok:
                    print("    All verified OK")
        except Exception as e:
            print(f"    Verify read failed: {e}")

    print("\n  Done. Unplug and replug the pad if the new mapping doesn't take effect.")


PERMISSION_MSG = """
USB access denied. Either:

  1. Run with sudo:
     sudo python3 program_macropad.py

  2. Or add a udev rule (one-time):
     echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1189", ATTR{idProduct}=="8840", MODE="0666"' | sudo tee /etc/udev/rules.d/99-macropad.rules
     sudo udevadm control --reload-rules
     (then unplug and replug the pad)
"""


if __name__ == "__main__":
    try:
        main()
    except usb.core.USBError as e:
        if e.errno == 13:  # EACCES
            print("Error: insufficient permissions.", file=sys.stderr)
            print(PERMISSION_MSG, file=sys.stderr)
            sys.exit(1)
        raise
