# ViewSonic Projector Indigo Plugin

A robust Indigo plugin for controlling ViewSonic projectors via RS-232 over TCP (Global Caché iTach).

Built for reliability with proper state tracking, debounce logic, and real-world serial timing handling.

---

## ✨ Features

* Power control (On / Off)
* Accurate power state tracking:

  * Off
  * Warming
  * On
  * Cooling
* Input selection:

  * HDMI 1
  * HDMI 2
* Smart polling (no false triggers)
* RS-232 debug logging (TX/RX)
* Lamp hours tracking
* Maintenance alerts with configurable threshold
* Friendly maintenance summary display
* Indigo-native device states and triggers

---

## 🧰 Requirements

* Indigo 2025.x or later
* Global Caché iTach IP2SL (or compatible TCP → RS-232 bridge)
* ViewSonic projector supporting RS-232 control

---

## ⚙️ Setup

1. Install the plugin in Indigo
2. Create a new device:

   * Type: **ViewSonic Projector**
3. Configure:

   * IP Address of Global Caché device
   * TCP Port (default: `4999`)
   * Poll interval (recommended: 30s)
4. Save device

---

## 🔌 Supported Commands

* Power On / Off
* Power Status Query
* Input Select (HDMI 1 / HDMI 2)
* Input Status Query
* Lamp Hours Query
* Equivalent Lamp Hours Query

---

## 📊 Device States

| State               | Description                     |
| ------------------- | ------------------------------- |
| power               | Current power state             |
| input               | Active input                    |
| lampHours           | Lamp usage hours                |
| equivalentLampHours | Equivalent light source hours   |
| maintenanceDue      | Boolean maintenance flag        |
| maintenanceSummary  | Friendly summary string         |
| readyState          | True when projector is fully ON |

---

## 🛠 Debug Logging

Enable via:

```
Plugin Menu → Configure → Enable RS232 Logging
```

Shows:

* TX (sent commands)
* RX (responses)
* Decoded messages

---

## ⚠️ Notes

* Uses debounce logic to avoid false cooling/warming triggers
* Optimized for Global Caché serial buffering behavior
* Avoids polling during transition states for stability

---

## 📦 Version

Current version: **v1.4.3**

---

## 👨‍💻 Author

Built with help from ChatGPT and refined in real-world AV deployment.

---

## 📄 License

MIT License
