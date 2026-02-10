# macropad

Program the **12-key + 2-knob USB macro pad** (VID:PID `1189:8840`, WCH CH552G) from Linux/macOS with Python. No Windows app needed.

Supports single keys, modifier combos (`ctrl+c`), multi-key macros (`["h","e","l","l","o"]`), per-layer LED colors, and 3 layers with 12 keys + 2 rotary encoders each.

## Setup

```bash
pip install pyusb
```

You also need libusb installed:

- **Debian/Ubuntu:** `sudo apt install libusb-1.0-0-dev`
- **Fedora:** `sudo dnf install libusb1-devel`
- **macOS:** `brew install libusb`

On Linux, either run with `sudo` or add a udev rule for non-root access:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1189", ATTR{idProduct}=="8840", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-macropad.rules
sudo udevadm control --reload-rules
# unplug and replug the pad
```

## Usage

```bash
python3 program_macropad.py                      # program from macropad.json
python3 program_macropad.py -c myconfig.json     # use a different config
python3 program_macropad.py --generate-config    # create a default macropad.json
python3 program_macropad.py --read               # read current config from the pad
python3 program_macropad.py --led blue wave      # set LEDs without editing JSON
```

## Config file

Edit `macropad.json`. Each layer maps button names to key bindings, with an optional LED setting:

```json
{
  "layers": {
    "1": {
      "led": {"color": "blue", "effect": "static"},
      "delay": 50,
      "key1": "a",
      "key2": "ctrl+c",
      "key3": ["h", "e", "l", "l", "o"],
      "knob1_left": "left",
      "knob1_right": "right",
      "knob2_press": "enter"
    }
  }
}
```

### Buttons

| Name | Controls |
|------|----------|
| `key1` - `key12` | The 12 main keys |
| `knob1_left`, `knob1_press`, `knob1_right` | First rotary encoder |
| `knob2_left`, `knob2_press`, `knob2_right` | Second rotary encoder |

### Key bindings

| Format | Example | What it does |
|--------|---------|--------------|
| Single key | `"a"` | Types a |
| Modifier combo | `"ctrl+c"` | Ctrl+C |
| Multi-key macro | `["h","e","l","l","o"]` | Types hello |
| Macro with mods | `["ctrl+a","ctrl+c"]` | Select all, copy |

**Modifiers:** `ctrl`, `shift`, `alt`, `meta` (or `win`/`cmd`/`gui`)

**Keys:** `a`-`z`, `0`-`9`, `f1`-`f24`, `enter`, `esc`, `tab`, `space`, `backspace`, `delete`, `insert`, `home`, `end`, `pageup`, `pagedown`, `up`, `down`, `left`, `right`, `minus`, `equal`, `lbracket`, `rbracket`, `backslash`, `semicolon`, `quote`, `grave`, `comma`, `period`, `slash`, `capslock`, `printscreen`, `scrolllock`, `pause`, `mute`, `volume_up`, `volume_down`

### Macro delay

Add a per-layer delay between keystrokes in multi-key macros (in milliseconds):

```json
"delay": 50
```

Set to `0` or omit to disable. Applies to all macros on that layer.

### LEDs

Each layer can have its own LED color and effect. Add `"led"` to a layer:

```json
"led": {"color": "cyan", "effect": "wave"}
```

| Colors | `off`, `red`, `orange`, `yellow`, `green`, `cyan`, `blue`, `purple` |
|--------|------|
| **Effects** | `off`, `static`, `ripple`, `wave`, `reactive`, `white` |

- **static** -- solid color
- **ripple** -- sequential wave on keypress
- **wave** -- continuous color wave
- **reactive** -- lights up only the pressed key
- **white** -- static white (ignores color)

Quick LED change from the command line (applies to all layers):

```bash
python3 program_macropad.py --led green static
python3 program_macropad.py --led blue wave
python3 program_macropad.py --led off          # LEDs off
```

## Protocol

Reverse-engineered from USB captures of the Windows `MINI_KEYBOARD.exe` app. All reports are 65 bytes (report ID `0x03` + 64 bytes data).

**Write a button:** `03 fd <button_id> <layer> 01 00 00 00 00 00 <type> <mod> <key> ...` then `03 fd fe ff` (commit), 200ms delay.

**Layer config (LEDs):** `03 fe b0 <layer> 08 <60 bytes>` then commit. Byte 12 = `(color << 4) | effect`.

**Macro delay:** `03 fd 00 <layer> 05 <delay_lo> <delay_hi>` then commit. 16-bit LE milliseconds.

**Save to flash:** `03 ef 03`

**Read buttons:** `03 fa 0f 03 <layer> 05` on OUT, 24 responses on IN.
